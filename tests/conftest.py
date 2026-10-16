import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from local_llm_admin.config import Settings

FIX = Path(__file__).parent / "fixtures"


@dataclass
class FakeMlx:
    root: Path
    state: Path
    settings: Settings

    def set_list(self, text: str) -> None:
        (self.state / "list.txt").write_text(text)

    def set_status(self, text: str) -> None:
        (self.state / "status.txt").write_text(text)

    def set_exit(self, code: int) -> None:
        (self.state / "exit").write_text(str(code))

    def set_delay(self, seconds: float) -> None:
        (self.state / "delay").write_text(str(seconds))

    def calls(self) -> list[str]:
        p = self.state / "calls.log"
        return p.read_text().splitlines() if p.exists() else []


@pytest.fixture
def fake_mlx(tmp_path, monkeypatch) -> FakeMlx:
    root = tmp_path / "Models"
    for d in ("bin", "etc", "run", "logs", "models/Qwen"):
        (root / d).mkdir(parents=True)
    (root / "models/Qwen/config.json").write_text(
        json.dumps(
            {
                "architectures": ["Qwen3ForCausalLM"],
                "quantization": {"bits": 4},
                "max_position_embeddings": 32768,
            }
        )
    )
    (root / "models/Qwen/model.safetensors").write_bytes(b"x" * 1000)
    (root / "models/active").symlink_to(root / "models/Qwen")
    (root / "run/active.env").write_text(
        f"MLX_ACTIVE_NAME=qwen\nMLX_ACTIVE_PATH={root}/models/Qwen\n"
    )
    (root / "logs/mlx-server.log").write_text((FIX / "mlx-server.log").read_text())
    state = tmp_path / "fake_state"
    state.mkdir()
    monkeypatch.setenv("FAKE_MLX_STATE", str(state))
    monkeypatch.setenv("FAKE_MLX_ROOT", str(root))
    settings = Settings(
        mlx_root=root,
        mlx_model_bin=FIX / "fake-mlx-model",
        mlx_root_must_be_mount=False,
        mlx_api_base="http://mlx.test",
        chat_ui_port=1,  # nothing listens on port 1
        mlx_port=1,
        strata_root=tmp_path / "strata-absent",
        strata_port=1,
        admin_data_dir=tmp_path / "data",
        admin_log_dir=tmp_path / "adminlogs",
        admin_password_hash="",
        litellm_base_url="http://litellm.test",
        litellm_admin_key="sk-viewer-SECRET-123",
    )
    fake = FakeMlx(root=root, state=state, settings=settings)
    fake.set_list(f"* qwen {root}/models/Qwen\n  gemma {root}/models/Gemma\n")
    fake.set_status(f"mlx-server running (pid {os.getpid()})\n")
    return fake


@dataclass
class FakeComfy:
    root: Path
    state: Path
    settings: Settings
    transport: httpx.MockTransport
    queue: dict
    history: dict = field(default_factory=dict)
    # "no_jobs_api": ComfyUI older than 0.37 (no /api/jobs); "fail_post": mutations 500;
    # "requests": every (method, path, body) the fake received
    flags: dict = field(default_factory=dict)

    def calls(self) -> list[str]:
        p = self.state / "calls.log"
        return p.read_text().splitlines() if p.exists() else []


