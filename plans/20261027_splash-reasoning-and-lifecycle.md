Overall Progress: 100%

# Splash Phase 0 reasoning and lifecycle

Date: 2026-10-27
Status: Both fixed live milestones reached terminal measured outcomes; publication and
validation are complete. User authorized both bounded milestones. Existing branch and plans
conventions take precedence over generic workflow branch/checkpoint suggestions.

## Scope and constraints

Use original isolated runtimes, both stock 8-bit and modified 4-bit checkpoints, 32K context,
BF16 KV/FP32 recurrent state, concurrency one, temperature/seed zero and GPU sidecars stopped.
Preserve all earlier publications and measurements byte-for-byte. No deployment, installed
source change, production default change or commit. All commands run through the existing
uv environment in the devcontainer. Keep unrelated pending edits unchanged.

## Reasoning protocol

Freeze eight independently checked cases covering arithmetic, dependency scheduling, code
tracing, ordering constraints, probability, constrained allocation, null aggregation and day
arithmetic. Each expects a strict JSON object with one `value`. Run the same cases with
reasoning off and on, 2,048 completion-token cap and 240-second deadline. Each cohort/mode
is a separate paired window with alternating engine order: four windows, 64 measured
requests plus eight excluded short reasoning-off warmups. Distinct namespaces prevent reuse.
This is a fixed representative diagnostic suite, not the broader quality-precision gate.

Require complete ordered accounting, correct model, zero cached tokens, valid positive usage
and ordered stream timestamps. A reasoning-on reply must contain a nonempty reasoning field;
reasoning-off must contain none. Report explicit reasoning-token counts where available,
otherwise unknown. Correctness and natural stop are evaluated independently from transport;
wrong answers/truncations remain in the full fixed series. Stop the campaign on transport,
accounting, ownership/resource failure or interruption; retain partial evidence without retries.
Scoped reasoning correctness passes only if every case passes in each mode/backend/cohort.
Report paired first-event, first-answer and total latency descriptively, with no p95,
confidence-interval, native-speed or general quality claim from eight cases.

Worst-case request budget per paired window: 16*240 + 2*30 = 3,900 seconds; allow 540
seconds setup and 600 restoration, total 5,040 < 6,600 collection seconds. Reserve all
remaining request deadlines and restoration time before every request.

## Lifecycle protocol

Two separate paired cohort windows; reverse backend order between cohorts. Per backend:
initial model-process cold launch, first successful inference, idle observation and resume,
explicit stop, then three fresh process-generation restart cycles with successful inference
and verified shutdown each time. Startup deadline 120 seconds, inference 90 seconds,
release observation 90 seconds, stop/reclaim observation 90 seconds. Use short deterministic
JSON retrievals with reasoning off, 128-token cap and unique namespaces, without excluded
warmups because first inference is the measurement. No host cache flush or OS-cold claim.

Splash: change only the isolated process idle-release flag to 20 seconds. Observe actual
weights released/not restoring within 90 seconds after the first response without inference
traffic, then send the resume request and verify restore count increases and loaded state
returns. Record runtime allocated/current bytes separately from process RSS. Require at
least 50% reduction in runtime allocated bytes across idle release. Do not substitute HTTP
liveness or manual release for automatic idle release.

MLX: inspected pinned server sources expose `/unload` but no automatic model idle-release
policy. Observe 25 seconds without inference, record residency, then explicitly unload the
isolated model, require absent loaded-model state and a successful request-triggered reload.
Label this manual unload/resume; automatic idle release remains unsupported/unqualified.
Require at least 50% reduction in owned RSS from the loaded snapshot after manual unload;
shared-host available memory is diagnostic, not proof of per-engine GPU reclamation.

Each stop must remove the recorded owned generations and listener. Compare host available
memory with the baseline taken while the original service is paused, before the engine is
loaded: recover to baseline minus max(2 GiB, 10% baseline) within 90 seconds, with three
consecutive observations spanning at least four seconds. Shared-host interference makes
memory evidence inconclusive, never an invented pass. Keep RSS and GPU allocation semantics
separate. Never signal a reused PID. Freeze and validate exact schedule and tolerances
before capture. Conservative per-backend budget 4*(120+90+90) + 90+90+90 = 1,470 seconds;
paired 2,940 + 540 setup + 600 restoration = 4,080 seconds, inside the guard bounds.

## Supervision and observability

Fresh independent 110-minute collection/120-minute restoration guards for every window.
Use existing generation-safe supervisor controls and pressure/swap/minimum 6 GiB headroom
aborts; check transport, original real inference/admin/jobs/configuration/intent/assets,
sidecars and source fingerprints before arming. Record private raw streams, request hashes,
process generations, monotonic timings and resource snapshots. Stable observers remain on.
Restore and independently verify original service/configuration/intent/assets/sidecars and
owned process/port cleanup after every attempted window, including failures. Reasoning and
lifecycle are independent milestones: a model correctness failure does not prohibit a safely
prepared separate lifecycle experiment after restoration; infrastructure failure does.

## Tasks

- [x] 🟩 Write failing reasoning/lifecycle review tests, including partial schedules,
  incorrect/truncated answers, unsupported idle behavior and memory-release failures.
- [x] 🟩 Implement minimal independent reviewers and frozen public design/suite.
- [x] 🟩 Freeze private requests, source/command/helper pins and fresh runtime preflight;
  run focused/full tests, Ruff, strict mypy and public-safety checks.
- [x] 🟩 Run the frozen reasoning series with independent restoration per attempted window.
- [x] 🟩 Run deliberate lifecycle windows with independent restoration per attempted window.
- [x] 🟩 Publish measured outcomes/limitations, verify source preservation and owned cleanup,
  update campaign status, append devlog, scan and stage only intended files.

## Validation and integration

Use `uv run pytest --cov --cov-fail-under=80`, `uv run ruff check src tests scripts`, strict
`uv run mypy src scripts` plus new standalone reviewer checks; target critical review paths
at 100% coverage. Run shell syntax/instruction parity/whitespace checks and private denylist
scan including nonignored untracked files. No new dependencies. Parent adoption and remaining
replay/quality/concurrency/gateway conditions stay open. See the qualification campaign and
parent benchmark plan for integration gates.

## Terminal outcome and validation

All six windows completed: 92/92 requests captured, 24 original-runtime launch receipts,
all six independent restorations and final owned cleanup verified. Reasoning on passes 32/32;
reasoning off passes 5/32, so the frozen both-mode gate fails. All 16 lifecycle starts,
first inferences and owned stops pass. Splash automatic idle release passes both checkpoints;
modified Splash passes its supported lifecycle protocol. Stock Splash host reclamation is
inconclusive and both MLX manual-unload RSS-release conditions fail. Automatic MLX idle
release remains unsupported. Tests are complete; these unresolved findings still block
qualification. No extra requests, retries, threshold changes or pooling were used.

Final validation: 2,745 full-suite tests passed, one skipped, 98.10% application coverage;
60 focused tests with 100% statement/branch reviewer coverage; Ruff and strict mypy pass
(64 source files). Pinned formatting/JSON/conflict/size checks, instruction parity and
whitespace checks pass. The final public scan is clean. Existing remote hook bootstrap and
Gitleaks were not rerun; their prior network limitation is not represented as a pass.
All 57 earlier tracked experiment files remain byte-identical. No commit or deployment.
See the [report](../ops/splash-phase0/reasoning-lifecycle-report.md).
