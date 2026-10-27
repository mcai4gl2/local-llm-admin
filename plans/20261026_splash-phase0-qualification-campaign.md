Overall Progress: 64%

# Splash Phase 0 qualification campaign

Date: 2026-10-26
Status: Quality pilots, near-limit 32K context, collector overhead audits and the fresh
native emission comparison and fixed 8K/32K replay/cache correctness pilot are complete. Both checkpoints pass the scoped emission speed
and client latency criteria. Original service restoration and owned cleanup are verified.
The separate reasoning/lifecycle series is now complete with failed/inconclusive conditions
recorded below. Broader quality precision and remaining qualification stay open; adoption is inconclusive.
Failed attempts remain separate from successful follow-up series.

The user resumed the next Phase 0 milestone on 2026-10-27. The fixed replay/cache pilot
reached its declared terminal outcome; remaining conditions stay on the checklist below.

The existing three-session client timing evidence remains frozen. This campaign keeps
stock 8-bit and the selected modified 4-bit checkpoint separate. Integration and deployment
remain gated by the original adoption criteria.

## Implementation and validation

- [x] Add test-first, standard-library native collectors for explicit engine token IDs,
  independent final accounting, bounded private buffers and visible failures.
- [x] Prepare source-pinned MLX/Splash instrumentation as isolated output bundles. Refuse
  changed source, overwrites and unsupported generation paths; never modify installed code.
- [x] Review callback/queue boundaries and exercise patched token hooks from the privately
  saved actual sources with mocked dependencies. Actual EOS/reasoning/overhead validation
  remains in the live campaign.
- [x] Run focused/full tests, lint, strict typing and repository checks; append the devlog
  and record the private scan outcome. Tests pass; the existing 59-hit label collision
  still blocks staging.

## Live qualification

- [x] Regain read-only Mac access and review fresh original-service, asset, runtime,
  resource, ownership and job state. Establish a fresh owned loopback tunnel.
- [x] Run the prepared quality pair per checkpoint in separate bounded windows, restoring
  the original service between windows; retain all failures and uncertainty.
- [x] Freeze a separate native series and bound collector overhead with instrumented and
  original profiles before comparing equivalent emission intervals.
- [ ] Complete replay p95 latency qualification and concurrency 2/4. Representative
  reasoning-on now passes 32/32 checks; retain the failed both-mode diagnostic gate and
  off-mode findings when choosing supported settings. The fixed fresh-32K and replay/cache correctness pilots pass.
- [ ] Resolve measured lifecycle/memory blockers: MLX manual-unload RSS retention and
  inconclusive stock Splash host reclamation. Cold/start/restart x3 and automatic Splash idle
  release were measured; modified Splash passes its supported lifecycle protocol. Confirm any
  intended GPU sidecar mix before qualifying coexistence; larger contexts are not required.
- [ ] Verify actual existing authenticated gateway inference with an appropriately scoped
  existing credential and model alias.
- [ ] Publish measured pass/fail/inconclusive outcomes and remaining blockers. A failed gate
  stops integration under the parent plan; incomplete evidence cannot imply a pass.

Reuse the independently supervised 110-minute collection cutoff and 120-minute restoration
deadline, pressure/swap/headroom aborts and generation-safe process ownership. Previous
window configurations are expired and cannot be reused. Preserve exact original service,
selection, configuration, sidecar intent and assets, and independently verify restoration.
No production deployment or default change is included.

## Current blockers

The earlier public-label collision was resolved in the committed publication cleanup;
the unchanged private denylist is clean. Broader quality precision and the remaining
secondary qualification conditions below still gate adoption.

## Measured quality outcome

See the [report](../ops/splash-phase0/quality-report.md). Stock: 31/35 on both engines;
modified 4-bit: 30/35 on both. All 140 follow-up replies are valid, with zero reuse, errors
or truncations and all required tool cases passing. Approximate paired intervals remain
[-12.55, +12.55] percentage points, so the fixed quality margin is inconclusive.
The larger representative suite needed for precision is not replaced by duplicated cases.

## Current context scope — user confirmed 2026-10-26

The user confirmed that 32K is sufficient for the current tests. Qualify actual rendered
inputs within the 32,768-token effective context, leaving room for completion. The 128K
and up-to-configured-limit probes are outside the current required context scope. The
current timing/collector profile keeps GPU sidecars stopped; a simultaneous service mix
has not been selected or qualified.

