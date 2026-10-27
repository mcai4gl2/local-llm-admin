Overall Progress: 0% implementation (planning complete)

# Splash Phase 1 — shared backend contract

Date: 2026-10-27
Status: Ready to implement. Phase 0 feasibility accepted by the user; remaining experiments
are deferred. This plan authorizes no live deployment or production engine change.

## Decision and outcome

The user considers baseline feasibility established and asked to move on from Phase 0.
Proceed with development of a shared backend contract, then an opt-in Splash integration.
This supersedes the parent plan's requirement that every adoption gate pass before any
backend integration work begins. It does not turn failed, inconclusive or unassessed
measurements into passes. Production adoption remains a separate decision.

Phase 0 evidence and diagnostics are committed in `7405b83`. Preserve the reports and raw
sample references. Do not schedule more benchmark windows as a prerequisite for this phase.
The 32K context scope remains sufficient. Keep stock 8-bit and modified 4-bit identities
separate; reasoning-on is the measured reliable mode, while off-mode findings remain visible.
The isolated MLX unload fix is not installed, and stock Splash's available-memory check is
still inconclusive. Neither investigation is a dependency of this repository refactor.

Deliver one registry-based lifecycle and observation path for the existing MLX, Strata and
ComfyUI engines. Preserve their public target strings, lifecycle job slot, rollback behavior,
service intent and inference behavior. The next phase should add Splash by implementing an
adapter and host wrapper rather than extending boolean lists throughout the coordinator.

## Scope

In scope: small typed contracts; adapters over current clients; a static registry; collection
of engine observations; target validation; transition planning; shared target resolution for
status, chat and current-engine quality runs; resource-policy lookup; recovery eligibility.

Keep `off` as a transition target and `none`, `unknown` and `conflict` as observation results,
not runnable backend entries. Keep ComfyUI in engine control without pretending it supports
LLM chat. MLX remains the default; Strata's existing enable flag remains effective.

Defer Splash installation/wrappers/selection to Phase 2 and its usage ingestion/rollout to
Phase 3. No plugin discovery, inference proxy, generalized downloader, simultaneous full
engine residency, new benchmark series, runtime source patch or database migration is
required here. Existing sidecar coordination stays in place; move its engine policy lookup
without redesigning every service. Add storage only if implementation proves it necessary.

## Code reviewed and concrete changes

| Current location | Finding | Phase 1 change |
|---|---|---|
| `src/local_llm_admin/engines.py` | `Target`, `EngineOps`, `resolve_active`, `badge_state` and `plan_transition` encode individual engines; `_stop_engine` falls through to MLX | Introduce shared types/registry, explicit dispatch and observation-based planning; retain the pure planner and `Transition` result |
| `src/local_llm_admin/control.py` | `EngineOpsBinding` binds separate methods; `engine()` collects booleans and claims the lifecycle slot; observation and intent are engine-specific | Construct adapters once, collect normalized observations under the claim, resolve targets/policy centrally and preserve intent serialization |
| `src/local_llm_admin/jobs.py` | `Step` sequences, rollback boundary, reservations and persistence already coordinate lifecycle | Reuse these contracts; do not add another executor or job slot |
| `src/local_llm_admin/mlx.py` and current clients in `engines.py` | Owning CLIs, PID/port observations and HTTP readiness have different meanings | Wrap current clients; distinguish identity, listener ownership and serving readiness; preserve MLX in-engine switch and Strata pack restart semantics |
| `src/local_llm_admin/app.py`, `chat.py`, `quality_runner.py` | Status/chat target and quality scope/context branch on the active engine | Resolve common observations/inference metadata through adapters; retain existing payload settings and scope restrictions |
| `src/local_llm_admin/sidecars.py`, `watchdog.py` | Existing exclusion rules, service restoration and MLX-only recovery are explicit | Read equivalent policy/capabilities from the registry; preserve current MLX-only automatic recovery |
| `alerts.py`, `availability.py`, `logs.py`, templates and `static/app.js` | Consume engine names, states and existing response fields | Audit consumers; keep response compatibility and make only changes needed to use the shared resolver |

Use `src/local_llm_admin/backends.py` for the initial types, registry and adapters. Split it
only if a concrete import cycle or module size warrants it. Keep existing client classes
and compatibility imports during migration; avoid moving files merely to match a diagram.

## Minimal contract

These names guide implementation; keep the public boundary smaller if existing types suffice.

