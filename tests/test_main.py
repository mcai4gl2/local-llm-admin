from pathlib import Path
from typing import Any

import pytest

from local_llm_admin import __main__ as entry


def test_main_uses_env_file_and_runs_uvicorn(monkeypatch, tmp_path):
    env = tmp_path / "env"
    env.write_text("MLX_API_BASE=http://from-env-file:8080\nADMIN_DATA_DIR=%s\n" % (tmp_path / "d"))
    seen = {}

    def fake_run(app, host, port, **kw):
        seen.update(app=app, host=host, port=port)

    monkeypatch.setattr(entry.uvicorn, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--port", "8099", "--env-file", str(env)])
    entry.main()
    assert (seen["host"], seen["port"]) == ("0.0.0.0", 8099)
    assert seen["app"].title == "Local LLM Admin"


def test_main_without_env_file(monkeypatch, tmp_path):
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, host, port, **kw: None)
    monkeypatch.setenv("ADMIN_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--env-file", str(tmp_path / "missing")])
    entry.main()


def test_main_runs_the_gateway_next_to_the_admin_when_enabled(monkeypatch, tmp_path):
    env = tmp_path / "env"
    env.write_text(f"ADMIN_DATA_DIR={tmp_path / 'd'}\nDECISION_ENABLED=true\n")
    seen = {}

    async def fake_serve(servers):
        seen["servers"] = [(s.config.app, s.config.host, s.config.port) for s in servers]

    monkeypatch.setattr(entry, "serve_all", fake_serve)
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--env-file", str(env)])
    entry.main()
    (admin, ahost, aport), (gw, ghost, gport) = seen["servers"]
    assert admin.title == "Local LLM Admin" and (ahost, aport) == ("0.0.0.0", 8090)
    assert gw.title == "sidecar gateway" and (ghost, gport) == ("0.0.0.0", 8082)
    assert gw.state.inflight == 0  # its own app, not a mount inside the admin


def test_main_runs_the_gateway_with_speech_when_only_tts_is_enabled(monkeypatch, tmp_path):
    env = tmp_path / "env"
    env.write_text(f"ADMIN_DATA_DIR={tmp_path / 'd'}\nTTS_ENABLED=true\n")
    seen = {}

    async def fake_serve(servers):
        seen["apps"] = [s.config.app for s in servers]

    monkeypatch.setattr(entry, "serve_all", fake_serve)
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--env-file", str(env)])
    entry.main()
    _admin, gw = seen["apps"]
    assert "/v1/audio/speech" in {getattr(r, "path", "") for r in gw.routes}


async def test_serve_all_runs_every_server(monkeypatch):
    ran = []

    class Fake:
        def __init__(self, name):
            self.name = name

        async def serve(self):
            ran.append(self.name)

    await entry.serve_all([Fake("a"), Fake("b")])
    assert sorted(ran) == ["a", "b"]


def test_main_runs_the_gateway_with_embeddings_when_only_embed_is_enabled(monkeypatch, tmp_path):
    env = tmp_path / "env"
    env.write_text(f"ADMIN_DATA_DIR={tmp_path / 'd'}\nEMBED_ENABLED=true\n")
    seen = {}

    async def fake_serve(servers):
        seen["apps"] = [s.config.app for s in servers]

    monkeypatch.setattr(entry, "serve_all", fake_serve)
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--env-file", str(env)])
    entry.main()
    _admin, gw = seen["apps"]
    paths = {getattr(r, "path", "") for r in gw.routes}
    assert {"/v1/embeddings", "/v1/rerank"} <= paths and "/v1/audio/speech" not in paths


def test_shared_inference_keeps_existing_public_port_with_native_ports_internal(
    monkeypatch, tmp_path
):
    env = tmp_path / "env"
    env.write_text(
        f"ADMIN_DATA_DIR={tmp_path / 'd'}\nINFERENCE_API_KEY=public-test-key\nMLX_ROOT={_native_topology(tmp_path)}\nMLX_PORT=8086\nMLX_API_BASE=http://127.0.0.1:8086\nSTRATA_PORT=8087\nSTRATA_API_BASE=http://127.0.0.1:8087\nSPLASH_PORT=8085\nSPLASH_API_BASE=http://127.0.0.1:8085\n"
    )
    seen = []
    monkeypatch.setattr(
        entry.uvicorn,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("expected separate inference server")
        ),
    )

    async def serve(servers: Any) -> None:
        seen.extend(servers)

    monkeypatch.setattr(entry, "serve_all", serve)
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--env-file", str(env)])
    entry.main()
    assert [s.config.port for s in seen] == [8090, 8080]
    assert "/v1" in {getattr(r, "path", "") for r in seen[-1].config.app.routes}


