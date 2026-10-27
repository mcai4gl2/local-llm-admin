Overall Progress: 0%

# Splash Phase 1 — shared backend contract

Date: 2026-10-27
Status: Implementation plan ready; no implementation started. Phase 0 feasibility accepted
by the user; further experiments deferred. Current planning baseline: `f3802b8`.

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

Put shared dataclasses/protocols in `src/local_llm_admin/backend_types.py` and the concrete
registry/adapters in `src/local_llm_admin/backends.py`. This avoids a concrete import cycle:
`backends.py` wraps clients currently in `engines.py`, while the planner in `engines.py`
needs shared contracts. `engines.py` may import `backend_types.py`, never `backends.py`.
Keep runtime dependencies of the types module limited to the standard library; use typing-only
imports for `Step`/`ChatTarget` if needed. Keep current client classes in place during Phase 1.

Create one registry per app/control-service instance from the existing injected clients and
Settings object. `ControlService` can accept an optional registry and build the equivalent
one from its existing constructor arguments for current callers/tests. App, quality and
monitoring consumers use that same instance. Registry membership is static, but enable flags,
timeouts and sidecar policy must read the current Settings object: existing tests mutate it
after service creation. Do not cache those settings permanently at construction.

## Minimal contract

Use frozen dataclasses and protocols with explicit types. Keep the existing `Target.kind`
and `Target.name` constructor contract as a compatibility alias/re-export; avoid a mass rename.
A registered ID is validated at lookup, including for targets created directly in Python.
These operations form the initial adapter seam; add methods only when a migrated caller
needs them:

| Type | Required responsibility |
|---|---|
| `EngineTarget` (evolve existing `Target`) | Backend ID and validated model/pack/profile reference; lossless serialization of existing target strings |
| `EngineSpec` | Stable ID/label, enable state, supported operations, start/stop deadlines, coexistence and recovery policy |
| `EngineObservation` | Backend, selected/loaded reference when known, verified generation, observed state, readiness and identity/ownership confidence; unavailable fields remain unknown |
| `EngineBackend` protocol | Validate/list targets, observe, build lifecycle `Step` sequences and resolve optional inference/log metadata through existing clients |
| `BackendRegistry` | Explicit immutable lookup of configured adapters; reject duplicate/unknown IDs and disabled activation without fallback |
| Inference descriptor | Existing `ChatTarget` plus supported request-setting/context resolution, only for LLM-capable adapters |

The adapter validates its target reference, provides cheap process/ownership observation,
provides bounded async readiness/details separately, constructs start/switch/stop `Step`
sequences, and resolves optional inference metadata. It does not claim job slots, write
intent, run a job, decide rollback or coordinate sidecars. Those remain coordinator concerns.
The planner accepts observations plus a typed registry lookup interface, so its module does
not depend on concrete adapters. It remains pure: no HTTP, filesystem reads or command runs.

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

## Implementation tasks and review boundaries

Execute packages 1–6 in order on the current branch/checkout. Each is a suggested commit
boundary, not authorization to commit automatically. For existing behavior, characterization
tests should pass before the refactor. For new behavior, first demonstrate a failing test,
then implement the smallest change that passes it. Distinguish intentional hardening of
unknown IDs/ownership from assertions about what the old implementation already does.

### 1. Characterize lifecycle behavior

Files: `tests/test_engine_plan.py`, `test_engines.py`, `test_control_strata.py`,
`test_control_sidecars.py` and `test_engine_api.py`; reuse `tests/conftest.py` fake wrappers.

- [ ] 🟥 Inventory existing assertions; add only missing cases for all existing engine-pair
  transitions/off, MLX in-engine switch, Strata default/explicit pack restart, same-target
  refusal, ComfyUI busy/unknown queue, forced stops and optional service restoration.
- [ ] 🟥 Pin step labels/argv/order, optional flags, deadlines, `rollback_after`, rollback
  after target-readiness failure and refusal to restart the source before target exit.
  Include MLX Chat UI ownership with its API down, not just the API-server PID.
