---
name: lla-venv-exec
description: Run Python commands for local-llm-admin through its uv-managed .venv inside the devcontainer, syncing first when the environment is missing or stale.
---

# local-llm-admin venv exec

## When to use
Use for any Python command: tests, type checks, lint, the app itself, or scripts under `scripts/` and `ops/`.

## Workflow
1. Work inside the devcontainer (see `lla-devcontainer-exec`).
2. If `.venv/` is missing, or `pyproject.toml`/`uv.lock` changed, run `uv sync --frozen --all-extras`.
3. Run the command with `uv run <command>` (for example `uv run pytest tests/test_tts.py`).
4. Report the command and a concise result.

## Rules
- One project, one environment: the repo-root `.venv/`, managed by uv. Never create another venv, and never on the host.
- Do not `pip install` into the venv; add dependencies to `pyproject.toml` and run `uv lock` (ask first).
- Use `--frozen` in syncs so `uv.lock` is not rewritten by accident.
