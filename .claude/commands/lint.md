Run code quality checks (ruff, mypy) on a file inside the dev container.

## Usage
`/lint <file>` — e.g. `/lint src/local_llm_admin/config.py`

## Steps

1. Find the running dev container ID:
```bash
docker ps --format '{{.ID}}\t{{.Image}}\t{{.Names}}' | grep -m1 local-llm-admin
```

2. Run ruff, then mypy (strict, configured in `pyproject.toml`):
```bash
docker exec -u vscode -w /workspaces/local-llm-admin <container-id> uv run ruff check $ARGUMENTS
docker exec -u vscode -w /workspaces/local-llm-admin <container-id> uv run mypy $ARGUMENTS
```
mypy is for `src/` and `scripts/` files; tests are checked by ruff only.

3. Report each finding with `file:line`, and suggest fixes. Apply `ruff check --fix` only if the user asks.

**Always use `-u vscode`** to prevent file permission issues.