- [ ] 🟥 Record persisted intent/history behavior, including plain `strata` intent versus
  the actual observed pack used for rollback. Do not accidentally introduce a storage change
  by treating an existing lossy intent field as a new profile store.

Run:

```bash
uv run pytest -q tests/test_engine_plan.py tests/test_engines.py tests/test_control_strata.py tests/test_control_sidecars.py tests/test_engine_api.py
```

Exit: existing behavior is pinned without production-code changes. Suggested commit:
`test: characterize existing engine lifecycle contracts`.

### 2. Add shared contracts, registry and existing-client adapters

Files: new `src/local_llm_admin/backend_types.py`, `backends.py`, new
`tests/test_backends.py`; compatibility re-exports in `engines.py` only as needed.

- [ ] 🟥 Add failing tests for duplicate/unknown IDs, direct invalid targets, current enable
  settings, model/pack validation, lifecycle deadlines and absent LLM capabilities. Assert
  unknown dispatch never builds an MLX command and disabled activation executes nothing.
- [ ] 🟥 Add failing adapter tests using existing clients/fake transports for readiness,
  wrapper command construction, Strata pack restart, MLX switch and inference/context
  metadata. Keep process observation separate from HTTP probing; unavailable data is unknown.
- [ ] 🟥 Implement the neutral types, static registry and three adapters with a typed lookup
  interface. Preserve existing client instances/transports and current Settings reads; no
  global registry, plugin discovery, additional model inventory or command executor.
- [ ] 🟥 Add a test-only fourth adapter and demonstrate registered target parsing/lookup and
  plan inputs without adding production engine flags. Verify the import graph is acyclic.

Run:

```bash
uv run pytest -q tests/test_backends.py tests/test_engines.py
uv run mypy src scripts
```

Exit: common contracts are usable alongside the old coordinator. Suggested commit:
`refactor: add typed engine registry and existing backend adapters`.

### 3. Make transition planning use observations and registry lookup

Files: `src/local_llm_admin/engines.py`, `backend_types.py`, `backends.py`,
`tests/test_engine_plan.py`, `tests/test_backends.py`.

- [ ] 🟥 Add failing planner tests for the fourth adapter, ambiguous/unowned shared-port
  listeners, conflicting ownership and unknown IDs. Require no target start while ownership
  is ambiguous; preserve existing recovery of verified known multi-engine conflicts.
- [ ] 🟥 Replace growing `mlx_running`/`strata_running` argument lists and fallback dispatch
  with observation inputs plus typed adapter lookup. Keep resource-policy decisions and
  rollback in the planner; adapters construct only their own steps. Preserve `Transition`.
- [ ] 🟥 Apply the characterization matrix to the new planner. Check all step order,
  rollback boundary, queue confirmation, force and service-intent effects. Add the fourth
  adapter to pair tests without adding a coordinator branch. Keep temporary compatibility
  shims only until package 4 migrates their final caller.

Run:

```bash
uv run pytest -q tests/test_engine_plan.py tests/test_backends.py tests/test_sidecar_plan.py
```

Exit: the pure planner is backend-independent; original engine outcomes remain equivalent
except for explicitly tested unknown-ID/ownership hardening. Suggested commit:
`refactor: plan engine transitions from backend observations`.

### 4. Wire the registry into control and preserve persistence

Files: `src/local_llm_admin/control.py`, `app.py`, `mlx.py` only for shared-port attribution
needed by the new observation path; `tests/test_control*.py`, `test_engine_api.py`,
`test_jobs.py`, `test_mlx.py` and `test_backends.py`. Keep `jobs.py` execution/storage intact.

- [ ] 🟥 Add failing tests for slot contention, observation/preflight failure, stale/reused
  generations and an owner changing before execution. Include known disabled-engine process
  ownership during activation: disabled means unavailable to start, not safe to ignore.
