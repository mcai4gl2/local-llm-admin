"""host/decision/bin/openjev and the guards around it, run with real zsh (M6 Phase 1)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from .host_harness import HOST, Host, _exe, make_host, redirect

CLI = HOST / "decision/bin/openjev"

# The sidecar's python: records its argv and OPENJEV_* env, then serves until TERM.
# $FAKE_HOST_STATE/openjev_exit makes it die at once; openjev_ignore_term makes it ignore TERM.
FAKE_OPENJEV = r"""#!/bin/bash
printf '%s\n' "$@" > "$FAKE_HOST_STATE/openjev_args"
env | grep -E '^(OPENJEV_|HF_|PYTHONUNBUFFERED)' | sort > "$FAKE_HOST_STATE/openjev_env"
[ -f "$FAKE_HOST_STATE/openjev_exit" ] && { echo "boom" >&2; exit 1; }
if [ -f "$FAKE_HOST_STATE/openjev_ignore_term" ]; then trap '' TERM; else trap 'exit 0' TERM; fi
while :; do sleep 0.1; done
"""

KEY = "k" * 64


class Decision:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.root = host.root / "decision"
        self.cli = self.root / "bin/openjev"
        self.model = self.root / "models/diffusiongemma-26B-A4B-it-4bit"
        # the test copy gives SIGTERM 2 s (not 20 s) before SIGKILL
        # and rotates its log at 200 bytes (not 50 MiB) when it starts
        redirect(
            CLI,
            self.cli,
            host.root,
            {"STOP_TRIES=80": "STOP_TRIES=8", "LOG_ROTATE_MAX=52428800": "LOG_ROTATE_MAX=200"},
        )
        (self.root / "app/openjev").mkdir(parents=True)
        (self.root / "app/openjev/__init__.py").write_text("")
        _exe(self.root / "venv/bin/python", FAKE_OPENJEV)
        self.write_env(f"OPENJEV_API_KEY={KEY}\nOPENJEV_MLX_CACHE_LIMIT_GB=2\n")
        self.write_weights()

    def write_env(self, text: str, mode: int = 0o600) -> None:
        env = self.root / "etc/openjev.env"
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text(text)
        env.chmod(mode)

    def write_weights(self, shards: dict[str, int] | None = None, total: int = 30) -> None:
        shards = shards if shards is not None else {"a.safetensors": 16, "b.safetensors": 16}
        self.model.mkdir(parents=True, exist_ok=True)
        (self.model / "config.json").write_text("{}")
        for name, size in shards.items():
            (self.model / name).write_bytes(b"x" * size)
        index = {
            "metadata": {"total_size": total},
            "weight_map": {f"w{i}": n for i, n in enumerate(["a.safetensors", "b.safetensors"])},
        }
        (self.model / "model.safetensors.index.json").write_text(json.dumps(index))

    @property
    def pidfile(self) -> Path:
        return self.root / "run/openjev.pid"

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.host.run(*args, cli=self.cli)

    def pid(self) -> int:
        return int(self.pidfile.read_text())

    def env_seen(self) -> dict[str, str]:
        self.host.wait_for(self.host.state / "openjev_env")
        lines = (self.host.state / "openjev_env").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines)


@pytest.fixture
def dj(tmp_path):
    host = make_host(tmp_path)
    d = Decision(host)
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


def test_status_when_stopped(dj):
    r = dj.run("status")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "openjev: stopped"
    assert "listen: http://127.0.0.1:8081" in r.stdout
    assert "weights: complete" in r.stdout


def test_start_then_status_then_stop(dj):
    r = dj.run("start")
    assert r.returncode == 0, r.stderr
    pid = dj.pid()
    assert f"started (pid {pid})" in r.stdout
    s = dj.run("status")
    assert s.stdout.splitlines()[0] == f"openjev: running pid {pid}"
    assert (dj.host.state / "openjev_args").read_text().split() == ["-m", "openjev"]
    r = dj.run("stop")
    assert r.returncode == 0 and "stopped" in r.stdout
    assert not dj.pidfile.exists()
    assert not _alive(pid)


def test_start_forces_loopback_port_backend_and_model(dj):
    dj.write_env(
        f"OPENJEV_API_KEY={KEY}\nOPENJEV_HOST=0.0.0.0\nOPENJEV_PORT=9999\n"
        "OPENJEV_BACKEND=vllm\nOPENJEV_MLX_MODEL=/elsewhere\nOPENJEV_MLX_CACHE_LIMIT_GB=2\n"
    )
    assert dj.run("start").returncode == 0
    env = dj.env_seen()
    assert env["OPENJEV_HOST"] == "127.0.0.1"
    assert env["OPENJEV_PORT"] == "8081"
    assert env["OPENJEV_BACKEND"] == "mlx"
    assert env["OPENJEV_MLX_MODEL"] == str(dj.model)
    assert env["OPENJEV_API_KEY"] == KEY
    assert env["OPENJEV_MLX_CACHE_LIMIT_GB"] == "2"
    assert env["HF_HUB_OFFLINE"] == "1"


def test_env_file_is_parsed_not_executed(dj):
    marker = dj.host.state / "pwned"
    dj.write_env(f"OPENJEV_API_KEY={KEY}\ntouch {marker}\nOTHER=$(touch {marker})\n")
    assert dj.run("start").returncode == 0
    env = dj.env_seen()
    assert not marker.exists()
    assert "OTHER" not in env


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda d: (d.root / "etc/openjev.env").unlink(), "openjev.env"),
        (lambda d: d.write_env(f"OPENJEV_API_KEY={KEY}\n", mode=0o644), "mode 600"),
        (lambda d: d.write_env("OPENJEV_MLX_CACHE_LIMIT_GB=2\n"), "OPENJEV_API_KEY"),
        (lambda d: (d.root / "venv/bin/python").unlink(), "venv"),
        (lambda d: (d.root / "app/openjev/__init__.py").unlink(), "OpenJev checkout"),
        (lambda d: (d.model / "b.safetensors").unlink(), "weights"),
        (lambda d: (d.model / "b.safetensors").write_bytes(b"x" * 4), "weights"),
        (lambda d: (d.model / "model.safetensors.index.json").unlink(), "weights"),
    ],
)
def test_start_refuses_incomplete_install(dj, setup, message):
    setup(dj)
    r = dj.run("start")
    assert r.returncode == 1
    assert message in r.stderr
    assert not dj.pidfile.exists()
    assert not (dj.host.state / "openjev_args").exists()


@pytest.mark.parametrize(
    ("key", "ok"),
    [
        ("a" * 31, False),
        ("a" * 32, True),
        ("a" * 256, True),
        ("a" * 257, False),
        ("a" * 40 + "!", False),
    ],
)
def test_api_key_length_and_charset(dj, key, ok):
    """No bounded regex repetition: macOS's regex library rejects counts above 255."""
    dj.write_env(f"OPENJEV_API_KEY={key}\n")
    r = dj.run("start")
    assert (r.returncode == 0) is ok, r.stderr
    assert "failed to compile regex" not in r.stderr


