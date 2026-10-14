"""M3b downloader with real zsh and the real helper against a fake Hugging Face.

``mlx-model download`` starts ``bin/mlx-download`` in the background; it stages a pinned
commit, verifies every file against its published checksum, and publishes the directory
into models/ under run/mlx.lock. Nothing here touches the network.
"""

from __future__ import annotations

import ast
import errno
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .fake_hf import FakeHF, Repo, serve
from .host_harness import HOST, make_host, redirect

SHA = "a" * 40
RUN = "r00000000001"
BIG = bytes(range(256)) * 4096  # 1 MiB


def _files(shards: int = 2) -> dict[str, bytes]:
    files = {
        "config.json": b'{"model_type": "llama"}',
        "tokenizer.json": b'{"version": "1.0"}',
        "README.md": "# Tiny model\n\nA <b>card</b> with ünïcode.\n".encode(),
    }
    wm = {}
    for i in range(1, shards + 1):
        name = f"model-0000{i}-of-0000{shards}.safetensors"
        files[name] = BIG[i:] + bytes([i])
        wm[f"layer.{i}"] = name
    files["model.safetensors.index.json"] = json.dumps({"weight_map": wm}).encode()
    return files


@pytest.fixture
def hf():
    fake = FakeHF()
    fake.repos["mlx-community/Tiny"] = Repo(SHA, _files())
    server = serve(fake)
    yield fake
    server.shutdown()


@pytest.fixture
def host(tmp_path, hf):
    h = make_host(tmp_path)
    install_helper(h.root, hf.endpoint)
    py = os.environ.get("MLX_DOWNLOAD_PY")  # e.g. a Python 3.9, the Mac's version
    if py:
        h.cli.write_text(h.cli.read_text().replace('HOST_PY="/usr/bin/python3"', f'HOST_PY="{py}"'))
    yield h
    h.cleanup()
    for p in h.procs:
        if p.poll() is None:
            p.kill()


def install_helper(root: Path, endpoint: str, **extra: str) -> Path:
    subs = {
        'ENDPOINT = "https://huggingface.co"': f'ENDPOINT = "{endpoint}"',
        'ALLOWED_SCHEMES = ("https",)': 'ALLOWED_SCHEMES = ("http", "https")',
        "RETRY_BASE_S = 2.0": "RETRY_BASE_S = 0.01",
        "PUBLISH_POLL_S = 5.0": "PUBLISH_POLL_S = 0.05",
        "HEADROOM_MIN = 2 << 30": "HEADROOM_MIN = 1024",
        **extra,
    }
    return redirect(HOST / "bin/mlx-download", root / "bin/mlx-download", root, subs)


def _json(r):
    return json.loads(r.stdout)


def _start(host, run=RUN, repo="mlx-community/Tiny", sha=SHA, dest="Tiny"):
    return host.run("download", repo, sha, dest, run)


