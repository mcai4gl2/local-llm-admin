"""Internal serving ports leave the existing public endpoint free for all engines."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from .host_harness import HOST, make_host, redirect


@pytest.fixture
def ports() -> Any:
    from types import ModuleType

    module = ModuleType("engine_ports_test")
    path = HOST / "bin/engine-ports"
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def test_absent_configuration_preserves_legacy_ports(ports: Any, tmp_path: Path) -> None:
    assert ports.load(tmp_path / "absent") == {"mlx": 8080, "strata": 8080, "splash": 8080}


@pytest.mark.parametrize(
    "body",
    [
        "{}",
        "[]",
        "bad",
        '{"mlx":8086,"strata":8087,"splash":8085,"other":1}',
        '{"mlx":8086,"mlx":8088,"strata":8087,"splash":8085}',
        '{"mlx":8080,"strata":8087,"splash":8085}',
        '{"mlx":8086,"strata":8086,"splash":8085}',
        '{"mlx":true,"strata":8087,"splash":8085}',
        '{"mlx":65536,"strata":8087,"splash":8085}',
        '{"mlx":0,"strata":8087,"splash":8085}',
    ],
)
def test_invalid_port_configuration_refuses(ports: Any, tmp_path: Path, body: str) -> None:
    path = tmp_path / "ports.json"
    path.write_text(body)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        ports.load(path)


@pytest.mark.parametrize("mode", [0o644, 0o400])
def test_configuration_requires_private_mode(ports: Any, tmp_path: Path, mode: int) -> None:
    path = tmp_path / "ports.json"
    path.write_text("{}")
    path.chmod(mode)
    with pytest.raises(ValueError):
        ports.load(path)


def test_valid_config_and_cli_are_identical(ports: Any, tmp_path: Path) -> None:
    path = tmp_path / "ports.json"
    expected = {"mlx": 8086, "strata": 8087, "splash": 8085}
    path.write_text(json.dumps(expected))
    path.chmod(0o600)
    assert ports.load(path) == expected
    for kind, port in expected.items():
        result = subprocess.run(
            [sys.executable, str(HOST / "bin/engine-ports"), str(path), kind],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0 and int(result.stdout) == port
    result = subprocess.run(
        [sys.executable, str(HOST / "bin/engine-ports"), str(path)], capture_output=True, text=True
    )
    assert json.loads(result.stdout) == expected
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(ValueError):
        ports.load(alias)


def test_mlx_internal_start_ignores_public_gateway_and_uses_its_private_port(
    tmp_path: Path,
) -> None:
    h = make_host(tmp_path)
    try:
        redirect(HOST / "bin/engine-ports", h.root / "bin/engine-ports", h.root)
        config = h.root / "etc/engine-ports.json"
        config.write_text(json.dumps({"mlx": 8086, "strata": 8087, "splash": 8085}))
        config.chmod(0o600)
        proxy = h.sleeper()
        (h.state / "listen_8080").write_text(str(proxy.pid))
        (h.state / "healthy").touch()
        result = h.run("start", "a")
        assert result.returncode == 0, result.stderr
        args = (h.state / "server_args").read_text().splitlines()
        assert args[args.index("--port") + 1] == "8086"
        assert "MLX_PORT=8086" in (h.root / "run/active.env").read_text().splitlines()
    finally:
        h.cleanup()


@pytest.mark.parametrize(
    "args,expected",
    [
        ([], 2),
        (["path", "unknown"], 2),
        (["path", "mlx", "extra"], 2),
        (["path"], 0),
        (["path", "mlx"], 0),
    ],
)
def test_port_cli_dispatch_is_bounded_and_quiet(
    ports: Any, monkeypatch: Any, capsys: Any, args: list[str], expected: int
) -> None:
    monkeypatch.setattr(ports.sys, "argv", ["engine-ports", *args])
    monkeypatch.setattr(ports, "load", lambda path: {"mlx": 8086, "strata": 8087, "splash": 8085})
    assert ports.main() == expected
    captured = capsys.readouterr()
    assert not captured.err
    if expected:
        assert not captured.out
    elif len(args) == 2:
        assert captured.out.strip() == "8086"
    else:
        assert json.loads(captured.out)["strata"] == 8087


@pytest.mark.parametrize("error", [OSError("private path"), ValueError("private data")])
def test_port_cli_errors_never_print_private_details(
    ports: Any, monkeypatch: Any, capsys: Any, error: Exception
) -> None:
    monkeypatch.setattr(ports.sys, "argv", ["engine-ports", "private"])

    def fail(path: Path) -> Any:
        raise error

    monkeypatch.setattr(ports, "load", fail)
    assert ports.main() == 1
    assert "private" not in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["directory", "oversize"])
def test_non_file_or_oversize_topology_is_refused(ports: Any, tmp_path: Path, kind: str) -> None:
    path = tmp_path / "config"
    if kind == "directory":
        path.mkdir(mode=0o700)
    else:
        path.write_text(" " * 1025)
        path.chmod(0o600)
    with pytest.raises(ValueError):
        ports.load(path)


def test_native_port_helper_runs_as_a_standalone_entrypoint(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    import runpy

    monkeypatch.setattr(sys, "argv", ["engine-ports", str(tmp_path / "absent")])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(HOST / "bin/engine-ports"), run_name="__main__")
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out) == {"mlx": 8080, "strata": 8080, "splash": 8080}