| Type | Required responsibility |
|---|---|
| `EngineTarget` (evolve existing `Target`) | Backend ID and validated model/pack/profile reference; lossless serialization of existing target strings |
| `EngineSpec` | Stable ID/label, enable state, supported operations, start/stop deadlines, coexistence and recovery policy |
| `EngineObservation` | Backend, selected/loaded reference when known, verified generation, observed state, readiness and identity/ownership confidence; unavailable fields remain unknown |
| `EngineBackend` protocol | Validate/list targets, observe, build lifecycle `Step` sequences and resolve optional inference/log metadata through existing clients |
| `BackendRegistry` | Explicit immutable lookup of configured adapters; reject duplicate/unknown IDs and disabled activation without fallback |
| Inference descriptor | Existing `ChatTarget` plus supported request-setting/context resolution, only for LLM-capable adapters |

Reuse `ProcessStatus`, `process_generation`, `ChatTarget`, `Step` and `Transition` where they
fit. Keep model identity distinct from runtime profile: an MLX registered model name and a
Strata pack are not interchangeable. A richer model-asset inventory may be added with Splash
profiles in Phase 2; do not create a second model registry for this refactor.

Normalize absent, starting, ready, released/recovering and unknown observations without
requiring every backend to support every state. HTTP liveness alone cannot establish backend
identity or loaded readiness. PID plus creation time identifies a generation; a reused PID
must not authorize a stop. An unowned shared-port listener is a conflict/unknown condition,
not evidence for MLX and not permission to kill it. A disabled backend cannot be started;
its known process ownership must still prevent unsafe activation of another engine. Avoid
HTTP polling of disabled backends just to populate the selector.

Keep unknown backend IDs as explicit errors, including internal dispatch paths. Current
recognized multi-engine conflict handling may stop verified known owners; preserve its
characterized behavior. Unverified/ambiguous ownership must block a new start. Continue to
revalidate ownership at execution boundaries where a process could change after planning.

## Implementation milestones

Each milestone is a reviewable change on the current repository branch. Work in the existing
checkout; no worktrees. New behavior gets a failing test before implementation. Reuse the
substantial current tests rather than rebuilding an exhaustive test framework.

### 1. Capture existing behavior and add the typed registry

- [ ] Extend `tests/test_engine_plan.py`, `test_engines.py` and `test_control_strata.py`
  only where coverage is missing: target parsing, all existing engine pairs/off, same-engine
  switches, Strata default/explicit pack, stop/readiness failure and rollback boundary.
- [ ] Characterize ComfyUI busy/unknown queue confirmation, optional service restoration,
  forced sidecar stops, disabled Strata and the existing conflict-recovery cases.
- [ ] Add `tests/test_backends.py` for registry uniqueness, lookup, target validation,
  capabilities and unknown/disabled behavior; include a fake adapter to prove extension
  without another engine-specific planner parameter. The fake is test-only.
- [ ] Implement the small contracts and adapters over `MlxClient`, `StrataClient` and
  `ComfyClient`, with no lifecycle behavior change yet.

Exit: current behavior is pinned by meaningful tests; the three adapters share a typed
interface and no unknown ID can silently dispatch a command to MLX.

### 2. Migrate lifecycle planning and control

- [ ] Replace per-engine running/ready arguments with normalized observation inputs and
  registry dispatch; preserve step order, deadlines, `rollback_after` and optional waits.
  Use a temporary compatibility entry point only while migrating existing callers, then
  remove it once unused.
- [ ] Migrate `EngineOpsBinding`, `ControlService.engine()` and shared observation helpers.
  Claim the existing job slot before observations/preflight; retain drive/CLI lock checks,
  target validation and release-on-error. Do not call an unrelated backend's wrapper.
- [ ] Preserve start/switch/stop intent, Strata pack identity, job/history/API fields and
  interrupted-job recovery. A failed/incomplete job must not assert the requested target
  as the observed engine. Do not rewrite historical rows to fit the new types.
- [ ] Test stale/reused generations, occupied shared port, failed target stop during
  rollback, cancellation, concurrent submissions and interrupted jobs. Never restart the
  source while the failed target is still running. Preserve sidecar/service restoration.

Exit: lifecycle uses the registry end to end, with existing transition behavior and critical
ownership/rollback paths fully covered. Adding a fake registered backend requires no new
`*_running` argument or coordinator dispatch branch.

### 3. Migrate consumers without changing product behavior

- [ ] Resolve `/api/status`, badges and engine choices from common observations; preserve
  current fields and labels. Keep capabilities accurate and disabled choices unavailable.
