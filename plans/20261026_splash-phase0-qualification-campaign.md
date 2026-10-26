Overall Progress: 64%

# Splash Phase 0 qualification campaign

Date: 2026-10-26
Status: Quality pilots, near-limit 32K context, collector overhead audits and the fresh
native emission comparison are complete. Both checkpoints pass the scoped emission speed
and client latency criteria. Original service restoration and owned cleanup are verified.
Broader quality precision and secondary qualification remain pending; adoption is inconclusive.
Failed attempts remain separate from successful follow-up series.

Further live qualification is deferred at the user's request on 2026-10-27.
The remaining checklist is retained for later; this cleanup prepares the completed progress for commit.

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
- [ ] Run fresh 32K, replay 8K/32K, concurrency 2/4 and representative reasoning-on cases.
- [ ] Measure deliberate cold/idle/restart cycles and memory release; confirm any required
  context beyond 32K and intended GPU sidecar mix before qualifying those conditions.
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

The original stale SSH-agent socket was replaced by a private transport to the active
existing host agent. Guarded quality capture and restoration are complete. The private leak scan also has an unresolved public cohort-label collision;
do not change private patterns or rewrite historical append-only logs to bypass it.

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