@pytest.fixture
def fake_comfy(fake_mlx, monkeypatch):
    root = fake_mlx.root.parent / "comfyui"
    for d in ("run", "logs", "models/checkpoints", "output", "cache/hf/hub"):
        (root / d).mkdir(parents=True)
    (root / "logs/comfyui.log").write_text(
        "Starting server\nTo see the GUI go to: http://0.0.0.0:8188\n"
    )
    monkeypatch.setenv("FAKE_COMFY_ROOT", str(root))
    (fake_mlx.state / "track_pid").write_text("")
    s = fake_mlx.settings
    s.comfyui_root = root
    s.comfyui_bin = FIX / "fake-comfyui"
    s.comfyui_api_base = "http://comfy.test"
    s.comfyui_port = 1
    s.engine_stop_timeout_s = 3.0
    s.comfyui_ready_timeout_s = 3.0
    queue = {"queue_running": [], "queue_pending": []}
    stats = {
        "system": {"comfyui_version": "0.37.0", "ram_total": 1, "ram_free": 1},
        "devices": [{"name": "mps", "type": "mps", "vram_total": 2, "vram_free": 1}],
    }

    def handler(request):
        if not (fake_mlx.state / "comfy_ready").exists():
            raise httpx.ConnectError("not listening")
        if request.url.path == "/system_stats":
            return httpx.Response(200, json=stats)
        path, method = request.url.path, request.method
        body = json.loads(request.content) if method == "POST" and request.content else {}
        fake.flags.setdefault("requests", []).append((method, path, body))
        if path == "/queue" and method == "GET":
            return httpx.Response(200, json=queue)
        if path == "/history" and method == "GET":
            return httpx.Response(200, json=fake.history)
        if path == "/api/jobs" and method == "GET":
            if fake.flags.get("no_jobs_api"):
                return httpx.Response(404)
            return httpx.Response(200, json={"jobs": [], "pagination": {}})
        if method == "POST" and fake.flags.get("fail_post"):
            return httpx.Response(500)

        def drop(key, pid):
            before = len(queue[key])
            queue[key] = [i for i in queue[key] if not (isinstance(i, list) and i[1:2] == [pid])]
            return len(queue[key]) != before

        if path == "/queue" and method == "POST":  # upstream: clear, then delete by id
            if body.get("clear"):
                queue["queue_pending"] = []
            for pid in body.get("delete", []):
                drop("queue_pending", pid)
            return httpx.Response(200)
        if path == "/interrupt" and method == "POST":
            pid = body.get("prompt_id")
            if not pid:
                queue["queue_running"] = []
            else:
                drop("queue_running", pid)
            return httpx.Response(200)
        if path.startswith("/api/jobs/") and path.endswith("/cancel") and method == "POST":
            if fake.flags.get("no_jobs_api"):
                return httpx.Response(404)
            pid = path.split("/")[3]
            return httpx.Response(200, json={"cancelled": drop("queue_running", pid)})
        return httpx.Response(404)

    fake = FakeComfy(root, fake_mlx.state, s, httpx.MockTransport(handler), queue)
    yield fake
    for pidfile in (root / "run/comfyui.pid", fake_mlx.root / "run/mlx-server.pid"):
        try:
            os.kill(int(pidfile.read_text()), 9)
        except (OSError, ValueError):
            pass


@dataclass
class FakeStrata:
    root: Path
    state: Path
    settings: Settings
    transport: httpx.MockTransport
    flags: dict[str, Any]

    def start(self) -> None:
        subprocess.run([str(self.settings.strata_path), "start"], capture_output=True, check=False)


@pytest.fixture
def fake_strata(fake_comfy, monkeypatch):
    """The Strata engine next to the fake MLX and ComfyUI; ready once its CLI started it."""
    root = fake_comfy.root.parent / "strata"
    for d in ("run", "logs"):
        (root / d).mkdir(parents=True)
    monkeypatch.setenv("FAKE_STRATA_ROOT", str(root))
    s = fake_comfy.settings
    s.strata_enabled = True
    s.strata_root = root
    s.strata_bin = FIX / "fake-strata"
    s.strata_api_base = "http://strata.test"
    s.strata_port = 1
    s.strata_ready_timeout_s = 3.0
    health = json.loads((FIX / "strata/health.json").read_text())
    flags: dict[str, Any] = {}

    def handler(request):
        if not (fake_comfy.state / "strata_ready").exists():
            raise httpx.ConnectError("not listening")
        if request.url.path == "/health":
            pack = fake_comfy.state / "strata_pack"
            model = (
                "qwen3.8-flash-next-" + pack.read_text().strip().lower()
                if pack.exists()
                else health["model"]
            )
            return httpx.Response(
                200, json={**health, "model": model, "loaded": not flags.get("loading")}
            )
        if request.url.path == "/metrics" and "metrics" in flags:
            return httpx.Response(200, json=flags["metrics"])
        return httpx.Response(404)

    fake = FakeStrata(root, fake_comfy.state, s, httpx.MockTransport(handler), flags)
    yield fake
    try:
        os.kill(int((root / "run/strata.pid").read_text()), 9)
    except (OSError, ValueError):
        pass