def _wait(host, run=RUN, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = _json(host.run("download-status", run))
        if st["state"] not in ("running", "publishing"):
            return st
        time.sleep(0.1)
    raise AssertionError("download did not finish")


def _wait_for(pred, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met")


def _log(host, run=RUN):
    return (host.root / f"logs/download-{run}.log").read_text()


def _registry(host):
    return (host.root / "etc/models.conf").read_text()


# --- preview --------------------------------------------------------------------------


def test_preview_pins_the_commit_and_lists_files(host):
    r = host.run("download-preview", "mlx-community/Tiny")
    assert r.returncode == 0, r.stderr + r.stdout
    p = _json(r)
    assert p["revision"] == SHA and p["requested_revision"] == "main"
    names = [f["path"] for f in p["files"]]
    assert "config.json" in names and "model-00001-of-00002.safetensors" in names
    shard = next(f for f in p["files"] if f["path"].endswith("00001-of-00002.safetensors"))
    assert shard["sha256"] == hashlib.sha256(_files()[shard["path"]]).hexdigest()
    assert p["total_bytes"] == sum(len(v) for v in _files().values())
    assert p["missing"] == [] and p["unknown_size_files"] == []
    assert p["card"].startswith("# Tiny model") and "<b>card</b>" in p["card"]
    assert p["suggested_dest"] == "Tiny" and p["dest_exists"] is False
    assert p["fits"] is True and p["token"] is False


def test_preview_of_a_gated_repo_explains_the_token(host, hf):
    hf.repos["org/Gated"] = Repo(SHA, _files(1), gated=True)
    hf.token = "hf_SECRETTOKEN123456"
    r = host.run("download-preview", "org/Gated")
    assert r.returncode == 2
    err = _json(r)
    assert err["error"] == "auth" and "token" in err["message"]
    (host.root / "hf").mkdir()
    (host.root / "hf/token").write_text(hf.token + "\n")
    r = host.run("download-preview", "org/Gated")
    assert r.returncode == 0, r.stdout
    assert _json(r)["token"] is True
    assert hf.token not in r.stdout


@pytest.mark.parametrize(
    "args", [("../x",), ("mlx-community/Tiny", "../../etc"), ("a/b/c",), ("https://evil/x",)]
)
def test_preview_rejects_non_repo_ids(host, args):
    r = host.run("download-preview", *args)
    assert r.returncode == 2 and _json(r)["error"] == "usage"


def test_preview_refuses_unsafe_paths_from_the_server(host, hf):
    hf.repos["org/Evil"] = Repo(
        SHA, _files(1), extra_siblings=[{"rfilename": "../../bin/mlx-model", "size": 1}]
    )
    r = host.run("download-preview", "org/Evil")
    assert r.returncode == 2 and _json(r)["error"] == "metadata"


# --- a full run -----------------------------------------------------------------------


def test_download_verifies_and_publishes_without_registering(host, hf):
    before = _registry(host)
    r = _start(host)
    assert r.returncode == 0, r.stderr
    assert _json(r)["state"] in ("running", "publishing", "published")
    st = _wait(host)
    assert st["state"] == "published", st
    assert st["published_path"] == str(host.root / "models/Tiny")
    assert st["bytes_done"] == st["bytes_total"] == sum(len(v) for v in _files().values())
    for name, data in _files().items():
        assert (host.root / "models/Tiny" / name).read_bytes() == data
    assert not list((host.root / "models/Tiny").rglob("*.part"))
    assert not (host.root / f"downloads/{RUN}/files").exists()
    assert _registry(host) == before  # registration stays a separate step
    log = _log(host)
    assert log.count(" start =====") == 1 and log.count("done fail=0 =====") == 1
    assert "GET mlx-community/Tiny/config.json" in log
    assert not (host.root / "run/download.lock").exists() and not host.lock.exists()


def test_the_token_never_leaves_the_hugging_face_host(host, hf):
    hf.token = "hf_SECRETTOKEN123456"
    hf.repos["mlx-community/Tiny"].gated = True
    (host.root / "hf").mkdir()
    (host.root / "hf/token").write_text(hf.token)
    hf.cut_once.add("model-00002-of-00002.safetensors")  # a logged retry on the CDN
    _start(host)
    st = _wait(host)
    assert st["state"] == "published", st
    cdn = [r for r in hf.requests if r["host"] == "localhost"]
    assert cdn and all(r["auth"] is None for r in cdn)
    home = [r for r in hf.requests if r["host"] == "127.0.0.1"]
    assert all(r["auth"] == f"Bearer {hf.token}" for r in home)
    text = _log(host) + json.dumps(st)
    assert hf.token not in text and "SECRETSIG" not in text


def test_transient_errors_and_dropped_connections_are_retried(host, hf):
    hf.fail_times["config.json"] = 2
    hf.cut_once.add("model-00001-of-00002.safetensors")
    _start(host)
    st = _wait(host)
    assert st["state"] == "published", st
    assert "retry config.json (1/4)" in _log(host)
    ranges = hf.ranges("model-00001-of-00002.safetensors")
    assert any(r and not r.startswith("bytes=0-") for r in ranges)  # resumed, not restarted


def test_a_server_that_ignores_ranges_restarts_the_file(host, hf):
    hf.ignore_range = True
    hf.cut_once.add("model-00001-of-00002.safetensors")
    _start(host)
    st = _wait(host)
    assert st["state"] == "published", st
    assert "range ignored for model-00001-of-00002.safetensors" in _log(host)
    name = "model-00001-of-00002.safetensors"
    assert (host.root / "models/Tiny" / name).read_bytes() == _files()[name]


def test_checksum_mismatch_fails_and_publishes_nothing(host, hf):
    hf.corrupt.add("model-00002-of-00002.safetensors")
    _start(host)
    st = _wait(host)
    assert st["state"] == "failed" and st["error_kind"] == "checksum"
    assert st["retryable"] is True
    assert not (host.root / "models/Tiny").exists()
    assert _log(host).count("done fail=1 =====") == 1


def test_a_shard_missing_from_the_repo_fails_before_the_big_files(host, hf):
    files = _files(2)
    del files["model-00002-of-00002.safetensors"]
    hf.repos["org/Broken"] = Repo(SHA, files)
    _start(host, repo="org/Broken", dest="Broken")
    st = _wait(host)
    assert st["state"] == "failed" and st["error_kind"] == "incomplete"
    assert "model-00002-of-00002.safetensors" in st["message"]
    assert hf.ranges("model-00001-of-00002.safetensors") == []
    assert not (host.root / "models/Broken").exists()


def test_a_repo_that_is_not_a_model_is_refused_up_front(host, hf):
    hf.repos["org/Docs"] = Repo(SHA, {"README.md": b"# docs"})
    _start(host, repo="org/Docs", dest="Docs")
    st = _wait(host)
    assert st["state"] == "failed" and st["error_kind"] == "incomplete"
    assert "config.json" in st["message"]


def test_a_changed_revision_is_refused(host, hf):
    hf.repos["mlx-community/Tiny"].api_sha = "b" * 40
    _start(host)
    st = _wait(host)
    assert st["state"] == "failed" and st["error_kind"] == "revision"


# --- cancel, resume, interruption ----------------------------------------------------


def test_cancel_keeps_partials_and_resume_finishes(host, hf):
    name = "model-00001-of-00002.safetensors"
    hf.slow[name] = 0.05
    _start(host)
    part = host.root / f"downloads/{RUN}/files/{name}.part"
    _wait_for(lambda: part.exists() and part.stat().st_size > 0)
    r = host.run("download-cancel", RUN)
    assert r.returncode == 0, r.stderr
    st = _json(host.run("download-status", RUN))
    assert st["state"] == "cancelled" and st["retryable"] is True
    assert part.exists() and not (host.root / "run/download.lock").exists()
    assert "done fail" not in _log(host)  # a cancel is not a finished run: no alert
    have = part.stat().st_size
    del hf.slow[name]
    r = host.run("download-resume", RUN)
    assert r.returncode == 0, r.stderr
    st = _wait(host)
    assert st["state"] == "published", st
    assert f"bytes={have}-" in hf.ranges(name)
    assert (host.root / "models/Tiny" / name).read_bytes() == _files()[name]
    assert _log(host).count("done fail=0 =====") == 1


def test_one_download_at_a_time(host, hf):
    hf.slow["model-00001-of-00002.safetensors"] = 0.05
    _start(host)
    _wait_for(lambda: (host.root / "run/download.lock/owner").exists())
    r = _start(host, run="r00000000002", dest="Other")
    assert r.returncode == 0
    st = _json(host.run("download-status", "r00000000002"))
    assert st["state"] == "failed" and st["error_kind"] == "busy"
    assert _json(host.run("download-status", RUN))["state"] == "running"
    host.run("download-cancel", RUN)


def test_a_dead_downloader_shows_as_interrupted(host):
    d = host.root / f"downloads/{RUN}"
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"id": RUN, "state": "running", "started": 1}))
    st = _json(host.run("download-status", RUN))
    assert st["state"] == "interrupted" and st["retryable"] is True
    listing = _json(host.run("download-status"))
    assert [s["id"] for s in listing] == [RUN]


