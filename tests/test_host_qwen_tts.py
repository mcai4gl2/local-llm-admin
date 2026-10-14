"""host/tts/bin/qwen-tts and the guards around it, run with real zsh (M7)."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from .host_harness import HOST, Host, _exe, make_host, redirect

CLI = HOST / "tts/bin/qwen-tts"

FAKE_SERVER = r"""#!/bin/bash
[ "$1" = "-c" ] && exit 0  # require_install's `python -c 'import mlx_audio'`
printf '%s\n' "$@" > "$FAKE_HOST_STATE/tts_args"
env | grep -E '^(TTS_|HF_|PYTHONUNBUFFERED|MLX_AUDIO)' | sort > "$FAKE_HOST_STATE/tts_env"
[ -f "$FAKE_HOST_STATE/tts_exit" ] && { echo "boom" >&2; exit 1; }
if [ -f "$FAKE_HOST_STATE/tts_ignore_term" ]; then trap '' TERM; else trap 'exit 0' TERM; fi
while :; do sleep 0.1; done
"""

KEY = "k" * 64


class Tts:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.root = host.root / "tts"
        self.cli = self.root / "bin/qwen-tts"
        self.model = self.root / "models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
        redirect(
            CLI,
            self.cli,
            host.root,
            {
                "STOP_TRIES=80": "STOP_TRIES=8",
                "LOG_ROTATE_MAX=52428800": "LOG_ROTATE_MAX=200",
                "MIN_WEIGHTS_BYTES=1000000000": "MIN_WEIGHTS_BYTES=10",
            },
        )
        _exe(self.root / "venv/bin/python", FAKE_SERVER)
        self.write_env(f"TTS_API_KEY={KEY}\nTTS_LOG_LEVEL=info\n")
        self.write_weights()

    def write_env(self, text: str, mode: int = 0o600) -> None:
        env = self.root / "etc/qwen-tts.env"
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text(text)
        env.chmod(mode)

    def write_weights(self, size: int = 32) -> None:
        self.model.mkdir(parents=True, exist_ok=True)
        (self.model / "config.json").write_text("{}")
        (self.model / "model.safetensors").write_bytes(b"x" * size)

    @property
    def pidfile(self) -> Path:
        return self.root / "run/qwen-tts.pid"

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.host.run(*args, cli=self.cli)

    def pid(self) -> int:
        return int(self.pidfile.read_text())


@pytest.fixture
def tts(tmp_path):
    host = make_host(tmp_path)
    d = Tts(host)
    yield d
    try:
        os.kill(d.pid(), signal.SIGKILL)
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


def test_script_is_valid_zsh():
    assert subprocess.run(["zsh", "-n", str(CLI)]).returncode == 0


def test_status_when_stopped(tts):
    r = tts.run("status")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "qwen-tts: stopped"
    assert "listen: http://127.0.0.1:8083" in r.stdout
    assert "weights: complete" in r.stdout
    assert "decision: stopped" in r.stdout


def test_start_then_status_then_stop(tts):
    r = tts.run("start")
    assert r.returncode == 0, r.stderr
    pid = tts.pid()
    s = tts.run("status")
    assert s.stdout.splitlines()[0] == f"qwen-tts: running pid {pid}"
    r = tts.run("stop")
    assert r.returncode == 0 and "stopped" in r.stdout
    assert not tts.pidfile.exists()
    assert not _alive(pid)


def test_start_args_pin_host_port_and_model(tts):
    assert tts.run("start").returncode == 0
    tts.host.wait_for(tts.host.state / "tts_args")
    args = (tts.host.state / "tts_args").read_text().split()
    assert args[:2] == ["-m", "mlx_audio.server"]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "8083"


def test_env_file_is_parsed_not_executed(tts):
    marker = tts.host.state / "pwned"
    tts.write_env(f"TTS_API_KEY={KEY}\nTTS_LOG_LEVEL=info; touch {marker}\n")
    assert tts.run("start").returncode == 0
    assert not marker.exists()
    env = (tts.host.state / "tts_env").read_text()
    assert f"TTS_API_KEY={KEY}" in env
    assert "HF_HUB_OFFLINE=1" in env


def test_env_wrong_mode_refused(tts):
    tts.write_env(f"TTS_API_KEY={KEY}\n", mode=0o644)
    r = tts.run("start")
    assert r.returncode == 1 and "mode 600" in r.stderr


def test_start_refuses_incomplete_weights(tts):
    (tts.model / "model.safetensors").write_bytes(b"x")
    r = tts.run("start")
    assert r.returncode == 1 and "weights" in r.stderr
    assert not tts.pidfile.exists()


def test_start_refuses_when_already_running(tts):
    assert tts.run("start").returncode == 0
    r = tts.run("start")
    assert r.returncode == 1 and "already running" in r.stderr


def test_start_refuses_when_port_busy(tts):
    s = tts.host.sleeper()
    (tts.host.state / "listen_8083").write_text(str(s.pid))
    r = tts.run("start")
    assert r.returncode == 1 and "8083" in r.stderr
    assert not tts.pidfile.exists()


def test_start_refuses_while_comfyui_pid_alive(tts):
    s = tts.host.sleeper()
    (tts.host.root / "comfyui/run").mkdir(parents=True)
    (tts.host.root / "comfyui/run/comfyui.pid").write_text(str(s.pid))
    r = tts.run("start")
    assert r.returncode == 1 and "ComfyUI" in r.stderr


def test_start_refuses_while_openjev_pid_alive(tts):
    pidfile = tts.host.root / "decision/run/openjev.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    sleeper = tts.host.sleeper()
    pidfile.write_text(str(sleeper.pid))
    r = tts.run("start")
    assert r.returncode == 1 and "decision sidecar is running" in r.stderr
    assert not tts.pidfile.exists()


def test_start_allowed_while_mlx_serves(tts):
    s = tts.host.sleeper()
    (tts.host.state / "listen_8080").write_text(str(s.pid))
    assert tts.run("start").returncode == 0


def test_start_reports_immediate_exit(tts):
    (tts.host.state / "tts_exit").write_text("")
    r = tts.run("start")
    assert r.returncode == 1 and "exited immediately" in r.stderr
    assert not tts.pidfile.exists()


def test_start_refuses_without_volume(tmp_path):
    host = make_host(tmp_path)
    cli = redirect(CLI, tmp_path / "qwen-tts", tmp_path / "missing")
    r = host.run("start", cli=cli)
    assert r.returncode == 1 and "not mounted" in r.stderr
    host.cleanup()


def test_stop_when_not_running_is_idempotent(tts):
    r = tts.run("stop")
    assert r.returncode == 0 and "not running" in r.stdout


def test_stop_escalates_to_kill(tts):
    (tts.host.state / "tts_ignore_term").write_text("")
    assert tts.run("start").returncode == 0
    pid = tts.pid()
    t = time.monotonic()
    r = tts.run("stop")
    assert r.returncode == 0 and "killed" in r.stdout
    assert time.monotonic() - t < 15
    assert not _alive(pid)


def test_unknown_command_prints_usage(tts):
    r = tts.run("rm")
    assert r.returncode == 2 and "Usage" in r.stdout


def test_start_rotates_a_large_log(tts):
    log = tts.root / "logs/qwen-tts.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"x" * 201)
    assert tts.run("start").returncode == 0
    assert log.stat().st_size < 200
    assert list(log.parent.glob("qwen-tts.log.*"))


def _comfy(host: Host) -> Path:
    root = host.root / "comfyui"
    cli = redirect(HOST / "comfyui/bin/comfyui", root / "bin/comfyui", host.root)
    _exe(root / "venv/bin/python", "#!/bin/bash\nexit 0\n")
    (root / "app").mkdir(parents=True)
    (root / "app/main.py").write_text("")
    return cli


def test_comfyui_start_refuses_while_tts_runs(tts):
    assert tts.run("start").returncode == 0
    comfy = _comfy(tts.host)
    (tts.host.state / "listen_8083").write_text(str(tts.pid()))
    r = tts.host.run("start", cli=comfy)
    assert r.returncode == 1 and "TTS sidecar is running" in r.stderr


def test_comfyui_status_mentions_tts(tts):
    comfy = _comfy(tts.host)
    assert "tts: stopped" in tts.host.run("status", cli=comfy).stdout
