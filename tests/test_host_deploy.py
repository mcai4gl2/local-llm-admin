"""ops/deploy-host.sh and ops/host-diff.sh against a fake Mac (fake ssh runs locally)."""

import hashlib
import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

from .host_harness import FIX, HOST, ROOT, make_host, redirect

FAKE_SSH = r"""#!/bin/bash
# ssh [-o opt]... [-q] host command...  ->  run command locally
nostdin=0
while [ $# -gt 0 ]; do
  case "$1" in -o) shift 2 ;; -n) nostdin=1; shift ;; -q|-T) shift ;; *) break ;; esac
done
shift
# real ssh forwards (and so consumes) its stdin even when the remote command ignores it
tmp=$(mktemp)
[ "$nostdin" = 1 ] || cat > "$tmp"
bash -c "$*" < "$tmp"
rc=$?
rm -f "$tmp"
exit $rc
"""

FAKE_SQLITE = r"""#!/bin/bash
# prints the next queued running-job count (default 0)
q="$FAKE_HOST_STATE/running_jobs"
[ -f "$q" ] || { echo 0; exit 0; }
[ "$(head -n1 "$q")" = fail ] && exit 1
head -n1 "$q"; tail -n +2 "$q" > "$q.tmp"; mv "$q.tmp" "$q"; [ -s "$q" ] || rm -f "$q"
"""

FAKE_MV = r"""#!/bin/bash
for last; do :; done
if [ -n "${FAKE_MV_FAIL:-}" ] && [ "$last" = "$FAKE_MV_FAIL" ]; then
  echo "mv: injected failure" >&2; exit 1
fi
exec /bin/mv "$@"
"""

CODE = [
    "bin/mlx-model",
    "bin/mlx-download",
    "bin/mlx-profile",
    "bin/mlx-env.sh",
    "bin/chat-qwen.sh",
    "bin/serve-qwen.sh",
    "bin/download-models.sh",
    "bin/README.md",
    "comfyui/bin/comfyui",
    "decision/bin/openjev",
    "decision/README.md",
    "tts/bin/qwen-tts",
    "tts/README.md",
    "embed/bin/qwen-embed",
    "embed/README.md",
]


def _exe(path, text):
    path.write_text(text)
    path.chmod(0o755)


