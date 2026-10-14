import os
import time as _time
from pathlib import Path

import httpx
import pytest

from local_llm_admin import engines
from local_llm_admin.config import Settings

MAC = Path(__file__).parent / "fixtures" / "mac"

SYSTEM_STATS = {
    "system": {
        "os": "darwin",
        "ram_total": 68719476736,
        "ram_free": 30000000000,
        "comfyui_version": "0.37.0",
        "pytorch_version": "2.9.0",
        "argv": ["main.py"],
    },
    "devices": [
        {
            "name": "mps",
            "type": "mps",
            "index": None,
            "vram_total": 68719476736,
            "vram_free": 30000000000,
            "torch_vram_total": 0,
            "torch_vram_free": 0,
        }
    ],
}


def test_settings_defaults():
    s = Settings()
    assert s.comfyui_root == Path("/Volumes/Models/comfyui")
    assert s.comfyui_path == Path("/Volumes/Models/comfyui/bin/comfyui")
    assert (s.comfyui_api_base, s.comfyui_port, s.mlx_port) == ("http://127.0.0.1:8188", 8188, 8080)
    assert s.comfyui_public_url == "http://127.0.0.1:8188"
    assert s.litellm_base_url == "http://127.0.0.1:4000"
    # no MLX model may share the GPU with a sidecar until the deployment lists it
    assert (s.decision_allowed_mlx, s.tts_allowed_mlx, s.embed_allowed_mlx) == ("", "", "")
    assert (s.comfyui_ready_timeout_s, s.engine_stop_timeout_s) == (120.0, 30.0)


@pytest.mark.parametrize(
    ("raw", "kind", "name"),
    [("off", "off", None), ("comfyui", "comfyui", None), ("mlx:qwen3.8-27b", "mlx", "qwen3.8-27b")],
)
def test_parse_target(raw, kind, name):
    assert engines.parse_target(raw) == engines.Target(kind, name)


@pytest.mark.parametrize("raw", ["", "mlx", "mlx:", "mlx:a;b", "comfy", "mlx:../x", "OFF"])
def test_parse_target_rejects(raw):
    with pytest.raises(ValueError):
        engines.parse_target(raw)


def test_parse_comfy_status_recorded_and_running():
    st = engines.parse_comfy_status((MAC / "comfyui-status.txt").read_text())
    assert (st.running, st.pid) == (False, None)
    st = engines.parse_comfy_status("comfyui: running pid 4321\nmlx: stopped\n")
    assert (st.running, st.pid) == (True, 4321)
    assert engines.parse_comfy_status("garbage").running is False


def test_parse_system_stats():
    assert engines.parse_system_stats(SYSTEM_STATS) == {
        "version": "0.37.0",
        "pytorch_version": "2.9.0",
        "device": "mps",
        "device_type": "mps",
        "vram_total": 68719476736,
        "vram_free": 30000000000,
        "ram_total": 68719476736,
        "ram_free": 30000000000,
    }
    assert engines.parse_system_stats({})["device"] is None


def test_queue_counts():
    assert engines.queue_counts({"queue_running": [[1]], "queue_pending": [[2], [3]]}) == (1, 2)
    assert engines.queue_counts({}) == (0, 0)
    assert engines.queue_counts({"queue_running": "bad"}) == (0, 0)


@pytest.mark.parametrize(
    ("mlx", "comfy", "active"),
    [
        (True, False, "mlx"),
        (False, True, "comfyui"),
        (False, False, "none"),
        (True, True, "conflict"),
    ],
)
def test_resolve_active(mlx, comfy, active):
    assert engines.resolve_active(mlx, comfy) == active


@pytest.mark.parametrize(
    ("mlx_state", "active", "ready", "badge"),
    [
        ("serving", "mlx", False, "serving"),
        ("stopped", "none", False, "stopped"),
        ("stopped", "comfyui", True, "comfyui"),
        ("stopped", "comfyui", False, "comfyui-starting"),
        ("serving", "conflict", True, "conflict"),
        ("switching", "comfyui", True, "switching"),
    ],
)
def test_badge_state(mlx_state, active, ready, badge):
    assert engines.badge_state(mlx_state, active, ready) == badge


def _comfy(tmp_path, handler=None):
    root = tmp_path / "comfyui"
    for d in ("run", "logs", "models/checkpoints", "output"):
        (root / d).mkdir(parents=True)
    s = Settings(comfyui_root=root, comfyui_port=1, comfyui_api_base="http://comfy.test")
    transport = httpx.MockTransport(handler) if handler else None
    return engines.ComfyClient(s, transport=transport), root


def test_argv_is_fixed(tmp_path):
    c, root = _comfy(tmp_path)
    assert c.argv("start") == [str(root / "bin/comfyui"), "start"]
    with pytest.raises(ValueError):
        c.argv("rm -rf")


def test_running_from_pid_file(tmp_path):
    c, root = _comfy(tmp_path)
    assert c.running().running is False and c.down_sync() is True
    (root / "run/comfyui.pid").write_text(f"{os.getpid()}\n")
    assert c.running() == engines.ProcessStatus(True, os.getpid())
    assert c.down_sync() is False
    (root / "run/comfyui.pid").write_text("999999999")
    assert c.running().running is False
    (root / "run/comfyui.pid").write_text("junk")
    assert c.pid_from_file() is None


