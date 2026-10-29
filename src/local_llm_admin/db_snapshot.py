"""SQLite online backup and explicitly offline restore; does not import/migrate the app DB."""

from __future__ import annotations

import argparse
import contextlib
import os
import sqlite3
import tempfile
import time
from pathlib import Path


def _validate(path: Path, expected_version: int | None = None) -> int:
    with contextlib.closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("database integrity check failed")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if expected_version is not None and version != expected_version:
            raise ValueError("snapshot schema version does not match the old application")
        return version


def backup(database: Path, output: Path) -> int:
    """SQLite backup API includes committed WAL pages. Publish atomically, never overwrite."""
    if output.exists() or output.is_symlink():
        raise FileExistsError("backup destination already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".sqlite-backup-", dir=output.parent)
    os.close(fd)
    temporary = Path(temp_name)
    deadline = time.monotonic() + 60

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("database backup exceeded 60 seconds")

    try:
        with contextlib.closing(
            sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        ) as source:
            with contextlib.closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination, pages=512, progress=progress, sleep=0.05)
        version = _validate(temporary)
        with temporary.open("rb") as f:
            os.fsync(f.fileno())
        os.link(temporary, output)
        return version
    finally:
        temporary.unlink(missing_ok=True)


def restore(
    snapshot: Path, database: Path, *, expected_version: int, preserve: Path | None
) -> None:
    """Caller must stop all writers. Refuse journal companions; keep the displaced DB."""
    _validate(snapshot, expected_version)
    if snapshot.resolve() == database.resolve():
        raise ValueError("snapshot must differ from database")
    if database.exists() and preserve is None:
        raise ValueError("preserve path is required for the current database")
    if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("restore requires an offline database without journal companions")
    if database.exists() and preserve is not None:
        backup(database, preserve)
    database.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".sqlite-restore-", dir=database.parent)
    os.close(fd)
    temporary = Path(name)
    temporary.unlink()
    try:
        backup(snapshot, temporary)
        _validate(temporary, expected_version)
        os.replace(temporary, database)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("backup")
    save.add_argument("--database", type=Path, required=True)
    save.add_argument("--output", type=Path, required=True)
    load = commands.add_parser("restore")
    load.add_argument("--snapshot", type=Path, required=True)
    load.add_argument("--database", type=Path, required=True)
    load.add_argument("--expected-version", type=int, required=True)
    load.add_argument("--preserve-current", type=Path, required=True)
    load.add_argument(
        "--offline",
        action="store_true",
        required=True,
        help="Confirm all database writers are stopped; required",
    )
    args = parser.parse_args()
    if args.command == "backup":
        version = backup(args.database, args.output)
        print(f"Backup verified; schema {version}.")
    else:
        restore(
            args.snapshot,
            args.database,
            expected_version=args.expected_version,
            preserve=args.preserve_current,
        )
        print("Offline restore verified.")


if __name__ == "__main__":
    main()