class Env:
    def __init__(self, tmp_path):
        self.mac = make_host(tmp_path / "mac")  # the "Mac": a working mlx-model root
        self.root = self.mac.root
        self.proj = tmp_path / "proj"
        for rel in (
            "ops/deploy-host.sh",
            "ops/deploy-host-remote.sh",
            "ops/host-diff.sh",
            "ops/deploy-env.sh",
        ):
            redirect(ROOT / rel, self.proj / rel, self.root)
        shutil.copy(ROOT / "ops/host-files.list", self.proj / "ops/host-files.list")
        for f in HOST.rglob("*"):
            if f.is_file():
                dst = self.proj / "host" / f.relative_to(HOST)
                redirect(f, dst, self.root)
                dst.chmod(f.stat().st_mode & 0o777)
        # the Mac starts identical to the repo (registry aside)
        for rel in CODE:
            dst = self.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.proj / "host" / rel, dst)
            dst.chmod((self.proj / "host" / rel).stat().st_mode & 0o777)
        fakebin = Path(self.mac.env["PATH"].split(":")[0])
        _exe(fakebin / "ssh", FAKE_SSH)
        _exe(fakebin / "sqlite3", FAKE_SQLITE)
        _exe(fakebin / "mv", FAKE_MV)
        self.admin = tmp_path / "admin"
        (self.admin / "data").mkdir(parents=True)
        (self.admin / "data/admin.sqlite").write_text("")
        self.env = {**self.mac.env, "MAC_HOST": "fake-mac", "MAC_DEST": str(self.admin)}

    def run(self, script, *args):
        return subprocess.run(
            ["bash", str(self.proj / "ops" / script), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def deploy(self, *args):
        return self.run("deploy-host.sh", *args)

    def change(self, rel, text):
        (self.proj / "host" / rel).write_text(text)

    def leftovers(self):
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if ".new-" in p.name or ".restore-" in p.name
        )

    def backups(self, rel):
        return sorted((self.root / rel).parent.glob(Path(rel).name + ".bak-*"))


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.mac.cleanup()


SCRIPT = "#!/bin/zsh\necho changed\n"


def test_dry_run_reports_and_writes_nothing(env):
    env.change("bin/serve-qwen.sh", SCRIPT)
    before = (env.root / "bin/serve-qwen.sh").read_text()
    r = env.deploy()
    assert r.returncode == 0, r.stderr
    assert "DIFFERS  bin/serve-qwen.sh" in r.stdout and "same     bin/mlx-model" in r.stdout
    assert "+echo changed" in r.stdout and "dry run" in r.stdout
    assert (env.root / "bin/serve-qwen.sh").read_text() == before
    assert env.backups("bin/serve-qwen.sh") == [] and env.leftovers() == []
    assert not (env.root / "deploy").exists()


def test_apply_backs_up_replaces_keeps_mode_and_writes_manifest(env):
    target = env.root / "bin/serve-qwen.sh"
    old = target.read_text()
    target.chmod(0o750)
    env.change("bin/serve-qwen.sh", SCRIPT)
    r = env.deploy("--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert target.read_text() == SCRIPT and target.stat().st_mode & 0o777 == 0o750
    (bak,) = env.backups("bin/serve-qwen.sh")
    assert bak.read_text() == old and bak.stat().st_mode & 0o777 == 0o750
    (manifest,) = (env.root / "deploy").glob("*.manifest")
    (line,) = manifest.read_text().splitlines()
    path, backup, mode, sha = line.split()
    assert (path, backup, mode) == (str(target), str(bak), "750")
    assert sha == hashlib.sha256(SCRIPT.encode()).hexdigest()
    assert env.leftovers() == [] and not env.mac.gate.exists()
    assert env.deploy().stdout.count("DIFFERS") == 0  # now in sync


def test_only_named_files_are_deployed(env):
    env.change("bin/serve-qwen.sh", SCRIPT)
    env.change("bin/chat-qwen.sh", SCRIPT)
    r = env.deploy("--apply", "bin/chat-qwen.sh")
    assert r.returncode == 0, r.stderr
    assert (env.root / "bin/chat-qwen.sh").read_text() == SCRIPT
    assert (env.root / "bin/serve-qwen.sh").read_text() != SCRIPT


def test_new_file_is_created_executable(env):
    (env.root / "bin/serve-qwen.sh").unlink()
    r = env.deploy("--apply", "bin/serve-qwen.sh")
    assert r.returncode == 0, r.stderr
    assert (env.root / "bin/serve-qwen.sh").stat().st_mode & 0o111
    assert env.backups("bin/serve-qwen.sh") == []


def test_new_file_in_a_new_directory_is_created(env):
    """M6: the first deploy of decision/ runs before that directory exists on the Mac."""
    shutil.rmtree(env.root / "decision")
    r = env.deploy("--apply", "decision/bin/openjev")
    assert r.returncode == 0, r.stdout + r.stderr
    target = env.root / "decision/bin/openjev"
    assert target.read_text() == (env.proj / "host/decision/bin/openjev").read_text()
    assert target.stat().st_mode & 0o111
    assert env.leftovers() == []


def test_broken_python_helper_changes_nothing(env):
    env.change("bin/mlx-download", "#!/usr/bin/python3\ndef broken(:\n")
    before = (env.root / "bin/mlx-download").read_text()
    r = env.deploy("--apply")
    assert r.returncode != 0 and "syntax check failed: " in r.stdout + r.stderr
    assert (env.root / "bin/mlx-download").read_text() == before
    assert env.leftovers() == []


@pytest.mark.parametrize(
    "arg", ["etc/models.conf", "../x", "bin/../../etc/passwd", "/etc/passwd", "bin/mlx-model;id"]
)
def test_registry_and_unlisted_paths_are_refused(env, arg):
    (env.proj / "host/etc/models.conf").write_text("x\t/y\n")
    r = env.deploy("--apply", arg)
    assert r.returncode == 2 and "not deployable" in r.stderr
    assert env.leftovers() == [] and not (env.root / "deploy").exists()


def test_registry_is_never_part_of_the_default_set(env):
    (env.proj / "host/etc/models.conf").write_text("x\t/y\n")
    before = (env.root / "etc/models.conf").read_text()
    r = env.deploy("--apply")
    assert r.returncode == 0 and "nothing to deploy" in r.stdout
    assert (env.root / "etc/models.conf").read_text() == before


def test_broken_script_changes_nothing(env):
    env.change("bin/serve-qwen.sh", SCRIPT)
    env.change("bin/chat-qwen.sh", "#!/bin/zsh\nif then fi (\n")
    before = {rel: (env.root / rel).read_text() for rel in CODE}
    r = env.deploy("--apply")
    assert r.returncode != 0 and "syntax check failed: " in r.stdout + r.stderr
    assert {rel: (env.root / rel).read_text() for rel in CODE} == before
    assert env.backups("bin/serve-qwen.sh") == [] and env.leftovers() == []
    assert not env.mac.gate.exists()


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda e: (e.mac.state / "running_jobs").write_text("1\n"), "admin job"),
        (lambda e: (e.mac.state / "running_jobs").write_text("fail\n"), "cannot read admin jobs"),
        (lambda e: e.mac.lock.mkdir(), "mlx.lock"),
        # the second check happens after the syntax checks, right before replacing
        (lambda e: (e.mac.state / "running_jobs").write_text("0\n1\n"), "admin job"),
    ],
)
def test_preflight_refusals_change_nothing(env, setup, message):
    env.change("bin/serve-qwen.sh", SCRIPT)
    before = (env.root / "bin/serve-qwen.sh").read_text()
    setup(env)
    r = env.deploy("--apply")
    assert r.returncode != 0 and message in r.stdout + r.stderr
    assert (env.root / "bin/serve-qwen.sh").read_text() == before
    assert env.backups("bin/serve-qwen.sh") == [] and env.leftovers() == []
    assert not env.mac.gate.exists()


