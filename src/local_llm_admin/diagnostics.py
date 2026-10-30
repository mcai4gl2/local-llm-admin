"""Diagnostics: independent, time-boxed checks with evidence, a fix and an optional action.

A timed-out ``asyncio.to_thread`` call keeps its thread (a hung read on a USB volume
cannot be interrupted), so probes go through ``ProbeRunner``: at most one outstanding
call per probe id. While one is still running, later requests report ``unknown`` and
``stale`` instead of starting another thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import errno
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import psutil

from .backend_types import resolve_observations
from .config import Settings
from .control import UNKNOWN, ControlService
from .ingestion import IngestStore, error_hint
from .mlx import MlxClient
from .sidecars import SidecarSpec, read_intent, read_service_intent

_LIVE_CLI_RE = re.compile(r"bin/mlx-model (start|stop|switch|add)\b")
_UNREACHABLE = {errno.EHOSTUNREACH, errno.ENETUNREACH, 65}  # 65: EHOSTUNREACH on macOS


class SamplerLike(Protocol):
    def current(self) -> dict[str, Any]: ...


@dataclass
class ProbeResult:
    state: str  # done | timeout | busy | error
    value: Any = None
    error: BaseException | None = None


class ProbeRunner:
    def __init__(self, max_workers: int = 16) -> None:
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="probe"
        )
        self._pending: dict[str, tuple[concurrent.futures.Future[Any], float]] = {}

    def busy_for(self, key: str) -> float | None:
        entry = self._pending.get(key)
        if entry and not entry[0].done():
            return time.monotonic() - entry[1]
        return None

    async def run(self, key: str, fn: Callable[[], Any], timeout: float) -> ProbeResult:
        if self.busy_for(key) is not None:
            return ProbeResult("busy")
        fut = self._pool.submit(fn)
        # an abandoned probe that fails later must not log "exception never retrieved"
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        self._pending[key] = (fut, time.monotonic())
        try:
            value = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(fut)), timeout)
        except TimeoutError:
            return ProbeResult("timeout")
        except Exception as exc:
            return ProbeResult("error", error=exc)
        return ProbeResult("done", value)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _ps_start(pid: int) -> float | None:
    """Process start as epoch seconds from ``ps -o lstart`` (the format mlx-model records)."""
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _parse_lstart(out)


def _parse_lstart(text: str) -> float | None:
    try:
        return time.mktime(time.strptime(" ".join(text.split()), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def live_cli_pids() -> list[int]:
    pids = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmd = " ".join(proc.info.get("cmdline") or [])
        if _LIVE_CLI_RE.search(cmd):
            pids.append(proc.info["pid"])
    return pids


def lock_info(settings: Settings, now: float | None = None) -> dict[str, Any]:
    """``state``: none | live | stale | unknown, following ``mlx-model unlock``'s rules."""
    lock = settings.run_dir / "mlx.lock"
    try:
        st = lock.stat()
    except FileNotFoundError:
        return {"state": "none", "age_s": None, "owner": None, "legacy": False}
    age = max(0.0, (now or time.time()) - st.st_mtime)
    owner_file = lock / "owner"
    try:
        text = owner_file.read_text()
    except FileNotFoundError:
        state = "live" if live_cli_pids() else "stale"
        return {"state": state, "age_s": age, "owner": None, "legacy": True}
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    pid_raw, start_raw = fields.get("pid", ""), fields.get("start", "")
    recorded = _parse_lstart(start_raw)
    if not pid_raw.isdigit() or recorded is None:
        return {"state": "unknown", "age_s": age, "owner": None, "legacy": False}
    pid = int(pid_raw)
    actual = _ps_start(pid)
    state = "live" if actual is not None and abs(actual - recorded) < 2 else "stale"
    owner = {"pid": pid, "cmd": fields.get("cmd", "")}
    return {"state": state, "age_s": age, "owner": owner, "legacy": False}


