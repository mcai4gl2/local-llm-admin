# Remaining Splash qualification

Status: **fresh native emission comparison complete; overall adoption inconclusive**. See the [current report](native-comparison-report.md).

## Native emission traces

The [pinned source audit](native-timing-audit.md) still applies. The new
[native_timing.py](native_timing.py) accepts only `splash-native-emission-1` records with:

- Pinned runtime, adapter and exact request digests.
- Nonempty batches of explicit engine token IDs and monotonic nanosecond timestamps, taken
  at entry to the engine-token callback. HTTP/SSE events and decoded-text chunks are invalid.
- A successful final reason, final completion count and a digest of the finalized completion
  IDs in order. Callback IDs must concatenate to exactly that sequence, including the same
  EOS/stop-token policy; any unmatched or omitted token makes the trace ineligible.
- At least two callback batches and a positive first-to-last callback interval. The numerator
  excludes the **whole first batch**, including multiple speculative tokens. The endpoint
  is the last token callback, without native Done or HTTP completion time.

```bash
uv run python ops/splash-phase0/native_timing.py \
  --trace /tmp/splash-qualification/request-trace.json \
  --out /tmp/splash-qualification/request-trace-review.json
```

The output always says `structurally_valid_only` and `native_gate: unassessed`. This metric
is an engine emission interval, not isolated GPU compute. It is a proposed common contract;
it is not proof that equivalent points were instrumented. The new collector/bundle
implementation is documented below. The live accounting/functional pilot, separate overhead
audits and fresh native emission comparison are complete; the separate strict
[native comparison reviewer](native_comparison.py) assesses that frozen series. Its scoped
speed and latency criteria pass for both checkpoints; broader adoption gates remain open.

The concrete requirements below governed the native series. The capture points were
implemented in isolated bundles and exercised by the functional pilot and fresh comparison:

1. For MLX, locate the explicit engine output IDs before text decoding. The existing metrics
   source can fall back to counting a chunk as one token; that fallback is forbidden here.
   Capture actual batches and one `time.monotonic_ns()` clock per request at callback entry.
2. For Splash, inspect the pinned wire Tokens event before `state.streamer.put_tokens`.
   Preserve its token IDs/batch boundaries and callback-entry timestamps. Do not copy
   `decode_micros`, a Done timestamp or the derived response `predicted_ms` into this trace.
3. Prove final accounting, EOS/stop behavior, reasoning tokens and cancellation behavior on
   both adapters. Raw traces and token IDs remain private. Incomplete traces cannot pass.
4. Pin and review the exact runtime/adapter source bytes, callback delivery/queue semantics,
   timestamp points and overhead. Buffer traces per request; avoid synchronous per-token
   disk writes. Bound overhead before comparing instrumented runtimes in a separately
   frozen experiment. Do not insert a trace-derived rate into the existing v2 results.

Adapters were prepared against actual pinned snapshots and executed only from isolated
original/instrumented package copies. Installed production source remains unchanged. If
equivalent boundaries and bounded overhead cannot be established, native qualification
remains unavailable.

## Fixed quality pilot

[quality-suite.json](quality-suite.json) contains 35 explicit tasks: the existing 25 core
instruction/extraction/math/reasoning/tool cases plus ten coding-output questions. The
runner reuses `eval_suites` validation and `eval_graders.grade_response`; no model-supplied
code or tool call is executed. Generated needle suites and LLM judges are refused. Identical
case inputs cannot inflate the sample count. This is a deterministic pilot: coding synthesis,
open-ended writing, multimodal quality and the actual daily workload require further suites.

Quality uses a separate `splash-quality-1` artifact. Its embedded timing manifest is only
pinned launch-profile provenance; the quality suite supplies actual prompts and output caps.
Source timing artifacts are never rewritten. The suite's complete SHA-256 and the grading
source digest (quality harness, benchmark translation and eval modules) freeze both inputs
and interpretation. Fresh requests use paired nonce prefixes
and the existing audited cache namespaces. Nonstreaming replies must have the expected model,
valid usage, zero reused input and valid tool-call structure. A normal stop/tool call is
expected; a token-limit finish remains a visible truncation and prevents a quality pass.