@dataclass
class FakeDecision:
    root: Path
    state: Path
    settings: Settings
    transport: httpx.MockTransport
    reads: list[dict[str, Any]]
    flags: dict[str, Any]

    def start(self) -> None:
        subprocess.run(
            [str(self.settings.decision_path), "start"], capture_output=True, check=False
        )


@pytest.fixture
def fake_decision(fake_comfy, monkeypatch):
    """The decision sidecar next to the fake MLX and ComfyUI (M6)."""
    root = fake_comfy.root.parent / "decision"
    for d in ("run", "logs", "bin"):
        (root / d).mkdir(parents=True)
    monkeypatch.setenv("FAKE_DECISION_ROOT", str(root))
    s = fake_comfy.settings
    s.decision_enabled = True
    s.decision_root = root
    s.decision_bin = FIX / "fake-openjev"
    s.decision_api_base = "http://decision.test"
    s.decision_port = 1
    s.decision_api_key = SecretStr("k" * 64)
    s.decision_ready_timeout_s = 3.0
    s.decision_allowed_mlx = "qwen"
    reads: list[dict[str, Any]] = []
    flags: dict[str, Any] = {}
    responses = {
        name: json.loads((FIX / "decision" / f"{name}.json").read_text())
        for name in ("noul", "choice", "score", "error_400_choice_empty", "error_400_bad_type")
    }

    def handler(request):
        if not (fake_comfy.state / "decision_ready").exists():
            raise httpx.ConnectError("not listening")
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models" and request.method == "GET":
            if request.headers.get("authorization") != "Bearer " + "k" * 64:
                return httpx.Response(401, json={"detail": {"error_type": "authentication_error"}})
            return httpx.Response(200, json={"models": [{"name": "openjev-latest"}]})
        if request.url.path == "/v1/systemone" and request.method == "POST":
            if flags.get("timeout"):
                raise httpx.ReadTimeout("slow")
            if request.headers.get("authorization") != "Bearer " + "k" * 64:
                return httpx.Response(
                    401,
                    json={"detail": {"error_type": "authentication_error", "message": "bad key"}},
                )
            body = json.loads(request.content)
            reads.append(body)
            if "status" in flags:
                return httpx.Response(flags["status"], json=flags.get("body", {}))
            if "raw" in flags:
                return httpx.Response(200, content=flags["raw"])
            fx = responses[flags.get("answer", "choice")]
            return httpx.Response(
                fx["status"],
                json=fx["response"],
                headers={"server-timing": fx["headers"].get("server-timing", "")},
            )
        return httpx.Response(404)

    fake = FakeDecision(root, fake_comfy.state, s, httpx.MockTransport(handler), reads, flags)
    yield fake
    try:
        os.kill(int((root / "run/openjev.pid").read_text()), 9)
    except (OSError, ValueError):
        pass


def _wav(frames: int = 2400, rate: int = 24000) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * frames)
    return buf.getvalue()


@dataclass
class FakeTts:
    root: Path
    state: Path
    settings: Settings
    transport: httpx.MockTransport
    requests: list[dict[str, Any]]
    flags: dict[str, Any]

    def start(self) -> None:
        subprocess.run([str(self.settings.tts_path), "start"], capture_output=True, check=False)


