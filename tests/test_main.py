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
