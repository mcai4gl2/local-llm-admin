"""``local-llm-admin``: run the admin with uvicorn, plus the sidecar gateway when a sidecar is enabled."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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
    app = create_app(settings)
    if not (settings.decision_enabled or settings.tts_enabled or settings.embed_enabled):
        uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)
        return
    # M6: the gateway is its own app on its own port (no session middleware, no admin
    # routes) that reuses the admin's sidecar client, key store and history
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
    servers = [
        uvicorn.Server(
            uvicorn.Config(app, host=args.host, port=args.port, log_level="info", access_log=False)
        ),
        uvicorn.Server(
            uvicorn.Config(
                gateway,
                host=settings.decision_gateway_host,
                port=settings.decision_gateway_port,
                log_level="info",
                access_log=False,
                lifespan="off",
            )
        ),
    ]
    asyncio.run(serve_all(servers))


if __name__ == "__main__":
    main()
