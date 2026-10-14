"""host/bin/mlx-model lock ownership, cleanup and unlock, run with real zsh (M1 §2)."""

import signal
import subprocess
import time

import pytest

from .host_harness import FIX, HOST, make_host

LEGACY = FIX / "mac/mlx-model.dc1"


@pytest.fixture
def host(tmp_path):
    h = make_host(tmp_path)
    yield h
    h.cleanup()


def _start_blocking(host, *args):
    """A start whose server never gets healthy: it holds the lock in its wait loop."""
    p = host.spawn("start", *args)
    host.wait_for(host.lock / "owner")
    host.wait_for(host.root / "run/mlx-server.pid")
    return p


def _sig_and_wait(p, sig):
    p.send_signal(sig)
    p.wait(timeout=10)
    return p.returncode


def test_script_is_valid_zsh():
    assert subprocess.run(["zsh", "-n", str(HOST / "bin/mlx-model")]).returncode == 0


def test_successful_start_releases_its_lock(host):
    (host.state / "healthy").write_text("")
    r = host.run("start", "a")
    assert r.returncode == 0, r.stderr
    assert "Serving a" in r.stdout
    assert not host.lock.exists() and not host.gate.exists()


def test_start_prints_the_api_url_for_this_host(host):
    (host.state / "healthy").write_text("")
    r = host.run("start", "a")
    name = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    assert f"  API: http://{name}:8080/v1\n" in r.stdout


def test_start_api_url_honors_mlx_public_host(host):
    (host.state / "healthy").write_text("")
    host.env["MLX_PUBLIC_HOST"] = "mac-mini.local"
    r = host.run("start", "a")
    assert "  API: http://mac-mini.local:8080/v1\n" in r.stdout


def test_start_api_url_comes_from_the_optional_local_env_file(host):
    (host.state / "healthy").write_text("")
    (host.root / "etc/local.env").write_text("export MLX_PUBLIC_HOST=192.0.2.7\n")
    r = host.run("start", "a")
    assert "  API: http://192.0.2.7:8080/v1\n" in r.stdout


def test_lock_records_owner_identity(host):
    p = _start_blocking(host, "a")
    owner = host.owner(host.lock)
    assert owner["pid"] == str(p.pid) and owner["start"] and owner["cmd"] == "start_server"
    _sig_and_wait(p, signal.SIGTERM)


def test_rejected_start_leaves_no_lock(host):
    s = host.sleeper()
    (host.state / "listen_8188").write_text(str(s.pid))
    r = host.run("start", "a")
    assert r.returncode == 1 and "ComfyUI" in r.stderr
    assert not host.lock.exists()


def test_failing_stop_leaves_no_lock(host):
    s = host.sleeper()
    (host.state / "listen_8080").write_text(str(s.pid))
    r = host.run("stop")
    assert r.returncode == 1 and "Port 8080 is still in use" in r.stderr
    assert not host.lock.exists()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_term_and_int_during_start_release_the_lock(host, sig):
    p = _start_blocking(host, "a")
    assert _sig_and_wait(p, sig) == -sig  # re-raised: a shell caller sees 128 + sig
    assert not host.lock.exists() and not host.gate.exists()


def test_sigkill_leaves_stale_lock_that_unlock_removes(host):
    p = _start_blocking(host, "a")
    assert _sig_and_wait(p, signal.SIGKILL) == -9
    assert host.lock.exists()
    r = host.run("unlock")
    assert r.returncode == 0 and "Removed stale lock" in r.stdout, r.stderr
    assert not host.lock.exists() and not host.gate.exists()
    host.run("stop")
    (host.state / "healthy").write_text("")
    assert host.run("start", "a").returncode == 0


def test_unlock_when_unlocked_is_idempotent(host):
    for _ in range(2):
        r = host.run("unlock")
        assert r.returncode == 0 and "No lock held" in r.stdout
    assert not host.gate.exists()


def test_unlock_refuses_live_owner(host):
    p = _start_blocking(host, "a")
    r = host.run("unlock")
    assert r.returncode == 1 and "held by a running" in r.stderr
    assert host.owner(host.lock)["pid"] == str(p.pid)
    _sig_and_wait(p, signal.SIGTERM)


def test_unlock_refuses_unverifiable_owner(host):
    host.lock.mkdir()
    (host.lock / "owner").write_text("garbage\n")
    r = host.run("unlock")
    assert r.returncode == 1 and "Cannot verify" in r.stderr
    assert host.lock.exists()


def test_unlock_treats_reused_pid_as_stale(host):
    s = host.sleeper()  # a live pid, but not the process that took the lock
    host.lock.mkdir()
    (host.lock / "owner").write_text(
        f"pid={s.pid}\nstart=Thu Jan  1 00:00:00 1970\ncmd=start_server\n"
    )
    r = host.run("unlock")
    assert r.returncode == 0 and "Removed stale lock" in r.stdout
    assert s.poll() is None  # the unrelated process is untouched


def test_unlock_removes_legacy_empty_lock_after_recheck(host):
    host.lock.mkdir()
    t0 = time.monotonic()
    r = host.run("unlock")
    assert r.returncode == 0 and "Removed stale lock" in r.stdout
    assert time.monotonic() - t0 >= 1  # stable recheck
    assert not host.lock.exists()


