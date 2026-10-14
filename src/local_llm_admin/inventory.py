"""Model inventory and removal preview (M3a). Read-only: removal runs through the CLI.

The inventory reports what the admin can observe: registry entries, weights directories,
their size, a metadata fingerprint (file names and sizes plus config/index content, not
a hash of the weights), readiness, aliases, selection and observed use. "First observed"
is when the admin first saw a directory, never presented as its download date.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .mlx import MlxClient, validate_name
from .storage import Database

_META_FILES = ("config.json", "model.safetensors.index.json")
_META_MAX_BYTES = 4 * 1024 * 1024
TOKEN_ATTRIBUTION = (
    "LiteLLM token counts are per route (one stable model id for every local model), so"
    " they are attributed to a model only on days when a single model was active. The"
    " request counts here come from mlx-server.log (M4) and cover only the logged period."
)


def metadata_fingerprint(d: Path) -> str | None:
    """Identity of a weights directory without reading the weights: every top-level
    file's name and size plus the bytes of config.json and the safetensors index."""
    h = hashlib.sha256()
    try:
        entries = sorted(os.scandir(d), key=lambda e: e.name)
    except OSError:
        return None
    for e in entries:
        try:
            if e.is_file(follow_symlinks=False):
                h.update(f"{e.name}\0{e.stat(follow_symlinks=False).st_size}\n".encode())
        except OSError:
            continue
    for name in _META_FILES:
        try:
            with (d / name).open("rb") as fh:
                h.update(name.encode() + b"\0" + fh.read(_META_MAX_BYTES))
        except OSError:
            continue
    return "meta:" + h.hexdigest()[:16]


def build_remove_argv(
    bin_path: Path,
    name: str,
    delete_files: bool,
    expect_path: str | None,
    expect_inode: int | None,
) -> list[str]:
    """Fixed argv for ``mlx-model remove``; never passed through a shell."""
    argv = [str(bin_path), "remove", validate_name(name)]
    if delete_files:
        argv.append("--delete-files")
    if expect_path is not None:
        if not expect_path.startswith("/"):
            raise ValueError("expected path must be absolute")
        argv += ["--expect-path", expect_path]
    if expect_inode is not None:
        argv += ["--expect-inode", str(int(expect_inode))]
    return argv


def _canonical(path: str) -> str | None:
    try:
        return os.path.realpath(path) if os.path.lexists(path) else None
    except OSError:
        return None


def removal_preview(
    registered: list[dict[str, Any]],
    name: str,
    *,
    selected: str | None,
    loaded: bool,
    models_root: Path,
    dir_size: Callable[[Path], int],
) -> dict[str, Any] | None:
    """What removing ``name`` would touch, and why each kind of removal is refused.

    Mirrors ``mlx-model remove``'s checks; the CLI checks again under its lock, and the
    returned canonical path and inode are passed to it so a changed target is refused.
    None if ``name`` is not registered."""
    entries = [m for m in registered if m["name"] == name]
    if not entries:
        return None
    raw = entries[0]["dir"]
    canon = _canonical(raw)
    inode: int | None = None
    if canon is not None and os.path.isdir(canon):
        try:
            inode = os.stat(canon).st_ino
        except OSError:
            inode = None
    exists = inode is not None
    aliases = sorted(
        m["name"]
        for m in registered
        if m["name"] != name and canon and _canonical(m["dir"]) == canon
    )
    both: list[str] = []
    if len(entries) > 1:
        both.append(f"{name} is registered more than once; fix the registry by hand first")
    if selected == name:
        both.append(
            f"{name} is {'loaded' if loaded else 'the selected model'}; switch to another"
            " model first"
        )
    active = _canonical(str(models_root / "active"))
    if canon and active == canon and selected != name:
        both.append(f"models/active points at {canon}; switch to another model first")
    delete: list[str] = []
    root = _canonical(str(models_root))
    if not exists or canon is None:
        delete.append(f"{raw} is missing or not a directory; remove without deleting files")
    elif os.path.islink(raw) or canon != os.path.normpath(raw):
        delete.append(f"{raw} is a symlink or has a symlink in its path")
    elif root is None or os.path.dirname(canon) != root or Path(canon).name.startswith("."):
        delete.append(f"{canon} is outside {models_root}; only a directory directly under it")
    if aliases:
        delete.append(f"the directory is also registered as {', '.join(aliases)}")
    return {
        "name": name,
        "dir": raw,
        "canonical": canon,
        "inode": inode,
        "exists": exists,
        "size_bytes": dir_size(Path(canon)) if exists and canon else None,
        "aliases": aliases,
        "selected": selected == name,
        "loaded": loaded and selected == name,
        "unregister_refusals": both,
        "delete_refusals": both + delete,
    }


