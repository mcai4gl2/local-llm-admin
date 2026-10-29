"""``local-llm-admin``: run the admin with uvicorn, plus the sidecar gateway when a sidecar is enabled."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn

from .app import create_app
from .config import Settings
from .gateway import create_gateway_app

DEFAULT_ENV_FILE = Path.home() / ".config" / "local-llm-admin" / "env"


async def serve_all(servers: Sequence[Any]) -> None:
    """Run several uvicorn servers in one event loop (they share the admin's objects)."""
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    parser = argparse.ArgumentParser(prog="local-llm-admin")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(os.environ.get("LOCAL_LLM_ADMIN_ENV", DEFAULT_ENV_FILE)),
    )
    args = parser.parse_args()
    env_file = args.env_file if args.env_file.is_file() else None
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    inference_enabled = bool(settings.inference_api_key.get_secret_value())
    sidecars_enabled = settings.decision_enabled or settings.tts_enabled or settings.embed_enabled
    if inference_enabled:
        native_ports = (settings.mlx_port, settings.strata_port, settings.splash_port)
        if settings.inference_port in native_ports or len(set(native_ports)) != 3:
            raise ValueError("Shared inference requires distinct native engine ports.")
        if settings.inference_port in {
            args.port,
            settings.decision_gateway_port,
            7860,
            8188,
            8081,
            8083,
            8084,
        }:
            raise ValueError("Shared inference port conflicts with another service.")
        for base, port in zip(
            (settings.mlx_api_base, settings.strata_api_base, settings.splash_api_base),
            native_ports,
            strict=True,
        ):
            url = urlsplit(base)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.port != port
                or url.path not in {"", "/"}
                or url.query
                or url.fragment
                or url.username
            ):
                raise ValueError("Shared inference requires matching native API bases and ports.")
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(settings.mlx_root / "bin/engine-ports"),
                    str(settings.mlx_root / "etc/engine-ports.json"),
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            expected = dict(zip(("mlx", "strata", "splash"), native_ports, strict=True))
            if result.returncode != 0 or json.loads(result.stdout) != expected:
                raise ValueError("native host topology mismatch")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise ValueError(
                "Shared inference native ports must match the private host configuration."
            ) from None
    app = create_app(settings)
    if not (sidecars_enabled or inference_enabled):
        uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)
        return
    servers = [
        uvicorn.Server(
            uvicorn.Config(app, host=args.host, port=args.port, log_level="info", access_log=False)
        )
    ]
    if sidecars_enabled:
        gateway = create_gateway_app(
            settings,
            app.state.decision,
            app.state.decision_keys,
            app.state.decision_history,
            registry=app.state.sidecars,
            tts=app.state.tts if settings.tts_enabled else None,
            tts_history=app.state.tts_history,
            embed=app.state.embed if settings.embed_enabled else None,
            embed_history=app.state.embed_history,
            embed_reason=app.state.embed_reason,
        )
        servers.append(
            uvicorn.Server(
                uvicorn.Config(
                    gateway,
                    host=settings.decision_gateway_host,
                    port=settings.decision_gateway_port,
                    log_level="info",
                    access_log=False,
                    lifespan="off",
                )
            )
        )
    if inference_enabled:
        servers.append(
            uvicorn.Server(
                uvicorn.Config(
                    app.state.inference,
                    host=settings.inference_host,
                    port=settings.inference_port,
                    log_level="info",
                    access_log=False,
                    lifespan="off",
                )
            )
        )
    asyncio.run(serve_all(servers))


if __name__ == "__main__":
    main()
