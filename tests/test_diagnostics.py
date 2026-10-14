import asyncio
import errno
import os
import subprocess
import threading
import time

import pytest

from local_llm_admin.control import ControlService
from local_llm_admin.diagnostics import Diagnostics, ProbeRunner, lock_info
from local_llm_admin.engines import ComfyClient
from local_llm_admin.ingestion import IngestStore
from local_llm_admin.jobs import JobRunner, JobStore
from local_llm_admin.mlx import MlxClient


class FakeSampler:
    def __init__(self):
        self.pressure = "normal"

    def current(self):
        return {"pressure": self.pressure}


def refused(addr, timeout):
    raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")


@pytest.fixture
def diag(fake_comfy):
    s = fake_comfy.settings
    s.probe_timeout_s = 0.5
    mlx = MlxClient(s)
    comfy = ComfyClient(s, transport=fake_comfy.transport)
    path = s.admin_data_dir / "admin.sqlite"
    store = JobStore(path)
    runner = JobRunner(s, store, current_name=lambda: "qwen")
    control = ControlService(s, store, runner, mlx, comfy)
    control.store.set_kv("intent", {"value": "off"})
    probes = ProbeRunner()
    d = Diagnostics(
        s,
        mlx,
        control,
        FakeSampler(),
        IngestStore(path),
        probes,
        watchdog_state=lambda: {"enabled": True, "suspended": control.recovery_blocked()},
        connect=refused,
    )
    yield d
    probes.shutdown()


def _by_id(checks):
    return {c["id"]: c for c in checks}


async def test_healthy_baseline(diag):
    checks = _by_id(await diag.run())
    for cid in ("drive", "drive_access", "mlx_lock", "engine_conflict", "memory", "watchdog"):
        assert checks[cid]["status"] == "ok", checks[cid]
    assert checks["admin_python"]["status"] == "ok"
    assert os.path.realpath(__import__("sys").executable) in checks["admin_python"]["evidence"]
    for c in checks.values():
        assert c["observed_at"] > 0 and set(c) >= {"title", "evidence", "fix", "action", "stale"}


async def test_network_error_kinds(diag):
    checks = _by_id(await diag.run())
    assert checks["litellm_network"]["status"] == "fail"
    assert "refused" in checks["litellm_network"]["evidence"]

    def unreachable(addr, timeout):
        raise OSError(errno.EHOSTUNREACH, "No route to host")

    diag.connect = unreachable
    net = _by_id(await diag.run())["litellm_network"]
    assert f"errno {errno.EHOSTUNREACH}" in net["evidence"]
    assert "Local Network" in net["fix"] and "possible" in net["evidence"]

    def timeout(addr, timeout):
        raise TimeoutError("timed out")

    diag.connect = timeout
    assert "no answer" in _by_id(await diag.run())["litellm_network"]["evidence"]

    diag.connect = lambda addr, timeout: _Closeable()
    assert _by_id(await diag.run())["litellm_network"]["status"] == "ok"


class _Closeable:
    def close(self):
        pass


async def test_missing_drive(diag):
    diag.settings.mlx_root = diag.settings.mlx_root.parent / "gone"
    checks = _by_id(await diag.run())
    assert checks["drive"]["status"] == "fail" and "Mount" in checks["drive"]["fix"]


async def test_hung_probe_is_bounded_and_not_duplicated(diag, monkeypatch):
    release = threading.Event()
    calls = []

    def hang():
        calls.append(1)
        release.wait(10)
        return None

    monkeypatch.setattr(diag.mlx, "access_error", hang)
    t0 = time.monotonic()
    first = _by_id(await diag.run())["drive_access"]
    assert first["status"] == "unknown" and "timed out" in first["evidence"]
    second = _by_id(await diag.run())["drive_access"]
    assert second["status"] == "unknown" and second["stale"] is True
    assert "still running" in second["evidence"]
    assert time.monotonic() - t0 < 3 and len(calls) == 1
    release.set()
    await asyncio.sleep(0.1)
    assert _by_id(await diag.run())["drive_access"]["status"] == "ok"
    assert len(calls) == 2


def _owner(settings, pid, start, cmd="start_server"):
    lock = settings.run_dir / "mlx.lock"
    lock.mkdir()
    (lock / "owner").write_text(f"pid={pid}\nstart={start}\ncmd={cmd}\n")


def _lstart(pid):
    return subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()


async def test_lock_states(diag):
    s = diag.settings
    p = subprocess.Popen(["sleep", "30"])
    try:
        _owner(s, p.pid, _lstart(p.pid))
        info = lock_info(s)
        assert info["state"] == "live" and info["owner"]["pid"] == p.pid
        c = _by_id(await diag.run())["mlx_lock"]
        assert c["status"] == "warn" and c["action"] is None
    finally:
        p.kill()
        p.wait()
    assert lock_info(s)["state"] == "stale"
    c = _by_id(await diag.run())["mlx_lock"]
    assert c["status"] == "fail" and c["action"] == "unlock"


async def test_lock_pid_reuse_is_stale_and_garbage_is_unknown(diag):
    s = diag.settings
    _owner(s, os.getpid(), "Thu Jan  1 00:00:00 1970")
    assert lock_info(s)["state"] == "stale"
    (s.run_dir / "mlx.lock/owner").write_text("nonsense\n")
    assert lock_info(s)["state"] == "unknown"
    assert _by_id(await diag.run())["mlx_lock"]["status"] == "unknown"