Capture one sequential response per case. There are no performance samples, per-case timing
warmups or speed verdicts in this quality series. Stored wall duration is diagnostic only.
Raw answers, reasoning, tool arguments, manifests, inventories and results remain private.
Each response checkpoints atomically; existing run IDs are refused and cancellation preserves
an interrupted record plus the in-flight case. The first request error or truncation stops
further capture, retains the failed sample and marks the run failed for restoration. Missing
cases remain visible. Reports recompute binary case passes from raw responses and show per-case/category
results, errors, truncations, paired score differences and source digests.

### Predeclared quality decision

Each case contributes one binary pass; partial JSON-field credit does not count as a pass.
Compare Splash minus MLX on the same complete suite, target assets, settings and prompt token
counts, keeping stock 8-bit and modified 4-bit separate. All required candidate tool cases
must pass; a net gain elsewhere cannot hide one failing required case. Named tool cases
require exactly one call, and no-call cases require none. Invalid or failed
responses prevent a pass. Missing/partial/incompatible artifacts remain inconclusive.

- Observed score loss greater than 0.02: **fail**.
- Otherwise, at least ten unique paired cases and an approximate 95% interval lower bound
  at or above -0.02: fixed-suite **pass**, subject to required cases and valid captures.
- Otherwise: **inconclusive**. An all-tied 35-case pilot cannot establish the 2-point margin.

The interval subtracts simultaneous Bonferroni-adjusted Wilson bounds for paired binary wins
and losses. It is conservative relative to a degenerate empirical all-ties bootstrap and
remains approximate. Cases, not repeated requests or tokens, are the sampling units. The interval assumes case
independence; related task families can reduce effective diversity, so suite review remains
necessary before broad quality claims. A small
pilot can find regressions; a sufficiently broad, independently frozen suite is needed for
precise quality qualification. Passing this suite does not close native, context or operations
gates. The existing main plan's speed thresholds and measured timing series are unchanged.

## Prepare concrete private inputs

Run everything inside the devcontainer as `vscode`. Use the saved complete source pair for
one cohort; the same frozen asset/profile fingerprints are carried forward:

```bash
uv run python ops/splash-phase0/quality.py prepare \
  --mlx-run /tmp/splash-phase0/results/stock-8bit/stock-8bit-mlx-3.json \
  --splash-run /tmp/splash-phase0/results/stock-8bit/stock-8bit-splash-3.json \
  --cohort stock-8bit --out /tmp/splash-qualification/stock-8bit

uv run python ops/splash-phase0/quality.py prepare \
  --mlx-run /tmp/splash-phase0/results/modified-4bit/modified-4bit-mlx-3.json \
  --splash-run /tmp/splash-phase0/results/modified-4bit/modified-4bit-splash-3.json \
  --cohort modified-4bit --out /tmp/splash-qualification/modified-4bit
```

`prepare` does not contact a service. It refuses an existing output directory, validates
all source evidence without dropping failures, and writes private draft manifests and a
worksheet. Each cohort pair has 70 requests, a 60-second per-request deadline, a 15-minute
setup/switch reserve and a 10-minute restoration reserve: **95 minutes worst-case request
budget including reserves**, capped at 120 minutes. These are timeout bounds, not duration
predictions. The two cohort pairs require **two separate proposed windows**, restoring and
verifying the original service between them; the worksheet is not approval.

Before each actual launch, refresh process ownership, original selection/intent/configuration,
assets, active jobs, resource baseline and independent guard deadlines. Reuse no expired
mailbox/guard configuration. Review real target readiness, effective context/precision/cache
settings and the pinned draft again. Collect a fresh reviewed inventory with `complete: true`,
no issues, real readiness and a review record. Only after live approval and those checks:

```bash
uv run python ops/splash-phase0/quality.py freeze \
  --draft /tmp/splash-qualification/stock-8bit/stock-8bit-quality-mlx-1.draft.json \
  --preflight /tmp/splash-qualification/stock-8bit-mlx-reviewed.json \
  --out /tmp/splash-qualification/stock-8bit-quality-mlx-1.frozen.json

uv run python ops/splash-phase0/quality.py run \
  --manifest /tmp/splash-qualification/stock-8bit-quality-mlx-1.frozen.json \
  --out /tmp/splash-qualification/results/stock-8bit

uv run python ops/splash-phase0/quality.py compare \
  --results /tmp/splash-qualification/results/stock-8bit \
  --out /tmp/splash-qualification/stock-8bit-quality-report.json
```

Freeze each backend separately just before its run; reviewed preflight must be at most
15 minutes old, with no more than 30 seconds of future clock skew. Drafts and stale frozen
manifests cannot start capture. A digest records the review; it does not establish actual
process ownership or authorize maintenance. The external independent host guard remains
mandatory and must stop collection with restoration time reserved, including on timeout,
pressure, swap growth, ownership/intent changes, errors or user interruption. The quality
runner owns no server process and cannot restore a service itself. Stop and verify process
exit/port closure before switching backends; independently verify real original inference,
admin and sidecars, configuration/assets and cleanup afterward. Record restoration and later
read-only audit times separately. Gateway inference still needs an appropriately scoped
existing route credential; no credential is invented by these tools.

## Additional qualification matrix

The quality worksheet above authorizes and budgets none of the following work. These remain
separate measured campaigns with their own frozen inputs, reviewed profiles and bounds:

| Condition | Prepared path / prerequisite | Required evidence |
|---|---|---|
| Fresh approximately 32K | Existing v2 `32k` case support; freeze text after exact template/tokenizer counting, leaving output room within 32,768 | Validated actual prompt band, zero reuse, TTFT/completion and resources |
| Replayed 8K/32K histories | Existing v2 replay mode, sequential histories, measured server reuse | Paired history bytes, per-step reuse and separate latency distribution |
| Concurrency 2/4 | Existing v2 bounded scheduling; reviewed server capacity and queue policy | Aggregate tokens/wall plus per-request latency, errors and memory; no single-user speed substitution |
| Reasoning on | Separate manifest/profile condition and sufficient output budget | Visible answer correctness and separate reasoning usage/latency |
| Cold/idle/restart x3 | Pinned wrapper/adapter and generation-safe lifecycle supervisor; idle-off timing profile cannot prove idle resume | Three real successful cycles, time-to-ready, shutdown and memory release |
| Required production context | Confirm actual requirement before creating 128K/up-to-configured-limit profiles | Exact rendered tokens, retrieval accuracy and host-resource bounds |
| Intended sidecars | Explicit service mix plus reciprocal ownership guards | No new swap/critical pressure, at least 6 GiB headroom under that actual mix |
| Gateway inference | Existing correctly scoped route credential and unchanged endpoint/model alias | Authenticated end-to-end inference and independent original-service restoration |

No new inference, native trace, quality score, operations pass, integration or deployment is
claimed by unit tests, dry-run worksheets or this document.

## Prepared native collectors — 2026-10-26

The standard-library [collector](native_capture.py) and [bundle preparer](native_prepare.py)
now implement the proposed capture points. The preparer accepts only the exact source
digests above, requires each patch anchor once, compiles the result and writes a new private
bundle. Its receipt pins the collector, preparer, instrumented body and final files. It never
installs the bundle, changes an environment or starts a process.

MLX hooks ordinary text generation's `_stream_text` before text decoding, after any scalar
token extraction. It records a single explicit ID. Independent `_TokenIterator` observations
reconstruct the delivered IDs using explicit `token_count`; a zero-count terminal placeholder
adds no token. Missing, boolean or multi-token counts fail capture. MLX drafts, diffusion,
multimodal input and custom logits processors are unsupported by this adapter.