@pytest.fixture
def fake_tts(fake_decision, monkeypatch):
    """The TTS sidecar next to the fake MLX, ComfyUI and OpenJev (M7)."""
    root = fake_decision.root.parent / "tts"
    for d in ("run", "logs", "bin"):
        (root / d).mkdir(parents=True)
    monkeypatch.setenv("FAKE_TTS_ROOT", str(root))
    s = fake_decision.settings
    s.tts_enabled = True
    s.tts_root = root
    s.tts_bin = FIX / "fake-qwen-tts"
    s.tts_api_base = "http://tts.test"
    s.tts_port = 1
    s.tts_ready_timeout_s = 3.0
    s.tts_allowed_mlx = "qwen"
    s.tts_voices = "Vivian,Ryan"
    s.tts_default_voice = "Vivian"
    s.tts_chunk_chars = 100
    requests: list[dict[str, Any]] = []
    flags: dict[str, Any] = {}

    def handler(request):
        if not (fake_decision.state / "tts_ready").exists():
            raise httpx.ConnectError("not listening")
        if request.method == "GET" and request.url.path == s.tts_ready_path:
            return httpx.Response(
                200, json={"object": "list", "data": [{"id": s.tts_upstream_model}]}
            )
        if request.method == "POST" and request.url.path == "/v1/audio/speech":
            if flags.get("timeout"):
                raise httpx.ReadTimeout("slow")
            body = json.loads(request.content)
            requests.append(body)
            if "status" in flags:
                return httpx.Response(flags["status"], json=flags.get("body", {}))
            if "raw" in flags:
                return httpx.Response(
                    200, content=flags["raw"], headers={"content-type": "audio/wav"}
                )
            rate = flags.get("rates", {}).get(len(requests), 24000)
            frames = flags.get("frames", len(body["input"]) * 10)
            # mlx-audio streams one complete WAV per segment, back to back (Phase 0)
            n = flags.get("segments", 1)
            sizes = [frames // n + (1 if i < frames % n else 0) for i in range(n)]
            return httpx.Response(
                200,
                content=b"".join(_wav(k, rate) for k in sizes),
                headers={"content-type": "audio/wav"},
            )
        return httpx.Response(404)

    fake = FakeTts(root, fake_decision.state, s, httpx.MockTransport(handler), requests, flags)
    yield fake
    try:
        os.kill(int((root / "run/qwen-tts.pid").read_text()), 9)
    except (OSError, ValueError):
        pass


def _vec(text: str, dims: int) -> list[float]:
    """Deterministic, distinct per text length, never zero."""
    base = float(len(text) % 7 + 1)
    return [base + i for i in range(dims)]


@dataclass
class FakeEmbed:
    root: Path
    state: Path
    settings: Settings
    transport: httpx.MockTransport
    requests: list[tuple[str, dict[str, Any]]]
    flags: dict[str, Any]

    def start(self) -> None:
        subprocess.run([str(self.settings.embed_path), "start"], capture_output=True, check=False)


@pytest.fixture
def fake_embed(fake_comfy, monkeypatch):
    """The always-on embedding service next to the fake MLX and ComfyUI (M8)."""
    root = fake_comfy.root.parent / "embed"
    for d in ("run", "logs", "bin"):
        (root / d).mkdir(parents=True)
    monkeypatch.setenv("FAKE_EMBED_ROOT", str(root))
    s = fake_comfy.settings
    s.embed_enabled = True
    s.embed_root = root
    s.embed_bin = FIX / "fake-qwen-embed"
    s.embed_api_base = "http://embed.test"
    s.embed_port = 1
    s.embed_ready_timeout_s = 3.0
    s.embed_allowed_mlx = "qwen"
    s.embed_dims = 8
    s.embed_min_interval_ms = 0  # pacing has its own test
    requests: list[tuple[str, dict[str, Any]]] = []
    flags: dict[str, Any] = {}

    def handler(request):
        if not (fake_comfy.state / "embed_ready").exists():
            raise httpx.ConnectError("not listening")
        path = request.url.path
        if request.method == "GET" and path == s.embed_ready_path:
            return httpx.Response(200, json={"status": "healthy"})
        if request.method != "POST" or path not in ("/v1/embeddings", "/v1/rerank"):
            return httpx.Response(404)
        if flags.get("timeout"):
            raise httpx.ReadTimeout("slow")
        body = json.loads(request.content)
        requests.append((path, body))
        if "status" in flags:
            return httpx.Response(flags["status"], json=flags.get("body", {}))
        if "raw" in flags:
            return httpx.Response(200, content=flags["raw"])
        if "json" in flags:
            return httpx.Response(200, json=flags["json"])
        if path == "/v1/embeddings":
            texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
            width = flags.get("width", s.embed_dims)
            data = [
                {"object": "embedding", "index": i, "embedding": _vec(t, width)}
                for i, t in enumerate(texts)
            ]
            tokens = sum(len(t) // 4 + 1 for t in texts)
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": data,
                    "model": body["model"],
                    "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
                },
            )
        results = [
            {"index": i, "relevance_score": round(len(d) / (len(d) + 10), 4)}
            for i, d in enumerate(body["documents"])
        ]
        return httpx.Response(200, json={"results": results})

    fake = FakeEmbed(root, fake_comfy.state, s, httpx.MockTransport(handler), requests, flags)
    yield fake
    try:
        os.kill(int((root / "run/qwen-embed.pid").read_text()), 9)
    except (OSError, ValueError):
        pass