def test_unlock_refuses_legacy_lock_held_by_live_legacy_cli(host):
    legacy = host.install_cli(LEGACY, host.root / "legacy/bin/mlx-model")
    p = host.spawn("start", "a", cli=legacy)
    host.wait_for(host.root / "run/mlx-server.pid")
    assert host.lock.exists() and not (host.lock / "owner").exists()
    r = host.run("unlock")
    assert r.returncode == 1 and "mlx-model command is running" in r.stderr
    assert host.lock.exists()
    _sig_and_wait(p, signal.SIGKILL)


def test_unlock_refuses_lock_with_unexpected_contents(host):
    host.lock.mkdir()
    (host.lock / "stray").write_text("x")
    r = host.run("unlock")
    assert r.returncode == 1 and host.lock.exists() and (host.lock / "stray").exists()
    assert not host.gate.exists()


def test_commands_wait_for_then_refuse_a_held_gate(host):
    s = host.sleeper()
    host.gate.mkdir()
    start = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(s.pid)], capture_output=True, text=True
    )
    (host.gate / "owner").write_text(
        f"pid={s.pid}\nstart={start.stdout.strip()}\ncmd=deploy-host\n"
    )
    for args in (("stop",), ("unlock",), ("add", "c", str(host.root / "models/A"))):
        r = host.run(*args)
        assert r.returncode == 1 and "Maintenance in progress" in r.stderr, args
        assert "deploy-host" in r.stderr
    assert host.gate.exists() and not host.lock.exists()
    assert "\tc\t" not in (host.root / "etc/models.conf").read_text()


def test_command_proceeds_once_the_gate_is_released(host):
    host.gate.mkdir()
    p = host.spawn("stop")
    time.sleep(0.2)
    host.gate.rmdir()
    out, err = p.communicate(timeout=10)
    assert p.returncode == 0, err
    assert "No MLX server is running." in out


def test_add_is_serialized_by_the_lock(host):
    p = _start_blocking(host, "a")
    before = (host.root / "etc/models.conf").read_text()
    r = host.run("add", "c", str(host.root / "models/A"))
    assert r.returncode == 1 and "Another mlx-model command is running." in r.stderr
    assert (host.root / "etc/models.conf").read_text() == before
    _sig_and_wait(p, signal.SIGTERM)
    r = host.run("add", "c", str(host.root / "models/A"))
    assert r.returncode == 0 and "Registered c" in r.stdout
    assert not host.lock.exists()


def test_status_and_list_are_byte_identical_to_legacy(host):
    legacy = host.install_cli(LEGACY, host.root / "legacy/bin/mlx-model")
    (host.state / "healthy").write_text("")
    assert host.run("start", "b").returncode == 0
    for args in (("status",), ("list",)):
        new, old = host.run(*args), host.run(*args, cli=legacy)
        assert (new.returncode, new.stdout, new.stderr) == (old.returncode, old.stdout, old.stderr)
    host.run("stop")
    new, old = host.run("status"), host.run("status", cli=legacy)
    assert new.stdout == old.stdout and "process: stopped" in new.stdout


def test_switch_still_stops_then_starts(host):
    (host.state / "healthy").write_text("")
    assert host.run("start", "a").returncode == 0
    first = int((host.root / "run/mlx-server.pid").read_text())
    r = host.run("switch", "b")
    assert r.returncode == 0 and "Serving b" in r.stdout
    assert int((host.root / "run/mlx-server.pid").read_text()) != first
    assert not host.lock.exists()


# --- M2: select changes the selection without loading ---------------------------------


def _active(host):
    text = (host.root / "run/active.env").read_text()
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_select_while_stopped_changes_selection_only(host):
    r = host.run("select", "b")
    assert r.returncode == 0 and "Selected b" in r.stdout, r.stderr
    assert _active(host)["MLX_ACTIVE_NAME"] == "b"
    assert (host.root / "models/active").resolve() == (host.root / "models/B").resolve()
    assert not (host.root / "run/mlx-server.pid").exists()
    assert not host.lock.exists()
    assert "* b" in host.run("list").stdout


def test_select_refuses_while_a_server_runs(host):
    (host.state / "healthy").write_text("")
    assert host.run("start", "a").returncode == 0
    r = host.run("select", "b")
    assert r.returncode == 1 and "switch" in r.stderr
    assert _active(host)["MLX_ACTIVE_NAME"] == "a"
    assert not host.lock.exists()


def test_select_refuses_while_comfyui_runs(host):
    s = host.sleeper()
    (host.state / "listen_8188").write_text(str(s.pid))
    r = host.run("select", "b")
    assert r.returncode == 1 and not (host.root / "run/active.env").exists()


def test_select_unknown_name_and_held_lock(host):
    r = host.run("select", "zzz")
    assert r.returncode == 1 and "Unknown model" in r.stderr
    p = _start_blocking(host, "a")
    r = host.run("select", "b")
    assert r.returncode == 1 and "Another mlx-model command is running." in r.stderr
    _sig_and_wait(p, signal.SIGTERM)


def test_select_requires_exactly_one_name(host):
    assert host.run("select").returncode == 1


@pytest.mark.parametrize("cmd", ["start", "switch", "select"])
def test_a_failure_inside_the_lock_releases_it(host, cmd):
    # zsh skips the EXIT trap when errexit fires inside a function (5.8 and the Mac's 5.9)
    r = host.run(cmd, "zzz")
    assert r.returncode == 1 and "Unknown model" in r.stderr
    assert not host.lock.exists() and not host.gate.exists()
