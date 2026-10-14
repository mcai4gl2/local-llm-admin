"""Per-model profiles (M3c): read and change them only through ``mlx-model profile``.

The host helper owns the allow-list (types, ranges, enums) and the files; the admin
reads its schema, shows saved versus running settings, and adds one model-specific
check it alone can make: a ``reasoning_effort`` default is accepted only if that value
passed the model's M2 compatibility check. Saving never restarts a model: the running
server keeps its flags until the next start, and the page shows the mismatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from collections.abc import Callable
from typing import Any

from .config import Settings
from .control import ControlService
from .mlx import MlxClient, validate_name
from .quality_store import QualityStore

log = logging.getLogger("local_llm_admin")

CHAT_CACHE_S = 10.0
Cli = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


class ProfileError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
    )


def build_profile_argv(
    bin_path: Any, action: str, name: str | None = None, doc: Any = None
) -> list[str]:
    """Fixed argv; the profile travels as one JSON argument, never through a shell."""
    if action in ("schema", "list"):
        if name is not None or doc is not None:
            raise ValueError(f"{action} takes no arguments")
        return [str(bin_path), "profile", action]
    if action not in ("show", "set", "reset", "revert") or name is None:
        raise ValueError(f"unsupported profile action: {action}")
    argv = [str(bin_path), "profile", action, validate_name(name)]
    if action == "set":
        argv.append(json.dumps(doc, allow_nan=False, separators=(",", ":")))
    elif doc is not None:
        raise ValueError(f"{action} takes no profile")
    return argv


def verified_efforts(compat: dict[str, dict[str, Any]] | None) -> list[str]:
    """reasoning_effort values that passed this model's compatibility check."""
    out = []
    for value in ("low", "medium", "high"):
        r = (compat or {}).get(f"reasoning_effort_{value}")
        if r and r.get("status") == "pass":
            out.append(value)
    return out


class ProfileService:
    def __init__(
        self,
        settings: Settings,
        mlx: MlxClient,
        control: ControlService,
        quality: QualityStore,
        *,
        cli: Cli = _run,
    ) -> None:
        self.settings = settings
        self.mlx = mlx
        self.control = control
        self.quality = quality
        self._cli = cli
        self._schema: dict[str, Any] | None = None
        self._chat_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _call(self, action: str, name: str | None = None) -> Any:
        argv = build_profile_argv(self.settings.mlx_model_path, action, name)
        try:
            proc = self._cli(argv, 30.0)
        except subprocess.TimeoutExpired:
            raise ProfileError(504, f"mlx-model profile {action} did not answer in time") from None
        except OSError as exc:
            raise ProfileError(503, f"cannot run mlx-model: {exc}") from None
        if proc.returncode != 0:
            raise ProfileError(
                400 if proc.returncode == 2 else 502,
                (proc.stderr or proc.stdout or f"profile {action} failed").strip(),
            )
        try:
            return json.loads(proc.stdout)
        except ValueError:
            raise ProfileError(502, f"mlx-model profile {action} returned no JSON") from None

    def schema(self) -> dict[str, Any]:
        if self._schema is None:
            self._schema = self._call("schema")
        return self._schema

    def fingerprints(self) -> dict[str, str]:
        try:
            value: dict[str, str] = self._call("list")
        except ProfileError as exc:
            log.warning("profile list failed: %s", exc.message)
            return {}
        return value

    def _known(self, name: str) -> None:
        try:
            validate_name(name)
        except ValueError:
            raise ProfileError(400, "invalid model name") from None
        if name not in {m["name"] for m in self.mlx.list_models()}:
            raise ProfileError(404, "unknown model name")

    def show(self, name: str) -> dict[str, Any]:
        self._known(name)
        profile = self._call("show", name)
        compat = self.quality.latest_compatibility().get(name) or {}
        env = self.mlx.active_env()
        running = env.get("MLX_ACTIVE_NAME") == name and self.mlx.engine_running()
        running_fp = env.get("MLX_PROFILE_FINGERPRINT") if running else None
        checked: set[str] = {
            str(r["profile_fingerprint"]) for r in compat.values() if r.get("profile_fingerprint")
        }
        return {
            "name": name,
            "schema": self.schema(),
            "profile": profile,
            "verified_reasoning_effort": verified_efforts(compat),
            "running": running,
            "running_fingerprint": running_fp,
            # a server started by an older mlx-model records no fingerprint: unknown
            "restart_required": running
            and running_fp is not None
            and running_fp != profile["fingerprint"],
            "checks_fingerprint": sorted(checked)[0] if len(checked) == 1 else None,
            "checks_stale": bool(checked) and checked != {profile["fingerprint"]},
        }

    def validate(self, name: str, doc: dict[str, Any]) -> None:
        """Model-specific checks on top of the host's allow-list."""
        effort = (doc.get("chat") or {}).get("reasoning_effort")
        if effort is not None:
            ok = verified_efforts(self.quality.latest_compatibility().get(name))
            if effort not in ok:
                raise ProfileError(
                    409,
                    f"reasoning_effort {effort!r} has not passed this model's compatibility"
                    " check (Quality page); verified values: " + (", ".join(ok) or "none"),
                )

    async def change(
        self, name: str, action: str, doc: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Save, reset or revert as a job: serialized with lifecycle and quality runs."""
        await asyncio.to_thread(self._known, name)
        if action == "set":
            assert doc is not None
            await asyncio.to_thread(self.validate, name, doc)
        try:
            argv = build_profile_argv(self.settings.mlx_model_path, action, name, doc)
        except ValueError as exc:
            raise ProfileError(400, str(exc)) from None
        self._chat_cache.pop(name, None)
        return await self.control.cli_job(
            "profile", name, argv, f"mlx-model profile {action} {name}"
        )

    def chat_defaults(self, name: str | None) -> dict[str, Any]:
        """The selected model's admin-chat defaults (cached briefly); {} on any error."""
        if not name:
            return {}
        hit = self._chat_cache.get(name)
        if hit and time.monotonic() - hit[0] < CHAT_CACHE_S:
            return hit[1]
        try:
            value = dict(self._call("show", name).get("chat") or {})
        except (ProfileError, ValueError) as exc:
            log.warning("chat defaults for %s unavailable: %s", name, exc)
            value = {}
        self._chat_cache[name] = (time.monotonic(), value)
        return value
