"""Preview-first, single-route LiteLLM administration with private rollback receipts."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx


class RouteError(ValueError):
    """An unsafe, stale or ambiguous route operation was refused."""


def _base(value: str, *, gateway: bool = False) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") != ("/v1" if gateway else "")):
        raise RouteError("Invalid API base; use an HTTP(S) origin and the documented path.")
    try:
        _ = parsed.port
    except ValueError:
        raise RouteError("Invalid API port.") from None
    return value.rstrip("/")


def route_template(gateway_base: str, key_env: str = "SIDECAR_GATEWAY_API_KEY") -> dict[str, Any]:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key_env):
        raise RouteError("Use an environment variable reference for the gateway key.")
    return {
        "model_name": "local-ocr",
        "litellm_params": {
            "model": "openai/local-ocr", "api_base": _base(gateway_base, gateway=True),
            "api_key": "os.environ/" + key_env, "timeout": 300,
            "num_retries": 0, "max_retries": 0,
        },
        "model_info": {"mode": "chat", "supports_vision": True, "supports_pdf_input": True,
                       "supports_streaming": False, "input_cost_per_token": 0,
                       "output_cost_per_token": 0},
    }


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _save(path: Path, body: dict[str, Any], *, exclusive: bool = False) -> None:
    if path.is_symlink() or exclusive and path.exists():
        raise RouteError("Rollback receipt already exists or is a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".ocr-route-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(body, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class OcrRouteManager:
    def __init__(
        self, base: str, admin_key: str, *, transport: httpx.BaseTransport | None = None,
        gateway_key: tuple[str, str] | None = None,
    ) -> None:
        self.base = _base(base)
        self.gateway_key = gateway_key
        self.client = httpx.Client(base_url=self.base, headers={"Authorization": "Bearer " + admin_key},
                                   transport=transport, timeout=30)

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if (body and self.gateway_key and body.get("litellm_params", {}).get("api_key")
                == "os.environ/" + self.gateway_key[0]):
            body = copy.deepcopy(body)
            body["litellm_params"]["api_key"] = self.gateway_key[1]
        try:
            response = self.client.request(method, path, json=body)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise RouteError("LiteLLM returned invalid management metadata.")
            return result
        except (httpx.HTTPError, ValueError):
            raise RouteError("LiteLLM operation failed; inspect the private receipt before retrying.") from None

    def _current(self) -> dict[str, Any] | None:
        result = self._request("GET", "/model/info")
        if (not isinstance(result, dict) or not isinstance(result.get("data"), list)
                or any(not isinstance(model, dict) for model in result["data"])):
            raise RouteError("LiteLLM model inventory is invalid.")
        matching = [model for model in result["data"] if model.get("model_name") == "local-ocr"]
        if len(matching) > 1:
            raise RouteError("Multiple local-ocr deployments require manual reconciliation.")
        return copy.deepcopy(matching[0]) if matching else None

    def preview(self, desired: dict[str, Any], *, rollback: dict[str, Any] | None = None) -> dict[str, Any]:
        params = desired.get("litellm_params", {})
        expected = route_template(params.get("api_base", ""),
                                  params.get("api_key", "").removeprefix("os.environ/"))
        if desired != expected:
            raise RouteError("Use the fixed OCR route template.")
        if urlsplit(params["api_base"]).netloc == urlsplit(self.base).netloc:
            raise RouteError("The OCR route must not point back to LiteLLM.")
        current = self._current()
        previous = None
        if current:
            info = current.get("model_info", {})
            if not isinstance(info.get("id"), str) or not info["id"] or not info.get("db_model"):
                raise RouteError("Only a database route with a known rollback identity can be updated.")
            # Inventories may redact credentials. Never infer an old credential or API base.
            previous = rollback
            if previous is None:
                raise RouteError("An existing route requires a complete private rollback definition.")
            if (previous.get("model_name") != "local-ocr"
                    or not all(previous.get("litellm_params", {}).get(key)
                               for key in ("model", "api_base", "api_key"))):
                raise RouteError("The private rollback definition is incomplete.")
            if any(not set(desired[key]) <= set(previous.get(key, {}))
                   for key in ("litellm_params", "model_info")):
                raise RouteError("The rollback definition cannot remove newly added PATCH fields.")
            if self.gateway_key and previous["litellm_params"]["api_key"] == "os.environ/" + self.gateway_key[0]:
                raise RouteError("The private rollback key must preserve the previous credential explicitly.")
            previous = copy.deepcopy(previous)
            previous["model_info"] = {**previous.get("model_info", {}), "id": info["id"]}
        return {"schema_version": 1, "target": _fingerprint(self.base),
                "action": "update" if current else "create", "observed": current,
                "observed_hash": _fingerprint(current), "before": previous, "desired": copy.deepcopy(desired)}

    def apply(self, preview: dict[str, Any], snapshot: Path) -> None:
        if preview.get("target") != _fingerprint(self.base):
            raise RouteError("The preview belongs to a different LiteLLM target.")
        if _fingerprint(self._current()) != preview["observed_hash"]:
            raise RouteError("The OCR route changed after preview; make a fresh preview.")
        receipt = {**copy.deepcopy(preview), "state": "prepared"}
        _save(snapshot, receipt, exclusive=True)
        try:
            if preview["action"] == "create":
                result = self._request("POST", "/model/new", preview["desired"])
                identity = result.get("model_info", {}).get("id")
            else:
                identity = preview["observed"]["model_info"]["id"]
                self._request("PATCH", "/model/" + quote(identity, safe="") + "/update", preview["desired"])
            current = self._current()
            if not current or not identity or current.get("model_info", {}).get("id") != identity:
                raise RouteError("Could not verify the applied OCR route identity.")
            receipt.update(state="applied", applied=current, applied_hash=_fingerprint(current), id=identity)
            _save(snapshot, receipt)
        except RouteError:
            receipt["state"] = "unknown"
            _save(snapshot, receipt)
            raise

    def rollback(self, snapshot: Path) -> None:
        if snapshot.is_symlink() or snapshot.stat().st_mode & 0o077:
            raise RouteError("Rollback receipt must be a private regular file.")
        receipt = json.loads(snapshot.read_text())
        if receipt.get("target") != _fingerprint(self.base) or receipt.get("state") != "applied":
            raise RouteError("Rollback receipt is not an applied operation for this target.")
        if _fingerprint(self._current()) != receipt["applied_hash"]:
            raise RouteError("The OCR route changed; refusing to overwrite another operation.")
        receipt["state"] = "rolling_back"
        _save(snapshot, receipt)
        try:
            if receipt["action"] == "create":
                self._request("POST", "/model/delete", {"id": receipt["id"]})
                if self._current() is not None:
                    raise RouteError("OCR deletion could not be verified.")
            else:
                self._request("PATCH", "/model/" + quote(receipt["id"], safe="") + "/update",
                              receipt["before"])
                if not self._current():
                    raise RouteError("OCR restoration could not be verified.")
            receipt["state"] = "rolled_back"
            _save(snapshot, receipt)
        except RouteError:
            receipt["state"] = "unknown"
            _save(snapshot, receipt)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("LITELLM_BASE_URL"))
    parser.add_argument("--gateway-base")
    parser.add_argument("--key-env", default="SIDECAR_GATEWAY_API_KEY")
    parser.add_argument("--resolve-key-env", action="store_true",
                        help="Read the gateway key locally and send it privately; avoids a proxy environment restart.")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--previous-definition", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    try:
        if not args.base or not os.environ.get("LITELLM_ADMIN_KEY"):
            raise RouteError("Set LITELLM_BASE_URL and LITELLM_ADMIN_KEY privately.")
        if args.resolve_key_env:
            if not os.environ.get(args.key_env):
                raise RouteError("The local gateway key environment variable is missing.")
            manager = OcrRouteManager(args.base, os.environ["LITELLM_ADMIN_KEY"],
                                      gateway_key=(args.key_env, os.environ[args.key_env]))
        else:
            manager = OcrRouteManager(args.base, os.environ["LITELLM_ADMIN_KEY"])
        if args.rollback:
            if not args.snapshot:
                raise RouteError("Rollback requires --snapshot.")
            manager.rollback(args.snapshot)
            print("OCR route rollback verified.")
            return 0
        previous = json.loads(args.previous_definition.read_text()) if args.previous_definition else None
        preview = manager.preview(route_template(args.gateway_base or "", args.key_env), rollback=previous)
        print(json.dumps({"model_name": "local-ocr", "action": preview["action"],
                          "observed_hash": preview["observed_hash"],
                          "desired": preview["desired"],
                          "desired_hash": _fingerprint(preview["desired"])}))
        if args.apply:
            if not args.snapshot:
                raise RouteError("Apply requires a new private --snapshot path.")
            manager.apply(preview, args.snapshot)
            print("OCR route apply verified; private rollback receipt saved.")
        return 0
    except (RouteError, OSError, ValueError):
        print("OCR route operation refused or incomplete; check configuration and private receipt.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