## Native pilot outcome — historical stage

The [collector pilot](../ops/splash-phase0/native-validation-report.md) completed both
checkpoint validations and retained the interrupted initial stock attempt. All 90 successful
traces reconcile with native inputs and final HTTP counts; stop/EOS, reasoning and
cancellation checks pass. Both stock statistical overhead bounds and modified Splash meet
the frozen 5% criterion. Modified MLX's 8K upper bound is 1.0764, so its overhead remains
inconclusive. Read-only audit activity overlapping MLX segments is disclosed as possible
noise. No full native speed series or gate pass is claimed.

All attempts restored original inference/admin/configuration/intent/assets/sidecars within
their independent bounds. Final source/import checks pass and owned transports are closed.
The native task remains open until a separately frozen, more precise overhead audit and
full comparison resolve it. The main plan remains 9/23 tasks complete.

## Near-limit context smoke complete

Both checkpoint pairs pass the [fixed 32K retrieval smoke](../ops/splash-phase0/context-report.md):
24/24 long requests correct at exact frozen 31,858–31,864 token counts, no errors/reuse/
truncations, four declared excluded warmups and independent original-service restoration
after each window. This resolves the current fixed synthetic near-limit context check;
replay, concurrency, deliberate operations, broader quality precision and gateway inference
remain open. The separately fixed four-window modified-MLX overhead follow-up is now complete; see below.

## Expanded collector overhead complete — before the fresh comparison

The [separate follow-up](../ops/splash-phase0/overhead-followup-report.md) completes all
four windows and meets the 5% collector-overhead criterion overall and in every category:
144 valid requests, 48 measured pairs and 72 accounted native traces, with 8K upper bound
1.002261. The retained initial expanded attempt stopped on a reviewer bug after two
excluded warmups. Its evidence is not pooled or repaired. Regression/full checks pass.
All original-service restoration, source/import and owned-helper cleanup proofs pass.

At this earlier stage, the native checklist task stayed open because a full separately frozen
cross-engine speed series was still required, and the earlier stock pilot's observer noise remains disclosed.
The context/replay/concurrency task also stays open for its remaining components. No
quality, operations, gateway, integration or deployment pass is implied. At that stage, campaign
progress was 6/11 tasks (55%); the parent project remains 9/23 (39%).

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

The native experiment task is complete: campaign 7/11 (64%). The parent full project remains 9/23 (39%) because secondary qualification is incomplete. See the [report](../ops/splash-phase0/native-comparison-report.md).

## Replay/cache pilot result — 2026-10-27

The fixed synthetic replay/cache pilot is **pass**; overall adoption remains
**inconclusive**. The frozen four-window schedule retained 80 attempts
and 80 responses, with cold prime, identical replay and fixed-history continuation.
Every attempted maintenance window is independently restored and final owned cleanup passes.

- stock-8bit / 8k: pass; 20 attempted, 20 captured; restored True.
- modified-4bit / 8k: pass; 20 attempted, 20 captured; restored True.
- stock-8bit / 32k: pass; 20 attempted, 20 captured; restored True.
- modified-4bit / 32k: pass; 20 attempted, 20 captured; restored True.

Exact prompt/tokenization and namespace receipts are retained privately; cold requests need
zero reuse and repeated/extended requests need at least 80% shared-prefix reuse plus correct
JSON answers. Original isolated sources and installed runtime fingerprints remain unchanged,
including 1476 pinned MLX Python source files.
All 53 previously committed experiment files remain byte-for-byte unchanged.
Client latency is descriptive; this small pilot does not qualify p95 latency, native speed or
broad quality. No measurement is pooled or previous report rewritten.

Remaining Phase 0 work: replay p95 latency qualification, representative quality precision,
concurrency 2/4, representative reasoning, deliberate cold/idle/restart cycles and memory release, any required selected
sidecar mix and authenticated gateway inference. Any replay conditions without a pass remain
open as shown above. Larger contexts remain outside the user's current 32K requirement.
Integration/deployment remain gated; no default or installed source was changed.

Validation: 55 new replay tests, 100% statement/branch reviewer coverage; full suite 2,685
passed, one skipped, 98.10% application coverage; Ruff and strict mypy pass (63 source files).
See the [replay report](../ops/splash-phase0/replay-report.md) and
[evidence](../ops/splash-phase0/replay-evidence.json).

