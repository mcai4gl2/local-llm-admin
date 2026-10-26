Overall Progress: 100%

# Fresh native emission comparison for both selected checkpoints

Date: 2026-10-26
Status: Complete on 2026-10-27. Both checkpoints pass the scoped emission speed,
p95 TTFT and completion criteria; overall adoption remains inconclusive.

## Scope and measurement

Preserve all published client, quality, context, pilot and expanded-overhead evidence.
Use unchanged isolated source-pinned collectors and installed runtimes. Keep the existing
branch, 32K profile, BF16 KV/FP32 recurrent state, 40 GiB Metal guideline, reasoning off,
seed/temperature zero, one request at a time and GPU sidecars stopped. No deployment,
registry/default changes, new dependencies or worktrees.

First collect a separately frozen clean stock-8bit MLX overhead audit using the same four-
window ABBA design and 5% overall/per-category rule as the completed modified audit.
The earlier stock pilot's overlapping source inspection remains disclosed. Do not pool it
or extend the new fixed audit until eligible; failed capture requires restoration/retention.

Then run three independent paired native sessions per checkpoint, alternating engine order.
Each engine launch includes one excluded warmup per short/2K/8K case and five measured
repetitions, using the unchanged source corpus and 512/1024/1024 caps. Six independently
restored paired windows collect 216 requests: 180 measured and 36 excluded warmups.
Fresh nonces, namespace salts, exact rendered input/token receipts and trace directories
are frozen before launch. Account for every response and every explicit native trace.

The common metric is post-first-batch native token count divided by first-to-last engine-
token-delivery callback time. Exclude the entire first speculative batch. This measures
native emission including runtime delivery/IPC, not isolated GPU compute. Reuse the parent
plan's median-of-paired-per-prompt ratios, session/prompt clustered bounds, >=1.25 median,
lower 95% bound >1 and no category regression. Report p95 TTFT and fixed-length completion
separately. Broader quality/operations/gateway gates continue to block adoption.

## Tasks

- [x] 🟩 Add failing tests for exact schedule, collector proof/pins, source/input/HTTP/native
  association, first-batch arithmetic, settings, partial/failure evidence and safe output.
- [x] 🟩 Implement the strict new-contract reviewer by reusing native trace accounting and
  existing session/prompt statistics without editing or reinterpreting old results.
- [x] 🟩 Freeze fresh stock-overhead and native-series input/command/tokenization ledgers,
  source/helper/code hashes, reviewed callback-boundary scope and budgets; run quality gates.
- [x] 🟩 Execute the four independently guarded/restored stock-overhead windows and publish
  their full pass/fail/inconclusive outcome before using stock collector eligibility.
- [x] 🟩 Execute the six independently guarded/restored paired native windows, retaining
  every request/trace/failure with source and actual input accounting.
- [x] 🟩 Publish per-checkpoint distributions, bounds, gate decisions and remaining Phase 0
  requirements; verify original inference/assets/admin/intent/sidecars and owned cleanup.

## Budgets, observability and restoration

Stock overhead: 36 requests/window, short/2K/8K deadlines 60/60/90 seconds; 58 minutes
worst case with setup and restoration. Native pair: 36 requests/window, deadlines
90/150/210 seconds; 90 minutes request budget plus 10 minutes setup and 10 minutes restore
=110 minutes within each independent 110-minute collection/120-minute restoration guard.
Abort rather than run without restoration reserve. Pressure/swap/headroom/identity/job
checks remain mandatory. Keep stable resource/thermal observers; perform one-off source,
full-weight and tokenizer inspections only outside timed phases. Restore and verify the
original service after every window, including failures. Do not retry incomplete windows
silently, drop records, rewrite old evidence or stop early based on an eligible statistic.

## Test and validation plan

Run commands in the devcontainer as vscode through the existing uv environment. Require
red-before-green tests, focused tooling coverage, full application coverage >=80%, Ruff,
strict mypy, instruction parity, diff checks and the unchanged-publication digest baseline.
Scan tracked and nonignored untracked public files with the private denylist. Existing
59 pattern-23 matches remain a staging blocker; do not alter private patterns or append-
only history to bypass them. Append devlogs; no commit.

## Phase 0 remaining after this comparison

A broader representative quality suite is needed to resolve the two-point score margin.
Replay at 8K/32K, concurrency 2/4, representative reasoning behavior/performance, deliberate
cold/idle/restart x3 and memory release, and actual authenticated gateway inference remain.
32K near-limit synthetic retrieval already passes; larger contexts are outside the user's
current requirement. Sidecar coexistence needs its own explicitly selected mix if required.

## Pre-capture validation complete

The new reviewer passed red-before-green tests: 39 focused cases and 92.38% branch-aware
module coverage. Full suite: 2,627 passed, one skipped, 98.10% application coverage; Ruff
and strict mypy pass (62 source files). All four stock and six native controller budget
checks pass. All 360 exact fresh request bodies rendered within 32K and match four saved
actual native rendering fixtures. Source/helper/controller/model/tokenizer/campaign pins
are frozen before capture. The restored private scanner still reports the same 59 existing
pattern-23 matches; staging stays empty. No deployment or dependency change occurred.

See the frozen [native design](../ops/splash-phase0/native-comparison-design.json),
[callback boundary scope](../ops/splash-phase0/native-comparison-boundaries.md), and separate
[clean stock overhead design](../ops/splash-phase0/stock-overhead-design.json).

## Fresh native emission comparison complete — 2026-10-27

Six independently restored paired windows completed 216 requests/native traces: 180 measured
responses and 36 excluded category warmups. Three alternating paired sessions per checkpoint
use unchanged short/2K/8K cases, 512/1024 caps, 32K settings and fresh namespaces. All explicit
native input/final-ID and HTTP accounting reconciles; prior measurements remain unchanged.

- stock-8bit: emission speed pass, p95 TTFT pass, completion pass; ratio 2.4949961802269556, 95% interval [2.389975934189482, 3.328202089354354].
- modified-4bit: emission speed pass, p95 TTFT pass, completion pass; ratio 2.365861424040426, 95% interval [2.2750806109009956, 2.9449391336223347].

Overall adoption: **inconclusive**. Rates measure native delivery-callback emission,
including runtime IPC, with the entire first batch excluded. GPU compute is not isolated.
The clean stock collector audit is eligible; earlier pilot confounds remain in their frozen
reports. Overhead calibration used 256-token caps; no 512/1024-cap bound is invented.
All source/import, original inference/admin/configuration/intent/assets/sidecars and owned
transport/observer cleanup proofs pass. All 19 earlier publication/design digests match.

Still pending: broader representative quality precision for the two-point margin; replay
at 8K/32K; concurrency 2/4; representative reasoning behavior/performance; deliberate cold/
idle/restart x3 and memory release; any required selected sidecar mix; and actual authenticated
gateway inference. Near-limit 32K synthetic retrieval already passes, and larger contexts
are outside the user's current requirement. Integration/deployment remain gated.

Final code validation: 40 focused tests, 92.52% reviewer coverage; full suite 2628 passed,
1 skipped, 98.10% application coverage; Ruff and strict mypy pass (62 source files).
The failure-reporting regression was fixed and frozen before any native-series request;
actual request inputs, schedule, thresholds and statistics remained unchanged.

See the [report](../ops/splash-phase0/native-comparison-report.md). This experiment plan is complete; full adoption is still gated.