async def test_legacy_lock_without_owner(diag):
    (diag.settings.run_dir / "mlx.lock").mkdir()
    info = lock_info(diag.settings)
    assert info["state"] == "stale" and info["legacy"] is True


async def test_memory_pressure(diag):
    diag.sampler.pressure = "critical"
    assert _by_id(await diag.run())["memory"]["status"] == "fail"
    diag.sampler.pressure = "warn"
    assert _by_id(await diag.run())["memory"]["status"] == "warn"
    diag.sampler.pressure = None
    assert _by_id(await diag.run())["memory"]["status"] == "unknown"


async def test_watchdog_and_intent(diag):
    diag.control.store.set_kv("recovery_suspended", {"reason": "second crash"})
    diag.control.store.set_kv("intent", {"value": "unknown"})
    checks = _by_id(await diag.run())
    assert checks["watchdog"]["status"] == "fail" and checks["watchdog"]["action"] == "resume"
    assert "second crash" in checks["watchdog"]["evidence"]
    assert checks["intent"]["status"] == "warn"


async def test_request_errors_grouped(diag):
    now = time.time()
    events = [
        {
            "kind": "error",
            "message": "Error loading model gpt-4o",
            "ts": now - i,
            "observed": now,
            "signature": "sig1",
            "dedup": f"d{i}",
            "data": {},
        }
        for i in range(3)
    ]
    diag.events.commit(
        "mlx-server",
        {
            "dev": 1,
            "inode": 1,
            "offset": 0,
            "carry": b"",
            "skipping": 0,
            "gap_at": None,
            "updated": now,
        },
        events,
    )
    c = _by_id(await diag.run())["request_errors"]
    assert c["status"] == "warn" and "3×" in c["evidence"] and "gpt-4o" in c["evidence"]
    assert "openai//Volumes/Models/models/active" in c["fix"]


async def test_engine_conflict(diag, fake_comfy):
    subprocess.run([str(diag.settings.comfyui_path), "start"], check=False)
    subprocess.run(
        [
            "sh",
            "-c",
            f"sleep 60 >/dev/null 2>&1 & echo $! > {diag.settings.run_dir}/mlx-server.pid",
        ],
        check=False,
    )
    assert _by_id(await diag.run())["engine_conflict"]["status"] == "fail"


# --- M6: the decision sidecar ------------------------------------------------------------


async def test_decision_check_hidden_when_disabled(diag):
    assert "decision" not in _by_id(await diag.run())


async def test_decision_check_states(diag, fake_decision):
    from local_llm_admin.decision import DecisionClient
    from local_llm_admin.sidecars import write_intent

    diag.control.decision = DecisionClient(fake_decision.settings)
    check = _by_id(await diag.run())["decision"]
    assert check["status"] == "ok" and "stopped" in check["evidence"]
    assert "weights complete" in check["evidence"]
    fake_decision.start()
    check = _by_id(await diag.run())["decision"]
    assert check["status"] == "ok" and "running pid" in check["evidence"]
    subprocess.run([str(fake_decision.settings.decision_path), "stop"], capture_output=True)
    write_intent(diag.control.store, "openjev", "running")
    check = _by_id(await diag.run())["decision"]
    assert check["status"] == "warn" and "started from the admin" in check["evidence"]
    (fake_decision.state / "decision_weights").write_text("incomplete")
    check = _by_id(await diag.run())["decision"]
    assert check["status"] == "fail" and "weights incomplete" in check["evidence"]
    fake_decision.settings.decision_bin = fake_decision.root / "missing"
    check = _by_id(await diag.run())["decision"]
    assert check["status"] == "fail" and "openjev" in check["evidence"]


async def test_sidecar_conflict_check(diag, fake_tts, fake_decision):
    from local_llm_admin.sidecars import SidecarRegistry

    from .test_control_sidecars import _race_tts

    diag.control.sidecars = SidecarRegistry(fake_tts.settings)
    by = _by_id(await diag.run())
    assert by["sidecar_conflict"]["status"] == "ok" and by["tts"]["status"] == "ok"
    fake_decision.start()
    _race_tts(fake_tts)
    assert _by_id(await diag.run())["sidecar_conflict"]["status"] == "fail"


async def test_embed_service_check(diag, fake_embed):
    from local_llm_admin.sidecars import SidecarRegistry, embed_spec, write_service_intent

    from .test_control import _start_comfy

    diag.control.sidecars = SidecarRegistry(fake_embed.settings)
    by = _by_id(await diag.run())
    assert by["embeddings"]["status"] == "ok" and "stopped" in by["embeddings"]["evidence"]
    assert "sidecar_conflict" not in by  # one slot sidecar or none: the service never counts
    write_service_intent(diag.control.store, embed_spec(fake_embed.settings), "running")
    assert _by_id(await diag.run())["embeddings"]["status"] == "warn"
    _start_comfy(diag.control)
    check = _by_id(await diag.run())["embeddings"]
    assert check["status"] == "ok" and "ComfyUI" in check["evidence"]
