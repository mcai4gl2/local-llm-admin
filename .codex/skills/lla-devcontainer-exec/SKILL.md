---
name: lla-devcontainer-exec
description: Run local-llm-admin commands in its devcontainer by detecting the running container and using docker exec with -u vscode -w /workspaces/local-llm-admin.
---

# local-llm-admin Devcontainer Exec

## When to use
Use this skill whenever a task needs shell commands (tests, lint, scripts, git) and container mode is expected.

## Workflow
1. Detect the running container.
   - `docker ps --format "{{.ID}}\t{{.Image}}\t{{.Names}}" | grep -m1 local-llm-admin`
2. If none is found, ask the user to open the repository in its devcontainer. Do not fall back to the host.
3. Build the command prefix.
   - `docker exec -u vscode -w /workspaces/local-llm-admin <container-id>`
4. Run the requested command with that prefix and report concise output and failures.

## Rules
- Always include `-u vscode` to prevent root-owned files.
- Always include `-w /workspaces/local-llm-admin` for consistent relative paths.
- Run Python tools through uv (`uv run …`), never the container-global interpreter.
- Live Mac scripts (`ops/deploy-mac.sh`, `ops/rollback-mac.sh`, `ops/deploy-host.sh --apply`) need explicit user approval.

## Examples
- Tests: `docker exec -u vscode -w /workspaces/local-llm-admin <id> uv run pytest --cov`
- Lint: `docker exec -u vscode -w /workspaces/local-llm-admin <id> uv run ruff check src tests scripts`
- Drift check (read-only): `docker exec -u vscode -w /workspaces/local-llm-admin <id> ops/host-diff.sh`