- [ ] 🟥 Construct/inject the shared registry and migrate `EngineOpsBinding`, `engine()`,
  `observe()` and `exclusive_running()`. Keep the cheap synchronous observation path for
  the watchdog; offload blocking work from async routes. Revalidate the relevant generation
  before a destructive step and before start/rollback after stop. Unknown ownership blocks
  activation, never authorizes killing an unrelated process.
- [ ] 🟥 Preserve claim-before-observation, existing drive/CLI lock checks, validation and
  release-on-error. Use existing `Step` predicates/callbacks and `JobRunner`; do not create
  a second lifecycle executor or infer success from HTTP liveness.
- [ ] 🟥 Test and preserve successful/failed/rolled-back/cancelled/interrupted job intent,
  job/history field shapes and original-engine restoration. Existing rows must round-trip
  without migration or rewriting. Remove the now-unused planner/binding compatibility shims.

Run:

```bash
uv run pytest -q tests/test_control*.py tests/test_engine_api.py tests/test_jobs.py tests/test_mlx.py tests/test_backends.py
```

Exit: registry-driven lifecycle works end to end; generation and rollback safety are covered.
Suggested commit: `refactor: coordinate engine lifecycle through the backend registry`.

### 5. Migrate status, inference, quality and policy consumers

Files: `src/local_llm_admin/app.py`, `chat.py`, `quality_runner.py`, `sidecars.py`,
`watchdog.py`; minimal compatibility changes in `alerts.py`, `availability.py`, `logs.py`,
`metrics.py` and UI files only where their actual consumers require them.

- [ ] 🟥 Add failing resolver tests and preserve API response fields/badges for MLX,
  ComfyUI, Strata, loading, off, conflict and unavailable observations. Disabled status must
  retain its current no-HTTP/no-PID-probe behavior; local ownership checks needed for safe
  lifecycle activation belong to that control path, not every selector refresh.
- [ ] 🟥 Route status and chat through common observations/inference metadata. Test exact
  upstream base URL/model/auth/settings for MLX and Strata, rejection for non-LLM/ambiguous
  states, stream cancellation and final usage. Keep current `ChatTarget`, profile defaults,
  request validation and direct HTTP streaming; never put credentials into public descriptors.
- [ ] 🟥 Add failing quality-resolution tests, then migrate context/model/settings and
  generation checks. Preserve MLX multi-model runs, Strata current-only scope, cancellation,
  deadlines, shutdown and refusal to restore over an external operator switch. Do not
  generalize all quality scheduling or add cross-engine runs during this refactor.
- [ ] 🟥 Add failing policy tests, then resolve coexistence/recovery eligibility through
  the registry. Preserve MLX allowlists, Strata/ComfyUI exclusivity, forced-stop intent and
  optional service restoration. Keep the same MLX-only watchdog restart evidence/grace/job
  checks; starting/released/recovering is not a crash signal for a new recovery loop.
- [ ] 🟥 Audit log sources, process metrics, alert and availability consumers. Reuse the
  common selected observation where needed, retaining legacy fields and existing availability
  denominator/telemetry attribution. Do not change ingest/deduplication or historical rollups.

Run:

```bash
uv run pytest -q tests/test_engine_api.py tests/test_chat.py tests/test_quality*.py tests/test_eval_runner.py tests/test_control*.py tests/test_sidecar*.py tests/test_watchdog.py tests/test_alerts.py tests/test_availability.py tests/test_logs.py tests/test_performance*.py tests/test_strata_usage.py
```

Exit: consumers use the common contract; existing public APIs, request payloads, quality
scope and recovery behavior are preserved. Suggested commit:
`refactor: resolve engine consumers through shared backend capabilities`.

### 6. Remove migration scaffolding and finish the handoff

Files: changed implementation/tests, `docs/DESIGN.md`, this plan, parent progress and the
current append-only devlog.