def test_refuses_while_the_gate_is_held(env):
    s = env.mac.sleeper()
    env.mac.gate.mkdir()
    (env.mac.gate / "owner").write_text(f"pid={s.pid}\nstart=x\ncmd=unlock\n")
    env.change("bin/serve-qwen.sh", SCRIPT)
    r = env.deploy("--apply")
    assert r.returncode != 0 and "maintenance gate" in r.stdout + r.stderr
    assert (env.mac.gate / "owner").exists()  # someone else's gate is left alone
    assert env.leftovers() == []


def test_refuses_while_a_legacy_cli_runs(env):
    legacy = env.mac.install_cli(FIX / "mac/mlx-model.dc1", env.root / "legacy/bin/mlx-model")
    p = env.mac.spawn("start", "a", cli=legacy)
    env.mac.wait_for(env.root / "run/mlx-server.pid")
    (env.mac.lock).rename(env.root / "run/moved.lock")  # only the process check remains
    env.change("bin/serve-qwen.sh", SCRIPT)
    r = env.deploy("--apply")
    assert r.returncode != 0 and "mlx-model command is running" in r.stdout + r.stderr
    p.send_signal(signal.SIGKILL)
    p.wait()


def test_failed_replacement_restores_earlier_files(env):
    first, second = env.root / "bin/chat-qwen.sh", env.root / "bin/serve-qwen.sh"
    olds = first.read_text(), second.read_text()
    env.change("bin/chat-qwen.sh", SCRIPT)
    env.change("bin/serve-qwen.sh", SCRIPT)
    env.env["FAKE_MV_FAIL"] = str(second)
    r = env.deploy("--apply")
    assert r.returncode != 0 and "restored" in r.stdout
    assert (first.read_text(), second.read_text()) == olds
    assert env.leftovers() == [] and not env.mac.gate.exists()