def test_cancel_only_signals_the_recorded_downloader(host):
    s = host.sleeper()
    start = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(s.pid)], capture_output=True, text=True
    ).stdout.strip()
    lock = host.root / "run/download.lock"
    lock.mkdir()
    (lock / "owner").write_text(f"pid={s.pid}\nstart={start}\nrun={RUN}\ncmd=mlx-download\n")
    r = host.run("download-cancel", RUN)
    assert r.returncode == 1 and "not the downloader" in r.stderr
    assert s.poll() is None
    r = host.run("download-cancel", "r00000000009")
    assert r.returncode == 1 and "not running" in r.stderr
    assert s.poll() is None


def test_a_stale_lock_from_a_dead_process_is_taken_over(host):
    lock = host.root / "run/download.lock"
    lock.mkdir()
    (lock / "owner").write_text("pid=999999\nstart=Thu Jan  1 00:00:00 1970\nrun=x\n")
    _start(host)
    assert _wait(host)["state"] == "published"


# --- publication ----------------------------------------------------------------------


def test_publication_never_overwrites_an_existing_directory(host, hf):
    hf.slow["model-00001-of-00002.safetensors"] = 0.02
    _start(host)
    _wait_for(lambda: (host.root / f"downloads/{RUN}/files/config.json").exists())
    (host.root / "models/Tiny").mkdir()
    (host.root / "models/Tiny/keep.txt").write_text("mine")
    st = _wait(host)
    assert st["state"] == "failed" and st["error_kind"] == "exists"
    assert os.listdir(host.root / "models/Tiny") == ["keep.txt"]
    assert (host.root / f"downloads/{RUN}/files/config.json").exists()