- [ ] 🟥 Search remaining old planner calls and explicit engine branches. Remove unused
  migration shims; keep intentional engine-specific payload parsing inside adapters and
  preserve compatibility exports still used externally. Do not turn every engine string
  comparison into a generic framework merely to make a search return zero results.
- [ ] 🟥 Run the full quality gates below. Check new ownership/dispatch/rollback decisions
  at 100% coverage and explicitly verify HTTP/probe counts and stream behavior. Fix actual
  failures; do not broaden testing into another live Phase 0 campaign.
- [ ] 🟥 Update design/plan/devlog with final contracts, validation and deliberate behavior
  changes. Scan tracked/new files with the private denylist, review the diff and stage only
  intended files. Leave implementation commits for explicit user authorization.
- [ ] 🟥 Write the Phase 2 handoff: adapter extension points, wrapper/profile inputs,
  parent/native-child ownership, reciprocal guards and feature-gated registration. Record
  exactly what remains before Splash can be enabled; no live installation or deployment.

Exit: complete tested Phase 1 foundation with a concrete Splash extension seam. Suggested
commit: `docs: document the backend contract and Splash integration handoff`.

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

## Performance and observability

The registry is control-plane work. Target: no additional upstream inference request, no
new polling loop, no per-token registry lookup/copy/logging and no synchronous storage write
on the streaming path. Resolve metadata once per request. Retain current probe/request
budgets and timeouts, offload filesystem/process work from the event loop and reuse an
observation within an operation rather than repeating probes. Never reuse a stale lifecycle
observation as authorization for a later destructive step.

Use mock-transport request counts and fake-wrapper call logs before/after the consumer
migration to verify these budgets. The focused chat/control suites above are the measurement
commands; a separate GPU benchmark or new timing harness is not needed for this refactor.
Existing cancellation and fragmented SSE tests verify that dispatch does not buffer or
reframe every chunk. Record any unavoidable extra safety probe explicitly in the review.

Keep existing job steps/errors, source labels and observation fields sufficient to explain
identity conflicts, readiness failure and rollback. Add only concise refusal reasons where
new ownership validation needs them. No new persistent metrics, schema, credential-bearing
logs or changes to historical usage/availability semantics belong in Phase 1.

## Risks, rollback and decisions

| Risk | Implementation rule and proof |
|---|---|
| Import cycle between clients, planner and adapters | Neutral `backend_types.py`; import/type-check all three modules in package 2 |
| Settings frozen in a long-lived registry | Current Settings reads; mutate `strata_enabled` after construction in a regression test |
| A healthy shared-port listener misidentified as MLX | Explicit ownership classification, generation tests and no-start-on-unknown tests |
| MLX Chat UI omitted from observations | Characterize API-down/Chat-UI-running behavior before changing the resolver |
| Intent loses engine/pack meaning | Preserve stored fields and old rows; keep observed Strata pack for rollback separately |
| A stop fails but rollback starts the source | Existing mandatory down wait and new failed-target-stop tests |
| Async status begins blocking or overpolling | Separate cheap process observation from bounded HTTP details; assert call counts |
| Refactor expands into another platform rewrite | No runtime patch, generic quality scheduler, telemetry redesign or host integration |

No live runtime or persistent schema changes occur. Before deployment, rollback is removal
or reversal of the repository changes in dependency order while preserving unrelated work;
commits/reverts still require user instruction. Do not use a destructive reset. If a schema
change appears necessary, record that concrete need and revise the plan before adding it.

No user decision is outstanding for Phase 1. Existing current behavior, constructor injection
and public API shapes settle the routine choices. The dated plan location and current branch
follow repository/session conventions in place of the workflow skill's generic plan-folder
and new-branch setup. The user's request to write this plan covers analysis and plan creation;
no intermediate approval checkpoint is needed for the planning work.

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

Start package 1 by running the existing lifecycle suites and filling only missing
characterization coverage. Then write the failing registry tests in package 2 before adding
`backend_types.py` and `backends.py`. This request delivers the plan only; implementation
has not begun.
