"""``mlx-model remove`` with real zsh (M3a): unregister or delete, never a live model.

Deletion moves the validated directory into a journaled quarantine on the same volume,
rewrites the registry atomically, and only then removes the quarantined copy.
"""

from __future__ import annotations

import os

import pytest

from .host_harness import make_host


@pytest.fixture
def host(tmp_path):
    h = make_host(tmp_path)
    yield h
    h.cleanup()


def _registry(host):
    return (host.root / "etc/models.conf").read_text()


def _names(host):
    return [
        ln.split("\t")[0] for ln in _registry(host).splitlines() if ln and not ln.startswith("#")
    ]


def _quarantine(host):
    q = host.root / "quarantine"
    return sorted(p.name for p in q.iterdir()) if q.exists() else []


def _fake(host, name, body):
    p = host.root.parent / "fakebin" / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(0o755)


def _fail_mv_into(host, needle):
    # a failing rename for one destination only; everything else is the real mv
    _fake(
        host,
        "mv",
        f'for a in "$@"; do case "$a" in *{needle}*) exit 1 ;; esac; done\nexec /bin/mv "$@"\n',
    )


def _select(host, name, model_dir):
    (host.root / "run/active.env").write_text(
        f"MLX_ACTIVE_NAME={name}\nMLX_ACTIVE_PATH={model_dir}\n"
    )


def test_unregister_keeps_files_and_backs_up_the_registry(host):
    r = host.run("remove", "b")
    assert r.returncode == 0, r.stderr
    assert "Unregistered b" in r.stdout
    assert _names(host) == ["a"]
    assert _registry(host).startswith("# Short name")  # comments survive the rewrite
    assert (host.root / "models/B/config.json").exists()
    backups = list((host.root / "etc/backups").iterdir())
    assert len(backups) == 1 and "\tb\t" not in backups[0].read_text()
    assert f"b\t{host.root}/models/B" in backups[0].read_text()
    assert not host.lock.exists() and not host.gate.exists()


def test_delete_files_removes_the_directory_through_quarantine(host):
    r = host.run("remove", "b", "--delete-files")
    assert r.returncode == 0, r.stderr
    assert "Deleted b" in r.stdout
    assert _names(host) == ["a"]
    assert not (host.root / "models/B").exists()
    assert _quarantine(host) == []
    assert (host.root / "models/A/config.json").exists()


def test_selected_model_is_refused(host):
    _select(host, "b", host.root / "models/B")
    r = host.run("remove", "b", "--delete-files")
    assert r.returncode == 1
    assert "selected" in r.stderr
    assert _names(host) == ["a", "b"] and (host.root / "models/B").exists()
    assert not host.lock.exists()


def test_directory_behind_the_active_link_is_refused(host):
    _select(host, "a", host.root / "models/A")
    (host.root / "models/active").symlink_to(host.root / "models/B")
    r = host.run("remove", "b")
    assert r.returncode == 1
    assert "active" in r.stderr
    assert _names(host) == ["a", "b"]


def test_loaded_model_is_refused_while_the_server_runs(host):
    (host.state / "healthy").write_text("")
    assert host.run("start", "a").returncode == 0
    r = host.run("remove", "a")
    assert r.returncode == 1 and "selected" in r.stderr
    # another model may go while the server runs
    r = host.run("remove", "b", "--delete-files")
    assert r.returncode == 0, r.stderr
    assert _names(host) == ["a"]


@pytest.mark.parametrize("name", ["zzz", "active", "../a", "-x", ""])
def test_unknown_or_invalid_names_are_refused(host, name):
    r = host.run("remove", name)
    assert r.returncode != 0
    assert _names(host) == ["a", "b"]


def test_shared_directory_can_be_unregistered_but_not_deleted(host):
    with (host.root / "etc/models.conf").open("a") as fh:
        fh.write(f"b2\t{host.root}/models/B\n")
    r = host.run("remove", "b", "--delete-files")
    assert r.returncode == 1 and "also registered as b2" in r.stderr
    assert (host.root / "models/B").exists() and _names(host) == ["a", "b", "b2"]
    r = host.run("remove", "b")
    assert r.returncode == 0, r.stderr
    assert _names(host) == ["a", "b2"]


def test_symlinked_or_outside_directories_are_never_deleted(host, tmp_path):
    outside = tmp_path / "elsewhere/X"
    outside.mkdir(parents=True)
    (outside / "config.json").write_text("{}")
    (host.root / "models/L").symlink_to(host.root / "models/A")
    with (host.root / "etc/models.conf").open("a") as fh:
        fh.write(f"x\t{outside}\nl\t{host.root}/models/L\n")
    r = host.run("remove", "x", "--delete-files")
    assert r.returncode == 1 and "outside" in r.stderr
    r = host.run("remove", "l", "--delete-files")
    assert r.returncode == 1 and "symlink" in r.stderr
    assert outside.exists() and (host.root / "models/A/config.json").exists()
    assert host.run("remove", "x").returncode == 0  # unregistering never touches files
    assert outside.exists()


