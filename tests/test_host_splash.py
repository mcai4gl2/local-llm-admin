"""Host/admin profile contract parity without an installed Splash runtime."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import sysconfig
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from .host_harness import HOST, Host, make_host, redirect
from .test_splash import captured_status, catalog, write


@pytest.fixture
def host_profiles() -> Iterator[ModuleType]:
    path = Path(__file__).resolve().parent.parent / "host/splash/lib/profiles.py"
    spec = importlib.util.spec_from_file_location("splash_host_profiles", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    yield module
    del sys.modules[spec.name]


def test_standalone_host_reads_the_same_private_profiles(
    tmp_path: Path, host_profiles: ModuleType
) -> None:
    path, doc = catalog(tmp_path)
    assert (
        host_profiles.load_profiles(path, tmp_path)["stock-8bit-32k"].model == "example/stock-8bit"
    )
    doc["profiles"]["stock-8bit-32k"]["aliases"] = ["/Volumes/Models/models/active"]
    write(path, doc)
    with pytest.raises(host_profiles.ProfileError):
        host_profiles.load_profiles(path, tmp_path)


FAKE_SPLASH_SERVER = r"""
import argparse
import json
import os
import signal
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('prepared')
p.add_argument('--binary')
p.add_argument('--model')
p.add_argument('--host')
p.add_argument('--port', type=int)
p.add_argument('--max-context', type=int, default=32768)
p.add_argument('--served-model-name', action='append', default=[])
args, _ = p.parse_known_args()
root = Path(__file__).resolve().parents[2]
run = root / 'run'
run.mkdir(exist_ok=True)
(run / 'launch.json').write_text(json.dumps({'argv': sys.argv, 'env': {k: os.environ.get(k) for k in ('SPLASH_API_KEY', 'HOME', 'PYTHONPATH', 'HF_HOME', 'UV_CACHE_DIR', 'TMPDIR')}}))
if (run / 'fail_start').exists():
    sys.exit(3)
native_code = "import os,signal,time; from pathlib import Path; r=Path(os.readlink('/proc/self/exe')).parents[2]/'run'; signal.signal(signal.SIGTERM, signal.SIG_IGN if (r/'ignore_term').exists() else signal.SIG_DFL); time.sleep(300)"
child = subprocess.Popen([args.binary, '-c', native_code, 'serve-native', args.prepared])
(run / 'native-pid').write_text(str(child.pid))
(root.parent.parent / ('state/listen_' + str(args.port))).write_text(str(os.getpid()))

def terminate(*unused: object) -> None:
    if not (run / 'ignore_term').exists():
        os._exit(0)
signal.signal(signal.SIGTERM, terminate)

