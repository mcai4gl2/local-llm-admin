"""MLX watchdog (D-R4) and the shared monitoring loop.

``decide`` is pure: it maps one observation to an action. ``Supervisor.tick`` gathers
the observation through bounded probes, feeds the alert evaluator, applies the decision
through ``ControlService`` (so a restart is an ordinary ``source="watchdog"`` job that
races manual actions for the same slot), and turns finished restart jobs into events.

Policy (M1 plan §5): only MLX is restarted; ComfyUI is alert-only. A confirmed crash of
the observed MLX generation, with complete log evidence and no intentional-stop marker,
gets one restart. A second distinct crash within the window suspends before restarting;
so does a failed restart or missing evidence. Repeated ticks over one outage count once.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from .alerts import AlertStore, Evaluator, Snapshot
from .config import Settings
from .control import CRASHES_KEY, UNKNOWN, ControlError, ControlService
from .diagnostics import ProbeRunner, SamplerLike, lock_info, probe_tcp
from .ingestion import HostLogs, IngestStore, PollResult
from .mlx import MlxClient, is_serving

log = logging.getLogger("local_llm_admin")

# listener(snapshot, observed): runs after each tick's decision and alert evaluation
TickListener = Callable[[Snapshot, dict[str, Any] | None], Any]
GENERATION_KEY = "watchdog_generation"  # last observed MLX generation {pid, started}
HANDLED_KEY = "watchdog_handled"  # the generation whose disappearance was acted on
PENDING_KEY = "watchdog_pending"  # {job_id, generation} of a submitted restart


@dataclass(frozen=True)
class WatchInput:
    enabled: bool
    intent: str | None
    engine: str  # mlx | comfyui | none | conflict | unknown
    drive_ok: bool | None
    job_busy: bool
    lock_state: str | None  # none | live | stale | unknown; None when not observed
    suspended: str | None
    pending: dict[str, Any] | None
    last_generation: dict[str, Any] | None
    handled_generation: dict[str, Any] | None
    outage_for_s: float | None  # how long MLX has been seen down in this process
    in_startup_grace: bool
    evidence: str  # complete | behind | gap | unavailable
    shutdown_seen: bool  # a graceful-stop marker for last_generation
    crashes: list[float] = field(default_factory=list)
    now_wall: float = 0.0
    grace_s: float = 30.0
    window_s: float = 1800.0
    recovery_eligible: bool = True


@dataclass(frozen=True)
class Decision:
    action: str  # none | wait | restart | suspend | mark_off
    reason: str
    record_crash: bool = False


def decide(i: WatchInput) -> Decision:
    if not i.enabled:
        return Decision("none", "watchdog disabled")
    if i.pending:
        return Decision("none", "a restart is in progress")
    if i.job_busy:
        return Decision("none", "a job is running")
    if i.drive_ok is not True:
        return Decision("none", "drive unavailable")
    if i.engine in ("unknown", "conflict"):
        return Decision("none", f"engine state is {i.engine}")
    if not i.intent or i.intent == UNKNOWN:
        return Decision("none", "intended engine unknown")
    if not i.intent.startswith("mlx:") or not i.recovery_eligible:
        return Decision("none", "only MLX is restarted automatically")
    if i.engine != "none":
        return Decision("none", f"{i.engine} is running")
    if i.in_startup_grace or i.outage_for_s is None or i.outage_for_s < i.grace_s:
        return Decision("wait", "grace period")
    if i.suspended:
        return Decision("none", "suspended")
    if i.last_generation is None:
        return Decision(
            "suspend",
            "MLX is down and no MLX process was observed; cannot tell a crash from a stop",
        )
    if i.handled_generation == i.last_generation:
        return Decision("none", "this outage was already handled")
    if i.lock_state != "none":
        return Decision("none", f"mlx.lock is {i.lock_state or 'not observed'}")
    if i.evidence == "behind":
        return Decision("wait", "log ingestion is catching up")
    if i.shutdown_seen:
        return Decision("mark_off", "MLX stopped gracefully outside the admin")
    if i.evidence != "complete":
        return Decision("suspend", f"stop evidence unavailable (log {i.evidence})")
    # a timestamp in the future means the wall clock went back: count it (conservative)
    recent = [c for c in i.crashes if c > i.now_wall - i.window_s]
    if recent:
        minutes = int(i.window_s // 60)
        return Decision("suspend", f"second MLX crash within {minutes} min", record_crash=True)
    return Decision("restart", "MLX crashed", record_crash=True)


def _gen_key(gen: dict[str, Any] | None) -> str:
    return f"{gen['pid']}@{gen['started']}" if gen else "none"


class Supervisor:
    def __init__(
        self,
        settings: Settings,
        control: ControlService,
        alerts: AlertStore,
        events: IngestStore,
        logs: HostLogs,
        probes: ProbeRunner,
        sampler: SamplerLike,
        mlx: MlxClient,
        *,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        backend_health: Callable[[dict[str, Any] | None], dict[str, dict[str, Any]]] | None = None,
        connect: Callable[..., Any] = socket.create_connection,
    ) -> None:
        self.settings = settings
        self.control = control
        self.store = control.store
        self.alerts = alerts
        self.events = events
        self.logs = logs
        self.probes = probes
        self.sampler = sampler
        self.mlx = mlx
        self.clock = clock
        self.mono = mono
        self.connect = connect
        self.backend_health = backend_health or (lambda observed: {})
        self.evaluator = Evaluator(alerts, events)
        self._startup_until = mono() + settings.watchdog_grace_s
        self._outage_since: float | None = None
        self._last_litellm: float | None = None
        self._last_cleanup = 0.0
        self._task: asyncio.Task[None] | None = None
        self.last_decision: Decision | None = None
        self._listeners: list[TickListener] = []

    def add_listener(self, listener: TickListener) -> None:
        """Reuse this tick's bounded observation (M4 availability, log maintenance)."""
        self._listeners.append(listener)

    # --- state -------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.watchdog_enabled,
            "suspended": self.control.recovery_blocked(),
            "pending": self.store.get_kv(PENDING_KEY),
            "last_decision": self.last_decision.__dict__ if self.last_decision else None,
        }

    async def _probe(self, key: str, fn: Callable[[], Any]) -> Any:
        res = await self.probes.run(f"watch:{key}", fn, self.settings.probe_timeout_s)
        if res.state != "done":
            if res.state == "error":
                log.warning("probe %s failed: %s", key, res.error)
            return None
        return res.value

    # --- one tick -------------------------------------------------------------------
    async def tick(self) -> None:
        wall, mono = self.clock(), self.mono()
        polled: dict[str, PollResult] | None = await self._probe("ingest", self.logs.poll_all)
        if polled:
            self._download_events(polled, wall)
        drive = await self._probe("drive", self.mlx.drive_mounted)
        access = await self._probe("access", lambda: self.mlx.access_error() or "")
        observed = await self._probe("observe", self.control.observe) if drive else None
        lock = await self._probe("lock", lambda: lock_info(self.settings)) if drive else None
        engine = observed["engine"] if observed else "unknown"
        serving = None
        if engine == "mlx":
            health = await self.mlx.health()
            serving = is_serving(health)
        intent_row = self.control.intent()
        intent = intent_row["value"] if intent_row else None
        job_busy = self.control.runner.running() is not None
        snap = Snapshot(
            wall=wall,
            mono=mono,
            drive_mounted=drive,
            access_error=access or None,
            access_known=access is not None,
            engine=engine,
            mlx_serving=serving,
            intent=intent,
            job_running=job_busy,
            lock=lock,
            pressure=self.sampler.current().get("pressure"),
            litellm_ok=await self._litellm(mono),
            suspended=self.control.recovery_blocked(),
            backends=self.backend_health(observed),
        )
        self._finish_pending(snap)
        await self._watch(snap, observed, polled, lock)
        # evaluate after the decision, so a graceful stop that the watchdog just marked
        # off (or a suspension it just set) is not reported as a stale state for a tick
        intent_row = self.control.intent()
        snap = replace(
            snap,
            intent=intent_row["value"] if intent_row else None,
            suspended=self.control.recovery_blocked(),
        )
        self.evaluator.evaluate(snap)
        for listener in self._listeners:
            try:
                result = listener(snap, observed)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # one listener must not stop monitoring or the others
                log.exception("monitor listener failed")
        if wall - self._last_cleanup > 3600:
            self._last_cleanup = wall
            self.alerts.cleanup(wall, self.settings.alert_retention_days)
            self.events.prune_events(wall - 7 * 86400)

    async def _litellm(self, mono: float) -> bool | None:
        interval = self.settings.litellm_check_interval_s
        if self._last_litellm is not None and mono - self._last_litellm < interval:
            return None
        self._last_litellm = mono
        res = await self._probe(
            "litellm", lambda: probe_tcp(self.settings.litellm_base_url, self.connect)
        )
        return None if res is None else res[0] == "ok"

    def _download_events(self, polled: dict[str, PollResult], wall: float) -> None:
        for source, result in polled.items():
            for ev in result.events:
                if ev["kind"] != "download_done":
                    continue
                failed = ev["data"]["failed"]
                if failed:
                    self.alerts.event(
                        "download_failed",
                        "warning",
                        f"Download run failed ({source})",
                        f"{failed} file(s) failed; finished {ev['data']['finished']}.",
                        wall,
                        dedup=f"{source}:{ev['id']}",
                    )
                else:
                    self.alerts.event(
                        "download_finished",
                        "info",
                        f"Download run finished ({source})",
                        f"Finished {ev['data']['finished']}.",
                        wall,
                        dedup=f"{source}:{ev['id']}",
                    )

    def _finish_pending(self, snap: Snapshot) -> None:
        pending = self.store.get_kv(PENDING_KEY)
        if not pending:
            return
        job = self.control.runner.get(pending["job_id"])
        status = job["status"] if job else "interrupted"
        if status == "running":
            return
        gen = _gen_key(pending.get("generation"))
        if status == "succeeded":
            if snap.engine == "mlx" and snap.mlx_serving is not True:
                return  # restarted but not yet confirmed healthy
            if snap.engine == "mlx":
                self.alerts.event(
                    "engine_restarted",
                    "warning",
                    "Watchdog restarted MLX",
                    f"MLX generation {gen} stopped without a graceful-stop marker; the"
                    f" restart (job {pending['job_id']}) is serving.",
                    snap.wall,
                    dedup=pending["job_id"],
                )
        else:
            self.control.suspend(f"watchdog restart failed (job {pending['job_id']}: {status})")
            self.alerts.event(
                "engine_restart_failed",
                "critical",
                "Watchdog restart of MLX failed",
                f"Job {pending['job_id']} ended {status}; automatic recovery is suspended.",
                snap.wall,
                dedup=pending["job_id"],
            )
        self.store.delete_kv(PENDING_KEY)

    async def _watch(
        self,
        snap: Snapshot,
        observed: dict[str, Any] | None,
        polled: dict[str, PollResult] | None,
        lock: dict[str, Any] | None,
    ) -> None:
        last_gen = self.store.get_kv(GENERATION_KEY)
        if observed and observed["engine"] == "mlx" and observed["generation"]:
            self._outage_since = None
            if observed["generation"] != last_gen:
                self.store.set_kv(GENERATION_KEY, observed["generation"])
            self.last_decision = Decision("none", "MLX is running")
            return
        if snap.engine == "none" and (snap.intent or "").startswith("mlx:"):
            if self._outage_since is None:
                self._outage_since = snap.mono
        else:
            self._outage_since = None
        inp = WatchInput(
            enabled=self.settings.watchdog_enabled,
            recovery_eligible=self.control.backends.recovery_eligible(snap.intent or ""),
            intent=snap.intent,
            engine=snap.engine,
            drive_ok=snap.drive_mounted,
            job_busy=snap.job_running,
            lock_state=lock["state"] if lock else None,
            suspended=snap.suspended,
            pending=self.store.get_kv(PENDING_KEY),
            last_generation=last_gen,
            handled_generation=self.store.get_kv(HANDLED_KEY),
            outage_for_s=None if self._outage_since is None else snap.mono - self._outage_since,
            in_startup_grace=snap.mono < self._startup_until,
            evidence=self._evidence(polled, last_gen),
            shutdown_seen=self._shutdown_seen(last_gen),
            crashes=self.store.get_kv(CRASHES_KEY) or [],
            now_wall=snap.wall,
            grace_s=self.settings.watchdog_grace_s,
            window_s=self.settings.watchdog_crash_window_s,
        )
        decision = decide(inp)
        self.last_decision = decision
        await self._apply(decision, inp, snap)

    def _evidence(self, polled: dict[str, PollResult] | None, gen: dict[str, Any] | None) -> str:
        result = (polled or {}).get(HostLogs.MLX_SOURCE)
        if result is None or not result.ok:
            return "unavailable"
        if not result.caught_up:
            return "behind"
        cursor = self.events.cursor(HostLogs.MLX_SOURCE)
        gap = cursor["gap_at"] if cursor else None
        if gen and gap is not None and gap >= gen["started"]:
            return "gap"
        return "complete"

    def _shutdown_seen(self, gen: dict[str, Any] | None) -> bool:
        if not gen:
            return False
        rows = self.events.events(
            kinds=("shutdown",), since=gen["started"] - 1, source=HostLogs.MLX_SOURCE
        )
        return any(r["data"].get("pid") == gen["pid"] for r in rows)

    async def _apply(self, d: Decision, inp: WatchInput, snap: Snapshot) -> None:
        gen = inp.last_generation
        if d.action in ("none", "wait"):
            return
        log.warning("watchdog: %s (%s)", d.action, d.reason)
        if d.action == "mark_off":
            self.control.set_intent("off", "stopped outside the admin")
            self.alerts.event(
                "stopped_outside_admin",
                "info",
                "MLX was stopped outside the admin",
                f"A graceful stop of generation {_gen_key(gen)} was logged (for example"
                " mlx-model stop over SSH). Intended engine set to off; no restart.",
                snap.wall,
                dedup=_gen_key(gen),
            )
            self.store.set_kv(HANDLED_KEY, gen)
            return
        if d.action == "suspend":
            if d.record_crash:
                self.store.set_kv(CRASHES_KEY, [*inp.crashes, snap.wall])
            self.control.suspend(d.reason)
            self.store.set_kv(HANDLED_KEY, gen)
            return
        assert d.action == "restart" and inp.intent is not None
        try:
            job = await self.control.lifecycle("start", inp.intent[4:], source="watchdog")
        except ControlError as exc:
            # a manual action or a blocker won the race; decide again next tick
            log.warning("watchdog restart not submitted: %s", exc.message)
            return
        self.store.set_kv(CRASHES_KEY, [*inp.crashes, snap.wall])
        self.store.set_kv(HANDLED_KEY, gen)
        self.store.set_kv(PENDING_KEY, {"job_id": job["id"], "generation": gen})

    # --- background loop ----------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # one bad tick must not stop monitoring
                log.exception("monitor tick failed")
            await asyncio.sleep(self.settings.monitor_interval_s)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._startup_until = self.mono() + self.settings.watchdog_grace_s
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