def probe_tcp(
    url: str, connect: Callable[..., Any], timeout: float = 2.0
) -> tuple[str, str, str | None]:
    """(status, evidence, fix) for a TCP connect to ``url``'s host and port."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    target = f"{host}:{port}"
    try:
        connect((host, port), timeout=timeout).close()
    except TimeoutError:
        return "fail", f"{target}: no answer within {timeout:.0f} s", "Check the gateway host."
    except ConnectionRefusedError:
        return (
            "fail",
            f"{target}: connection refused (host reachable, nothing listening)",
            "Start LiteLLM on the gateway host.",
        )
    except OSError as exc:
        code = exc.errno
        if code in _UNREACHABLE:
            python = os.path.realpath(sys.executable)
            return (
                "fail",
                f"{target}: errno {code} ({exc.strerror}): possible Local Network restriction"
                " for this Python, or a routing failure",
                f"If other machines reach {target}, allow {python} under Privacy & Security >"
                " Local Network (a launchd-started process needs it), then restart the admin.",
            )
        return "fail", f"{target}: {exc}", "Check the gateway host and the network."
    return "ok", f"{target} accepts connections", None


def _check(
    cid: str,
    title: str,
    status: str,
    evidence: str,
    fix: str | None = None,
    action: str | None = None,
    stale: bool = False,
) -> dict[str, Any]:
    return {
        "id": cid,
        "title": title,
        "status": status,
        "evidence": evidence,
        "fix": fix,
        "action": action,
        "stale": stale,
        "observed_at": time.time(),
    }


class Diagnostics:
    def __init__(
        self,
        settings: Settings,
        mlx: MlxClient,
        control: ControlService,
        sampler: SamplerLike,
        events: IngestStore,
        probes: ProbeRunner,
        *,
        watchdog_state: Callable[[], dict[str, Any]],
        backend_health: Callable[[], dict[str, dict[str, Any]]] | None = None,
        connect: Callable[..., Any] = socket.create_connection,
    ) -> None:
        self.settings = settings
        self.mlx = mlx
        self.control = control
        self.sampler = sampler
        self.events = events
        self.probes = probes
        self.watchdog_state = watchdog_state
        self.connect = connect
        self.backend_health = backend_health or (lambda: {})

    async def _probe(
        self, cid: str, title: str, fn: Callable[[], dict[str, Any]]
    ) -> dict[str, Any]:
        timeout = self.settings.probe_timeout_s
        res = await self.probes.run(f"diag:{cid}", fn, timeout)
        if res.state == "done":
            out: dict[str, Any] = res.value
            return out
        if res.state == "busy":
            return _check(cid, title, "unknown", "the previous check is still running", stale=True)
        if res.state == "timeout":
            return _check(cid, title, "unknown", f"check timed out after {timeout:g} s")
        return _check(cid, title, "unknown", f"check failed: {res.error}")

    # --- individual checks (run in probe threads) -----------------------------
    def _drive(self) -> dict[str, Any]:
        root = self.settings.mlx_root
        if self.mlx.drive_mounted():
            return _check("drive", "Model drive", "ok", f"{root} is mounted")
        return _check(
            "drive",
            "Model drive",
            "fail",
            f"{root} is not mounted",
            "Mount the drive, then use Engine to start the model.",
        )

    def _drive_access(self) -> dict[str, Any]:
        err = self.mlx.access_error()
        if err:
            return _check("drive_access", "Drive access", "fail", "permission denied", err)
        return _check("drive_access", "Drive access", "ok", "readable by the admin")

    def _network(self) -> dict[str, Any]:
        status, evidence, fix = probe_tcp(self.settings.litellm_base_url, self.connect)
        return _check("litellm_network", "LiteLLM gateway", status, evidence, fix)

    def _lock(self) -> dict[str, Any]:
        info = lock_info(self.settings)
        title = "mlx-model lock"
        state, age = info["state"], info["age_s"]
        if state == "none":
            return _check("mlx_lock", title, "ok", "run/mlx.lock is free")
        held = f"held for {int((age or 0) // 60)} min"
        who = (
            f"pid {info['owner']['pid']} ({info['owner']['cmd']})"
            if info["owner"]
            else "no owner recorded (older mlx-model)"
        )
        if state == "live":
            return _check(
                "mlx_lock", title, "warn", f"{held} by running {who}", "Wait for it to finish."
            )
        if state == "unknown":
            return _check(
                "mlx_lock",
                title,
                "unknown",
                f"{held}; the owner record is unreadable",
                "Inspect /Volumes/Models/run/mlx.lock on the Mac.",
            )
        return _check(
            "mlx_lock",
            title,
            "fail",
            f"{held}; {who} is no longer running",
            "Unlock removes the stale lock (mlx-model unlock).",
            action="unlock",
        )

    def _conflict(self) -> dict[str, Any]:
        observations = self.control.backends.observe(include_disabled=True)
        up = {self.control.backends.get(o.backend).spec.label: o.running for o in observations}
        active = resolve_observations(observations)
        if active == "unknown":
            return _check(
                "engine_conflict",
                "Engines",
                "unknown",
                "engine ownership is unknown",
                "Inspect the owning PID files and shared-port listener.",
            )
        if active == "conflict":
            return _check(
                "engine_conflict",
                "Engines",
                "fail",
                " and ".join(k for k, v in up.items() if v) + " are all running"
                if sum(up.values()) > 2
                else " and ".join(k for k, v in up.items() if v) + " are both running",
                "Keep one engine (banner at the top of the page).",
            )
        return _check("engine_conflict", "Engines", "ok", f"active: {active}")

    def _sidecar_check(self, spec: SidecarSpec) -> dict[str, Any]:
        """M6/M7: a sidecar's install and whether it runs as the admin last left it."""
        cid, title = spec.check_id, spec.title
        proc = self.control.sidecars.process(spec.name)
        cli = proc.cli_status()
        if not cli["ok"]:
            return _check(
                cid,
                title,
                "fail",
                f"{spec.name} status failed: {cli['error'] or 'no output'}",
                f"Deploy {spec.cli_path} (ops/deploy-host.sh).",
            )
        if cli["weights"] != "complete":
            return _check(
                cid, title, "fail", f"weights {cli['weights'] or 'unknown'}", spec.weights_hint
            )
        st = proc.status()
        if st.running and not st.owned:
            return _check(cid, title, "fail", "Process ownership is unknown.",
                          "Inspect the service generation records and component listeners.")
        if st.running and spec.kind == "service" and not self.control.sidecars.ready_sync(spec.name):
            return _check(cid, title, "fail", "API or dependency is not ready; recognition warmup is required.",
                          "Inspect the service logs, then restart from Models after active work stops.")
        if spec.kind == "service":
            meant = read_service_intent(self.control.store, spec) == "running"
        else:
            intent = read_intent(self.control.store) or {}
            meant = intent.get("name") == spec.name and intent.get("value") == "running"
        state = f"running pid {st.pid}" if st.running else "stopped"
        if meant and not st.running:
            engine = self.control.exclusive_running() if spec.kind == "service" else None
            if engine is not None:
                label = self.control.backends.get(engine).spec.label
                return _check(
                    cid, title, "ok", f"stopped while {label} runs; restored when you switch back"
                )
            return _check(
                cid,
                title,
                "warn",
                f"{state}, but it was started from the admin; weights complete",
                "Start it again from the Models page (it is never restarted automatically).",
            )
        return _check(cid, title, "ok", f"{state}; weights complete")

    def _sidecar_conflict(self) -> dict[str, Any]:
        running = self.control.sidecars.running()
        if len(running) > 1:
            return _check(
                "sidecar_conflict",
                "Sidecars",
                "fail",
                f"{', '.join(s.name for s in running)} are running together",
                "Stop all but one (Models page, Sidecar card). Starts are refused until then.",
            )
        return _check("sidecar_conflict", "Sidecars", "ok", "at most one sidecar running")

    # --- in-memory checks --------------------------------------------------------
    def _python(self) -> dict[str, Any]:
        return _check(
            "admin_python",
            "Admin Python",
            "ok",
            f"{os.path.realpath(sys.executable)} (grant privacy permissions to this path)",
        )

    def _memory(self) -> dict[str, Any]:
        level = self.sampler.current().get("pressure")
        status = {"normal": "ok", "warn": "warn", "critical": "fail"}.get(level or "", "unknown")
        fix = (
            "Switch to a smaller model or stop ComfyUI jobs."
            if status in ("warn", "fail")
            else None
        )
        return _check(
            "memory", "Memory pressure", status, f"pressure: {level or 'unavailable'}", fix
        )

    def _watchdog(self) -> dict[str, Any]:
        state = self.watchdog_state()
        if state.get("suspended"):
            return _check(
                "watchdog",
                "Watchdog",
                "fail",
                f"suspended: {state['suspended']}",
                "Check the engine, then Resume. Current blockers still apply.",
                action="resume",
            )
        if not state.get("enabled", True):
            return _check("watchdog", "Watchdog", "warn", "disabled (WATCHDOG_ENABLED=false)")
        return _check("watchdog", "Watchdog", "ok", "active")

    def _intent(self) -> dict[str, Any]:
        intent = self.control.intent()
        value = intent["value"] if intent else None
        if value in (None, UNKNOWN):
            reason = intent.get("reason") if intent else "not recorded yet"
            return _check(
                "intent",
                "Intended engine",
                "warn",
                f"unknown ({reason})",
                "Start, switch or stop an engine from the admin to record it.",
            )
        return _check("intent", "Intended engine", "ok", str(value))

    def _request_errors(self) -> dict[str, Any]:
        since = time.time() - 3600
        rows = self.events.events(kinds=("error",), since=since)
        if not rows:
            return _check("request_errors", "Request errors (1 h)", "ok", "none")
        groups = Counter(r["signature"] for r in rows)
        sample = {r["signature"]: r["message"] for r in reversed(rows)}
        lines, hints = [], []
        for sig, n in groups.most_common(5):
            lines.append(f"{n}× {sample[sig]}")
            if (h := error_hint(sample[sig])) and h not in hints:
                hints.append(h)
        return _check(
            "request_errors",
            "Request errors (1 h)",
            "warn",
            "; ".join(lines),
            " ".join(hints) or "See the mlx-server log.",
        )

    def _serving_backend(self) -> dict[str, Any]:
        intent = self.control.intent() or {}
        wanted = str(intent.get("value", "")).partition(":")[0]
        descriptor = self.backend_health().get(wanted, {})
        label = descriptor.get("label", wanted)
        if descriptor.get("enabled") is False:
            return _check(
                "engine_readiness", f"{label} serving", "fail", "Intended backend is disabled."
            )
        state = descriptor.get("state", "unknown")
        status = (
            "ok" if descriptor.get("ready") is True else "unknown" if state == "unknown" else "warn"
        )
        return _check(
            "engine_readiness",
            f"{label} serving",
            status,
            f"Intended {intent.get('value')}; observed {state}. Telemetry freshness is separate.",
            "Recovery remains runtime-owned; no automatic admin restart.",
        )

    async def run(self) -> list[dict[str, Any]]:
        drive, access, net, lock, conflict = await asyncio.gather(
            self._probe("drive", "Model drive", self._drive),
            self._probe("drive_access", "Drive access", self._drive_access),
            self._probe("litellm_network", "LiteLLM gateway", self._network),
            self._probe("mlx_lock", "mlx-model lock", self._lock),
            self._probe("engine_conflict", "Engines", self._conflict),
        )
        local = [self._python(), self._memory(), self._watchdog()]
        db = await asyncio.gather(
            self._probe("intent", "Intended engine", self._intent),
            self._probe("request_errors", "Request errors (1 h)", self._request_errors),
        )
        extra = []
        wanted = str((self.control.intent() or {}).get("value", "")).partition(":")[0]
        if any(
            b.spec.id == wanted and "profiles" in b.spec.operations for b in self.control.backends
        ):
            extra.append(
                await self._probe("engine_readiness", "Serving backend", self._serving_backend)
            )
        specs = self.control.sidecars.specs()
        for spec in [*specs, *self.control.sidecars.services()]:

            def check(spec: SidecarSpec = spec) -> dict[str, Any]:
                return self._sidecar_check(spec)

            extra.append(await self._probe(spec.check_id, spec.title, check))
        if len(specs) > 1:
            extra.append(await self._probe("sidecar_conflict", "Sidecars", self._sidecar_conflict))
        return [drive, access, net, lock, conflict, *local, *db, *extra]