Campaign progress remains 7/11 (64%): the combined secondary task still includes concurrency and reasoning.

## User priority — reasoning and lifecycle, 2026-10-27

The user prioritizes representative reasoning-on behavior/performance and deliberate
lifecycle/memory qualification ahead of further replay p95, concurrency or quality-precision
runs. The existing replay pilot remains complete and unchanged; the other adoption gates
remain open. This priority change does not itself add measured evidence.

Next milestone: freeze a representative reasoning suite with independently checked expected
answers before capture, paired reasoning-off/on requests, identical input histories and
explicit output budgets for stock 8-bit and modified 4-bit on both engines. Report correctness,
errors/truncation, time to first streamed event, time to first answer and total latency
separately. Report explicit reasoning-token accounting where supported; do not infer token
counts from text length or mistake a reasoning event for the first answer. Keep a functional
smoke distinct from broader reasoning qualification. Freeze deadlines, stop criteria and
interpretation before any request; retain all failures without extending the series after
seeing results. Use the existing independently guarded/restored isolated windows.

Following milestone: deliberate model-process cold start, verified idle release/resume,
explicit stop and three restart cycles for each engine/checkpoint. Observe readiness and
first successful inference separately, sample memory before load, while loaded and after
release/stop, and verify owned process generations and listener cleanup. Inspect the pinned
runtime's actual idle controls and status semantics before choosing the isolated launch
settings. A live HTTP listener alone cannot prove model readiness or successful resume.
Distinguish model-process cold start from OS/filesystem cold caches; do not flush host caches
or claim OS-cold measurements without evidence. If an engine lacks an idle-release control,
record that limitation rather than substituting an ordinary restart and claiming idle resume.
Set memory-release tolerances and observation windows before capture, accounting for shared
host activity and the difference between process RSS and GPU allocations.

Keep the user-confirmed 32K context and separate checkpoint results. Preserve original
service/configuration/intent/assets and independently verify restoration after every attempted
window. No deployment, production default change or new commit is included in reprioritization.

## Reasoning and lifecycle results — 2026-10-27

The fixed six-window series completed all 92 attempts/captures: 64 measured reasoning
responses, eight excluded warmups and 20 lifecycle requests. All six original-service
restorations, final source/import review and owned-helper/transport cleanup proofs pass.
All 57 previously tracked experiment files remain byte-for-byte unchanged. No new swap,
maximum pressure level 1 and minimum observed headroom 14.571 GiB across the windows.

Reasoning on passes **32/32** strict checks (8/8 per engine/checkpoint). Reasoning off
passes **5/32**: stock MLX 2/8, stock Splash 3/8, modified MLX and Splash 0/8. The fixed
both-mode diagnostic gate is **fail**; wrong values and format violations are retained.
These eight-case condition counts are not a general quality-precision or p95 result.
Median reasoning-on first-answer latency: stock MLX 16.909 s / Splash 4.536 s; modified
MLX 9.662 s / Splash 3.105 s. MLX does not emit explicit reasoning-token counts; they remain
unknown. All four reasoning-on conditions pass their scoped correctness/mode check.

Lifecycle: all **16 launches**, first inferences and owned stops pass, including three
restart cycles per engine/checkpoint. Splash automatic idle release passes both checkpoints,
with approximately 94%/90% lower runtime allocations and successful resumes in 6.354/3.191 s.
Modified Splash passes its full supported lifecycle protocol. Stock Splash reclamation is
**inconclusive**: after the initial stop, owned processes/listener were gone but shared-host
available memory missed the frozen tolerance within 90 s. MLX manual unload clears loaded
state and resumes successfully, but RSS falls only 0.065%/0.116% in the observation window,
so the frozen memory-release criterion **fails** on both checkpoints. MLX automatic idle
release is unsupported/unqualified. No OS-cold or GPU-allocation-leak claim is made.

Both experiments are complete as measured experiments; their failed/inconclusive conditions
are unresolved qualification blockers. Overall adoption remains **inconclusive**, with
integration/deployment gated. Next work should investigate MLX unload RSS retention and the
stock Splash host-reclamation observation before a separately frozen follow-up. Replay p95,
quality precision, concurrency, actual authenticated gateway inference and any required
selected sidecar mix remain open. No automatic retry, pooling or default change was made.
See the [report](../ops/splash-phase0/reasoning-lifecycle-report.md) and
[evidence](../ops/splash-phase0/reasoning-lifecycle-evidence.json).