Splash timestamps entry to `_on_event`, preserving each native Tokens batch before constraints,
latency observation and `streamer.put_tokens`. Its finalizer separately reads the completed
streamer's IDs and the native result's count/reason. Masks, stop sequences, score jobs and
image input are refused for this timing series. Cancelled, bounded and inconsistent results
cannot produce successful traces. Capture failure does not suppress final response delivery.

A process captures one sequential request at a time. Buffers have explicit completion and
native request-descriptor bounds. Successful finalization writes a private native-request
companion and trace; it performs no per-token disk writes. The companion is an immutable
snapshot used to associate exact rendered inputs/settings with the trace. Its hash domain is
backend-native inputs, **not HTTP body bytes**: MLX hashes rendered text plus generation
arguments; Splash hashes rendered token IDs plus the recorded generation parameters.
The trace's `runtime_sha256` pins the original source module; separately frozen runtime,
binary, dependency, asset and launch-profile fingerprints are still required.

This is an implementation and offline source-hook review, not an equivalence finding.
MLX's in-process output and Splash's wire-reader delivery have different queue/transport
paths. Account for those paths, reconcile every expected request with HTTP usage and its
native-input companion, prove EOS/reasoning/cancellation behavior on the actual runtimes,
and bound live overhead before a new series can qualify. Missing/failed artifacts are
ineligible; selecting only successful traces is forbidden. The original native decision
remains **unassessed** and all existing client timing results remain unchanged.

Prepare a separate isolated bundle per backend from private source snapshots:

```bash
uv run python ops/splash-phase0/native_prepare.py --backend mlx \
  --source /tmp/native-source/generation.py --out /tmp/native-bundle-mlx
uv run python ops/splash-phase0/native_prepare.py --backend splash \
  --source /tmp/native-source/backend.py --out /tmp/native-bundle-splash
```

Copy modules only into separately reviewed isolated runtime source copies. Set
`PHASE0_NATIVE_OUT` to an absolute new private output directory per process; existing
directories are refused. Never replace files in the production MLX environment. Match
every expected request and final HTTP count/reason with its trace and private native-input
companion; any failed, missing or partial artifact prevents qualification.

## Live pilot cap calibration — 2026-10-26

The user authorized the run. Fresh source/assets/ownership/readiness reviews passed and
the independent bounded guard controls every service transition.

The original stock MLX pilot stopped at case 15 (`ma-sum`) on its 256-token cap. Its fourteen
preceding replies and truncated fifteenth reply remain in the failed artifact; that capture
does not establish a paired quality score. A separate controller metadata error sent zero
quality cases and restored the original service. Both failures remain disclosed.

[quality-suite-512.json](quality-suite-512.json) is an explicitly separate version-2 follow-up:
the same 35 questions, graders and required tool cases, with a 512-token completion cap on
every case for both engines. Canonical suite SHA-256:
`25a34b84cd86234b561820fcb26b46b8164bafddbe8c6ed0761569d5ef321fb9`.
The follow-up retains temperature 0, seed 0, reasoning off, concurrency 1, 60-second request
deadlines, exact per-cohort assets/profiles and the existing quality margin/uncertainty rule.
Private run IDs/directories and reviewed launch records are separate.

Stock retries/follow-up keep the original collection cutoff and restoration deadline; they
do not reset its two-hour bound. The other checkpoint uses its own separately guarded window.
Only one cap-calibration follow-up is planned: another failed/truncated response remains an
incomplete result. Do not enlarge caps repeatedly until a passing outcome appears.
These versions repeat the same questions; never pool them as independent cases or splice
the original pilot's successful prefix into a follow-up score. Each complete pair still has
35 unique case inputs; independence assumptions still require suite review.

The follow-up completed 140 replies without request errors or truncations: stock 8-bit
31/35 on both engines; modified 4-bit 30/35 on both. Required tool cases all pass.
Per-case outcomes match within each checkpoint; the approximate paired interval remains
[-12.55, +12.55] percentage points. Both quality decisions are **inconclusive** for the
two-point margin. Original-service restoration and independent checks pass for every
attempt; native and remaining Phase 0 requirements remain open.

