"""Temporary-root harness for the versioned Mac scripts: real zsh, fake lsof/curl/server.

Production scripts keep their fixed ``/Volumes/Models`` paths; only the copies made here
are redirected (``sed`` on the copy).
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = ROOT / "host"
FIX = Path(__file__).parent / "fixtures"
PROD = "/Volumes/Models"

FAKE_LSOF = r"""#!/bin/bash
# listening ports are files $FAKE_HOST_STATE/listen_<port> holding the owner pid; an open
# log file is $FAKE_HOST_STATE/open_<basename>; $FAKE_HOST_STATE/lsof_broken fails lsof
[ -f "$FAKE_HOST_STATE/lsof_broken" ] && exit 2
port=""; terse=0
for a in "$@"; do
  case "$a" in
    /*)
      f="$FAKE_HOST_STATE/open_$(basename "$a")"
      [ -f "$f" ] || exit 1
      cat "$f"
      exit 0 ;;
  esac
  case "$a" in -t*) terse=1 ;; esac
  case "$a" in *TCP:[0-9]*) port="${a##*:}" ;; esac
done
f="$FAKE_HOST_STATE/listen_$port"
[ -f "$f" ] || exit 1
if [ "$terse" = 1 ]; then
  pid=$(cat "$f")
  # a killed test process stays a zombie until the test reaps it
  case "$(ps -o stat= -p "$pid" 2>/dev/null)" in ""|Z*) ;; *) echo "$pid" ;; esac
fi
exit 0
"""

FAKE_CURL = r"""#!/bin/bash
[ -f "$FAKE_HOST_STATE/healthy" ] || exit 7
echo '{"status":"healthy","loaded_model":"active"}'
"""

FAKE_SERVER = r"""#!/bin/bash
printf '%s\n' "$@" > "$FAKE_HOST_STATE/server_args"
trap 'exit 0' TERM
while :; do sleep 0.2; done
"""

FAKE_PYTHON = "#!/bin/bash\nexit 0\n"


def redirect(src: Path, dst: Path, root: Path, extra: dict[str, str] | None = None) -> Path:
    text = src.read_text().replace(PROD, str(root))
    for old, new in (extra or {}).items():
        assert old in text, f"{old!r} not in {src}"
        text = text.replace(old, new)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text)
    dst.chmod(0o755)
    return dst


def _exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


@dataclass
class Host:
    root: Path
    state: Path
    env: dict[str, str]
    procs: list[subprocess.Popen[str]] = field(default_factory=list)

    @property
    def cli(self) -> Path:
        return self.root / "bin/mlx-model"

    @property
    def lock(self) -> Path:
        return self.root / "run/mlx.lock"

    @property
    def gate(self) -> Path:
        return self.root / "run/maintenance.lock"

    def install_cli(self, source: Path, dest: Path | None = None) -> Path:
        # the test copy waits 0.5 s (not 5 s) for the maintenance gate
        extra = {"GATE_TRIES=50": "GATE_TRIES=5"} if "GATE_TRIES=50" in source.read_text() else {}
        return redirect(source, dest or self.cli, self.root, extra)

    def run(
        self, *args: str, cli: Path | None = None, timeout: float = 30
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["zsh", str(cli or self.cli), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def spawn(self, *args: str, cli: Path | None = None) -> subprocess.Popen[str]:
        p = subprocess.Popen(
            ["zsh", str(cli or self.cli), *args],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.procs.append(p)
        return p

    def sleeper(self) -> subprocess.Popen[str]:
        p = subprocess.Popen(["sleep", "300"], text=True)
        self.procs.append(p)
        return p

    def wait_for(self, path: Path, timeout: float = 10) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if path.exists():
                return
            time.sleep(0.02)
        raise AssertionError(f"{path} did not appear")

    def owner(self, d: Path) -> dict[str, str]:
        text = (d / "owner").read_text()
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    def cleanup(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
        pidfile = self.root / "run/mlx-server.pid"
        try:
            os.kill(int(pidfile.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def make_host(tmp_path: Path) -> Host:
    root = tmp_path / "Models"
    state = tmp_path / "state"
    fakebin = tmp_path / "fakebin"
    for d in ("run", "logs", "etc", "models/A", "models/B"):
        (root / d).mkdir(parents=True)
    state.mkdir()
    for m in ("A", "B"):
        (root / f"models/{m}/config.json").write_text("{}")
    (root / "etc/models.conf").write_text(
        f"# Short name, tab, model directory.\na\t{root}/models/A\nb\t{root}/models/B\n"
    )
    redirect(HOST / "bin/mlx-env.sh", root / "bin/mlx-env.sh", root)
    _exe(root / "venvs/mlx/bin/python", FAKE_PYTHON)
    _exe(root / "venvs/mlx/bin/mlx_vlm.server", FAKE_SERVER)
    _exe(fakebin / "lsof", FAKE_LSOF)
    _exe(fakebin / "curl", FAKE_CURL)
    env = {
        **os.environ,
        "PATH": f"{fakebin}:{os.environ['PATH']}",
        "FAKE_HOST_STATE": str(state),
    }
    host = Host(root, state, env)
    host.install_cli(HOST / "bin/mlx-model")
    return host