def test_shared_inference_refuses_native_port_collision_before_binding(
    monkeypatch: Any, tmp_path: Path
) -> None:
    import pytest

    env = tmp_path / "env"
    env.write_text(f"ADMIN_DATA_DIR={tmp_path / 'd'}\nINFERENCE_API_KEY=public-test-key\n")
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **kw: pytest.fail("unsafe bind"))
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--env-file", str(env)])
    with pytest.raises(ValueError, match="native"):
        entry.main()


@pytest.mark.parametrize(
    "extra",
    [
        "MLX_API_BASE=http://127.0.0.1:8080",
        "INFERENCE_PORT=8090",
        "INFERENCE_PORT=8082",
        "MLX_API_BASE=file:///tmp/model",
        "STRATA_API_BASE=http://127.0.0.1:8087/v1",
        "SPLASH_API_BASE=http://127.0.0.1:8085?token=private",
    ],
)
def test_shared_inference_refuses_unsafe_topology(
    monkeypatch: Any, tmp_path: Path, extra: str
) -> None:
    values = {
        "ADMIN_DATA_DIR": str(tmp_path / "d"),
        "INFERENCE_API_KEY": "public-test-key",
        "MLX_PORT": "8086",
        "MLX_API_BASE": "http://127.0.0.1:8086",
        "STRATA_PORT": "8087",
        "STRATA_API_BASE": "http://127.0.0.1:8087",
        "SPLASH_PORT": "8085",
        "SPLASH_API_BASE": "http://127.0.0.1:8085",
    }
    key, value = extra.split("=", 1)
    values[key] = value
    env = tmp_path / "env"
    env.write_text("\n".join(f"{k}={v}" for k, v in values.items()))
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--env-file", str(env)])
    with pytest.raises(ValueError):
        entry.main()


def test_shared_inference_runs_alongside_sidecar_gateway(monkeypatch: Any, tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.write_text(
        f"ADMIN_DATA_DIR={tmp_path / 'd'}\nINFERENCE_API_KEY=public-test-key\nMLX_ROOT={_native_topology(tmp_path)}\nMLX_PORT=8086\nMLX_API_BASE=http://127.0.0.1:8086\nSTRATA_PORT=8087\nSTRATA_API_BASE=http://127.0.0.1:8087\nSPLASH_PORT=8085\nSPLASH_API_BASE=http://127.0.0.1:8085\nDECISION_ENABLED=true\n"
    )
    seen = []

    async def serve(servers: Any) -> None:
        seen.extend(servers)

    monkeypatch.setattr(entry, "serve_all", serve)
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--env-file", str(env)])
    entry.main()
    assert [s.config.port for s in seen] == [8090, 8082, 8080]


def _native_topology(tmp_path: Path) -> Path:
    import json
    import shutil

    root = tmp_path / "native"
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "etc").mkdir(exist_ok=True)
    helper = Path(__file__).parent.parent / "host/bin/engine-ports"
    shutil.copy2(helper, root / "bin/engine-ports")
    config = root / "etc/engine-ports.json"
    config.write_text(json.dumps({"mlx": 8086, "strata": 8087, "splash": 8085}))
    config.chmod(0o600)
    return root


@pytest.mark.parametrize("broken", ["missing", "mismatch", "invalid", "missing-helper"])
def test_shared_listener_refuses_a_partial_host_configuration(
    monkeypatch: Any, tmp_path: Path, broken: str
) -> None:
    import json

    root = _native_topology(tmp_path)
    config = root / "etc/engine-ports.json"
    if broken == "missing":
        config.unlink()
    elif broken == "mismatch":
        config.write_text(json.dumps({"mlx": 8088, "strata": 8087, "splash": 8085}))
    elif broken == "invalid":
        config.write_text("invalid")
    else:
        (root / "bin/engine-ports").unlink()
    env = tmp_path / "env"
    env.write_text(
        f"ADMIN_DATA_DIR={tmp_path / 'd'}\nMLX_ROOT={root}\nINFERENCE_API_KEY=public-test-key\nMLX_PORT=8086\nMLX_API_BASE=http://127.0.0.1:8086\nSTRATA_PORT=8087\nSTRATA_API_BASE=http://127.0.0.1:8087\nSPLASH_PORT=8085\nSPLASH_API_BASE=http://127.0.0.1:8085\n"
    )

    async def serve(servers: Any) -> None:
        pytest.fail("unsafe listener bound")

    monkeypatch.setattr(entry, "serve_all", serve)
    monkeypatch.setattr("sys.argv", ["local-llm-admin", "--env-file", str(env)])
    with pytest.raises(ValueError, match="host"):
        entry.main()
