"""host/strata/bin/strata and the guards around it, run with real zsh (Strata Phase 1)."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

import pytest

from .host_harness import HOST, Host, _exe, make_host, redirect


@pytest.fixture
def host(tmp_path):
    h = make_host(tmp_path)
    yield h
    h.cleanup()


def _comfy(host: Host) -> Path:
    root = host.root / "comfyui"
    cli = redirect(HOST / "comfyui/bin/comfyui", root / "bin/comfyui", host.root)
    _exe(root / "venv/bin/python", "#!/bin/bash\nexit 0\n")
    (root / "app").mkdir(parents=True)
    (root / "app/main.py").write_text("")
    return cli


def test_comfyui_start_refuses_while_strata_listens(host):
    s = host.strata_server()
    (host.state / "listen_8080").write_text(str(s.pid))
    r = host.run("start", cli=_comfy(host))
    assert r.returncode == 1 and "Strata engine is running" in r.stderr


def test_comfyui_start_refuses_while_strata_pid_alive(host):
    s = host.strata_server()
    pidfile = host.root / "strata/run/strata.pid"
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text(f"{s.pid}\n")
    r = host.run("start", cli=_comfy(host))
    assert r.returncode == 1 and "strata stop" in r.stderr


def test_comfyui_ignores_a_stale_strata_pid_file(host):
    s = host.sleeper()
    pidfile = host.root / "strata/run/strata.pid"
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text(f"{s.pid}\n")
    assert "strata: stopped" in host.run("status", cli=_comfy(host)).stdout


def test_comfyui_status_mentions_strata(host):
    assert "strata: stopped" in host.run("status", cli=_comfy(host)).stdout


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return not stat.stdout.strip().startswith("Z")


# --- the wrapper ---------------------------------------------------------------------------
CLI = HOST / "strata/bin/strata"
KEY = "s" * 40

# Strata's server: records argv and STRATA_* env, starts a child "engine", serves until TERM.
# On TERM it exits WITHOUT stopping the child (the wrapper must clean it up).
# $FAKE_HOST_STATE/strata_exit makes it die at once; strata_ignore_term ignores TERM.
FAKE_SERVER_PY = r"""#!/bin/bash
printf '%s\n' "$@" > "$FAKE_HOST_STATE/strata_args"
env | grep -E '^(STRATA_|HOME=|PYTHONUNBUFFERED)' | sort > "$FAKE_HOST_STATE/strata_env"
[ -f "$FAKE_HOST_STATE/strata_exit" ] && { echo "boom" >&2; exit 1; }
sleep 300 &
echo $! > "$FAKE_HOST_STATE/strata_child"
if [ -f "$FAKE_HOST_STATE/strata_ignore_term" ]; then trap '' TERM; else trap 'exit 0' TERM; fi
while :; do sleep 0.1; done
"""


class Strata:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.root = host.root / "strata"
        self.cli = self.root / "bin/strata"
        self.app = self.root / "app"
        # the test copy gives SIGTERM 2 s (not 60 s) before SIGKILL
        redirect(CLI, self.cli, host.root, {"STOP_TRIES=240": "STOP_TRIES=8"})
        (self.app / "serve").mkdir(parents=True)
        (self.app / "serve/server.py").write_text("")
        _exe(self.app / ".venv/bin/python", FAKE_SERVER_PY)
        (self.app / "strata-q2_0.json").write_text("{}")
        self.write_env(f"STRATA_API_KEY={KEY}\nSTRATA_MODEL=Q2_0\nSTRATA_CONTEXT=32768\n")

    def write_env(self, text: str, mode: int = 0o600) -> None:
        env = self.root / "etc/strata.env"
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text(text)
        env.chmod(mode)

    @property
    def pidfile(self) -> Path:
        return self.root / "run/strata.pid"

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.host.run(*args, cli=self.cli)

    def pid(self) -> int:
        return int(self.pidfile.read_text())

    def child(self) -> int:
        self.host.wait_for(self.host.state / "strata_child")
        return int((self.host.state / "strata_child").read_text())


@pytest.fixture
def st(host):
    s = Strata(host)
    yield s
    for f in (s.pidfile, host.state / "strata_child"):
        try:
            os.kill(int(f.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def test_script_is_valid_zsh():
    assert subprocess.run(["zsh", "-n", str(CLI)]).returncode == 0


def test_status_when_stopped(st):
    r = st.run("status")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0] == "strata: stopped"
    assert "model: Q2_0" in lines and "context: 32768" in lines
    assert "listen: http://0.0.0.0:8080" in lines


def test_start_status_stop(st):
    r = st.run("start")
    assert r.returncode == 0, r.stderr
    pid = st.pid()
    assert _alive(pid)
    st.host.wait_for(st.host.state / "strata_args")
    args = (st.host.state / "strata_args").read_text().splitlines()
    assert args == [
        str(st.app / "serve/server.py"),
        "--engine",
        "strata",
        "--config",
        str(st.app / "strata-q2_0.json"),
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
    ]
    st.host.wait_for(st.host.state / "strata_env")
    env = (st.host.state / "strata_env").read_text()
    assert f"STRATA_API_KEY={KEY}" in env and f"HOME={st.root}/home" in env
    assert st.run("status").stdout.splitlines()[0] == f"strata: running pid {pid}"
    child = st.child()
    r = st.run("stop")
    assert r.returncode == 0 and "Strata stopped" in r.stdout
    assert not _alive(pid) and not _alive(child)  # the engine child does not outlive it
    assert not st.pidfile.exists()
    assert (st.root / "logs/strata.log").exists()


def test_stop_kills_after_term_is_ignored(st):
    (st.host.state / "strata_ignore_term").write_text("")
    assert st.run("start").returncode == 0
    pid = st.pid()
    r = st.run("stop")
    assert r.returncode == 0 and "killed" in r.stdout
    assert not _alive(pid)


def test_stop_when_not_running(st):
    r = st.run("stop")
    assert r.returncode == 0 and "not running" in r.stdout


def test_start_fails_cleanly_when_the_server_exits(st):
    (st.host.state / "strata_exit").write_text("")
    r = st.run("start")
    assert r.returncode == 1 and "exited" in r.stderr
    assert not st.pidfile.exists()


@pytest.mark.parametrize(
    ("port", "words"),
    [
        (8080, "MLX"),
        (8188, "ComfyUI"),
        (8081, "sidecar"),
        (8083, "sidecar"),
        (8084, "embedding"),
    ],
)
def test_start_refuses_when_another_engine_or_port_is_busy(st, port, words):
    s = st.host.sleeper()
    (st.host.state / f"listen_{port}").write_text(str(s.pid))
    r = st.run("start")
    assert r.returncode == 1 and words in r.stderr, r.stderr
    assert not st.pidfile.exists()


@pytest.mark.parametrize("lock", ["mlx.lock", "maintenance.lock"])
def test_start_refuses_while_an_mlx_command_or_maintenance_holds_its_lock(st, lock):
    (st.host.root / "run" / lock).mkdir()
    r = st.run("start")
    assert r.returncode == 1 and lock in r.stderr


@pytest.mark.parametrize(
    ("text", "mode", "err"),
    [
        (f"STRATA_API_KEY={KEY}\nSTRATA_MODEL=Q2_0\n", 0o644, "mode 600"),
        ("STRATA_API_KEY=short\nSTRATA_MODEL=Q2_0\n", 0o600, "STRATA_API_KEY"),
        ("STRATA_MODEL=Q2_0\nSTRATA_ALLOWED_HOSTS=a b\n", 0o600, "STRATA_ALLOWED_HOSTS"),
        (f"STRATA_API_KEY={KEY}\nSTRATA_MODEL=Q9_9\n", 0o600, "STRATA_MODEL"),
        (f"STRATA_API_KEY={KEY}\nSTRATA_MODEL=IQ3_XXS\n", 0o600, "strata-iq3_xxs.json"),
    ],
)
def test_start_refuses_a_bad_env_file_or_missing_config(st, text, mode, err):
    st.write_env(text, mode)
    r = st.run("start")
    assert r.returncode == 1 and err in r.stderr, r.stderr
    assert not st.pidfile.exists()


def test_env_file_is_parsed_not_sourced(st, tmp_path):
    marker = tmp_path / "pwned"
    st.write_env(f"STRATA_API_KEY={KEY}\nSTRATA_MODEL=Q2_0\n$(touch {marker})\n")
    assert st.run("start").returncode == 0
    assert not marker.exists()


def test_pid_file_of_another_process_is_never_signalled(st):
    s = st.host.sleeper()
    st.pidfile.parent.mkdir(parents=True, exist_ok=True)
    st.pidfile.write_text(f"{s.pid}\n")
    assert st.run("status").stdout.startswith("strata: stopped")
    assert st.run("stop").returncode == 0 and _alive(s.pid)


def test_status_defaults_to_the_models_trained_context(st):
    st.write_env(f"STRATA_API_KEY={KEY}\nSTRATA_MODEL=Q2_0\n")
    assert "context: 262144" in st.run("status").stdout.splitlines()


def test_status_shows_the_shared_url_and_auth(st):
    lines = st.run("status").stdout.splitlines()
    assert "listen: http://0.0.0.0:8080" in lines and "auth: api key" in lines


def test_without_a_key_it_serves_like_mlx_and_never_inherits_one(st):
    """No STRATA_API_KEY: open on the LAN like the MLX server, so LiteLLM's route needs nothing."""
    st.write_env("STRATA_MODEL=Q2_0\nSTRATA_ALLOWED_HOSTS=llm.example.com,mac-mini.local\n")
    st.host.env["STRATA_API_KEY"] = "inherited-" + "x" * 40
    assert "auth: none" in st.run("status").stdout
    r = st.run("start")
    assert r.returncode == 0, r.stderr
    st.host.wait_for(st.host.state / "strata_env")
    env = (st.host.state / "strata_env").read_text()
    assert "STRATA_API_KEY" not in env
    assert "STRATA_ALLOWED_HOSTS=llm.example.com,mac-mini.local" in env
    assert st.run("stop").returncode == 0