def test_upgrade_from_the_legacy_cli(env):
    """The first host deployment replaces the recorded D-C1 script, which has no gate."""
    legacy_text = redirect(FIX / "mac/mlx-model.dc1", env.root / "bin/mlx-model", env.root)
    legacy_text = legacy_text.read_text()
    env.mac.lock.mkdir()  # SIGKILLed legacy command: stale lock without owner
    r = env.deploy("--apply", "bin/mlx-model")
    assert r.returncode != 0 and "mlx.lock" in r.stdout + r.stderr
    env.mac.lock.rmdir()
    r = env.deploy("--apply", "bin/mlx-model")
    assert r.returncode == 0, r.stdout + r.stderr
    (bak,) = env.backups("bin/mlx-model")
    assert bak.read_text() == legacy_text
    assert "cmd_unlock" in (env.root / "bin/mlx-model").read_text()
    assert env.mac.run("unlock").stdout.strip() == "No lock held."


def test_host_diff_separates_registry_from_code_drift(env):
    (env.root / "etc/models.conf").write_text("other\t/x\n")
    r = env.run("host-diff.sh")
    assert r.returncode == 0, r.stdout
    assert "REGISTRY differs" in r.stdout and "same     bin/mlx-model" in r.stdout
    (env.root / "bin/chat-qwen.sh").write_text("#!/bin/zsh\n")
    r = env.run("host-diff.sh")
    assert r.returncode == 1 and "DIFFERS  bin/chat-qwen.sh" in r.stdout


def test_host_diff_ignores_deploy_leftovers_but_reports_unversioned(env):
    (env.root / "bin/mlx-model.bak-20260930-120000-1").write_text("")
    r = env.run("host-diff.sh")
    assert r.returncode == 0 and "NOT VERSIONED" not in r.stdout
    (env.root / "bin/extra.sh").write_text("")
    r = env.run("host-diff.sh")
    assert r.returncode == 1 and "extra.sh" in r.stdout


def test_host_diff_takes_known_names_from_the_manifest(env):
    for base in (env.proj / "host", env.root):
        _exe(base / "bin/new-tool.sh", "#!/bin/zsh\n")
    with (env.proj / "ops/host-files.list").open("a") as f:
        f.write("code bin/new-tool.sh\n")
    r = env.run("host-diff.sh")
    assert r.returncode == 0, r.stdout
    assert "NOT VERSIONED" not in r.stdout


def test_host_diff_ignores_names_listed_in_host_diff_ignore(env):
    (env.root / "bin/private-a.sh").write_text("")
    (env.root / "comfyui/bin").mkdir(parents=True, exist_ok=True)
    (env.root / "comfyui/bin/private-b").write_text("")
    env.env["HOST_DIFF_IGNORE"] = "private-a.sh private-b"
    r = env.run("host-diff.sh")
    assert r.returncode == 0, r.stdout
    assert "NOT VERSIONED" not in r.stdout
    env.env["HOST_DIFF_IGNORE"] = "private-a.sh"
    r = env.run("host-diff.sh")
    assert r.returncode == 1 and "private-b" in r.stdout and "private-a.sh" not in r.stdout