class Handler(BaseHTTPRequestHandler):

    def log_message(self, *unused: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path not in ('/ready', '/health') and self.headers.get('Authorization') != 'Bearer ' + os.environ['SPLASH_API_KEY']:
            self.send_response(401)
            self.end_headers()
            return
        ready = not (run / 'loading').exists()
        docs = {'/health': {'status': 'ok'}, '/ready': {'status': 'ready' if ready else 'unavailable'}, '/status': {'ready': ready, 'maximum_context_tokens': args.max_context, 'instance': {'pid': os.getpid(), 'model': args.model}, 'kv': {}, 'state': {}, 'identity': {'kv': {'format': 'bf16'}}, 'memory_plan': {'valid': True, 'maximum_context_tokens': 32768, 'model': {'kv_format': 'bf16', 'state_format': 'fp32'}}, 'weights': {'released': False, 'restoring': False}}, '/v1/models': {'data': [{'id': name, 'max_model_len': args.max_context} for name in [args.model, *args.served_model_name]]}}
        self.send_response(503 if self.path == '/ready' and (not ready) else 200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(docs.get(self.path, {})).encode())
HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
"""


class SplashHost:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.root = host.root / "splash"
        self.path, self.doc = catalog(self.root)
        self.cli = redirect(HOST / "splash/bin/splash", self.root / "bin/splash", host.root)
        for rel in ("profiles.py", "control.py"):
            redirect(
                HOST / "splash/lib" / rel,
                self.root / "lib" / rel,
                host.root,
                {
                    "READY_TIMEOUT = 300.0": "READY_TIMEOUT = 1.0",
                    "STOP_TIMEOUT = 30.0": "STOP_TIMEOUT = 0.3",
                }
                if rel == "control.py"
                else None,
            )
        redirect(HOST / "bin/engine-guard.sh", host.root / "bin/engine-guard.sh", host.root)
        for rel in ("python/bin/python3", "engine/splash"):
            path = self.root / "runtime" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sys.executable, path)
            # Standalone/uv Python discovers its stdlib relative to the copied executable.
            library = (
                path.parent.parent
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
            )
            library.parent.mkdir(parents=True, exist_ok=True)
            library.symlink_to(Path(sysconfig.get_path("stdlib")), target_is_directory=True)
        server = self.root / "runtime/server"
        server.mkdir()
        (server / "__init__.py").write_text("")
        (server / "server.py").write_text(FAKE_SPLASH_SERVER)
        (self.root / "run").mkdir()
        env = self.root / "etc/splash.env"
        env.write_text("SPLASH_API_KEY=" + "s" * 40 + "\nSPLASH_HOST=127.0.0.1\n")
        env.chmod(384)
        write(
            self.root / "etc/runtime.json",
            {"runtime_version": "1.4.0", "runtime_sha256": self.doc["runtime_sha256"]},
        )

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.host.run(*args, cli=self.cli, timeout=15)

    @property
    def record(self) -> Path:
        return self.root / "run/splash.json"

    def flag(self, name: str) -> None:
        (self.root / "run" / name).touch()


@pytest.fixture
def splash_host(tmp_path: Path) -> Iterator[SplashHost]:
    host = make_host(tmp_path)
    splash = SplashHost(host)
    yield splash
    splash.run("stop")
    import subprocess

    subprocess.run(
        [
            str(splash.root / "runtime/python/bin/python3"),
            "-I",
            str(splash.root / "lib/control.py"),
            str(splash.root),
            "stop",
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    try:
        os.kill(int((splash.root / "run/native-pid").read_text()), signal.SIGKILL)
    except (OSError, ValueError):
        pass
    import psutil

    expected = {
        str((splash.root / "runtime" / rel).resolve())
        for rel in ("python/bin/python3", "engine/splash")
    }
    for proc in psutil.process_iter(["exe", "cmdline"]):
        if proc.info["exe"] in expected:
            try:
                proc.kill()
            except psutil.Error:
                pass
    host.cleanup()


def test_wrapper_start_observes_parent_and_native_and_stops_both(splash_host: SplashHost) -> None:
    h = splash_host
    result = h.run("start", "stock-8bit-32k")
    assert result.returncode == 0, result.stdout + result.stderr
    before = h.record.read_bytes()
    status = h.run("status", "--json")
    assert status.returncode == 0, status.stdout + status.stderr
    doc = json.loads(status.stdout)
    assert doc["state"] == "ready" and doc["profile"] == "stock-8bit-32k"
    assert {p["role"] for p in doc["processes"]} == {"controller", "native"}
    assert before == h.record.read_bytes()
    assert "s" * 40 not in status.stdout + status.stderr
    stopped = h.run("stop")
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert json.loads(h.run("status", "--json").stdout)["state"] == "absent"
    assert h.run("stop").returncode == 0


@pytest.mark.parametrize(
    "problem",
    [
        "bad-profile",
        "bad-mode",
        "missing-runtime",
        "bad-pin",
        "missing-asset",
        "foreign-port",
        "maintenance",
        "lifecycle",
    ],
)
def test_wrapper_refuses_before_launch(splash_host: SplashHost, problem: str) -> None:
    h = splash_host
    profile = "stock-8bit-32k"
    if problem == "bad-profile":
        profile = "../bad"
    if problem == "bad-mode":
        h.path.chmod(420)
    if problem == "missing-runtime":
        (h.root / "runtime/engine/splash").unlink()
    if problem == "bad-pin":
        write(h.root / "etc/runtime.json", {"runtime_version": "latest"})
    if problem == "missing-asset":
        (h.root / "assets/stock-8bit/model.safetensors").unlink()
    if problem == "foreign-port":
        proc = h.host.sleeper()
        (h.host.state / "listen_8080").write_text(str(proc.pid))
    if problem == "maintenance":
        h.host.gate.mkdir()
    if problem == "lifecycle":
        h.host.lock.mkdir()
    result = h.run("start", profile)
    assert result.returncode != 0
    assert not h.record.exists()
    assert not (h.root / "run/native-pid").exists()


@pytest.mark.parametrize("flag", ["fail_start", "loading"])
def test_failed_start_cleans_only_owned_generations(splash_host: SplashHost, flag: str) -> None:
    h = splash_host
    h.flag(flag)
    result = h.run("start", "stock-8bit-32k")
    assert result.returncode != 0
    status = json.loads(h.run("status", "--json").stdout)
    assert status["state"] == "absent", status


def test_stop_surviving_native_when_parent_is_gone(splash_host: SplashHost) -> None:
    h = splash_host
    result = h.run("start", "stock-8bit-32k")
    assert result.returncode == 0, result.stdout + result.stderr
    doc = json.loads(h.record.read_text())
    os.kill(doc["controller"]["pid"], signal.SIGKILL)
    time.sleep(0.05)
    status = json.loads(h.run("status", "--json").stdout)
    assert status["state"] != "absent"
    result = h.run("stop")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(h.run("status", "--json").stdout)["state"] == "absent"


def test_stop_escalates_only_owned_unchanged_generations(splash_host: SplashHost) -> None:
    h = splash_host
    h.flag("ignore_term")
    result = h.run("start", "stock-8bit-32k")
    assert result.returncode == 0, result.stdout + result.stderr
    result = h.run("stop")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(h.run("status", "--json").stdout)["state"] == "absent"


@pytest.mark.parametrize(
    ("rel", "action"),
    [
        ("bin/mlx-model", "start"),
        ("bin/mlx-model", "stop"),
        ("comfyui/bin/comfyui", "start"),
        ("strata/bin/strata", "start"),
        ("decision/bin/openjev", "start"),
        ("tts/bin/qwen-tts", "start"),
        ("embed/bin/qwen-embed", "start"),
    ],
)
def test_direct_wrappers_refuse_splash_even_without_a_listener(
    splash_host: SplashHost, rel: str, action: str
) -> None:
    h = splash_host
    result = h.run("start", "stock-8bit-32k")
    assert result.returncode == 0, result.stdout + result.stderr
    (h.host.state / "listen_8080").unlink()
    cli = redirect(HOST / rel, h.host.root / rel, h.host.root)
    args = (action, "a") if rel == "bin/mlx-model" and action == "start" else (action,)
    (h.host.state / "healthy").touch()
    result = h.host.run(*args, cli=cli)
    assert result.returncode != 0 and "Splash" in result.stderr, result.stdout + result.stderr
    assert json.loads(h.run("status", "--json").stdout)["state"] == "ready"


@pytest.fixture
def host_control() -> Iterator[ModuleType]:
    path = Path(__file__).resolve().parent.parent / "host/splash/lib/control.py"
    spec = importlib.util.spec_from_file_location("splash_host_control_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    yield module
    del sys.modules[spec.name]


def test_process_table_failure_is_unknown_not_absence(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        host_control.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="")
    )
    with pytest.raises(host_control.HostError):
        host_control.possible_pids(tmp_path)
    assert host_control.observe(tmp_path)["state"] == "unknown"


def test_signal_revalidates_generation_and_does_not_signal_reused_pid(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    member = {"pid": 100, "started": 1.0, "executable": "/runtime/engine/splash", "role": "native"}
    calls = []
    monkeypatch.setattr(host_control.os, "kill", lambda *args: calls.append(args))
    monkeypatch.setattr(host_control, "_member", lambda *args: {**member, "started": 2.0})
    with pytest.raises(host_control.HostError):
        host_control._signal(tmp_path, {"prepared": "prepared"}, member, signal.SIGTERM)
    assert calls == []
    monkeypatch.setattr(host_control, "_member", lambda *args: dict(member))
    host_control._signal(tmp_path, {"prepared": "prepared"}, member, signal.SIGTERM)
    assert calls == [(100, signal.SIGTERM)]


@pytest.mark.parametrize("bad", ["profile", "fingerprint", "native-count", "role", "creation-time"])
def test_invalid_private_ownership_record_is_unknown(
    host_control: ModuleType, tmp_path: Path, bad: str
) -> None:
    member = {
        "pid": 100,
        "started": 1.0,
        "executable": str(tmp_path / "runtime/python/bin/python3"),
        "role": "controller",
    }
    doc = {
        "version": 1,
        "profile": "stock-8bit-32k",
        "fingerprint": "a" * 20,
        "model": "example/qwen",
        "prepared": str(tmp_path / "prepared/model"),
        "controller": member,
        "native": [],
    }
    if bad == "profile":
        doc["profile"] = {}
    if bad == "fingerprint":
        doc["fingerprint"] = "invalid"
    if bad == "native-count":
        doc["native"] = [{**member, "role": "native"}] * 5
    if bad == "role":
        member["role"] = "native"
    if bad == "creation-time":
        member["started"] = float("nan")
    (tmp_path / "run").mkdir()
    write(tmp_path / "run/splash.json", doc)
    with pytest.raises(host_control.HostError):
        host_control._record(tmp_path)


def test_shared_reservation_covers_work_without_holding_maintenance(
    splash_host: SplashHost,
) -> None:
    h = splash_host
    script = h.host.root / "bin/hold-engine-lock"
    entered = h.root / "run/guarded-work-entered"
    release = h.root / "run/guarded-work-release"
    script.write_text(
        'source "' + str(h.host.root / "bin/engine-guard.sh") + '"\n'
        "guarded_work() {\n"
        f'  touch "{entered}"\n'
        f'  while [[ ! -f "{release}" ]]; do sleep 0.02; done\n'
        "}\nengine_run guarded_work\n"
    )
    held = h.host.spawn(cli=script)
    try:
        h.host.wait_for(entered)
        owner = h.host.owner(h.host.lock)
        assert int(owner["pid"]) == held.pid
        assert not h.host.gate.exists()
        result = h.run("start", "stock-8bit-32k")
        assert result.returncode != 0 and "mlx.lock" in result.stderr
        assert not h.record.exists()
    finally:
        release.touch()
        held.wait(timeout=5)
    assert not h.host.lock.exists() and not h.host.gate.exists()


def test_recovered_native_child_requires_verified_membership(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent = {"pid": 100, "started": 1.0, "executable": "/runtime/python", "role": "controller"}
    old = {"pid": 101, "started": 1.0, "executable": "/runtime/engine/splash", "role": "native"}
    new = {**old, "pid": 102, "started": 2.0}
    record = {"controller": parent, "native": [old], "prepared": "prepared"}
    monkeypatch.setattr(
        host_control, "_same", lambda root, record, p: parent if p["pid"] == 100 else None
    )
    monkeypatch.setattr(host_control, "_run", lambda argv: "102 100")
    monkeypatch.setattr(host_control, "_member", lambda *args: new)
    monkeypatch.setattr(host_control, "possible_pids", lambda root: {100, 102})
    assert host_control.members(tmp_path, record) == [parent, new]

    def unverified(*args: Any) -> None:
        raise host_control.HostError("unknown native executable")

    monkeypatch.setattr(host_control, "_member", unverified)
    with pytest.raises(host_control.HostError):
        host_control.members(tmp_path, record)


def test_unrecorded_orphan_refuses_signals(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = {"controller": {"pid": 100}, "native": []}
    monkeypatch.setattr(host_control, "_record", lambda root: record)
    monkeypatch.setattr(host_control, "_same", lambda *args: None)
    monkeypatch.setattr(host_control, "possible_pids", lambda root: {102})
    signals = []
    monkeypatch.setattr(host_control, "_signal", lambda *args: signals.append(args))
    assert host_control.observe(tmp_path)["state"] == "unknown"
    with pytest.raises(host_control.HostError):
        host_control.stop(tmp_path)
    assert signals == []


def test_launch_environment_and_argv_are_pinned_and_do_not_use_caller_secrets(
    splash_host: SplashHost,
) -> None:
    h = splash_host
    h.host.env.update(
        PYTHONPATH="/tmp/untrusted", SPLASH_API_KEY="ambient-key", HOME="/tmp/untrusted-home"
    )
    result = h.run("start", "modified-4bit-32k")
    assert result.returncode == 0, result.stdout + result.stderr
    launch = json.loads((h.root / "run/launch.json").read_text())
    assert launch["env"]["SPLASH_API_KEY"] == "s" * 40
    assert launch["env"]["HOME"] == str(h.root / "home")
    assert launch["env"]["PYTHONPATH"] == str(h.root / "runtime")
    argv = launch["argv"]
    assert "--api-key" not in argv and "s" * 40 not in " ".join(argv)
    for flag, value in [
        ("--max-context", "32768"),
        ("--max-cache-disk", "0"),
        ("--idle-release", "off"),
        ("--kv-format", "bf16"),
        ("--state-format", "fp32"),
        ("--default-reasoning-effort", "high"),
    ]:
        assert argv[argv.index(flag) + 1] == value


def test_status_probe_handles_missing_credentials_as_unavailable(
    host_control: ModuleType, tmp_path: Path
) -> None:
    assert host_control._get(tmp_path, "/status") is None


def test_status_rejects_changed_or_invalid_profile_without_crashing(
    host_control: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = {
        "profile": "stock-8bit-32k",
        "fingerprint": "a" * 20,
        "model": "example/qwen",
        "controller": {"pid": 100},
    }
    monkeypatch.setattr(
        host_control,
        "observe",
        lambda root: {"state": "starting", "processes": [{"role": "native", "pid": 101}]},
    )
    monkeypatch.setattr(host_control, "_record", lambda root: record)
    bodies = {
        "/ready": {"status": "ready"},
        "/status": {
            "ready": True,
            "maximum_context_tokens": 32768,
            "instance": {"pid": 100, "model": "example/qwen"},
        },
        "/v1/models": {"data": [{"id": "example/qwen"}]},
    }
    monkeypatch.setattr(host_control, "_get", lambda root, path: bodies[path])

    def broken(*args: Any, **kwargs: Any) -> None:
        raise host_control.profiles.ProfileError("invalid private configuration")

    monkeypatch.setattr(host_control.profiles, "load_profiles", broken)
    assert host_control.details(tmp_path)["state"] == "unknown"


def test_control_source_start_stop_contract_in_process_for_coverage(
    splash_host: SplashHost, host_control: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = splash_host
    monkeypatch.setenv("PATH", h.host.env["PATH"])
    monkeypatch.setenv("FAKE_HOST_STATE", str(h.host.state))
    monkeypatch.setattr(host_control, "READY_TIMEOUT", 1.0)
    monkeypatch.setattr(host_control, "STOP_TIMEOUT", 0.3)
    host_control.start(h.root, "stock-8bit-32k")
    assert host_control.details(h.root)["state"] == "ready"
    host_control.stop(h.root)
    assert host_control.observe(h.root)["state"] == "absent"


@pytest.fixture
async def splash_app(splash_host: SplashHost, monkeypatch: pytest.MonkeyPatch) -> Any:
    import httpx
    import respx

    from local_llm_admin import auth, jobs
    from local_llm_admin.app import create_app
    from local_llm_admin.config import Settings

    h = splash_host
    monkeypatch.setenv("PATH", h.host.env["PATH"])
    monkeypatch.setenv("FAKE_HOST_STATE", str(h.host.state))
    monkeypatch.setattr(jobs, "WAIT_POLL_S", 0.01)
    (h.host.state / "healthy").touch()
    s = Settings(
        mlx_root=h.host.root,
        mlx_root_must_be_mount=False,
        comfyui_root=h.host.root / "comfyui",
        splash_enabled=True,
        splash_root=h.root,
        splash_api_key="s" * 40,
        admin_data_dir=h.root.parent / "admin-data",
        admin_log_dir=h.root.parent / "admin-logs",
        admin_job_timeout_s=15,
        admin_password_hash=auth.hash_password("pw"),
        admin_session_secret="test-session",
        splash_ready_timeout_s=1,
        splash_stop_timeout_s=1,
    )

    requests = []
    calls = []

    def api(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        assert req.headers["Authorization"] == "Bearer " + "s" * 40
        record = json.loads(h.record.read_text())
        model = record["model"]
        if req.method == "POST" and req.url.path == "/v1/chat/completions":
            payload = json.loads(req.content)
            requests.append(payload)
            if payload.get("stream"):
                events = []
                if payload.get("chat_template_kwargs", {}).get("enable_thinking"):
                    events.append(
                        {
                            "choices": [
                                {
                                    "delta": {"reasoning_content": "considering"},
                                    "finish_reason": None,
                                }
                            ]
                        }
                    )
                events.append(
                    {
                        "choices": [{"delta": {"content": "ready"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                    }
                )
                if getattr(app.state, "splash_missing_usage", False):
                    for event in events:
                        event.pop("usage", None)
                body = (
                    "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
                )
                return httpx.Response(
                    200, content=body, headers={"Content-Type": "text/event-stream"}
                )
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1},
                },
            )
        docs = {
            "/ready": {"status": "ready"},
            "/status": captured_status(record["controller"]["pid"], model),
            "/v1/models": {
                "data": [
                    {"id": model, "max_model_len": 32768},
                    {"id": "local-qwen", "max_model_len": 32768},
                ]
            },
        }
        return (
            httpx.Response(200, json=docs[req.url.path])
            if req.url.path in docs
            else httpx.Response(501)
        )

    transport = httpx.MockTransport(api)
    app = create_app(
        s,
        start_background=False,
        splash_transport=transport,
        quality_transport=transport,
        chat_transport=transport,
        comfy_transport=httpx.MockTransport(lambda r: httpx.Response(503)),
    )
    app.state.splash_requests = requests
    app.state.splash_api_calls = calls
    with respx.mock(assert_all_called=False) as routes:
        routes.get("http://127.0.0.1:8080/health").mock(
            return_value=httpx.Response(
                200, json={"status": "healthy", "loaded_model": str(h.host.root / "models/active")}
            )
        )
        async with app.router.lifespan_context(app):
            await app.state.startup_task
            await app.state.backfill_task
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
            ) as client:
                await client.post("/login", data={"password": "pw"})
                client.headers["X-CSRF-Token"] = (await client.get("/api/session")).json()["csrf"]
                yield client, app, h


async def test_admin_switches_mlx_splash_and_off_with_profile_history(splash_app: Any) -> None:
    import asyncio

    client, app, h = splash_app

    async def switch(target: str) -> dict[str, Any]:
        response = await client.post("/api/engine", json={"target": target})
        assert response.status_code == 202, response.text
        for _ in range(800):
            job = (await client.get("/api/jobs/" + response.json()["id"])).json()
            if job["status"] != "running":
                assert job["status"] == "succeeded", job
                return job
            await asyncio.sleep(0.01)
        raise AssertionError("job did not complete")

    await switch("mlx:a")
    job = await switch("splash:stock-8bit-32k")
    assert job["to_name"] == "splash:stock-8bit-32k"
    status = (await client.get("/api/status")).json()
    assert status["engine"]["active"] == "splash"
    assert status["engine"]["splash"]["ready"] is True
    descriptor = status["engine"]["backends"]["splash"]
    assert descriptor["enabled"] is True and "multi-model" not in descriptor["operations"]
    assert {p["value"] for p in descriptor["targets"]} == {
        "splash:stock-8bit-32k",
        "splash:modified-4bit-32k",
    }
    assert "s" * 40 not in json.dumps(status)
    assert str(h.root / "assets") not in json.dumps(status)
    assert app.state.control.intent()["value"] == "splash:stock-8bit-32k"
    await switch("off")
    assert app.state.control.intent()["value"] == "off"
    assert not h.record.exists()


async def test_missing_splash_assets_are_rejected_before_stopping_source(splash_app: Any) -> None:
    import asyncio

    client, app, h = splash_app
    job = (await client.post("/api/engine", json={"target": "mlx:a"})).json()
    for _ in range(800):
        if app.state.control.runner.get(job["id"])["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert app.state.control.observe()["engine"] == "mlx"
    (h.root / "assets/stock-8bit/model.safetensors").unlink()
    response = await client.post("/api/engine", json={"target": "splash:stock-8bit-32k"})
    assert response.status_code == 409, response.text
    assert app.state.control.observe()["engine"] == "mlx"
    assert not h.record.exists()


async def test_splash_quality_uses_status_context_and_profile_identity(splash_app: Any) -> None:
    client, app, h = splash_app
    result = h.run("start", "stock-8bit-32k")
    assert result.returncode == 0, result.stdout + result.stderr
    qr = app.state.quality
    plan, _ = await qr._plan_run("compatibility", "current", None, False)
    assert plan.targets == ["splash:stock-8bit-32k"]
    assert await qr.current_context(plan) == 32768
    async with qr._open("splash") as http:
        _, generation = await qr._current_ready(http, "splash")
    assert generation == app.state.control.observe()["generation"]
    from local_llm_admin.splash_profiles import load_profiles

    expected = load_profiles(h.path, h.root)["stock-8bit-32k"].fingerprint
    assert qr._profile_fingerprint("splash") == expected
    before = h.record.read_bytes()
    qr.compat_cases = ("plain",)
    run = await qr.start("compatibility", "current")
    await qr.wait()
    done = qr.store.get_run(run["id"], measurements=True)
    assert done["cases"][0]["case_key"] == "plain", done
    assert done["cases"][0]["model"] == "splash:stock-8bit-32k"
    assert done["cases"][0]["profile_fingerprint"] == expected
    assert done["cases"][0]["fingerprint"] == expected
    assert before == h.record.read_bytes()


def test_runtime_log_rotation_is_bounded_and_only_runs_when_needed(
    host_control: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "splash.log"
    log.write_text("old-runtime-output")
    (logs / "splash.log.000").write_text("first")
    (logs / "splash.log.001").write_text("second")
    monkeypatch.setattr(host_control, "LOG_ROTATE_MAX", 4, raising=False)
    monkeypatch.setattr(host_control, "LOG_ROTATE_KEEP", 2, raising=False)
    host_control.rotate_log(tmp_path)
    assert not log.exists()
    archives = sorted(logs.glob("splash.log.*"))
    assert len(archives) == 2
    assert any(p.read_text() == "old-runtime-output" for p in archives)
    log.write_text("ok")
    before = {p.name: p.read_bytes() for p in logs.iterdir()}
    host_control.rotate_log(tmp_path)
    assert before == {p.name: p.read_bytes() for p in logs.iterdir()}


async def _switch_test_engine(
    client: Any, target: str, status: str = "succeeded"
) -> dict[str, Any]:
    import asyncio

    response = await client.post("/api/engine", json={"target": target})
    assert response.status_code == 202, response.text
    for _ in range(1200):
        job = (await client.get("/api/jobs/" + response.json()["id"])).json()
        if job["status"] != "running":
            assert job["status"] == status, job
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("engine job did not complete")


async def test_splash_failed_loaded_readiness_restores_original_engine(splash_app: Any) -> None:
    client, app, h = splash_app
    await _switch_test_engine(client, "mlx:a")
    h.flag("loading")
    job = await _switch_test_engine(client, "splash:stock-8bit-32k", "failed")
    assert job["rolled_back"] is True, job["output"]
    assert app.state.control.observe()["engine"] == "mlx"
    assert app.state.control.intent()["value"] == "mlx:a"
    assert not h.record.exists()


async def test_splash_profile_switch_records_distinct_identity(splash_app: Any) -> None:
    client, app, h = splash_app
    await _switch_test_engine(client, "splash:stock-8bit-32k")
    before = app.state.control.observe()["generation"]
    job = await _switch_test_engine(client, "splash:modified-4bit-32k")
    assert job["from_name"] == "splash:stock-8bit-32k"
    assert job["to_name"] == "splash:modified-4bit-32k"
    assert app.state.control.intent()["value"] == "splash:modified-4bit-32k"
    assert app.state.control.observe()["generation"] != before
    assert json.loads(h.record.read_text())["profile"] == "modified-4bit-32k"


async def test_splash_chat_resolves_once_and_preserves_explicit_reasoning(splash_app: Any) -> None:
    client, app, h = splash_app
    result = h.run("start", "stock-8bit-32k")
    assert result.returncode == 0, result.stdout + result.stderr
    app.state.splash_api_calls.clear()
    response = await client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "hello"}]}
    )
    assert response.status_code == 200, response.text
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert any(e["type"] == "reasoning" for e in events), events
    assert any(e["type"] == "done" for e in events), events
    assert app.state.splash_api_calls == ["/ready", "/status", "/v1/models", "/v1/chat/completions"]
    payload = app.state.splash_requests[-1]
    assert payload["model"] == "example/stock-8bit"
    assert payload["max_tokens"] == 8192
    assert payload["chat_template_kwargs"]["enable_thinking"] is True
    response = await client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "hello"}], "thinking": False}
    )
    assert response.status_code == 200
    assert app.state.splash_requests[-1]["chat_template_kwargs"]["enable_thinking"] is False
    assert "s" * 40 not in response.text


async def test_disabled_splash_owner_can_be_stopped_without_profile_inventory(
    splash_app: Any,
) -> None:
    client, app, h = splash_app
    await _switch_test_engine(client, "splash:stock-8bit-32k")
    app.state.control.settings.splash_enabled = False
    h.path.unlink()
    job = await _switch_test_engine(client, "off")
    assert job["from_name"] == "splash:stock-8bit-32k"
    assert not h.record.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "kv",
        "state",
        "model-list",
        "model-context",
        "pid",
        "generation",
        "released",
        "recovering",
        "loading",
        "instance",
        "identity-type",
        "identity-format",
        "plan-type",
        "plan-valid",
        "plan-valid-int",
        "plan-model-missing",
        "plan-model-type",
        "plan-kv",
        "legacy",
    ],
)
def test_host_loaded_readiness_rejects_changed_or_malformed_metadata(
    host_control: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    from types import SimpleNamespace

    record = {
        "controller": {"pid": 100},
        "model": "example/model",
        "profile": "stock-8bit-32k",
        "fingerprint": "f" * 20,
    }
    snapshot = {
        "state": "starting",
        "profile": record["profile"],
        "processes": [{"pid": 100, "role": "controller"}, {"pid": 101, "role": "native"}],
    }
    profile = SimpleNamespace(
        fingerprint=record["fingerprint"],
        model=record["model"],
        aliases=("local-qwen",),
        max_context=32768,
        kv_format="bf16",
        state_format="fp32",
    )
    health = captured_status(100, record["model"])
    models = {
        "data": [{"id": name, "max_model_len": 32768} for name in (profile.model, *profile.aliases)]
    }
    docs = {"/ready": {"status": "ready"}, "/status": health, "/v1/models": models}
    monkeypatch.setattr(host_control, "observe", lambda root: __import__("copy").deepcopy(snapshot))
    monkeypatch.setattr(host_control, "_record", lambda root: record)
    monkeypatch.setattr(
        host_control.profiles, "load_profiles", lambda *a, **kw: {record["profile"]: profile}
    )
    monkeypatch.setattr(host_control, "_get", lambda root, path: docs[path])
    assert host_control.details(tmp_path)["state"] == "ready"
    if mutation == "kv":
        health["identity"]["kv"] = None
    elif mutation == "state":
        health["memory_plan"]["model"]["state_format"] = "fp16"
    elif mutation == "identity-type":
        health["identity"] = []
    elif mutation == "identity-format":
        health["identity"]["kv"]["format"] = "fp8"
    elif mutation == "plan-type":
        health["memory_plan"] = None
    elif mutation in ("plan-valid", "plan-valid-int"):
        health["memory_plan"]["valid"] = False if mutation == "plan-valid" else 1
    elif mutation == "plan-model-missing":
        health["memory_plan"]["model"] = {}
    elif mutation == "plan-model-type":
        health["memory_plan"]["model"] = []
    elif mutation == "plan-kv":
        health["memory_plan"]["model"]["kv_format"] = "fp8"
    elif mutation == "legacy":
        health.pop("identity")
        health.pop("memory_plan")
        health.update(kv={"format": "bf16"}, state_format="fp32")
    elif mutation == "model-list":
        models["data"] = [{"id": {}}]
    elif mutation == "model-context":
        models["data"][0]["max_model_len"] = 8192
    elif mutation == "pid":
        health["instance"]["pid"] = 100.0
    elif mutation in ("released", "recovering"):
        health["weights"] = {"released" if mutation == "released" else "restoring": True}
    elif mutation == "loading":
        health["ready"] = False
    elif mutation == "instance":
        health["instance"] = None
    else:

        def changed(root: Path) -> Any:
            snapshot["processes"][1]["pid"] = 102
            return record

        monkeypatch.setattr(host_control, "_record", changed)
    assert host_control.details(tmp_path)["state"] != "ready"


@pytest.mark.parametrize("outcome", ["success", "profile-change", "cancel", "deadline"])
async def test_splash_current_eval_is_bound_to_owned_profile(splash_app: Any, outcome: str) -> None:
    import asyncio

    import httpx

    from local_llm_admin.eval_suites import parse_suite

    client, app, h = splash_app
    await _switch_test_engine(client, "splash:stock-8bit-32k")
    qr = app.state.quality
    suite = {
        "id": "splash-tiny",
        "version": 1,
        "title": "Tiny",
        "cases": [
            {
                "id": "ready",
                "category": "instruction",
                "prompt": "Reply ready",
                "max_tokens": 16,
                "grader": {"type": "exact", "answer": "ready"},
            }
        ],
    }
    app.state.evals.add_custom(parse_suite(suite))
    original = qr.transport
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            entered.set()
            if outcome == "profile-change":
                h.doc["profiles"]["stock-8bit-32k"]["label"] = "Changed externally"
                write(h.path, h.doc)
            if outcome in ("cancel", "deadline"):
                try:
                    await asyncio.sleep(5)
                finally:
                    closed.set()
        return await original.handle_async_request(request)

    qr.transport = httpx.MockTransport(transport)
    if outcome == "deadline":
        qr.settings.eval_run_deadline_s = 0.5
    before = h.record.read_bytes()
    response = await client.post(
        "/api/quality/runs",
        json={"kind": "eval", "scope": "current", "suite": "splash-tiny", "thinking": True},
    )
    assert response.status_code == 202, response.text
    if outcome == "cancel":
        await asyncio.wait_for(entered.wait(), 5)
        cancelled = await client.post("/api/quality/runs/" + response.json()["id"] + "/cancel")
        assert cancelled.status_code == 202, cancelled.text
    await qr.wait()
    run = qr.store.get_run(response.json()["id"], measurements=True)
    expected = {
        "success": "succeeded",
        "profile-change": "intervention_required",
        "cancel": "cancelled",
        "deadline": "failed",
    }[outcome]
    assert run["status"] == expected, run
    assert h.record.read_bytes() == before
    if outcome == "success":
        rows = app.state.evals.results(run["id"])
        assert rows[0]["passed"] == 1
        assert rows[0]["model"] == "splash:stock-8bit-32k"
        assert app.state.splash_requests[-1]["model"] == "example/stock-8bit"
        assert app.state.splash_requests[-1]["chat_template_kwargs"]["enable_thinking"] is True
        assert run["cases"][0]["profile_fingerprint"] is not None
    else:
        assert app.state.evals.scorecard() == []
    if outcome in ("cancel", "deadline"):
        assert closed.is_set()


@pytest.mark.parametrize(
    "kind,scope", [("eval", "all"), ("compatibility", "all"), ("benchmark", "current")]
)
async def test_splash_quality_rejects_switching_and_unsupported_runs(
    splash_app: Any, kind: str, scope: str
) -> None:
    client, app, h = splash_app
    await _switch_test_engine(client, "splash:stock-8bit-32k")
    before = h.record.read_bytes()
    response = await client.post("/api/quality/runs", json={"kind": kind, "scope": scope})
    assert response.status_code in (400, 409, 422), response.text
    assert h.record.read_bytes() == before


async def test_splash_chat_keeps_unreported_usage_unknown(splash_app: Any) -> None:
    client, app, h = splash_app
    await _switch_test_engine(client, "splash:stock-8bit-32k")
    app.state.splash_missing_usage = True
    response = await client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    done = next(event for event in events if event["type"] == "done")
    assert done["usage"] == {"prompt_tokens": None, "completion_tokens": None}
    assert done["tok_s"] is None


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_shared_reservation_releases_on_signal(splash_host: SplashHost, sig: int) -> None:
    h = splash_host.host
    script = h.root / "bin/interrupted-lifecycle"
    script.write_text('source "' + str(h.root / "bin/engine-guard.sh") + '"\nengine_run sleep 2\n')
    process = h.spawn(cli=script)
    h.wait_for(h.lock / "owner")
    process.send_signal(sig)
    process.wait(timeout=5)
    assert process.returncode == -sig
    assert not h.lock.exists() and not h.gate.exists()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize("point", ["before-unlink", "after-unlink"])
@pytest.mark.parametrize("target", ["gate", "lifecycle"])
def test_shared_reservation_signal_during_gate_release(
    splash_host: SplashHost, sig: int, point: str, target: str
) -> None:
    """Deliver the signal in the owner-unlink/rmdir gap rather than racing a sleep."""
    import shlex

    h = splash_host.host
    fake_bin = h.root / "signal-bin"
    fake_bin.mkdir()
    real_rm = shutil.which("rm")
    assert real_rm
    owner = shlex.quote(str((h.gate if target == "gate" else h.lock) / "owner"))
    marker = shlex.quote(str(h.state / "gate-signal-fired"))
    remove = shlex.quote(real_rm) + ' "$@"\n'
    trigger = f'printf fired > {marker}\nkill -{int(sig)} "$PPID"\nsleep 0.05\n'
    fake_rm = fake_bin / "rm"
    critical = remove + trigger if point == "after-unlink" else trigger + remove
    fake_rm.write_text(
        "#!/bin/bash\n"
        + f'if [[ "$2" == {owner} && ! -e {marker} ]]; then\n'
        + critical
        + "\nelse\n"
        + remove
        + "fi\n"
    )
    fake_rm.chmod(0o755)
    h.env["PATH"] = str(fake_bin) + os.pathsep + h.env["PATH"]
    script = h.root / "bin/signalled-gate-release"
    script.write_text('source "' + str(h.root / "bin/engine-guard.sh") + '"\nengine_run true\n')
    result = h.run(cli=script, timeout=5)
    assert result.returncode == -sig
    assert not h.lock.exists() and not h.gate.exists()


def test_custom_port_keeps_owned_lifecycle_and_reciprocal_mlx_exclusion(
    splash_host: SplashHost,
) -> None:
    import socket

    h = splash_host
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = h.root / "etc/splash.env"
    original = env.read_text()
    env.write_text(original + f"SPLASH_PORT={port}\n")
    result = h.run("start", "stock-8bit-32k")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(h.record.read_text())["port"] == port
    argv = json.loads((h.root / "run/launch.json").read_text())["argv"]
    assert argv[argv.index("--port") + 1] == str(port)
    assert json.loads(h.run("status", "--json").stdout)["state"] == "ready"
    # A changed configuration cannot redirect an existing owner's probes or cleanup.
    env.write_text(original + "SPLASH_PORT=8086\n")
    assert json.loads(h.run("status", "--json").stdout)["state"] == "ready"
    blocked = h.host.run("start", "a")
    assert blocked.returncode != 0
    assert "Splash owns resources" in blocked.stdout + blocked.stderr
    stopped = h.run("stop")
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert not h.record.exists()


@pytest.mark.parametrize("cohort", ["stock-8bit", "modified-4bit"])
def test_128k_host_contract_and_owning_lifecycle(
    splash_host: SplashHost, host_profiles: ModuleType, cohort: str
) -> None:
    h = splash_host
    name = cohort + "-32k"
    h.doc["profiles"][name]["max_context"] = 131072
    write(h.path, h.doc)
    profile = host_profiles.load_profiles(h.path, h.root)[name]
    assert profile.max_context == 131072
    result = h.run("start", name)
    assert result.returncode == 0, result.stdout + result.stderr
    launch = json.loads((h.root / "run/launch.json").read_text())["argv"]
    assert launch[launch.index("--max-context") + 1] == "131072"
    observed = json.loads(h.run("status", "--json").stdout)
    assert observed["state"] == "ready" and observed["max_context"] == 131072
    assert observed["fingerprint"] == profile.fingerprint
    assert h.run("stop").returncode == 0
    assert json.loads(h.run("status", "--json").stdout)["state"] == "absent"


@pytest.mark.parametrize("relative", ["python/bin/python3", "engine/splash"])
def test_fake_runtime_python_has_an_explicit_standard_library(
    splash_host: SplashHost, relative: str
) -> None:
    binary = splash_host.root / "runtime" / relative
    # Force a bundled prefix on system Python, then exercise the actual isolated flag.
    for flags in ([], ["-I"]):
        result = subprocess.run(
            [
                str(binary),
                *flags,
                "-S",
                "-c",
                "import encodings,http.server,json; print(json.dumps({'ready': True}))",
            ],
            env={"PATH": "/usr/bin:/bin", "PYTHONHOME": str(binary.parent.parent)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"ready": True}


@pytest.mark.parametrize("mlx_busy", [False, True])
def test_shared_gateway_listener_is_not_a_native_owner(
    splash_host: SplashHost, mlx_busy: bool
) -> None:
    h = splash_host
    redirect(HOST / "bin/engine-ports", h.host.root / "bin/engine-ports", h.host.root)
    config = h.host.root / "etc/engine-ports.json"
    config.write_text(json.dumps({"mlx": 8086, "strata": 8087, "splash": 8085}))
    config.chmod(0o600)
    env = h.root / "etc/splash.env"
    env.write_text(env.read_text() + "SPLASH_PORT=8085\n")
    proxy = h.host.sleeper()
    (h.host.state / "listen_8080").write_text(str(proxy.pid))
    if mlx_busy:
        (h.host.state / "listen_8086").write_text(str(h.host.sleeper().pid))
    result = h.run("start", "stock-8bit-32k")
    if mlx_busy:
        assert result.returncode != 0 and not h.record.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(h.record.read_text())["port"] == 8085
        assert h.run("stop").returncode == 0
    assert proxy.poll() is None