class InventoryStore(Database):
    def observe(
        self, rows: list[tuple[str, str | None, int | None]], now: float
    ) -> dict[str, dict[str, float]]:
        """Record ``(path, fingerprint, size)`` sightings; returns first/last seen."""
        with self._conn() as c:
            for path, fp, size in rows:
                c.execute(
                    "INSERT INTO model_inventory (path, first_seen, last_seen, fingerprint,"
                    " size_bytes) VALUES (?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET"
                    " last_seen = excluded.last_seen, fingerprint = excluded.fingerprint,"
                    " size_bytes = excluded.size_bytes",
                    (path, now, now, fp, size),
                )
            out = {}
            for path, _fp, _size in rows:
                r = c.execute(
                    "SELECT first_seen, last_seen FROM model_inventory WHERE path = ?", (path,)
                ).fetchone()
                out[path] = {"first_seen": r["first_seen"], "last_seen": r["last_seen"]}
        return out

    def last_activated(self) -> dict[str, float]:
        """Latest successful or observed start/switch per registry name."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT to_name, MAX(ts) AS ts FROM switch_history WHERE to_name IS NOT NULL"
                " AND action IN ('switch', 'start', 'engine') AND result IN ('succeeded',"
                " 'observed') GROUP BY to_name"
            ).fetchall()
        return {r["to_name"]: r["ts"] for r in rows}

    def request_totals(self) -> dict[str, dict[str, Any]]:
        """Requests seen in mlx-server.log per model (attributed by its start marker)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT model, COUNT(*) AS n, SUM(prompt_tokens) AS p, SUM(generated_tokens)"
                " AS g, MIN(started) AS first, MAX(started) AS last FROM inference_requests"
                " WHERE model IS NOT NULL AND attribution = 'generation' GROUP BY model"
            ).fetchall()
        return {
            r["model"]: {
                "count": r["n"],
                "prompt_tokens": r["p"],
                "generated_tokens": r["g"],
                "first": r["first"],
                "last": r["last"],
            }
            for r in rows
        }

    def requests_since(self) -> float | None:
        with self._conn() as c:
            r = c.execute("SELECT MIN(started) AS s FROM inference_requests").fetchone()
        return r["s"] if r else None


def build_inventory(
    mlx: MlxClient,
    store: InventoryStore,
    *,
    downloaded: Callable[[], dict[str, float]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """One bounded scan (run it inside a probe: the drive can hang)."""
    now = time.time() if now is None else now
    root = mlx.settings.mlx_root / "models"
    registered = mlx.list_models()
    discovered = mlx.discover(registered)
    env = mlx.active_env()
    selected = env.get("MLX_ACTIVE_NAME")
    loaded = mlx.engine_running()
    try:
        du = shutil.disk_usage(root)
        disk: dict[str, int] | None = {
            "total_bytes": du.total,
            "used_bytes": du.used,
            "free_bytes": du.free,
        }
    except OSError:
        disk = None
    sightings: list[tuple[str, str | None, int | None]] = []
    fps: dict[str, str | None] = {}
    for m in registered:
        canon = _canonical(m["dir"])
        if canon and m["dir_exists"]:
            fps[canon] = metadata_fingerprint(Path(canon))
            sightings.append((canon, fps[canon], m["size_bytes"]))
    for d in discovered:
        canon = _canonical(d["path"])
        if canon and canon not in fps:
            fps[canon] = metadata_fingerprint(Path(canon))
            sightings.append((canon, fps[canon], d["present_bytes"]))
    seen = store.observe(sightings, now)
    activated = store.last_activated()
    totals = store.request_totals()
    downloads = downloaded() if downloaded else {}
    by_canon: dict[str, list[str]] = {}
    for m in registered:
        c = _canonical(m["dir"])
        if c:
            by_canon.setdefault(c, []).append(m["name"])
    models = []
    for m in registered:
        canon = _canonical(m["dir"])
        models.append(
            {
                "name": m["name"],
                "dir": m["dir"],
                "canonical": canon,
                "exists": bool(m["dir_exists"]),
                "has_config": m["has_config"],
                "size_bytes": m["size_bytes"],
                "weights": m["weights"],
                "fingerprint": fps.get(canon or ""),
                "aliases": sorted(n for n in by_canon.get(canon or "", []) if n != m["name"]),
                "selected": m["name"] == selected,
                "loaded": loaded and m["name"] == selected,
                "first_observed": (seen.get(canon or "") or {}).get("first_seen"),
                "downloaded_at": downloads.get(canon or ""),
                "last_activated": activated.get(m["name"]),
                "requests": totals.get(m["name"]),
            }
        )
    unregistered = []
    for d in discovered:
        if d["registered_as"]:
            continue
        canon = _canonical(d["path"])
        unregistered.append(
            {
                "dir_name": d["dir_name"],
                "path": d["path"],
                "state": d["state"],
                "size_bytes": d["present_bytes"],
                "fingerprint": fps.get(canon or ""),
                "first_observed": (seen.get(canon or "") or {}).get("first_seen"),
                "downloaded_at": downloads.get(canon or ""),
            }
        )
    return {
        "available": True,
        "checked_at": now,
        "disk": disk,
        "selected": selected,
        "loaded": loaded,
        "models": models,
        "unregistered": unregistered,
        "requests_since": store.requests_since(),
        "token_attribution": TOKEN_ATTRIBUTION,
    }
