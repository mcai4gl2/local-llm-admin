#!/usr/bin/python3
"""Strict private profile data for the pinned Splash runtime; no runtime imports."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

RUNTIME_VERSION = "1.4.0"
RUNTIME_SHA256 = "4c0f032a5392a6c559f081a0052278fbc0ea5abee2d16b820a015cc9b1826a2d"
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_HEX = re.compile(r"[a-f0-9]{64}")
_REV = re.compile(r"[a-f0-9]{40}")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")


class ProfileError(ValueError):
    """Messages deliberately exclude private file contents and paths."""


@dataclass(frozen=True)
class Asset:
    path: Path = field(repr=False)
    revision: str
    sha256: str


@dataclass(frozen=True)
class SplashProfile:
    id: str
    label: str
    cohort: str
    model: str
    aliases: tuple[str, ...]
    target: Asset = field(repr=False)
    draft: Asset = field(repr=False)
    prepared: Path = field(repr=False)
    max_context: int
    memory_gib: int
    kv_format: str
    state_format: str
    reasoning: bool
    fingerprint: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "cohort": self.cohort,
            "max_context": self.max_context,
            "reasoning": self.reasoning,
            "fingerprint": self.fingerprint,
        }


def runtime_precision_matches(document: object, *, kv_format: str, state_format: str) -> bool:
    """Check pinned runtime config; top-level kv/state objects hold allocation counters."""
    if not isinstance(document, dict):
        return False
    identity, plan = document.get("identity"), document.get("memory_plan")
    if not isinstance(identity, dict) or not isinstance(plan, dict):
        return False
    kv, model = identity.get("kv"), plan.get("model")
    # The planner's context capacity differs from the configured serving limit.
    return bool(
        isinstance(kv, dict)
        and kv.get("format") == kv_format
        and plan.get("valid") is True
        and isinstance(model, dict)
        and model.get("kv_format") == kv_format
        and model.get("state_format") == state_format
    )


@dataclass(frozen=True)
class SplashEnv:
    api_key: str = field(repr=False)
    host: str = "127.0.0.1"
    allowed_hosts: tuple[str, ...] = ()
    port: int = 8080


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError("duplicate JSON key")
        result[key] = value
    return result


def private_text(path: Path) -> str:
    try:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ProfileError("private Splash file must be a regular mode-600 file")
        if not path.is_file() or path.stat().st_size > 1_048_576:
            raise ProfileError("invalid private Splash file")
        return path.read_text()
    except (OSError, UnicodeError):
        raise ProfileError("cannot read private Splash file") from None


def _keys(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ProfileError("invalid Splash object fields")
    return value


def _text(value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ProfileError("invalid Splash value")
    return value


def profile_name(value: Any) -> str:
    return _text(value, _ID)


def model_alias(value: Any) -> str:
    # Matches the pinned server's alias grammar; absolute paths are not aliases.
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or value.startswith("-")
        or any(not c.isprintable() or c.isspace() or c in "\\%?#" for c in value)
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ProfileError("invalid Splash model alias")
    return value


def _absolute(value: Any) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise ProfileError("Splash asset roots and paths must be absolute")
    return Path(value).resolve()


def _asset(value: Any, roots: tuple[Path, ...], require_assets: bool) -> Asset:
    doc = _keys(value, {"path", "revision", "sha256"})
    path = _absolute(doc["path"])
    if not any(path.is_relative_to(root) for root in roots):
        raise ProfileError("Splash asset outside allowed roots")
    asset = Asset(path, _text(doc["revision"], _REV), _text(doc["sha256"], _HEX))
    if require_assets and (
        not path.is_dir()
        or not (path / "config.json").is_file()
        or not any(path.glob("*.safetensors"))
    ):
        raise ProfileError("incomplete Splash asset")
    return asset


def _receipt(root: Path, profile: SplashProfile) -> None:
    try:
        doc = json.loads(
            private_text(root / "etc/receipts" / (profile.id + ".json")), object_pairs_hook=_object
        )
    except (ValueError, RecursionError):
        raise ProfileError("invalid Splash preparation receipt") from None
    expected = {
        "runtime_sha256": RUNTIME_SHA256,
        "prepared": str(profile.prepared),
        "target_sha256": profile.target.sha256,
        "draft_sha256": profile.draft.sha256,
        "target_revision": profile.target.revision,
        "draft_revision": profile.draft.revision,
    }
    if doc != expected:
        raise ProfileError("Splash preparation receipt does not match profile assets")


def load_profiles(
    path: Path, root: Path, *, require_assets: bool = True
) -> Mapping[str, SplashProfile]:
    try:
        doc = json.loads(private_text(path), object_pairs_hook=_object)
        doc = _keys(
            doc, {"schema_version", "runtime_version", "runtime_sha256", "asset_roots", "profiles"}
        )
        if (
            type(doc["schema_version"]) is not int
            or doc["schema_version"] != 1
            or doc["runtime_version"] != RUNTIME_VERSION
            or doc["runtime_sha256"] != RUNTIME_SHA256
        ):
            raise ProfileError("unsupported Splash runtime or schema")
        if not isinstance(doc["asset_roots"], list) or not doc["asset_roots"]:
            raise ProfileError("Splash requires explicit asset roots")
        roots = tuple(_absolute(p) for p in doc["asset_roots"])
        if any(p == Path("/") for p in roots):
            raise ProfileError("Splash asset root is too broad")
        if not isinstance(doc["profiles"], dict) or not 1 <= len(doc["profiles"]) <= 32:
            raise ProfileError("invalid Splash profile inventory")
        result = {}
        for name, raw in doc["profiles"].items():
            profile_name(name)
            raw = _keys(
                raw,
                {
                    "label",
                    "cohort",
                    "model",
                    "aliases",
                    "target",
                    "draft",
                    "prepared",
                    "max_context",
                    "memory_gib",
                    "kv_format",
                    "state_format",
                    "reasoning",
                },
            )
            label = raw["label"]
            if (
                not isinstance(label, str)
                or not 1 <= len(label) <= 100
                or any(not c.isprintable() or c in "<>" for c in label)
            ):
                raise ProfileError("invalid Splash profile label")
            if raw["cohort"] not in ("stock-8bit", "modified-4bit"):
                raise ProfileError("unknown Splash checkpoint cohort")
            model = model_alias(raw["model"])
            if len(model.split("/")) != 2:
                raise ProfileError("Splash model must be an owner/repository ID")
            if not isinstance(raw["aliases"], list) or not 1 <= len(raw["aliases"]) <= 32:
                raise ProfileError("Splash requires explicit served aliases")
            aliases = tuple(model_alias(a) for a in raw["aliases"])
            if len(set(aliases)) != len(aliases):
                raise ProfileError("duplicate Splash alias")
            if (
                type(raw["max_context"]) is not int
                or raw["max_context"] not in (32768, 131072)
                or type(raw["memory_gib"]) is not int
                or not 1 <= raw["memory_gib"] <= 256
                or raw["kv_format"] != "bf16"
                or raw["state_format"] != "fp32"
                or raw["reasoning"] is not True
            ):
                raise ProfileError("unsupported Splash profile settings")
            prepared = (root / "prepared" / _text(raw["prepared"], _ID)).resolve()
            if not prepared.is_relative_to((root / "prepared").resolve()):
                raise ProfileError("Splash prepared directory escapes managed root")
            if require_assets and not (prepared / "tokenizer").is_dir():
                raise ProfileError("incomplete Splash prepared output")
            profile = SplashProfile(
                name,
                label,
                raw["cohort"],
                model,
                aliases,
                _asset(raw["target"], roots, require_assets),
                _asset(raw["draft"], roots, require_assets),
                prepared,
                raw["max_context"],
                raw["memory_gib"],
                "bf16",
                "fp32",
                True,
                hashlib.sha256(
                    json.dumps(
                        {
                            "profile": raw,
                            "prepared": str(prepared),
                            "target": str(_absolute(raw["target"]["path"])),
                            "draft": str(_absolute(raw["draft"]["path"])),
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest()[:20],
            )
            if require_assets:
                _receipt(root, profile)
            result[name] = profile
        if len({p.prepared for p in result.values()}) != len(result):
            raise ProfileError("Splash profiles must have separate prepared outputs")
        return MappingProxyType(result)
    except (ValueError, TypeError, RecursionError, OSError):
        raise ProfileError("invalid private Splash profiles") from None


def read_env(path: Path) -> SplashEnv:
    values: dict[str, str] = {}
    for line in private_text(path).splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if (
            not sep
            or key not in {"SPLASH_API_KEY", "SPLASH_HOST", "SPLASH_ALLOWED_HOSTS", "SPLASH_PORT"}
            or key in values
        ):
            raise ProfileError("invalid Splash env fields")
        values[key] = value
    key = values.get("SPLASH_API_KEY", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", key):
        raise ProfileError("Splash requires an explicit API key")
    host = values.get("SPLASH_HOST", "127.0.0.1")
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        raise ProfileError("Splash bind must be an IPv4 address") from None
    allowed = tuple(values.get("SPLASH_ALLOWED_HOSTS", "").split(","))
    if allowed == ("",):
        allowed = ()
    if any(not _HOST.fullmatch(h) for h in allowed):
        raise ProfileError("invalid Splash allowed host")
    port = values.get("SPLASH_PORT", "8080")
    if not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535:
        raise ProfileError("invalid Splash serving port")
    return SplashEnv(key, host, allowed, int(port))


def read_ownership(root: Path) -> dict[str, Any] | None:
    path = root / "run/splash.json"
    if path.is_symlink():
        raise ProfileError("Untrusted ownership record link.")
    if not path.exists():
        return None
    try:
        value = json.loads(private_text(path))
        if (
            not isinstance(value, dict)
            or set(value) - {"port"}
            != {"version", "profile", "fingerprint", "model", "prepared", "controller", "native"}
            or type(value.get("port", 8080)) is not int
            or not 1 <= value.get("port", 8080) <= 65535
            or value["version"] != 1
            or not isinstance(value["native"], list)
            or (root / "prepared").resolve() not in Path(value["prepared"]).resolve().parents
        ):
            raise ProfileError("Invalid private ownership record.")
        if (
            not isinstance(value["profile"], str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value["profile"])
            or not isinstance(value["fingerprint"], str)
            or not re.fullmatch(r"[a-f0-9]{20}", value["fingerprint"])
            or len(value["native"]) > 4
            or value["controller"].get("role") != "controller"
            or any(p.get("role") != "native" for p in value["native"])
        ):
            raise ProfileError("Invalid private ownership metadata.")
        model_alias(value["model"])
        for item in [value["controller"], *value["native"]]:
            if (
                not isinstance(item, dict)
                or set(item) != {"pid", "started", "executable", "role"}
                or type(item["pid"]) is not int
                or item["pid"] <= 0
                or type(item["started"]) not in (int, float)
                or item["started"] <= 0
                or not math.isfinite(item["started"])
                or item["role"] not in ("controller", "native")
            ):
                raise ProfileError("Invalid private generation record.")
        return value
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ProfileError("Splash ownership record cannot be verified.") from None
