"""WAL-consistent backups and old-schema restoration before a v16 rollout."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from local_llm_admin import storage
from local_llm_admin.db_snapshot import backup, restore


def test_backup_includes_live_wal_and_refuses_overwrite(tmp_path: Path) -> None:
    path, snapshot = tmp_path / "live.sqlite", tmp_path / "backup.sqlite"
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE value (n INTEGER)")
        c.execute("INSERT INTO value VALUES (7)")
        c.commit()
        backup(path, snapshot)
    with sqlite3.connect(snapshot) as c:
        assert c.execute("SELECT n FROM value").fetchone()[0] == 7
    assert snapshot.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        backup(path, snapshot)


def test_v16_migration_failure_is_atomic_and_old_app_can_use_restored_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, snapshot = tmp_path / "live.sqlite", tmp_path / "v15.sqlite"
    with sqlite3.connect(path, isolation_level=None) as c:
        for i, migration in enumerate(storage.MIGRATIONS[:15], 1):
            migration(c)
            c.execute(f"PRAGMA user_version={i}")
    backup(path, snapshot)
    original = storage.MIGRATIONS[15]

    def broken(conn: sqlite3.Connection) -> None:
        original(conn)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(storage, "MIGRATIONS", [*storage.MIGRATIONS[:15], broken, *storage.MIGRATIONS[16:]])
    with pytest.raises(RuntimeError, match="interrupted"):
        storage.Database(path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 15
        assert (
            c.execute("SELECT name FROM sqlite_master WHERE name='engine_readings'").fetchone()
            is None
        )
    storage.MIGRATIONS[15] = original
    storage.Database(path)
    storage.Database(path)  # repeated migration/open is safe
    monkeypatch.setattr(storage, "SCHEMA_VERSION", 15)
    with pytest.raises(RuntimeError, match="newer"):
        storage.Database(path)
    restore(snapshot, path, expected_version=15, preserve=tmp_path / "v16-preserved.sqlite")
    storage.Database(path)


def test_restore_requires_matching_version_offline_files_and_preservation(tmp_path: Path) -> None:
    path, snapshot = tmp_path / "live.sqlite", tmp_path / "backup.sqlite"
    storage.Database(path)
    backup(path, snapshot)
    with pytest.raises(ValueError, match="version"):
        restore(snapshot, path, expected_version=1, preserve=None)
    with pytest.raises(ValueError, match="preserve"):
        restore(snapshot, path, expected_version=storage.SCHEMA_VERSION, preserve=None)
    wal = Path(str(path) + "-wal")
    wal.touch()
    with pytest.raises(ValueError, match="offline"):
        restore(snapshot, path, expected_version=storage.SCHEMA_VERSION, preserve=tmp_path / "saved.sqlite")
    assert not (tmp_path / "saved.sqlite").exists()


def test_backup_cli_and_offline_restore_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_llm_admin.db_snapshot import main

    source, saved, target = (
        tmp_path / "source.sqlite",
        tmp_path / "saved.sqlite",
        tmp_path / "restored.sqlite",
    )
    storage.Database(source)
    monkeypatch.setattr(
        "sys.argv", ["snapshot", "backup", "--database", str(source), "--output", str(saved)]
    )
    main()
    monkeypatch.setattr(
        "sys.argv",
        [
            "snapshot",
            "restore",
            "--snapshot",
            str(saved),
            "--database",
            str(target),
            "--expected-version",
            str(storage.SCHEMA_VERSION),
            "--preserve-current",
            str(tmp_path / "previous.sqlite"),
            "--offline",
        ],
    )
    main()
    assert target.exists()
    with pytest.raises(ValueError, match="differ"):
        restore(target, target, expected_version=storage.SCHEMA_VERSION, preserve=tmp_path / "previous.sqlite")


def test_backup_timeout_and_integrity_validation_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import local_llm_admin.db_snapshot as snapshots

    source, output = tmp_path / "source.sqlite", tmp_path / "output.sqlite"
    storage.Database(source)
    times = iter([0, 100])
    monkeypatch.setattr(snapshots.time, "monotonic", lambda: next(times))
    with pytest.raises(TimeoutError):
        backup(source, output)
    assert not output.exists()

    class BadConnection:
        def execute(self, query: str) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(fetchone=lambda: ["not ok"])

        def close(self) -> None:
            pass

    monkeypatch.setattr(snapshots.sqlite3, "connect", lambda *a, **kw: BadConnection())
    with pytest.raises(ValueError, match="integrity"):
        snapshots._validate(source)


def test_snapshot_module_entrypoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    source = tmp_path / "a.sqlite"
    storage.Database(source)
    monkeypatch.setattr(
        "sys.argv",
        ["snapshot", "backup", "--database", str(source), "--output", str(tmp_path / "b.sqlite")],
    )
    with pytest.warns(RuntimeWarning):
        runpy.run_module("local_llm_admin.db_snapshot", run_name="__main__")
