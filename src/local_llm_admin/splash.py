"""Splash owning-record observations and bounded HTTP; no runtime imports."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import psutil

from .backend_types import EngineObservation, OwnedProcess, generation_record
from .config import Settings
from .splash_profiles import ProfileError, SplashProfile, load_profiles, read_ownership

MAX_STATUS_BYTES = 1_048_576


async def _async_document(http: httpx.AsyncClient, path: str) -> Any:
    async with http.stream("GET", path) as response:
        if response.status_code != 200:
            return None
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > MAX_STATUS_BYTES:
                return None
            body.extend(chunk)
        return json.loads(body)


def _sync_document(http: httpx.Client, path: str, timeout: float) -> Any:
    with http.stream("GET", path, timeout=timeout) as response:
        if response.status_code != 200:
            return None
        body = bytearray()
        for chunk in response.iter_bytes():
            if len(body) + len(chunk) > MAX_STATUS_BYTES:
                return None
            body.extend(chunk)
        return json.loads(body)


class SplashClient:
    def __init__(self, settings: Settings, transport: httpx.MockTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    def profiles(self, *, require_assets: bool = False) -> Mapping[str, SplashProfile]:
        return load_profiles(
            self.settings.splash_profiles_path,
            self.settings.splash_root,
            require_assets=require_assets,
        )

    def argv(self, action: str, name: str | None = None) -> list[str]:
        if action == "start" and name in self.profiles():
            return [str(self.settings.splash_path), action, str(name)]
        if action == "stop" and name is None:
            return [str(self.settings.splash_path), action]
        raise ValueError("unsupported Splash operation or profile")

    def _possible(self) -> set[int]:
        root = self.settings.splash_root / "runtime"
        if not self.settings.splash_root.exists():
            return set()
        found = set()
        for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
            try:
                exe = proc.info.get("exe")
                args = proc.info.get("cmdline") or []
                if (
                    exe
                    and Path(exe).is_relative_to(root)
                    and ("server.server" in args or "serve-native" in args)
                ):
                    found.add(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return found

    def _member(self, item: Mapping[str, Any], prepared: str) -> OwnedProcess | None:
        pid, role = int(item["pid"]), str(item["role"])
        try:
            proc = psutil.Process(pid)
            if proc.status() == psutil.STATUS_ZOMBIE:
                return None
            started = proc.create_time()
            expected = (
                self.settings.splash_root
                / "runtime"
                / ("python/bin/python3" if role == "controller" else "engine/splash")
            ).resolve()
            args = proc.cmdline()
            marker = any(args[i : i + 2] == ["-m", "server.server"] for i in range(len(args) - 1))
            if (
                not expected.is_relative_to((self.settings.splash_root / "runtime").resolve())
                or Path(proc.exe()).resolve() != expected
                or Path(item["executable"]).resolve() != expected
                or abs(started - float(item["started"])) >= 1.0
                or prepared not in args
                or (not marker if role == "controller" else "serve-native" not in args)
            ):
                raise ProfileError("Splash executable or generation identity is unknown")
            return OwnedProcess(
                pid, started, str(expected), "controller" if role == "controller" else "native"
            )
        except psutil.NoSuchProcess:
            return None

    def observe(self) -> EngineObservation:
        root = self.settings.splash_root
        try:
            record = read_ownership(root)
            if record is None:
                if self._possible():
                    raise ProfileError("unrecorded Splash process")
                return EngineObservation("splash", state="absent")
            controller = self._member(record["controller"], record["prepared"])
            owned = {
                p.pid: p
                for item in record["native"]
                if (p := self._member(item, record["prepared"])) is not None
            }
            if controller is not None:
                owned[controller.pid] = controller
                for child in psutil.Process(controller.pid).children():
                    item = {
                        "pid": child.pid,
                        "started": child.create_time(),
                        "executable": str(root / "runtime/engine/splash"),
                        "role": "native",
                    }
                    member = self._member(item, record["prepared"])
                    if member is not None:
                        owned[member.pid] = member
            if self._possible() - owned.keys():
                raise ProfileError("unverified Splash descendant")
            if not owned:
                return EngineObservation("splash", state="absent")
            if self.settings.splash_enabled:
                profile = self.profiles().get(record["profile"])
                if profile is None or profile.fingerprint != record["fingerprint"]:
                    raise ProfileError("Splash profile changed outside lifecycle control")
            primary = controller or next(iter(owned.values()))
            return EngineObservation(
                "splash",
                record["profile"],
                {
                    "pid": primary.pid,
                    "started": primary.started,
                    "profile_fingerprint": record["fingerprint"],
                },
                "starting",
                False,
                "verified",
                "verified",
                tuple(sorted(owned.values(), key=lambda p: (p.role, p.pid))),
            )
        except (ProfileError, psutil.Error, OSError, ValueError, TypeError, KeyError):
            return EngineObservation("splash", state="unknown")

    def _headers(self) -> dict[str, str]:
        key = self.settings.splash_api_key.get_secret_value()
        return {"Authorization": "Bearer " + key} if key else {}

    def _result(self, observed: EngineObservation, bodies: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "enabled": self.settings.splash_enabled,
            "running": observed.running,
            "state": observed.state,
            "ready": False,
            "profile": observed.reference,
            "reference": observed.reference,
            "pid": observed.generation.get("pid") if observed.generation else None,
            "model": None,
            "max_context": None,
            "ownership": observed.ownership,
        }
        if not bodies or observed.ownership != "verified":
            return result
        ready, health, models = bodies
        if not isinstance(health, dict) or not isinstance(models, dict):
            return result
        controller = next((p for p in observed.processes if p.role == "controller"), None)
        instance = health.get("instance")
        try:
            profile = self.profiles().get(observed.reference or "")
        except ValueError:
            result["state"] = "unknown"
            return result
        if (
            profile is None
            or controller is None
            or not isinstance(instance, dict)
            or type(instance.get("pid")) is not int
            or instance.get("pid") != controller.pid
            or instance.get("model") != profile.model
        ):
            result["state"] = "unknown"
            return result
        rows = models.get("data")
        rows = rows if isinstance(rows, list) else []
        by_id = {
            row["id"]: row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        expected = {profile.model, *profile.aliases}
        kv = health.get("kv")
        weights = health.get("weights", {})
        if (
            type(health.get("maximum_context_tokens")) is not int
            or health.get("maximum_context_tokens") != profile.max_context
            or not expected <= by_id.keys()
            or any(
                type(by_id[name].get("max_model_len")) is not int
                or by_id[name]["max_model_len"] != profile.max_context
                for name in expected
            )
            or not isinstance(kv, dict)
            or kv.get("format") != profile.kv_format
            or health.get("state_format") != profile.state_format
            or not isinstance(weights, dict)
        ):
            result["state"] = "unknown"
            return result
        result.update(
            model=profile.model,
            max_context=profile.max_context,
            fingerprint=profile.fingerprint,
            api_verified=True,
        )
        weights = health.get("weights")
        weights = weights if isinstance(weights, dict) else {}
        if weights.get("restoring") is True:
            result["state"] = "recovering"
        elif weights.get("released") is True:
            result["state"] = "released"
        elif ready == {"status": "ready"} and health.get("ready") is True:
            result.update(state="ready", ready=True)
        return result

    def _needs_http(self, observed: EngineObservation) -> bool:
        return observed.ownership == "verified" and any(
            p.role == "controller" for p in observed.processes
        )

    async def status(self) -> dict[str, Any]:
        if not self.settings.splash_enabled:
            return {"enabled": False, "running": False, "ready": False, "state": "absent"}
        observed = await asyncio.to_thread(self.observe)
        bodies: list[Any] = []
        if self._needs_http(observed):
            try:
                async with asyncio.timeout(self.settings.probe_timeout_s):
                    async with httpx.AsyncClient(
                        base_url=self.settings.splash_api_base,
                        headers=self._headers(),
                        timeout=self.settings.probe_timeout_s,
                        transport=self.transport,
                    ) as http:
                        for path in ("/ready", "/status", "/v1/models"):
                            bodies.append(await _async_document(http, path))
            except (httpx.HTTPError, ValueError, TimeoutError):
                bodies = []
            fresh = await asyncio.to_thread(self.observe)
            if (
                generation_record(fresh) != generation_record(observed)
                or fresh.ownership != "verified"
            ):
                observed = replace(fresh, state="unknown", ownership="unknown")
                bodies = []
        return self._result(observed, bodies)

    def ready_sync(self) -> bool:
        observed = self.observe()
        if not self.settings.splash_enabled or not self._needs_http(observed):
            return False
        try:
            with httpx.Client(
                base_url=self.settings.splash_api_base,
                headers=self._headers(),
                timeout=self.settings.probe_timeout_s,
                transport=self.transport,
            ) as http:
                bodies = []
                deadline = time.monotonic() + self.settings.probe_timeout_s
                for path in ("/ready", "/status", "/v1/models"):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    bodies.append(_sync_document(http, path, remaining))
            fresh = self.observe()
            return bool(
                generation_record(fresh) == generation_record(observed)
                and fresh.ownership == "verified"
                and self._result(fresh, bodies)["ready"]
            )
        except (httpx.HTTPError, ValueError, OSError):
            return False

    def down_sync(self) -> bool:
        if self.observe().running:
            return False
        try:
            with socket.create_connection(("127.0.0.1", self.settings.splash_port), timeout=0.2):
                return False
        except OSError:
            return True