- [ ] Route chat target, context and effective request settings through the adapter, keeping
  stream cancellation, credentials, usage handling and current MLX/Strata defaults intact.
  ComfyUI/off/conflict must continue to refuse LLM chat.
- [ ] Migrate current-engine compatibility/eval target resolution and generation checks in
  `quality_runner.py`; preserve MLX multi-model runs, Strata current-only scope, original
  engine restoration and refusal to overwrite an external operator's switch.
- [ ] Read coexistence and recovery policy through the registry. Preserve MLX sidecar
  allowlists and Strata/ComfyUI exclusivity. Watchdog restart remains MLX-only with the
  same maintenance/job/intent checks. Observation of released/recovering state must not
  itself trigger a second recovery loop.
- [ ] Audit log/alert/availability and telemetry consumers for assumptions that every LLM
  is MLX. Preserve storage/deduplication and do not introduce new telemetry ingestion.

Exit: the shared contract reaches lifecycle, status, chat and quality without broad API/UI
or storage changes. The existing engine, chat, quality, sidecar and watchdog suites pass.

### 4. Validate, document and hand off to Splash integration

- [ ] Run focused tests after each change and the complete quality gates once the refactor
  is integrated. Keep critical ownership, dispatch and rollback paths at 100% coverage.
- [ ] Update `docs/DESIGN.md` with the contract and extension points; append the devlog,
  scan tracked and new files with the private denylist, and stage only intended changes.
- [ ] Record a concise behavior comparison and identify the exact adapter/wrapper work
  needed for Phase 2. Present a repository change ready for review; do not deploy it.

Exit: a tested shared backend foundation, existing engine behavior preserved, and no live
service or default changed. Phase 1 does not require additional Mac performance experiments.

## Validation

Run everything in the devcontainer as `vscode`, through the existing uv-managed environment.
Use fake wrappers and `httpx.MockTransport` for lifecycle/HTTP behavior; no live Mac is needed.
Prioritize existing `test_engine_plan.py`, `test_engines.py`, `test_control*.py`,
`test_engine_api.py`, `test_jobs.py`, `test_chat.py`, `test_quality*.py`, `test_sidecar*.py`
and `test_watchdog.py`, plus the new registry tests. Test public request/response and intended
step behavior, not private class layout. Verify persisted intent and job/history round trips.

```bash
uv run pytest --cov --cov-fail-under=80
uv run ruff check src tests scripts
uv run mypy src scripts
cmp AGENTS.md CLAUDE.md
uv run python scripts/check_public.py --patterns-file ~/.config/local-llm-admin/public-denylist.txt --git .
```

Explicitly pass new untracked files to the public scanner before staging. The last Phase 0
validation was 2,784 passed, one skipped, 98.11% coverage; this is the starting baseline, not
proof of the future refactor. This planning change introduces no application behavior and
does not need a repeat of that full suite.

## Following phases and deferred work

**Phase 2: opt-in Splash adapter and host integration.** Build `host/splash/bin/splash` with
wrapper tests, validated private profiles, pinned runtime inventory and generation-safe
parent/native-child start/stop. Add reciprocal host exclusion guards and a `SplashBackend`;
keep it disabled by default, initially at 32K with GPU sidecars excluded. Add selector,
readiness/idle state, chat and current-engine quality support through the Phase 1 contract.
Use the measured reliable reasoning-on setting for the first profile, with off-mode limits
recorded. Verify served aliases and the actual authenticated gateway request before live
use on the shared endpoint. Repository implementation can proceed before that live check.

**Phase 3: observability and controlled rollout.** Adapt supported Splash counters/logs to
existing storage with explicit source/coverage and deduplication. Present the exact deploy
diff, rollback and bounded real-service verification before requesting live approval. Do
not change the default automatically. Start with the user's supported interactive profile;
qualify broader capabilities only when they are needed.

Keep the remaining Phase 0 questions as a deferred backlog: broader quality precision,
replay p95, concurrency 2/4, memory-contract refinement, installed MLX unload-fix adoption,
and any requested GPU sidecar coexistence. Actual authenticated gateway inference remains a
live-use acceptance check. No deferred item becomes a measured pass, but none forces another
open-ended Phase 0 campaign before Phase 1 development.

## First next action

Start milestone 1: add the missing characterization cases and failing registry tests, then
implement the typed registry and adapters around the current clients. Do not start a new
benchmark run or modify a live host.