def test_publication_waits_for_the_lifecycle_lock_then_resumes(host):
    install_helper(host.root, _endpoint(host), **{"PUBLISH_WAIT_S = 600.0": "PUBLISH_WAIT_S = 0.3"})
    host.lock.mkdir()  # an ownerless lock: every mlx-model mutation refuses
    _start(host)
    st = _wait(host)
    assert st["state"] == "failed" and st["error_kind"] == "publish" and st["retryable"]
    host.lock.rmdir()
    assert host.run("download-resume", RUN).returncode == 0
    st = _wait(host)
    assert st["state"] == "published", st
    assert host.run("download-resume", RUN).returncode == 0  # already published: refused
    assert _json(host.run("download-status", RUN))["state"] == "published"


def _endpoint(host):
    text = (host.root / "bin/mlx-download").read_text()
    return text.split('ENDPOINT = "', 1)[1].split('"', 1)[0]


@pytest.mark.parametrize(
    "args,msg",
    [
        (("mlx-community/Tiny", "main", "Tiny", RUN), "full commit hash"),
        (("../etc", SHA, "Tiny", RUN), "Invalid repository"),
        (("mlx-community/Tiny", SHA, "../x", RUN), "Invalid destination"),
        (("mlx-community/Tiny", SHA, "active", RUN), "Invalid destination"),
        (("mlx-community/Tiny", SHA, "Tiny", "BAD"), "Invalid run id"),
        (("mlx-community/Tiny", SHA, "A", RUN), "already exists"),
    ],
)
def test_download_argument_refusals(host, args, msg):
    r = host.run("download", *args)
    assert r.returncode == 1 and msg in r.stderr
    assert not (host.root / "downloads" / RUN).exists()


def test_rotation_defers_a_running_downloads_log(host, hf):
    hf.slow["model-00001-of-00002.safetensors"] = 0.05
    _start(host)
    _wait_for(lambda: (host.root / "run/download.lock/owner").exists())
    log = host.root / f"logs/download-{RUN}.log"
    with log.open("ab") as fh:
        fh.write(b"x" * (2 << 20))
    r = host.run("rotate-logs", "--max-bytes", str(1 << 20), "--keep", "3")
    assert f"deferred download-{RUN}.log active-writer" in r.stdout
    host.run("download-cancel", RUN)


# --- helper internals (in process) ---------------------------------------------------