def test_start_with_a_pack_overrides_the_env_file_and_is_remembered(st):
    (st.app / "strata-iq3_xxs.json").write_text(
        '{\n "args": [\n  "--max-context",\n  "131072"\n ]\n}\n'
    )
    r = st.run("start", "IQ3_XXS")
    assert r.returncode == 0, r.stderr
    st.host.wait_for(st.host.state / "strata_args")
    args = (st.host.state / "strata_args").read_text().splitlines()
    assert args[args.index("--config") + 1] == str(st.app / "strata-iq3_xxs.json")
    lines = st.run("status").stdout.splitlines()
    assert "model: IQ3_XXS" in lines and "context: 131072" in lines  # read from its config
    assert st.run("stop").returncode == 0
    (st.host.state / "strata_args").unlink()
    assert st.run("start").returncode == 0  # a plain start (the watchdog's) keeps the choice
    st.host.wait_for(st.host.state / "strata_args")
    assert str(st.app / "strata-iq3_xxs.json") in (st.host.state / "strata_args").read_text()


@pytest.mark.parametrize("pack", ["iq3_xxs", "Q9_9", "../x", ""])
def test_start_refuses_an_unknown_pack(st, pack):
    r = st.run("start", pack)
    assert r.returncode == 2 and "pack" in r.stderr
    assert not st.pidfile.exists()