Validation for this milestone: final full suite **2,745 passed, one skipped**, 98.10%
application coverage; 60 focused reviewer tests, 100% statement/branch coverage; Ruff and
strict mypy pass (64 source files). Pinned publication formatting/JSON/conflict/size checks
pass. Existing unrelated local setup/maintenance changes were left untouched; the full suite
includes those working-tree fixes. No commit was created. Campaign remains 7/11 (64%) and
the parent remains 9/23 (39%); failed/inconclusive qualification conditions remain blockers.

## MLX unload cause and isolated fix confirmed — 2026-10-27

A separate fixed diagnostic series confirms the manual-unload RSS cause: the unload loop
holds its last resource-owning cache dictionary through GC and allocator purge, so model
buffers enter the allocator cache only after that purge. After original unload, weak model/
generator references are dead and active bytes are only 1,032, but cached memory remains
27.483 GiB (stock) / 14.960 GiB (modified). An explicitly delayed purge removes it.

A one-line source-pinned change snapshots only cache-group names. Across two cycles per
variant/checkpoint, all four fixed unloads leave zero cached bytes before any diagnostic
purge, with 90-second RSS 0.240–0.256 GiB for stock and 0.242–0.258 GiB for modified.
Original 90-second RSS remains 27.812–28.023 / 15.290–15.500 GiB. All eight inference
requests, repeated unload/idempotence/resume and owned stops pass. Both original-service
restorations, final source/import checks and owned transport/helper cleanup pass. All 61
prior tracked experiment files are unchanged; installed runtimes are unchanged.

This resolves the cause and proves the fix in an isolated copy, while the earlier unmodified
MLX lifecycle failure remains valid. No installed patch, deployment or commit was made.
Active/draining requests, model replacement, APC-enabled unload, audio/vision and automatic
idle release remain outside this fix's measured scope. Overall adoption remains inconclusive;
other quality/replay/concurrency/gateway/coexistence and stock Splash reclamation conditions
remain open. Full suite 2,754 passed, one skipped, 98.10% coverage; nine focused preparer
regressions at 100% statement/branch coverage; Ruff and strict mypy pass (65 source files).
See the [investigation report](../ops/splash-phase0/mlx-unload-report.md) and
[patch preparer](../ops/splash-phase0/mlx_unload.py).

## Stock Splash host-reclamation attribution — 2026-10-27

Two independently restored default/GPU-only paired windows, with reversed order, completed
four launches, eight correct requests, four automatic idle releases/resumes and 56 whole-host
snapshots. Both first-position launches miss the frozen 90-second available-memory threshold:
gaps 10.968/11.919 GiB track active-page increases 10.957/11.917 GiB, while wired deltas are
only 0.012/0.010 GiB with no matching anonymous/compressor increase. Both second-position
launches pass from lower inherited baselines. Disabling ANE does not prevent the gap.
Pressure-query free remains 92–93% before/after shutdown. This supports active file-backed
page classification as the main explanation, without uniquely identifying an owner or
proving the absence of leaks under pressure. Restricted RSS/USS remains explicitly unknown.

All owned stops/listeners, independent service restorations, final source review and helper/
transport cleanup pass. All 65 earlier tracked experiment files and installed runtimes are
unchanged. Pressure level never exceeds 1, new swap is zero, minimum observed available
headroom is 14.853 GiB. No runtime patch, production default change, deployment or commit.
The original and follow-up reclamation gates remain **inconclusive**. Define a prospective
host-reclamation contract before fresh qualification; do not rescore the frozen evidence.
Quality precision, replay p95, concurrency, authenticated gateway inference and any required
sidecar mix remain open. Campaign stays 7/11 (64%); parent stays 9/23 (39%).

Validation: 2,784 passed, one skipped, 98.11% coverage; 30 focused tests at 100% statement/
branch coverage; Ruff and strict mypy pass (66 source files). See the
[report](../ops/splash-phase0/reclamation-report.md),
[evidence](../ops/splash-phase0/reclamation-evidence.json) and
[completed diagnostic plan](20261027_splash-stock-reclamation.md).
