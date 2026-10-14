import httpx
import pytest
import respx

from local_llm_admin.mlx import MlxClient


@pytest.fixture
def client(fake_mlx):
    return MlxClient(fake_mlx.settings)


def test_list_models_adds_dir_facts(client, fake_mlx):
    models = client.list_models()
    assert [m["name"] for m in models] == ["qwen", "gemma"]
    q, g = models
    assert q["selected"] is True and q["dir_exists"] is True and q["has_config"] is True
    assert q["size_bytes"] >= 1000
    assert g["selected"] is False and g["dir_exists"] is False and g["size_bytes"] is None


def test_list_models_empty_when_cli_fails(client, fake_mlx):
    (fake_mlx.state / "list.txt").unlink()  # fake's `cat` exits non-zero
    assert client.list_models() == []


def test_list_models_empty_when_cli_missing(client, fake_mlx):
    fake_mlx.settings.mlx_model_bin = fake_mlx.root / "bin/missing"
    assert client.list_models() == []


def test_dir_size_is_cached(client, fake_mlx):
    d = fake_mlx.root / "models/Qwen"
    first = client.dir_size(d)
    (d / "more.bin").write_bytes(b"y" * 500)
    assert client.dir_size(d) == first


def test_process_status_from_cli(client):
    st = client.process_status()
    assert st.running is True and st.pid is not None


def test_process_status_falls_back_to_pid_file(fake_mlx, client, monkeypatch):
    import os

    fake_mlx.settings.mlx_model_bin = fake_mlx.root / "bin/missing"
    (fake_mlx.root / "run/mlx-server.pid").write_text(f"{os.getpid()}\n")
    st = client.process_status()
    assert st.running is True and st.pid == os.getpid()
    (fake_mlx.root / "run/mlx-server.pid").write_text("999999999\n")
    assert client.process_status().running is False
    (fake_mlx.root / "run/mlx-server.pid").write_text("junk")
    assert client.process_status().running is False


def test_drive_and_lock_checks(client, fake_mlx):
    assert client.drive_mounted() is True
    assert client.lock_exists() is False
    (fake_mlx.root / "run/mlx.lock").write_text("")
    assert client.lock_exists() is True


def test_drive_missing_when_root_absent(fake_mlx):
    fake_mlx.settings.mlx_root = fake_mlx.root.parent / "nowhere"
    assert MlxClient(fake_mlx.settings).drive_mounted() is False


def test_drive_requires_mount_point_by_default(fake_mlx):
    fake_mlx.settings.mlx_root_must_be_mount = True
    assert MlxClient(fake_mlx.settings).drive_mounted() is False


def test_active_env(client):
    assert client.active_env()["MLX_ACTIVE_NAME"] == "qwen"


def test_active_env_missing(client, fake_mlx):
    (fake_mlx.root / "run/active.env").unlink()
    assert client.active_env() == {}


def test_model_details(client, fake_mlx):
    d = client.model_details(str(fake_mlx.root / "models/active"))
    assert d["architecture"] == "Qwen3ForCausalLM"
    assert d["quant_bits"] == 4
    assert d["max_position_embeddings"] == 32768
    assert d["size_bytes"] >= 1000
    assert client.model_details(str(fake_mlx.root / "models/none"))["size_bytes"] is None


def test_model_details_bad_config(client, fake_mlx):
    (fake_mlx.root / "models/Qwen/config.json").write_text("{not json")
    assert client.model_details(str(fake_mlx.root / "models/Qwen"))["architecture"] is None


@respx.mock
async def test_health_ok_and_down(client):
    respx.get("http://mlx.test/health").mock(
        return_value=httpx.Response(200, json={"status": "healthy", "loaded_model": "/m"})
    )
    assert (await client.health())["loaded_model"] == "/m"
    respx.get("http://mlx.test/health").mock(side_effect=httpx.ConnectError("down"))
    assert await client.health() is None
    respx.get("http://mlx.test/health").mock(return_value=httpx.Response(200, text="nope"))
    assert await client.health() is None


@respx.mock
async def test_status_serving(client, fake_mlx):
    respx.get("http://mlx.test/health").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "healthy",
                "loaded_model": "/m",
                "loaded_context_size": 32768,
                "loaded_tool_parser": "qwen",
            },
        )
    )
    st = await client.status(job_running=False)
    assert st["state"] == "serving"
    assert st["selected"] == "qwen"
    assert st["process"]["running"] is True
    assert st["health"]["loaded_context_size"] == 32768
    assert st["inference"]["last_decode_rate"] == 17.8
    assert st["inference"]["in_flight"] is True
    assert st["model"]["architecture"] == "Qwen3ForCausalLM"
    assert st["chat_ui"] is False


@respx.mock
async def test_status_drive_missing_skips_everything(fake_mlx):
    fake_mlx.settings.mlx_root = fake_mlx.root.parent / "gone"
    route = respx.get("http://mlx.test/health").mock(return_value=httpx.Response(500))
    st = await MlxClient(fake_mlx.settings).status(job_running=False)
    assert st["state"] == "drive-missing"
    assert st["selected"] is None
    assert route.called  # health still probed: the server may be running from RAM


def test_port_listening(client):
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    try:
        assert client.port_listening(srv.getsockname()[1]) is True
    finally:
        srv.close()
    assert client.port_listening(1) is False


def test_lock_is_a_directory_with_age(client, fake_mlx):
    import os
    import time as _t

    assert client.lock_age_s() is None
    lock = fake_mlx.root / "run/mlx.lock"
    lock.mkdir()  # mlx-model takes the lock with `mkdir`
    old = _t.time() - 600
    os.utime(lock, (old, old))
    assert client.lock_exists() is True
    assert 590 <= client.lock_age_s() <= 700


def test_engine_running_counts_chat_ui_mode(client, fake_mlx):
    """Chat UI mode (chat-qwen.sh on :7860) has a model loaded even though :8080 is down."""
    import socket

    assert client.engine_running() is False
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    try:
        fake_mlx.settings.chat_ui_port = srv.getsockname()[1]
        assert client.engine_running() is True
    finally:
        srv.close()