def _api(queue=None, stats=SYSTEM_STATS, fail=False, calls=None):
    def handler(request):
        if calls is not None:
            calls.append((request.method, request.url.path, request.content))
        if fail:
            raise httpx.ConnectError("down")
        if request.url.path == "/system_stats":
            return httpx.Response(200, json=stats)
        if request.url.path == "/queue" and request.method == "GET":
            return httpx.Response(200, json=queue or {"queue_running": [], "queue_pending": []})
        return httpx.Response(200)

    return handler


def test_ready_sync(tmp_path):
    assert _comfy(tmp_path / "a", _api())[0].ready_sync() is True
    assert _comfy(tmp_path / "b", _api(fail=True))[0].ready_sync() is False


async def test_system_stats_and_queue(tmp_path):
    c, _ = _comfy(tmp_path, _api(queue={"queue_running": [[1]], "queue_pending": [[2]]}))
    assert (await c.system_stats())["version"] == "0.37.0"
    assert await c.queue() == (1, 1)
    down, _ = _comfy(tmp_path / "d", _api(fail=True))
    assert await down.system_stats() is None and await down.queue() is None


def test_cancel_all_clears_queue_then_interrupts(tmp_path):
    calls = []
    c, _ = _comfy(tmp_path, _api(calls=calls))
    c.cancel_all_sync()
    assert [(m, p) for m, p, _ in calls] == [("POST", "/queue"), ("POST", "/interrupt")]
    assert calls[0][2] == b'{"clear":true}'


def test_cancel_all_raises_when_unreachable(tmp_path):
    c, _ = _comfy(tmp_path, _api(fail=True))
    with pytest.raises(httpx.HTTPError):
        c.cancel_all_sync()


async def test_status_block(tmp_path):
    c, root = _comfy(tmp_path, _api(queue={"queue_running": [[1]], "queue_pending": []}))
    (root / "run/comfyui.pid").write_text(str(os.getpid()))
    st = await c.status()
    assert st["running"] is True and st["ready"] is True and st["version"] == "0.37.0"
    assert (st["queue_running"], st["queue_pending"]) == (1, 0)
    stopped, _ = _comfy(tmp_path / "s", _api())
    st = await stopped.status()
    assert st["running"] is False and st["ready"] is False and st["queue_running"] is None


NOW = _time.time()


def _file(p: Path, size: int, age_s: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        fh.truncate(size)
    os.utime(p, (NOW - age_s, NOW - age_s))


def test_comfy_weights_states_and_hf_incomplete(tmp_path):
    models, hub = tmp_path / "models", tmp_path / "hf/hub"
    _file(models / "checkpoints/sdxl.safetensors", 6_000, 3600)
    _file(models / "diffusion_models/wan2.2.safetensors", 4_000, 5)
    _file(models / "checkpoints/.hidden", 1, 3600)
    _file(models / "README.txt", 10, 3600)  # top-level files are not weights
    _file(hub / "models--Org--Repo/blobs/abc123.incomplete", 2_000, 3)
    _file(
        hub / "models--Org--Repo/blobs/def456", 9, 3600
    )  # finished blob: listed in its folder, not here
    rows = engines.comfy_weights(models, hub, now=NOW)
    assert [(r["category"], r["file"], r["state"], r["size_bytes"]) for r in rows] == [
        ("checkpoints", "sdxl.safetensors", "complete", 6_000),
        ("diffusion_models", "wan2.2.safetensors", "downloading", 4_000),
        ("hf-cache", "Org/Repo", "downloading", 2_000),
    ]
    assert rows[0]["last_write_ago_s"] >= 3599


def test_comfy_weights_nested_files_and_missing_roots(tmp_path):
    models = tmp_path / "models"
    _file(models / "loras/style/a.safetensors", 5, 3600)
    rows = engines.comfy_weights(models, tmp_path / "nohub", now=NOW)
    assert [(r["category"], r["file"]) for r in rows] == [("loras", "style/a.safetensors")]
    assert engines.comfy_weights(tmp_path / "none", tmp_path / "nohub", now=NOW) == []


def test_output_summary(tmp_path):
    out = tmp_path / "output"
    _file(out / "a.png", 100, 10)
    _file(out / "sub/b.mp4", 50, 10)
    assert engines.output_summary(out) == {"files": 2, "bytes": 150}
    assert engines.output_summary(tmp_path / "missing") == {"files": 0, "bytes": 0}


def test_client_weights_and_output(tmp_path):
    c, root = _comfy(tmp_path)
    _file(root / "models/checkpoints/x.safetensors", 7, 3600)
    _file(root / "cache/hf/hub/models--A--B/blobs/z.incomplete", 3, 1)
    _file(root / "output/o.png", 4, 10)
    assert [r["file"] for r in c.weights()] == ["x.safetensors", "A/B"]
    assert c.output() == {"files": 1, "bytes": 4}


def test_inventory_skips_entries_that_cannot_be_stat(tmp_path):
    models, hub, out = tmp_path / "models", tmp_path / "hub", tmp_path / "out"
    (models / "vae").mkdir(parents=True)
    (models / "vae" / "gone.safetensors").symlink_to(tmp_path / "missing")
    (hub / "models--A--B" / "blobs").mkdir(parents=True)
    (hub / "models--A--B" / "blobs" / "x.incomplete").symlink_to(tmp_path / "missing")
    out.mkdir()
    (out / "gone.png").symlink_to(tmp_path / "missing")
    assert engines.comfy_weights(models, hub, now=NOW) == []
    assert engines.output_summary(out) == {"files": 0, "bytes": 0}
