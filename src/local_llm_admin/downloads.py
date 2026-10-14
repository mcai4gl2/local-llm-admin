"""Model downloads through ``mlx-model`` (M3b).

The admin only builds fixed argv for the CLI: ``download-preview``, ``download``,
``download-resume``, ``download-status`` and ``download-cancel``. The host helper does
the work in the background and owns its lock, staging directory and status file; the
admin keeps a row per run and refreshes it from ``download-status``, so an admin restart
reconnects to a running download instead of starting it again. Repository ids are never
used as paths: the log source of a run is ``download-<run id>``.

Terminal alerts come from the run's log marker (``===== … done fail=N =====``), which
M1's ingestion already turns into exactly one alert per finished run.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .alerts import Snapshot
from .config import Settings
from .diagnostics import ProbeRunner
from .download_store import ACTIVE_STATES, DownloadStore

log = logging.getLogger("local_llm_admin")

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_RE = re.compile(r"^[a-z0-9]{12}$")
DEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ERROR_CODES = {"usage": 400, "auth": 403, "not-found": 404, "metadata": 422}
Cli = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


class DownloadError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
    )


def allowed_owner(repo: str, owners: str) -> bool:
    allowed = {o.strip() for o in owners.split(",") if o.strip()}
    return "*" in allowed or repo.split("/", 1)[0] in allowed


def validate_repo(repo: str, owners: str) -> str:
    if not REPO_RE.fullmatch(repo) or ".." in repo:
        raise DownloadError(400, "enter a Hugging Face repository id such as owner/name")
    if not allowed_owner(repo, owners):
        raise DownloadError(
            403,
            f"{repo.split('/', 1)[0]} is not an allowed owner; add it to"
            " DOWNLOAD_ALLOWED_OWNERS to download from it",
        )
    return repo


def valid_dest(dest: str) -> bool:
    return bool(DEST_RE.fullmatch(dest)) and ".." not in dest and dest != "active"


def valid_run_id(run_id: str) -> bool:
    return bool(RUN_RE.fullmatch(run_id))


def build_argv(bin_path: Path, action: str, *args: str) -> list[str]:
    """Fixed argv for the download commands; every argument is validated."""
    checks: dict[str, tuple[Callable[[str], bool], ...]] = {
        "download-preview": (
            lambda r: bool(REPO_RE.fullmatch(r)) and ".." not in r,
            lambda v: bool(REVISION_RE.fullmatch(v)) and ".." not in v,
        ),
        "download": (
            lambda r: bool(REPO_RE.fullmatch(r)) and ".." not in r,
            lambda s: bool(SHA_RE.fullmatch(s)),
            valid_dest,
            valid_run_id,
        ),
        "download-resume": (valid_run_id,),
        "download-status": (valid_run_id,),
        "download-cancel": (valid_run_id,),
    }
    if action not in checks or len(args) != len(checks[action]):
        raise ValueError(f"unsupported download command: {action}")
    for check, arg in zip(checks[action], args, strict=True):
        if not check(arg):
            raise ValueError(f"invalid argument for {action}: {arg!r}")
    return [str(bin_path), action, *args]


def suggest_dest(repo: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", repo.split("/", 1)[-1]).strip("-.")
    name = re.sub(r"\.{2,}", ".", name)
    return name[:128] or "model"


class DownloadService:
    def __init__(
        self,
        settings: Settings,
        store: DownloadStore,
        probes: ProbeRunner,
        *,
        cli: Cli = _run,
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex[:12],
    ) -> None:
        self.settings = settings
        self.store = store
        self.probes = probes
        self._cli = cli
        self._new_id = new_id

    def _call(self, action: str, *args: str) -> subprocess.CompletedProcess[str]:
        argv = build_argv(self.settings.mlx_model_path, action, *args)
        try:
            return self._cli(argv, self.settings.download_cli_timeout_s)
        except subprocess.TimeoutExpired:
            raise DownloadError(504, f"mlx-model {action} did not answer in time") from None
        except OSError as exc:
            raise DownloadError(503, f"cannot run mlx-model: {exc}") from None

    @staticmethod
    def _json(proc: subprocess.CompletedProcess[str]) -> Any:
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return None

    # --- preview and start --------------------------------------------------------------
    def preview(self, repo: str, revision: str) -> dict[str, Any]:
        validate_repo(repo, self.settings.download_allowed_owners)
        if not REVISION_RE.fullmatch(revision) or ".." in revision:
            raise DownloadError(400, "invalid revision")
        proc = self._call("download-preview", repo, revision)
        doc = self._json(proc)
        if proc.returncode != 0 or not isinstance(doc, dict) or "revision" not in doc:
            if isinstance(doc, dict) and "error" in doc:
                raise DownloadError(
                    _ERROR_CODES.get(str(doc["error"]), 502), str(doc.get("message", ""))
                )
            raise DownloadError(502, (proc.stderr or proc.stdout or "preview failed").strip())
        doc["suggested_dest"] = suggest_dest(repo)
        return doc

    def start(self, repo: str, revision: str, dest: str) -> dict[str, Any]:
        validate_repo(repo, self.settings.download_allowed_owners)
        if not SHA_RE.fullmatch(revision):
            raise DownloadError(400, "the revision must be the commit hash from the preview")
        if not valid_dest(dest):
            raise DownloadError(400, "invalid directory name")
        if self.store.active():
            raise DownloadError(409, "a download is already running; one at a time")
        run_id = self._new_id()
        self.store.create(run_id, repo, revision, dest)
        proc = self._call("download", repo, revision, dest, run_id)
        doc = self._json(proc)
        if proc.returncode != 0 or not isinstance(doc, dict):
            message = (proc.stderr or proc.stdout or "download did not start").strip()
            self.store.update(run_id, {"state": "failed", "message": message, "retryable": False})
            raise DownloadError(409, message)
        self.store.update(run_id, doc)
        log.info("download %s: %s@%s -> %s", run_id, repo, revision[:12], dest)
        return self.get(run_id)

    # --- runs --------------------------------------------------------------------------
    def get(self, run_id: str) -> dict[str, Any]:
        row = self.store.get(run_id)
        if row is None:
            raise DownloadError(404, "unknown download")
        row["log_source"] = f"download-{run_id}"
        return row

    def refresh(self, run_id: str) -> dict[str, Any]:
        self.get(run_id)
        proc = self._call("download-status", run_id)
        doc = self._json(proc)
        if isinstance(doc, dict) and doc.get("state") not in (None, "unknown"):
            self.store.update(run_id, doc)
        elif isinstance(doc, dict):
            # the host has no record of it (for example it never started)
            self.store.update(
                run_id, {"state": "failed", "message": "the host has no record of this run"}
            )
        return self.get(run_id)

    def refresh_active(self) -> int:
        rows = self.store.active()
        for row in rows:
            try:
                self.refresh(row["id"])
            except DownloadError as exc:
                log.warning("download %s: status failed: %s", row["id"], exc.message)
        return len(rows)

    def cancel(self, run_id: str) -> dict[str, Any]:
        if not valid_run_id(run_id):
            raise DownloadError(400, "invalid run id")
        self.get(run_id)
        proc = self._call("download-cancel", run_id)
        if proc.returncode != 0:
            raise DownloadError(409, (proc.stderr or proc.stdout or "cancel failed").strip())
        return self.refresh(run_id)

    def resume(self, run_id: str) -> dict[str, Any]:
        if not valid_run_id(run_id):
            raise DownloadError(400, "invalid run id")
        row = self.refresh(run_id)
        if row["state"] in ACTIVE_STATES:
            raise DownloadError(409, "this download is still running")
        if row["state"] == "published":
            raise DownloadError(409, "this download is already published")
        if any(r["id"] != run_id for r in self.store.active()):
            raise DownloadError(409, "another download is running; one at a time")
        proc = self._call("download-resume", run_id)
        doc = self._json(proc)
        if proc.returncode != 0 or not isinstance(doc, dict):
            raise DownloadError(409, (proc.stderr or proc.stdout or "resume failed").strip())
        self.store.update(run_id, doc)
        log.info("download %s: resumed", run_id)
        return self.get(run_id)

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r, log_source=f"download-{r['id']}") for r in self.store.runs(limit)]

    def downloaded_at(self) -> dict[str, float]:
        return self.store.published()

    async def on_tick(self, snap: Snapshot, observed: dict[str, Any] | None) -> None:
        """Supervisor listener: follow running downloads (bounded: the drive can hang)."""
        if snap.drive_mounted is not True or not self.store.active():
            return
        res = await self.probes.run(
            "downloads", self.refresh_active, self.settings.download_cli_timeout_s + 5
        )
        if res.state == "error":
            log.warning("download status refresh failed: %s", res.error)