def test_key_check_uses_no_large_repetition_counts():
    import re

    counts = [int(n) for n in re.findall(r"\{\d+,(\d+)\}", CLI.read_text())]
    assert all(n <= 255 for n in counts), counts


def test_status_reports_incomplete_weights(dj):
    (dj.model / "b.safetensors").unlink()
    r = dj.run("status")
    assert r.returncode == 0
    assert "weights: incomplete" in r.stdout


def test_start_refuses_when_already_running(dj):
    assert dj.run("start").returncode == 0
    r = dj.run("start")
    assert r.returncode == 1 and "already running" in r.stderr


def test_start_refuses_when_port_busy(dj):
    s = dj.host.sleeper()
    (dj.host.state / "listen_8081").write_text(str(s.pid))
    r = dj.run("start")
    assert r.returncode == 1 and "8081" in r.stderr
    assert not dj.pidfile.exists()


def test_start_refuses_while_comfyui_listens(dj):
    s = dj.host.sleeper()
    (dj.host.state / "listen_8188").write_text(str(s.pid))
    r = dj.run("start")
    assert r.returncode == 1 and "ComfyUI" in r.stderr


def test_start_refuses_while_comfyui_pid_alive(dj):
    s = dj.host.sleeper()
    (dj.host.root / "comfyui/run").mkdir(parents=True)
    (dj.host.root / "comfyui/run/comfyui.pid").write_text(str(s.pid))
    r = dj.run("start")
    assert r.returncode == 1 and "ComfyUI" in r.stderr


def test_start_allowed_while_mlx_serves(dj):
    s = dj.host.sleeper()
    (dj.host.state / "listen_8080").write_text(str(s.pid))
    r = dj.run("start")
    assert r.returncode == 0, r.stderr


def test_start_reports_immediate_exit(dj):
    (dj.host.state / "openjev_exit").write_text("")
    r = dj.run("start")
    assert r.returncode == 1 and "exited immediately" in r.stderr
    assert not dj.pidfile.exists()


def test_start_refuses_without_volume(tmp_path):
    host = make_host(tmp_path)
    cli = redirect(CLI, tmp_path / "openjev", tmp_path / "missing")
    r = host.run("start", cli=cli)
    assert r.returncode == 1 and "not mounted" in r.stderr