def test_manifest_lists_every_versioned_host_file():
    entries = [
        line.split()
        for line in (ROOT / "ops/host-files.list").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    listed = {path for _kind, path in entries}
    versioned = {
        str(p.relative_to(HOST))
        for p in HOST.rglob("*")
        if p.is_file()
        and "environment" not in p.parts
        and (
            p.name != "README.md"
            or p
            in (
                HOST / "bin/README.md",
                HOST / "decision/README.md",
                HOST / "tts/README.md",
                HOST / "embed/README.md",
            )
        )
    }
    assert listed == versioned
    assert {k for k, p in entries if p == "etc/models.conf"} == {"registry"}
    assert all(k in ("code", "registry") for k, _ in entries)


def test_scripts_are_valid_bash():
    for rel in (
        "ops/deploy-host.sh",
        "ops/deploy-host-remote.sh",
        "ops/host-diff.sh",
        "ops/deploy-env.sh",
    ):
        assert subprocess.run(["bash", "-n", str(ROOT / rel)]).returncode == 0, rel
        assert os.access(ROOT / rel, os.X_OK), rel


MAC_SCRIPTS = [
    "deploy-mac.sh",
    "deploy-host.sh",
    "rollback-mac.sh",
    "host-diff.sh",
    "apply-mlx-model-guard.sh",
]

FAKE_REMOTE = r"""#!/bin/bash
# records the call and its target, then fails: the test only checks what got this far
echo "$(basename "$0") $*" >> "$FAKE_CALLS"
exit 7
"""


@pytest.fixture
def ops_copy(tmp_path):
    """The ops scripts in a scratch project whose ssh/scp/rsync only record their calls."""
    proj = tmp_path / "proj"
    shutil.copytree(ROOT / "ops", proj / "ops")
    (proj / "ops/deploy.env").unlink(missing_ok=True)
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    for tool in ("ssh", "scp", "rsync"):
        _exe(fakebin / tool, FAKE_REMOTE)
    calls = tmp_path / "calls"
    env = {k: v for k, v in os.environ.items() if k not in ("MAC_HOST", "MAC_DEST", "DEPLOY_ENV")}
    env.update(PATH=f"{fakebin}:{os.environ['PATH']}", FAKE_CALLS=str(calls), HOME=str(tmp_path))

    def run(script, extra=None):
        r = subprocess.run(
            ["bash", str(proj / "ops" / script)],
            env={**env, **(extra or {})},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return r, (calls.read_text() if calls.exists() else "")

    return proj, run


@pytest.mark.parametrize("script", MAC_SCRIPTS)
def test_missing_mac_host_is_refused_before_any_remote_call(ops_copy, script):
    _, run = ops_copy
    r, calls = run(script)
    assert r.returncode != 0
    assert "MAC_HOST" in r.stderr and "deploy.env" in r.stderr
    assert calls == ""


@pytest.mark.parametrize("script", ["deploy-mac.sh", "deploy-host.sh", "rollback-mac.sh"])
def test_missing_mac_dest_is_refused_where_it_is_used(ops_copy, script):
    _, run = ops_copy
    r, calls = run(script, {"MAC_HOST": "fake-mac"})
    assert r.returncode != 0
    assert "MAC_DEST" in r.stderr
    assert calls == ""


def test_deploy_env_file_supplies_the_target(ops_copy):
    proj, run = ops_copy
    (proj / "ops/deploy.env").write_text("MAC_HOST=user@file-mac\nMAC_DEST=/srv/lla\n")
    _, calls = run("rollback-mac.sh")
    assert calls.startswith("ssh user@file-mac ")


def test_exported_values_override_the_file(ops_copy):
    proj, run = ops_copy
    (proj / "ops/deploy.env").write_text("MAC_HOST=user@file-mac\nMAC_DEST=/srv/lla\n")
    _, calls = run("rollback-mac.sh", {"MAC_HOST": "user@env-mac"})
    assert calls.startswith("ssh user@env-mac ")


def test_deploy_env_path_can_point_outside_the_repo(ops_copy, tmp_path):
    _, run = ops_copy
    overlay = tmp_path / "overlay.env"
    overlay.write_text("MAC_HOST=user@overlay-mac\nMAC_DEST=/srv/lla\n")
    _, calls = run("rollback-mac.sh", {"DEPLOY_ENV": str(overlay)})
    assert calls.startswith("ssh user@overlay-mac ")


def test_no_ops_script_carries_a_default_target():
    for script in MAC_SCRIPTS:
        text = (ROOT / "ops" / script).read_text()
        assert "${MAC_HOST:-" not in text and "${MAC_DEST:-" not in text, script