def test_duplicate_registry_names_are_refused(host):
    with (host.root / "etc/models.conf").open("a") as fh:
        fh.write(f"b\t{host.root}/models/A\n")
    r = host.run("remove", "b")
    assert r.returncode == 1 and "more than once" in r.stderr


def test_expected_identity_must_still_match(host, tmp_path):
    canon = str((host.root / "models/B").resolve())
    inode = str(os.stat(canon).st_ino)
    r = host.run("remove", "b", "--delete-files", "--expect-path", canon + "x")
    assert r.returncode == 1 and "changed" in r.stderr
    # the directory was replaced after the preview: same path, another inode
    os.rename(canon, tmp_path / "old-B")
    os.mkdir(canon)
    (host.root / "models/B/config.json").write_text("{}")
    r = host.run("remove", "b", "--delete-files", "--expect-path", canon, "--expect-inode", inode)
    assert r.returncode == 1 and "changed" in r.stderr
    assert (host.root / "models/B/config.json").exists()
    new_inode = str(os.stat(canon).st_ino)
    r = host.run(
        "remove", "b", "--delete-files", "--expect-path", canon, "--expect-inode", new_inode
    )
    assert r.returncode == 0, r.stderr


def test_failed_quarantine_move_changes_nothing(host):
    _fail_mv_into(host, "quarantine/")
    r = host.run("remove", "b", "--delete-files")
    assert r.returncode == 1 and "quarantine" in r.stderr
    assert _names(host) == ["a", "b"] and (host.root / "models/B/config.json").exists()
    assert [p for p in _quarantine(host) if not p.startswith(".")] == []
    assert not host.lock.exists()


def test_failed_registry_write_restores_the_directory(host):
    _fail_mv_into(host, "etc/models.conf")
    r = host.run("remove", "b", "--delete-files")
    assert r.returncode == 1 and "registry" in r.stderr
    assert _names(host) == ["a", "b"]
    assert (host.root / "models/B/config.json").exists()
    assert _quarantine(host) == []
    assert not list((host.root / "etc").glob("models.conf.tmp-*"))


def test_failed_final_removal_is_a_recoverable_pending_cleanup(host):
    _fake(
        host,
        "rm",
        'for a in "$@"; do case "$a" in *quarantine/*) '
        '[ -d "$a" ] && exit 1 ;; esac; done\nexec /bin/rm "$@"\n',
    )
    r = host.run("remove", "b", "--delete-files")
    assert r.returncode == 0, r.stderr
    assert "cleanup-pending" in r.stdout
    assert _names(host) == ["a"] and not (host.root / "models/B").exists()
    (qid,) = [p for p in _quarantine(host) if not p.endswith(".journal")]
    journal = (host.root / "quarantine" / f"{qid}.journal").read_text()
    assert "state=cleanup-pending" in journal
    (host.root.parent / "fakebin/rm").unlink()
    r = host.run("remove-recover")
    assert r.returncode == 0, r.stderr
    assert f"cleaned {qid}" in r.stdout
    assert _quarantine(host) == []


def test_recover_restores_a_move_interrupted_before_the_registry_update(host):
    q = host.root / "quarantine"
    q.mkdir()
    canon = (host.root / "models/B").resolve()
    os.rename(canon, q / "r1")
    (q / "r1.journal").write_text(f"id=r1\nname=b\npath={canon}\nstate=moved\n")
    r = host.run("remove-recover")
    assert r.returncode == 0, r.stderr
    assert "restored r1" in r.stdout
    assert (canon / "config.json").exists() and _quarantine(host) == []
    assert _names(host) == ["a", "b"]


def test_recover_never_overwrites_a_reused_path(host):
    q = host.root / "quarantine"
    q.mkdir()
    canon = (host.root / "models/B").resolve()
    (q / "r2").mkdir()
    (q / "r2.journal").write_text(f"id=r2\nname=b\npath={canon}\nstate=moved\n")
    r = host.run("remove-recover")
    assert r.returncode == 1 and "kept r2" in r.stdout
    assert (q / "r2").exists() and (canon / "config.json").exists()


def test_lock_held_refuses(host):
    host.lock.mkdir()
    r = host.run("remove", "b")
    assert r.returncode == 1 and "Another mlx-model command" in r.stderr
    assert _names(host) == ["a", "b"]