@pytest.fixture
def helper(tmp_path):
    path = install_helper(tmp_path / "Models", "http://127.0.0.1:9")
    loader = importlib.machinery.SourceFileLoader("mlx_download_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_redaction(helper, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "tok-123-secret")
    text = helper.redact(
        "Bearer abc tok-123-secret hf_abcdefghijkl https://cdn.x/f?X-Amz-Signature=zzz"
    )
    assert "abc" not in text and "tok-123-secret" not in text
    assert "hf_abcdefghijkl" not in text and "zzz" not in text


@pytest.mark.parametrize(
    "p", ["", "/abs", "../x", "a/../b", "a\\b", "a//b", "x.part", "a/" * 9 + "b", "a\nb", None]
)
def test_unsafe_relpaths(helper, p):
    with pytest.raises(helper.Fail):
        helper.safe_relpath(p)


def test_safe_relpaths(helper):
    for p in (".gitattributes", "tokenizer.json", "sub/dir/model (1).safetensors"):
        assert helper.safe_relpath(p) == p


def test_not_enough_space_fails_before_downloading(helper, monkeypatch, tmp_path):
    root = tmp_path / "Models"
    (root / "downloads").mkdir(parents=True)
    run = helper.Run(RUN)
    run.dir.mkdir(parents=True)
    manifest = {
        "repo": "o/r",
        "revision": SHA,
        "dest": "R",
        "files": [
            {"path": "config.json", "size": 10, "sha256": None, "blob": "0" * 40},
            {"path": "model.safetensors", "size": 10**9, "sha256": "0" * 64, "blob": None},
            {"path": "tokenizer.json", "size": 1, "sha256": None, "blob": "0" * 40},
        ],
    }
    (run.dir / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(helper, "free_bytes", lambda p: 1000)
    with pytest.raises(helper.Fail) as exc:
        run.execute(None, None, None)
    assert exc.value.kind == "disk-full" and exc.value.retryable


def test_a_full_disk_mid_write_is_a_retryable_failure(helper, monkeypatch, tmp_path):
    (tmp_path / "Models/run").mkdir(parents=True)

    def full(self, *a):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(helper.Run, "execute", full)
    assert helper.run_download(RUN, "o/r", SHA, "R") == 1
    st = json.loads((tmp_path / f"Models/downloads/{RUN}/status.json").read_text())
    assert st["state"] == "failed" and st["error_kind"] == "disk-full" and st["retryable"]
    assert not (tmp_path / "Models/run/download.lock").exists()


def test_a_vanished_drive_is_reported(helper, monkeypatch, tmp_path):
    (tmp_path / "Models/run").mkdir(parents=True)

    def gone(self, *a):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(helper.Run, "execute", gone)
    helper.run_download(RUN, "o/r", SHA, "R")
    st = json.loads((tmp_path / f"Models/downloads/{RUN}/status.json").read_text())
    assert st["error_kind"] == "drive"


def test_helper_is_python_39_compatible():
    source = (HOST / "bin/mlx-download").read_text()
    ast.parse(source, feature_version=(3, 9))
    assert source.startswith("#!/usr/bin/python3\n")
    assert "import" in source and "huggingface_hub" not in source  # stdlib only
    assert os.access(HOST / "bin/mlx-download", os.X_OK)


PY39 = shutil.which("python3.9")


@pytest.mark.skipif(PY39 is None and not os.environ.get("MLX_DOWNLOAD_PY39"), reason="no 3.9")
def test_helper_runs_on_python_39(host, hf):
    py = os.environ.get("MLX_DOWNLOAD_PY39") or PY39
    cli = host.root / "bin/mlx-model"
    cli.write_text(cli.read_text().replace('HOST_PY="/usr/bin/python3"', f'HOST_PY="{py}"'))
    _start(host)
    st = _wait(host)
    assert st["state"] == "published", st
    assert sys.version_info >= (3, 9)


def test_a_cancel_during_the_rename_reports_the_real_outcome(host):
    """SIGTERM while publish-download runs must not leave a published model marked
    cancelled: the helper finishes the rename first and reports what happened."""
    real = host.root / "bin/mlx-model.real"
    host.cli.rename(real)
    host.cli.write_text(
        f'#!/bin/zsh\n[[ "$1" == publish-download ]] && sleep 1.5\nexec zsh {real} "$@"\n'
    )
    host.cli.chmod(0o755)
    _start(host)
    _wait_for(lambda: _json(host.run("download-status", RUN))["state"] == "publishing")
    time.sleep(0.3)  # inside the rename call
    r = host.run("download-cancel", RUN)
    assert r.returncode == 0, r.stderr
    assert "published" in r.stdout
    st = _json(host.run("download-status", RUN))
    assert st["state"] == "published" and (host.root / "models/Tiny/config.json").exists()