def test_stop_when_not_running_is_idempotent(dj):
    r = dj.run("stop")
    assert r.returncode == 0 and "not running" in r.stdout


def test_stop_escalates_to_kill(dj):
    (dj.host.state / "openjev_ignore_term").write_text("")
    assert dj.run("start").returncode == 0
    pid = dj.pid()
    t = time.monotonic()
    r = dj.run("stop")
    assert r.returncode == 0
    assert "killed" in r.stdout
    assert time.monotonic() - t < 15
    assert not _alive(pid)


def test_stale_pid_file_is_not_killed(dj):
    """A reused pid that is not the sidecar must never be signalled."""
    s = dj.host.sleeper()
    dj.pidfile.parent.mkdir(parents=True, exist_ok=True)
    dj.pidfile.write_text(str(s.pid))
    assert dj.run("status").stdout.splitlines()[0] == "openjev: stopped"
    r = dj.run("stop")
    assert r.returncode == 0 and "not running" in r.stdout
    assert _alive(s.pid)
    assert not dj.pidfile.exists()


def test_unknown_command_prints_usage(dj):
    r = dj.run("restart")
    assert r.returncode == 2 and "Usage" in r.stdout


# --- log rotation on start (M6 Phase 4) ---------------------------------------------------


def _logs(dj):
    return sorted(p.name for p in (dj.root / "logs").glob("openjev.log.*"))


def test_start_rotates_a_large_log(dj):
    log = dj.root / "logs/openjev.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * 300)
    assert dj.run("start").returncode == 0
    (archive,) = _logs(dj)
    assert len(archive) == len("openjev.log.20261001-120000")
    assert (dj.root / "logs" / archive).read_text() == "x" * 300
    assert "start openjev" in log.read_text() and "x" not in log.read_text().split("=====")[0]


def test_start_keeps_a_small_log(dj):
    log = dj.root / "logs/openjev.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("small\n")
    assert dj.run("start").returncode == 0
    assert _logs(dj) == [] and log.read_text().startswith("small\n")


def test_rotation_keeps_eight_archives(dj):
    logs_dir = dj.root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for i in range(9):
        (logs_dir / f"openjev.log.20260901-0000{i:02d}").write_text("old")
    (logs_dir / "openjev.log").write_text("x" * 300)
    (logs_dir / "openjev.log.notes").write_text("not an archive")
    assert dj.run("start").returncode == 0
    archives = _logs(dj)
    assert "openjev.log.notes" in archives  # never touched
    rotated = [a for a in archives if a != "openjev.log.notes"]
    assert len(rotated) == 8 and "openjev.log.20260901-000000" not in rotated


def test_refused_start_never_rotates(dj):
    log = dj.root / "logs/openjev.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    s = dj.host.sleeper()
    (dj.host.state / "listen_8081").write_text(str(s.pid))
    log.write_text("x" * 300)
    assert dj.run("start").returncode == 1
    assert _logs(dj) == []


# --- guards in the other scripts -------------------------------------------------------


def _comfy(host: Host) -> Path:
    root = host.root / "comfyui"
    cli = redirect(HOST / "comfyui/bin/comfyui", root / "bin/comfyui", host.root)
    _exe(root / "venv/bin/python", "#!/bin/bash\nexit 0\n")
    (root / "app").mkdir(parents=True)
    (root / "app/main.py").write_text("")
    return cli


def test_comfyui_start_refuses_while_sidecar_runs(dj):
    cli = _comfy(dj.host)
    assert dj.run("start").returncode == 0
    (dj.host.state / "listen_8081").write_text(str(dj.pid()))
    r = dj.host.run("start", cli=cli)
    assert r.returncode == 1 and "decision sidecar" in r.stderr


def test_comfyui_status_mentions_sidecar(dj):
    cli = _comfy(dj.host)
    s = dj.host.sleeper()
    (dj.host.state / "listen_8081").write_text(str(s.pid))
    r = dj.host.run("status", cli=cli)
    assert "decision: running (stop it before comfyui start)" in r.stdout


def test_start_refuses_while_tts_pid_alive(dj):
    pidfile = dj.host.root / "tts/run/qwen-tts.pid"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    sleeper = dj.host.sleeper()
    pidfile.write_text(str(sleeper.pid))
    r = dj.run("start")
    assert r.returncode == 1 and "TTS sidecar is running" in r.stderr
    assert not dj.pidfile.exists()


def test_status_mentions_tts(dj):
    assert "tts: stopped" in dj.run("status").stdout


def test_mlx_model_start_ignores_sidecar_port(dj):
    s = dj.host.sleeper()
    (dj.host.state / "listen_8081").write_text(str(s.pid))
    (dj.host.state / "healthy").write_text("")
    r = dj.host.run("start", "a")
    assert r.returncode == 0, r.stderr
