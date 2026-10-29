# local-llm-admin — agent instructions

`AGENTS.md` is canonical; `CLAUDE.md` is an identical copy (CI runs `cmp AGENTS.md CLAUDE.md`).
Edit both together.

## Project

A FastAPI web admin for a Mac mini that serves local LLMs with MLX: engine control, metrics,
model lifecycle, quality runs, sidecars (decision, TTS, embeddings), logs and LiteLLM usage.
Code in `src/local_llm_admin/`, tests in `tests/`, Mac-side scripts in `host/`, deploy scripts
in `ops/`, design in `docs/DESIGN.md`.

## This repository is public

Never write real IP addresses, usernames, hostnames, home paths, LiteLLM key aliases, user
ids, tokens or other deployment facts into tracked files, including plans, devlogs, fixtures,
test data, commit messages and comments. Use placeholders:

| Kind | Placeholder |
|------|-------------|
| The Mac | `mac-mini.local`, `you@mac-mini.local`, `/Users/you` |
| Other LAN hosts | `192.0.2.x` (RFC 5737), or `10.0.0.x` in tests |
| LiteLLM | `http://litellm.example.com:4000`, key aliases `app-a-key`, users `user-a` |

Real values live only in your local `ops/deploy.env` (gitignored) and the Mac's env file.
Before staging, run the leak scanner with your private pattern list:

```bash
uv run python scripts/check_public.py --patterns-file ~/.config/local-llm-admin/public-denylist.txt --git .
```

## Run everything in the dev container

Never run project commands on the host.

```bash
docker ps                                    # find the local-llm-admin container
docker exec -u vscode -w /workspaces/local-llm-admin <container-id> <command>
```

Always pass `-u vscode`; without it files end up owned by root.

## Python environment

- `uv sync --all-extras` inside the container. Never create a venv on the host.
- Run tools through uv: `uv run pytest`, `uv run ruff`, `uv run mypy`.

## Quality gates

- `uv run pytest --cov --cov-fail-under=80` (coverage at least 80%; keep critical paths at 100%).
- `uv run ruff check src tests scripts`
- `uv run mypy src scripts` (strict)
- Type hints on all functions.
- New behaviour gets a test first; a bug fix gets a test that failed before the fix.

## Plans and devlogs

Follow the plan lifecycle in `plans/README.md` automatically when starting, updating or
finishing planned work:

- Keep one public-safe plan per coherent change in `plans/active/YYYYMMDD_<name>.md`.
  Update that plan as work progresses; do not create a new plan for each checkpoint,
  test run or push. Small routine edits do not need a separate plan.
- Put proposed or deferred work in `plans/backlog/`, grouped by component when useful,
  with its status and next decision explicit. Move a scoped plan into `plans/active/`
  when implementation begins.
- When implementation is complete, move its plan into `plans/archive/<component>/`.
  Record the outcome and leave only a concise follow-up in backlog for deferred scope;
  do not leave completed implementation plans in active while PR checks finish.
- Update the active, backlog and relevant archive indexes, and fix links whenever a
  plan moves. Keep `plans/README.md` as the navigation entry point.
- Preserve original failed, partial or inconclusive investigation outcomes. Archiving
  does not imply qualification passed. Put current behavior and operating instructions
  in `docs/` and host operating guides; track delivery and CI status in the PR checks.

- Reusable deployment tools live in `ops/`; experiment bundles live in `experiments/`.
  Sanitized rollout evidence lives under `docs/reports/`; do not rewrite frozen JSON outcomes.
- After every change, append to `devlogs/YYYY-MM-DD.md` (append-only):

```markdown
# YYYY-MM-DD

## Feature/Fix Name

### Problem/Goal
### Solution/Implementation
### Results
### Files Modified

## Lessons Learned
## Next Steps
```

## Git policy

- Never commit: only stage with `git add`. Commit only when the user explicitly says so in the
  session. Commit messages never carry co-author trailers.
- Never use git worktrees (`git worktree add` or any other mechanism); work in the repo directly.

## Live Mac changes need approval

`ops/deploy-mac.sh`, `ops/rollback-mac.sh`, `ops/deploy-host.sh --apply` and
`ops/apply-mlx-model-guard.sh` change a running service. Ask before running them. Read-only
checks (`ops/host-diff.sh`, `ops/deploy-host.sh` without `--apply`) are fine.
The target comes from `MAC_HOST`/`MAC_DEST` (environment, `ops/deploy.env` or `DEPLOY_ENV`);
never hard-code it.