def test_a_pack_without_its_run_config_is_refused_and_not_remembered(st):
    r = st.run("start", "IQ3_XXS")
    assert r.returncode == 1 and "strata-iq3_xxs.json" in r.stderr
    assert "model: Q2_0" in st.run("status").stdout.splitlines()


def test_a_bad_remembered_pack_falls_back_to_the_env_file(st):
    (st.root / "run").mkdir(parents=True, exist_ok=True)
    (st.root / "run/strata.pack").write_text("junk\n")
    assert "model: Q2_0" in st.run("status").stdout.splitlines()


# --- logs ----------------------------------------------------------------------------------
def _small_rotation(st: Strata) -> None:
    """Re-copy the wrapper with a 100-byte limit and 2 archives kept."""
    redirect(
        CLI,
        st.cli,
        st.host.root,
        {
            "STOP_TRIES=240": "STOP_TRIES=8",
            "LOG_ROTATE_MAX=52428800": "LOG_ROTATE_MAX=100",
            "LOG_ROTATE_KEEP=8": "LOG_ROTATE_KEEP=2",
        },
    )


def test_status_names_the_engine_log(st):
    assert f"engine log: {st.app}/strata-q2_0.log" in st.run("status").stdout.splitlines()


def test_start_rotates_both_logs_over_the_limit(st):
    _small_rotation(st)
    (st.root / "logs").mkdir(parents=True)
    (st.root / "logs/strata.log").write_text("x" * 200)
    engine = st.app / "strata-q2_0.log"
    engine.write_text("e" * 200)
    for stamp in ("20260101-000000", "20260102-000000"):
        (st.app / f"strata-q2_0.log.{stamp}").write_text("old")
    assert st.run("start").returncode == 0
    archives = sorted(p.name for p in st.app.glob("strata-q2_0.log.*"))
    assert len(archives) == 2 and "strata-q2_0.log.20260101-000000" not in archives
    assert not engine.exists()  # Strata reopens it in append mode on its own start
    assert len(list((st.root / "logs").glob("strata.log.*"))) == 1


def test_start_leaves_small_logs_and_other_packs_alone(st):
    _small_rotation(st)
    (st.app / "strata-q2_0.log").write_text("small")
    (st.app / "strata-iq3_xxs.log").write_text("i" * 200)  # not the pack being started
    assert st.run("start").returncode == 0
    assert (st.app / "strata-q2_0.log").read_text() == "small"
    assert (st.app / "strata-iq3_xxs.log").stat().st_size == 200
    assert not list(st.app.glob("*.log.*"))
