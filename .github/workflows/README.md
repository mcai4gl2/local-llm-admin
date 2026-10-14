# GitHub Actions workflows

Every job sets `timeout-minutes`, runs with `permissions: contents: read`, and cancels older
runs of the same ref (`concurrency`).

| Workflow | Job | Timeout | Purpose |
|----------|-----|---------|---------|
| `ci.yml` | `lint` | 10 min | `uv sync --frozen --all-extras`, `ruff check src tests scripts`, `mypy src scripts` (strict) |
| `ci.yml` | `test` | 20 min | pytest with coverage (`--cov-fail-under=80`); htmlcov uploaded as an artifact for 30 days |
| `ci.yml` | `agent-docs` | 5 min | `cmp AGENTS.md CLAUDE.md` |
| `ci.yml` | `summary` | 5 min | step summary table; fails if any job did not succeed |
| `public-guard.yml` | `gitleaks` | 10 min | pinned gitleaks binary (checksum-verified) over the full history with `.gitleaks.toml` |
| `public-guard.yml` | `denylist` | 5 min | `scripts/check_public.py` over tracked files, commit messages, identities and history, with patterns from the `PUBLIC_DENYLIST` secret |

## Triggers

- **ci**: push to `main`, pull requests, weekly (Friday 00:00 UTC), manual.
- **public-guard**: every push, pull requests, manual. Fork pull requests get no secrets, so
  the `denylist` job skips with a notice and the next push to `main` runs it.

## Repository settings

- Secret scanning and push protection on.
- `main` protected; required checks: `ci` (summary) and `public-guard` (gitleaks, denylist).
- Default `GITHUB_TOKEN` read-only.
- One secret: `PUBLIC_DENYLIST` (private; kept outside this repository).
