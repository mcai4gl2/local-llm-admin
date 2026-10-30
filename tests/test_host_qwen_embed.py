"""host/embed/bin/qwen-embed and the guards around it, run with real zsh (M8)."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from .host_harness import HOST, Host, _exe, make_host, redirect

CLI = HOST / "embed/bin/qwen-embed"

# The server: records argv and env, then serves until TERM. $FAKE_HOST_STATE/embed_exit makes it
# die at once; embed_ignore_term makes it ignore TERM.
FAKE_SERVER = r"""#!/bin/bash
printf '%s\n' "$@" > "$FAKE_HOST_STATE/embed_args"
env | grep -E '^(EMBED_|HF_|PYTHONUNBUFFERED)' | sort > "$FAKE_HOST_STATE/embed_env"
[ -f "$FAKE_HOST_STATE/embed_exit" ] && { echo "boom" >&2; exit 1; }
if [ -f "$FAKE_HOST_STATE/embed_ignore_term" ]; then trap '' TERM; else trap 'exit 0' TERM; fi
while :; do sleep 0.1; done
"""


class Embed:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.root = host.root / "embed"
        self.cli = self.root / "bin/qwen-embed"
        self.embed_model = self.root / "models/Qwen3-Embedding-4B-8bit"
        self.rerank_model = self.root / "models/Qwen3-Reranker-0.6B"
        redirect(
            CLI,
            self.cli,
            host.root,
            {
                "STOP_TRIES=80": "STOP_TRIES=8",
                "LOG_ROTATE_MAX=52428800": "LOG_ROTATE_MAX=200",
                "MIN_WEIGHTS_BYTES=500000000": "MIN_WEIGHTS_BYTES=10",
            },
        )
        _exe(self.root / "venv/bin/mlx_vlm.server", FAKE_SERVER)
        self.write_env("EMBED_LOG_LEVEL=info\n")
        self.write_weights(self.embed_model)
        self.write_weights(self.rerank_model)

    def write_env(self, text: str, mode: int = 0o600) -> None:
        env = self.root / "etc/qwen-embed.env"
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text(text)
        env.chmod(mode)

    @staticmethod
    def write_weights(model: Path, size: int = 32) -> None:
        model.mkdir(parents=True, exist_ok=True)
        (model / "config.json").write_text("{}")
        (model / "model.safetensors").write_bytes(b"x" * size)

    @property
    def pidfile(self) -> Path:
        return self.root / "run/qwen-embed.pid"

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.host.run(*args, cli=self.cli)

    def pid(self) -> int:
        return int(self.pidfile.read_text())


@pytest.fixture
def emb(tmp_path):
    host = make_host(tmp_path)
    e = Embed(host)
    yield e
    try:
        os.kill(e.pid(), signal.SIGKILL)
    except (OSError, ValueError):
        pass
    host.cleanup()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return not stat.stdout.strip().startswith("Z")


def test_start_status_stop(emb):
    r = emb.run("start")
    assert r.returncode == 0, r.stderr
    pid = emb.pid()
    emb.host.wait_for(emb.host.state / "embed_args")
    args = (emb.host.state / "embed_args").read_text().split()
    assert args[:4] == ["--host", "127.0.0.1", "--port", "8084"]
    assert args[4:] == [
        "--embedding-model",
        str(emb.embed_model),
        "--reranker-model",
        str(emb.rerank_model),
    ]
    st = emb.run("status")
    assert f"qwen-embed: running pid {pid}" in st.stdout
    assert "weights: complete" in st.stdout and "comfyui: stopped" in st.stdout
    assert emb.run("stop").returncode == 0
    assert not _alive(pid) and not emb.pidfile.exists()


def test_env_is_offline_and_allow_listed(emb):
    emb.write_env("EMBED_LOG_LEVEL=debug\nEVIL=1\nEMBED_API_KEY=short\n")
    assert emb.run("start").returncode == 0
    emb.host.wait_for(emb.host.state / "embed_env")
    env = (emb.host.state / "embed_env").read_text()
    assert "HF_HUB_OFFLINE=1" in env and "EMBED_LOG_LEVEL=debug" in env
    assert "EVIL" not in env and "EMBED_API_KEY" not in env  # too short: dropped


def test_env_file_must_be_600(emb):
    emb.write_env("EMBED_LOG_LEVEL=info\n", mode=0o644)
    r = emb.run("start")
    assert r.returncode == 1 and "must be mode 600" in r.stderr


def test_start_refuses_while_comfyui_pid_alive(emb):
    s = emb.host.sleeper()
    (emb.host.root / "comfyui/run").mkdir(parents=True)
    (emb.host.root / "comfyui/run/comfyui.pid").write_text(str(s.pid))
    r = emb.run("start")
    assert r.returncode == 1 and "ComfyUI is running" in r.stderr
    assert not emb.pidfile.exists()


def test_start_refuses_while_comfyui_listens(emb):
    s = emb.host.sleeper()
    (emb.host.state / "listen_8188").write_text(str(s.pid))
    assert emb.run("start").returncode == 1


def test_start_ignores_other_sidecars(emb):
    for rel in ("decision/run/openjev.pid", "tts/run/qwen-tts.pid"):
        p = emb.host.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(emb.host.sleeper().pid))
    assert emb.run("start").returncode == 0


def test_incomplete_rerank_weights_refuse(emb):
    (emb.rerank_model / "config.json").unlink()
    r = emb.run("start")
    assert r.returncode == 1 and "Qwen3-Reranker-0.6B" in r.stderr
    assert "weights: incomplete" in emb.run("status").stdout


def test_start_twice_and_port_in_use(emb):
    assert emb.run("start").returncode == 0
    assert "already running" in emb.run("start").stderr
    emb.run("stop")
    (emb.host.state / "listen_8084").write_text(str(emb.host.sleeper().pid))
    r = emb.run("start")
    assert r.returncode == 1 and "Port 8084 is already in use" in r.stderr


def test_exits_immediately_is_reported(emb):
    (emb.host.state / "embed_exit").write_text("")
    r = emb.run("start")
    assert r.returncode == 1 and "exited immediately" in r.stderr
    assert not emb.pidfile.exists()


def test_stop_escalates_to_kill(emb):
    (emb.host.state / "embed_ignore_term").write_text("")
    assert emb.run("start").returncode == 0
    pid = emb.pid()
    time.sleep(0.3)
    r = emb.run("stop")
    assert "killed" in r.stdout and not _alive(pid)


def test_reused_pid_is_never_signalled(emb):
    s = emb.host.sleeper()  # a live process without the marker
    emb.pidfile.parent.mkdir(parents=True, exist_ok=True)
    emb.pidfile.write_text(str(s.pid))
    assert "qwen-embed: stopped" in emb.run("status").stdout
    assert emb.run("stop").returncode == 0 and _alive(s.pid)


def test_log_rotates_on_start(emb):
    log = emb.root / "logs/qwen-embed.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * 300)
    assert emb.run("start").returncode == 0
    assert list(log.parent.glob("qwen-embed.log.*"))


# --- guards in the other scripts -------------------------------------------------------

from .test_host_openjev import _comfy  # noqa: E402


def test_comfyui_start_refuses_while_embed_runs(emb):
    cli = _comfy(emb.host)
    assert emb.run("start").returncode == 0
    r = emb.host.run("start", cli=cli)
    assert r.returncode == 1 and "qwen-embed" in r.stderr


def test_comfyui_status_mentions_embed_but_not_as_mlx(emb):
    """The service is an mlx_vlm.server too; ComfyUI must not mistake it for the chat model."""
    cli = _comfy(emb.host)
    assert emb.run("start").returncode == 0
    r = emb.host.run("status", cli=cli)
    assert "embed: running (stop it before comfyui start)" in r.stdout
    assert "mlx: stopped" in r.stdout


def test_reranker_sentence_transformers_template_refuses(emb):
    """Phase 0: the repo's chat_template.jinja makes MLX-VLM score empty query/document
    pairs; the file must be renamed aside before the service may start."""
    (emb.rerank_model / "chat_template.jinja").write_text("{{ messages }}")
    r = emb.run("start")
    assert r.returncode == 1 and "chat_template.jinja" in r.stderr
    assert "weights: incomplete" in emb.run("status").stdout
