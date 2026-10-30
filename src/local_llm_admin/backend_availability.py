"""Backend-scoped observed availability, with no historical MLX reinterpretation."""

from __future__ import annotations

from typing import Any

from .alerts import Snapshot
from .availability import summarize
from .storage import Database

STATES = (
    "serving",
    "loading",
    "released",
    "recovering",
    "down",
    "off",
    "maintenance",
    "conflict",
    "unknown",
)
MAX_SAMPLES = 20_000


def backend_state(snap: Snapshot, source: str) -> dict[str, Any]:
    descriptor = snap.backends.get(source, {})
    wanted, _, profile = (snap.intent or "").partition(":")
    # Strata's released intent contract intentionally omits its pack.
    if source == "strata" and wanted == source and not profile:
        profile = descriptor.get("profile") or ""
    intended = None if not snap.intent or snap.intent == "unknown" else int(wanted == source)
    state = "unknown"
    if snap.drive_mounted is True and snap.engine != "unknown":
        if snap.engine == "conflict":
            state = "conflict"
        elif snap.job_running:
            state, intended = "maintenance", 0
        elif wanted != source:
            state = "off" if intended is not None else "unknown"
        elif snap.engine != source or descriptor.get("enabled") is False:
            state = "down"
        elif (profile or source == "splash") and descriptor.get("profile") != profile:
            state = "unknown"
        elif descriptor.get("ready") is True:
            state = "serving"
        else:
            state = {"starting": "loading", "released": "released", "recovering": "recovering"}.get(
                descriptor.get("state", ""), "unknown"
            )
    return {
        "state": state,
        "intended": intended,
        "profile": profile if wanted == source else None,
        "fingerprint": descriptor.get("fingerprint")
        if descriptor.get("profile") == profile
        else None,
        "epoch": descriptor.get("epoch"),
    }


class BackendAvailabilityStore(Database):
    def add(self, source: str, ts: float, sample: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO backend_availability (source, ts, profile, fingerprint, state, intended, epoch)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    source,
                    ts,
                    sample.get("profile"),
                    sample.get("fingerprint"),
                    sample["state"],
                    sample.get("intended"),
                    sample.get("epoch"),
                ),
            )

    def query(
        self,
        source: str,
        frm: float,
        to: float,
        *,
        interval_s: float,
        tz_offset_min: int,
        now: float,
        profile: str | None = None,
    ) -> dict[str, Any]:
        args: list[Any] = [source, frm - 2 * interval_s, to]
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM backend_availability WHERE source=? AND ts>=? AND ts<?"
                " ORDER BY ts DESC, id DESC LIMIT ?",
                (*args, MAX_SAMPLES + 1),
            ).fetchall()
        samples = [dict(r) for r in rows[:MAX_SAMPLES]]

        def report(items: list[dict[str, Any]]) -> dict[str, Any]:
            selected = {r["id"] for r in items}
            # Keep every transition as a boundary, even outside the requested scope.
            bounded = [
                r if r["id"] in selected else {**r, "state": "unknown", "intended": None}
                for r in samples
            ]
            return summarize(
                bounded,
                frm,
                to,
                interval_s=interval_s,
                tz_offset_min=tz_offset_min,
                now=now,
                states=STATES,
                intended_key="intended",
            )

        scoped = [r for r in samples if profile is None or r["profile"] == profile]
        profiles = sorted({(r["profile"] or "", r["fingerprint"] or "") for r in scoped})
        return {
            "source": source,
            **report(scoped),
            "truncated": len(rows) > MAX_SAMPLES,
            "semantics": "loaded serving / observed intended; released/recovering are not serving; maintenance excluded",
            "profiles": [
                {
                    "profile": p or None,
                    "fingerprint": fp or None,
                    **report(
                        [
                            r
                            for r in samples
                            if (r["profile"] or "", r["fingerprint"] or "") == (p, fp)
                        ]
                    ),
                }
                for p, fp in profiles
            ],
        }

    def prune(self, before: float, limit: int = 5000) -> int:
        with self._conn() as c:
            return c.execute(
                "DELETE FROM backend_availability WHERE id IN (SELECT id FROM backend_availability"
                " WHERE ts<? ORDER BY id LIMIT ?)",
                (before, limit),
            ).rowcount