## Measured native collector pilot — 2026-10-26

The [native pilot report](native-validation-report.md) and
[allowlisted evidence](native-validation-evidence.json) record the completed modified 4-bit
pilot, separate stock 8-bit follow-up and retained incomplete first stock attempt. The
[tested reviewer](native_validation.py) associates each native input and trace with its
unique HTTP request, exact settings and final count/reason before computing overhead.

Both complete pilots account for 36 successful traces each, with four expected cancellation
failures per checkpoint and successful post-cancellation inference. Including the interrupted
stock attempt, 90 successful native traces and 20 natural-stop EOS policies are verified.
The original stock final reference request timed out at 60 seconds. Its full artifact remains
incomplete; the independently frozen follow-up permits 90 seconds for 8K requests and
retains 60 seconds elsewhere, without pooling or replacing data.

Stock follow-up statistical overhead bounds meet the predeclared overall/per-category 5%
criterion for both engines. Modified Splash meets it; modified MLX is **inconclusive**
because its 8K upper bound is 1.0764. Two launch pairs are a pilot. Source/EOS inspections
overlapping MLX segments are also recorded as possible CPU/disk noise. A more precise
separately frozen audit must perform one-off inspections between timing phases. No native
speed qualification or production change follows from this pilot.

All three attempts pass independent original-service, admin, configuration/intent, assets
and sidecar restoration checks. Final isolated-source/import and 57 installed-source-file
checks pass; temporary owned transports are closed. The user confirmed that 32K is sufficient
for current context tests. Actual near-limit correctness/resources and the rest of the
secondary matrix remain separate from these short/2K/8K collector requests.

## Near-limit 32K context result — 2026-10-26

The [context report](context-report.md) and [evidence](context-evidence.json) record both
checkpoint pairs passing the fixed synthetic retrieval smoke: all 24 long requests correct,
exact 31,858–31,864 prompt tokens, zero reuse/errors/truncations, plus four declared short
warmups. Original service, admin, configuration/intent, target assets and sidecars were
independently verified restored between windows. The user confirmed 32K is sufficient.

This is six distinct synthetic documents per checkpoint at 10%, 50% and 90% retrieval
depths. It does not qualify broad quality, primary speed, replay, concurrency or sidecar
coexistence. The [expanded MLX overhead design](mlx-overhead-design.json) is fixed separately
and now has a [tested reviewer](overhead_validation.py) and private frozen inputs. Its
execution/result remains separate from the unchanged collector pilot and native speed gate.

## Separate expanded MLX overhead follow-up — 2026-10-26

The [follow-up report](overhead-followup-report.md) and
[evidence](overhead-followup-evidence.json) now establish collector-overhead eligibility
for the selected modified-4bit MLX profile under the frozen 5% overall/per-category rule.
Four independent ABBA windows completed 144 requests, 48 measured pairs, 48 excluded
warmups and 72 fully accounted native traces. Overall wall ratio is 0.997389 with 95%
interval [0.990996, 0.999361]; the 8K upper bound is 1.002261. These ratios evaluate
collector overhead and do not establish acceleration or cross-engine native speed.

The [initial expanded attempt](expanded-overhead-report.md) remains inconclusive: a
reviewer bug rejected the frozen conversation history after two successful warmups,
before any measured pair or native trace. A regression test failed before correction;
the [follow-up design](mlx-overhead-followup-design.json) uses a separately frozen fresh
series with unchanged sample count, criterion and analysis. No failed/pilot records are
pooled or rewritten, and no extra windows were added after viewing statistical results.

Original inference/admin/configuration/intent/assets/sidecars passed restoration after
every window. Final source checks and 41 import receipts pass; all owned temporary
transports/observers are closed. The old pilot's limits remain visible, including stock
observer noise. Native speed, broader quality and the remaining secondary requirements
are still open; integration and deployment remain gated.

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

See the [native report](native-comparison-report.md), [evidence](native-comparison-evidence.json) and [clean stock audit](stock-overhead-report.md).
