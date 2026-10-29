Overall Progress: 100%

# PR review: repository organization and shared serving

## Summary
Keep operational deployment tools discoverable, move experiment bundles out of ops,
separate current plans from backlog and historical implementation notes, and restore a
single OpenAI-compatible serving URL for engine switching through LiteLLM.

## Scope and constraints
Work on the existing PR branch and update the same PR. Preserve reproducible experiment
bundles and frozen evidence; do not rewrite past qualification outcomes. Keep devlogs
append-only. No live Mac or gateway configuration changes are part of this branch update.
All project work runs in the devcontainer. Existing session authorization covers commits
and push after validation.

## Tasks
- [x] 🟩 Inventory operational tools, experiment dependencies and plan status.
- [x] 🟩 Move experiment bundles and reports, repair references and add navigation indexes.
- [x] 🟩 Separate active, backlog and archived plans; document the new convention.
- [x] 🟩 Write failing shared-serving regressions for routing, model identity and refusals.
- [x] 🟩 Implement shared serving with ownership, authentication and cancellation preserved.
- [x] 🟩 Update operating/configuration and migration documentation.
- [x] 🟩 Run local coverage/focused gates, correct regressions, and verify lint/types/privacy.
- [x] 🟩 Prepare the same-PR delivery; keep final push/CI status in the PR checks.

## Test and performance plan
Run focused routing/host/config tests first, then uv run pytest --cov --cov-fail-under=80,
uv run ruff check src tests scripts, uv run mypy src scripts and standalone host coverage.
Exercise authenticated real-socket streaming, model translation or refusal, disconnection,
wrong-engine requests and engine switches. Preserve stream backpressure; no response
buffering, per-token database writes or inference benchmarks. Validate moved entry points
and relative links without rerunning historical experiments.

## Integration and observability
Admin settings/backend observations, owning wrappers and serving model identity, deploy
manifest, examples and current docs; experiment imports and plan/report references. Keep
existing metrics attributed to their verified native generation.

## Risks and rollback
The earlier dedicated endpoint avoided MLX clearing its loaded model on a foreign model
request. Shared serving must address that hazard and the pinned Splash alias grammar.
Unknown ownership and concurrent engines must still refuse. Revert the isolated serving
change if gates fail. Existing deployed receipts retain their original revision scope.

## Open question
Confirmed: preserve both the existing URL and upstream model ID. The shared listener
uses 8080; only native ports and private admin/host topology change during deployment.

## Implementation outcome
Repository organization and shared serving implementation are complete. The comprehensive
local run retained one compatibility-message failure with 3523 passes/two skips and
98.25% combined coverage; the corrected stop case and final routing/configuration/security
checks all pass (92 tests). Shared inference, native ports and Splash owning control have
100% statement/branch coverage. The shared listener is a separate app with no admin routes;
admin session middleware is unchanged. All 41 moved JSON files remain byte-identical.
Final remote full-suite results are maintained on PR #1 rather than adding checkpoint
plans or changing historical qualification. No live deployment was performed.
