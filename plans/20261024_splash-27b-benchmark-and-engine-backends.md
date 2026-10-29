Overall Progress: 91%

# Splash: prove the 27B speedup, then integrate another engine backend

Current result: supported admin/host/gateway functionality is deployed/enabled for both profiles, with MLX default. The remaining tasks concern broader qualification and the partial integrated benchmark/adoption evidence. See [verified rollout](../ops/splash-phase3/rollout-report.md).

Date: 2026-10-24
Status: Phase 0 baseline feasibility accepted by the user on 2026-10-27. Further Phase 0
experiments are deferred; their measured outcomes remain unchanged. Phase 1 shared backend development is complete under the [focused implementation plan](20261027_splash-phase1-backend-contract.md).
Production adoption and deployment remain separate decisions; MLX remains the default.
The 87% figure counts 20 of 23 parent checklist items. Repository presentation,
supported telemetry/monitoring and actual gateway/platform qualification are complete;
no Phase 0 qualification condition is rescored.

## Summary

Improve the speed of the existing Qwen3.8-27B workload by evaluating
[incoai/Splash](https://github.com/incoai/splash) against the current MLX server on the
same Mac. The isolated experiment and measured reports establish baseline feasibility. On 2026-10-27,
the user chose to proceed with backend integration development and defer remaining Phase 0
qualification; the earlier all-gates prerequisite is superseded for development only.
A negative result is a valid completed experiment; keep MLX and retain the findings.

Develop Splash beside MLX and Strata as a separately managed, opt-in engine after Phase 1.
Represent engine, model and runtime settings separately, and replace the repeated
MLX/Strata branching with a small, typed backend registry. Preserve the existing
single-engine policy, lifecycle job slot and stable LiteLLM endpoint.

## Scope and non-goals

- First deliverable: benchmark tooling, compatibility fixtures and an evidence report.
- Conditional second deliverable: shared backend interfaces and an opt-in Splash adapter,
  host wrapper, engine selection, chat, quality checks, logs and observability.
- Initial target: the currently used 27B model, including any locally selected derivative.
  The tracked registry includes both Qwen3.8-27B 4-bit and 8-bit; confirm the actual
  selected checkpoint and profile during preflight rather than assuming either.
- Do not change the default engine based on upstream benchmark claims. Backend integration
  development may proceed under the 2026-10-27 decision; live rollout requires separate approval.
- Do not add an inference proxy, concurrent residency of full engines, a plugin loader,
  a universal model downloader, or a refactor of every sidecar. Flash-Next on Splash is a
  possible later experiment, not part of proving a faster 27B.

## Existing system and integration touchpoints

The admin is an asynchronous FastAPI control plane with synchronous host operations
wrapped in jobs/worker threads, HTTP inference clients and SQLite observability.
Inference runs on macOS/Metal; the Linux devcontainer can test tooling and adapters,
not measure Apple GPU performance.

| Area | Current behavior | Planned use/change after the gate |
|---|---|---|
| `engines.py` | `EngineKind`, `Target`, `EngineOps`, `plan_transition`, `StrataClient`, `ComfyClient`; several explicit per-engine branches | Backend registry and common transition inputs; retain `Step` and `Transition` |
| `control.py`, `jobs.py` | Serial lifecycle slot, intent, observed process generation, rollback | Same coordinator and exclusion rules for every registered backend |
| `mlx.py`, host wrappers | MLX and Strata share port 8080; identity checks keep the listener from being misidentified as MLX | Identify all owners, including Splash and unknown listeners; never infer identity from a successful health request alone |
| `chat.py`, `quality.py`, `quality_runner.py`, `compatibility.py` | `ChatTarget` exists; request settings, health/context and quality paths still branch on engine | Resolve inference target and effective request settings through the backend |
| `strata_usage.py`, `ingestion.py`, `performance_store.py` | MLX log ingestion and durable Strata counters/request ingestion already exist | Reuse storage/rollup contracts with a distinct source; preserve existing deduplication |
| `app.py`, `static/app.js`, templates | Engine selector, status, test chat, quality/results, performance and usage | Render backend descriptions/capabilities without adding another set of special cases |
| `sidecars.py`, `watchdog.py`, `alerts.py`, `availability.py`, probes | MLX coexistence rules; Strata/ComfyUI exclusivity and engine-specific recovery | Backend resource policy, identity-aware health/recovery, meaningful availability |
| `host/`, `ops/host-files.list`, deployment examples | Versioned host wrappers, shared lifecycle/maintenance locks, separate environment records | Splash wrapper and configuration, all reciprocal host guards, pinned runtime inventory |

## Upstream feasibility and constraints

Research checked 2026-10-24; recheck against the exact release selected for the experiment.

- Splash is its own Metal inference runtime using a DFlash2 draft, not an MLX server flag.
  Upstream lists Qwen3.8-27B with MLX and GGUF inputs, and requires M3-or-newer hardware
  and macOS 26.4+. These are eligibility claims, not a measured speedup here.
  [Upstream README](https://github.com/incoai/splash/blob/1.4.0/README.md).
- Release 1.4.0 provides a standalone macOS arm64 archive and checksums. Use a pinned,
  verified archive for the isolated experiment instead of altering the system package
  manager. Record the runtime, target and draft revisions separately.
  [Release assets and notes](https://github.com/incoai/splash/releases/tag/1.4.0).
- `/health` checks HTTP liveness; `/ready` checks engine readiness. `/status` is JSON;
  `/metrics` is Prometheus, unlike Strata JSON metrics. Model aliases are configurable
  with `--served-model-name`. Local checkpoints can be prepared through
  `install.local.prepare_local_model` using Splash-owned Python. Audit the pinned
  release before using that path; a local directory is not assumed to work with
  `splash serve --model` directly.
  [Integration documentation](https://github.com/incoai/splash/blob/1.4.0/DEVELOPMENT.md).

The design document records an eligible Mac configuration, but preflight must verify it
again. Exact checkpoint, architecture, quantization, tokenizer, template and draft
compatibility remain unproven, especially for a modified/abliterated model. Never silently
substitute the stock checkpoint for a derivative or 4-bit weights for an 8-bit baseline.
A stock-model result can inform a separate choice; it does not establish a speedup for
the current weights. Do not require identical generated text across different runtimes;
use deterministic tasks and explicit quality checks.

## Phase 0A — Build the experiment only

- [x] 🟩 Record a read-only preflight: actual engine/model/profile, hardware/OS, free disk,
  memory pressure/swap, model asset hashes and tokenizer/template, serving configuration,
  running sidecars and recovery intent. Resolve SSH target through `MAC_HOST`/`MAC_DEST`
  and the existing deploy-env mechanism; never put deployment facts in tracked output.
- [x] 🟩 Add failing tests in `tests/test_splash_phase0.py` for fragmented/multi-token SSE,
  reasoning-only deltas, final usage, missing counts, HTTP/SSE failures, cancellation,
  timeout, settings translation, result comparability and restoring experiment state.
- [x] 🟩 Add `ops/splash-phase0/` with an audited launch/capture runbook, a small HTTP
  benchmark runner and a manifest schema. Reuse measurement concepts from `quality.py`
  and existing Phase 0 scripts; do not copy the old Strata runner unchanged: it uses
  Strata-specific model/process handling and falls back to counting chunks as tokens.
- [x] 🟩 Test malformed/partial samples and the comparison calculation first, then add a
  report command that rejects incompatible manifests or insufficient valid samples.
  Errors, early stops and missing metrics stay visible; never discard them to improve
  the reported speed. Run the tooling tests before touching the Mac.

Implemented tooling interface:

```bash
# Inside the devcontainer, against an explicitly established SSH tunnel to the Mac.
uv run python ops/splash-phase0/bench.py run --manifest /tmp/27b-mlx.json --out /tmp/27b-results
uv run python ops/splash-phase0/bench.py run --manifest /tmp/27b-splash.json --out /tmp/27b-results
uv run python ops/splash-phase0/bench.py compare --results /tmp/27b-results --out /tmp/27b-report.md
uv run pytest tests/test_splash_phase0.py
```

Manifests contain backend, base URL, actual model identity/revisions, effective generation
settings, corpus/version/hash, context/cache policy, concurrency, repetition order and
sample count. Credentials come from environment only. Raw local manifests/results remain
untracked; only scrubbed fixtures and the aggregate report enter this public repository.

### Initial Phase 0 delivery checkpoint (first window) — 2026-10-25

Nine of 23 tasks are complete. The [runbook](../ops/splash-phase0/README.md),
[manifest schema](../ops/splash-phase0/manifest.schema.json), frozen initial corpus,
checkpointed runner and independent window supervisor implement the isolated experiment.
The intentionally unfinished example cannot qualify an engine. Private inventory distinguishes
the original serving engine, inactive MLX selection and two explicitly selected benchmark targets.

The user approved the [two-hour window](../ops/splash-phase0/benchmark-window.md). The pinned
Splash archive and DFlash2 config/weights were verified and installed only in the isolated
area. Stock 27B 8-bit and modified 27B 4-bit each completed one MLX-then-Splash paired
session: 60 measured requests plus 12 excluded warmups, with no benchmark errors or early
stops, identical paired prompt counts and zero reported prefix reuse. All four target/runtime
combinations passed the selected text smoke checks. Scrubbed actual protocol/probe fixtures
are under `tests/fixtures/splash-phase0/`; raw data remains private.

Observed client output/wall throughput gains were 2.20–3.08× for stock 8-bit and 1.95–2.70×
for modified 4-bit across the short/code/prose tasks. Native timing intervals are recorded
separately and do not qualify the decode gate. Only one of three required paired sessions
fit the window; p95, confidence-bound and full adoption claims remain unqualified. See the
[report and evidence](../ops/splash-phase0/report.md) for distributions and raw-run digests.

The original engine/admin passed identity, real inference and health checks; reviewed snapshot
verification passed, experiment processes/port were gone, original sidecar states/intent were
preserved and both targets' weight/tokenizer/template hashes were unchanged. Gateway liveness
and readiness passed, but the existing read-only key exposes no routes, so authenticated
end-to-end gateway inference remains unverified. No production source, registry or default changed.

Validation: 135 focused tests pass, with 81.98% standalone branch-aware tooling coverage.
The full suite passes with 2,243 tests, one skip and 98.10% application coverage; Ruff,
strict mypy, shell syntax, instruction-file parity and supplemental public-safety checks pass.
See the [devlog](../devlogs/2026-10-25.md) for validation details.
The configured private denylist is unavailable; supplemental deployment-derived scans pass,
but changes remain unstaged/uncommitted until the required gate can run.

### Continuation preparation — 2026-10-25

Added a test-first [offline continuation planner](../ops/splash-phase0/continuation.py). It
validates complete paired evidence without dropping failures, preserves frozen conditions and
alternates the remaining session order. Budgets use the slowest recorded request per case,
including warmups, plus a declared slowdown margin, setup/switch and restoration reserves.
Whole pairs must fit; output overwrite is refused; no HTTP, SSH or lifecycle operation runs.

The [worksheet](../ops/splash-phase0/continuation-windows.md) specified two additional windows,
each capped at two hours, with original-service restoration and verification between them.
Each estimated 107.20 minutes including reserves and covered one remaining paired session for
both selected checkpoints. The user approved both windows. Sessions 2 and 3 completed and
proved original service restoration and real inference within their bounds. Fresh inventories,
identity/readiness checks and unchanged asset fingerprints were reviewed before activation.

The [pinned native timing audit](../ops/splash-phase0/native-timing-audit.md) confirms different
emission/completion boundaries and MLX's reconstructed `predicted_ms`. Keep the original wall
metric and frozen session-1 evidence unchanged; completing sessions 2 and 3 still cannot pass
the native decode gate. Preparation adds no measured samples or completed matrix task.

Validation: 30 new planner tests pass; the combined focused suite passes 165 tests with 84.15%
branch-aware tooling coverage (97% for the planner). The full regression run exposed an existing
download-test timing race: its first download could reach publishing before the final running
assertion. The test now retains the busy rejection check and verifies successful first-download
completion; its isolated regression passes. The full rerun also exposed a pre-existing
log-rotation submission/completion race. Three deterministic failing callback-order regressions
confirmed it; a narrow metadata-lock/merge fix now passes 115 maintenance/job/control tests at
100% log-rotation branch coverage. See the [debug plan](20261025_log-rotation-completion-race.md).
No live deployment has occurred. The final full suite passes: 2,276 tests, one skip, 98.11%
application coverage. Ruff and strict mypy pass. Results and public-safety limitations are
recorded in the [devlog](../devlogs/2026-10-25.md).

### Three-session timing result — 2026-10-25

Both approved continuation windows completed. Each selected checkpoint now has three paired
sessions in MLX-first, Splash-first, MLX-first order, with 90 measured requests and 18 excluded
warmups per checkpoint. All 180 measured requests and 36 warmups completed without benchmark
errors or early stops, at their frozen output caps with matching paired prompts and no cache
reuse. Original service restoration and real inference passed within every approved bound.
Independent admin, sidecar, configuration and target-asset reviews passed; session 2's expanded
read-only audit completed later, before session 3, as recorded in the report.

Stock 8-bit: paired client wall ratio 2.49× (bootstrap 95% interval 2.20–3.09×); pooled TTFT
and fixed-length completion gates pass.

modified 4-bit: paired client wall ratio 2.22× (bootstrap 95% interval 1.94–2.78×); pooled
TTFT and fixed-length completion gates pass.

Native timing boundaries remain incomparable; these client results do not qualify native
decode or task quality. Required context/replay/concurrency/service-mix/restart and gateway
inference checks remain pending. Nine of 23 full-plan tasks remain complete (39%); no backend
integration or deployment is implied. See the [final report](../ops/splash-phase0/report.md).

### Offline qualification preparation — 2026-10-26

Added the [qualification harness plan](20261026_splash-qualification-harness.md) and
[run guide](../ops/splash-phase0/qualification.md). A separate native emission trace validator
requires explicit engine token batches, finalized IDs and monotonic callback timestamps;
structural validation never qualifies native decode. Actual pinned callback adapters and an
equivalent-boundary/overhead audit remain prerequisites for a new native series.

The quality pilot freezes 35 deterministic cases, reuses existing graders and captures private
nonstreaming replies without executing generated code/tools. It refuses stale/draft manifests,
validates accounting and cache reuse, checkpoints cancellation/errors, stops on the first
failed/truncated request and recomputes paired case/category scores with approximate
conservative uncertainty. Identical inputs cannot inflate sample size; required single-tool
cases reject extra calls. A small all-tied pilot remains inconclusive for the 2-point margin.
Quality uses its own versioned contract and cannot rewrite the completed speed comparison.

Prepared private drafts for both selected checkpoints from their saved complete source pairs.
Each proposed quality-only window budgets 70 requests at 60-second deadlines, 15 minutes for
setup/switching and 10 minutes for restoration: 95 minutes within a 120-minute cap. New live
approval, fresh assets/process/preflight reviews and an independent host supervisor remain
mandatory. Context/replay/concurrency/operations campaigns are listed separately in the guide.
No additional live requests, native adapters, service changes, deployment or gate pass occurred;
the parent plan remains nine of 23 full tasks complete (39%).
Validation: 113 new tests pass; 278 focused tooling tests at 87.68% branch-aware coverage.
The full suite passes with 2,389 tests, one skip and 98.10% application coverage; Ruff and
strict mypy pass. The full private scan is now available but flags an existing public cohort
label; staging remains blocked until that collision is resolved.

### Qualification continuation — 2026-10-26

The user requested the remaining Phase 0 work. Added source-pinned native collector bundles,
explicit ID/final accounting and private native-input companions under the
[campaign plan](20261026_splash-phase0-qualification-campaign.md). No adapter is installed
on the Mac. Native accounting tests pass; native equivalence and live overhead remain unassessed.
Fresh read-only preflight first failed with "No route to host". A later retry reaches SSH
but cannot authenticate because the forwarded agent has no identities loaded. No new live
requests, service changes or qualification passes occurred; the parent remains 39% complete.

### Measured quality follow-up — 2026-10-26

The [quality report](../ops/splash-phase0/quality-report.md) records both completed
35-case-per-backend follow-up pairs: stock 8-bit scored 31/35 on both engines; modified
4-bit scored 30/35 on both, with identical per-case passes/failures. All 140 replies have
valid accounting, matching paired prompts, zero reuse and no request errors/truncations;
required tool cases pass. Approximate paired intervals remain [-12.55, +12.55] percentage
points, so the two-point quality margin is inconclusive. The original truncated baseline
pilot and zero-case controller failure are retained separately. Both checkpoints use the
same separately frozen 512-token suite; no score is spliced from the failed pilot.

Original service/admin real inference, intent/configuration/sidecars, target hashes and
cleanup are verified within all attempt bounds, with normal pressure and no new swap.
The original timing evidence is unchanged. Native, secondary matrix, required context/mix,
quality precision and authenticated gateway inference remain open; the parent remains 39%.

### Native collector live pilot — 2026-10-26

The [pilot report](../ops/splash-phase0/native-validation-report.md) records complete
stock/modified collector validations plus the retained incomplete original stock series.
All 90 successful native traces reconcile with input companions and HTTP counts; stop/EOS,
reasoning and cancellation checks pass. Modified MLX's 8K overhead upper bound is 1.0764
against the predeclared 1.05 limit, leaving collector overhead inconclusive. Audit activity
overlapping MLX segments is a disclosed possible timing confound. The native gate remains
unassessed and this does not advance integration. Original service and exact preserved state
are verified restored for all attempts; installed runtimes/defaults remain unchanged.

The user confirmed that 32K context testing is sufficient for now. Near-limit 32K, replay,
concurrency, operations, quality precision and authenticated gateway inference remain open.
Main progress remains 9/23 (39%).

## Phase 0B — Isolated Mac benchmark and decision

The initial approved window was restored inside its two-hour limit. Later live windows require
their own bounded maintenance scope; this completed session does not authorize a rollout.
Run project tooling from the devcontainer; engine/OS operations execute remotely on the Mac.
The primary comparison task below remains partial: all three primary client timing sessions
are complete, while native decode qualification and the secondary matrix remain outstanding.

- [x] 🟩 Prepare an isolated directory, e.g. `/Volumes/Models/splash-spike`, containing the
  verified runtime, private configuration, draft, caches, logs and results. Audit writes
  and redirect supported caches/temp paths under it. Keep the existing MLX environment
  and model registry untouched; read shared target weights without modifying them.
  Account for target copies, draft and caches in the disk budget. Stop if required writes
  cannot be confined and document the prerequisite before proceeding.
- [x] 🟩 Capture the original engine/model/pack, sidecar intent and recovery state; prevent
  watchdog/reconciliation from restarting another engine during the experiment using the
  supported maintenance mechanism. Stop through existing controls and wait for verified
  process exit before starting Splash. Use loopback and an unused experiment port (8086
  after checking availability), not the production endpoint. Start with all GPU sidecars
  stopped and an explicit memory/context cap and idle-release policy.
- [x] 🟩 Verify exact target compatibility and real inference, then capture scrubbed health,
  readiness, identity, model list, status/metrics, streamed response and log fixtures from
  the pinned version. Explicitly test reasoning off/on, tools, structured output,
  cancellation and image input if used by the current workload.
- [ ] 🟥 Run the primary comparison below with the same 27B checkpoint. If local preparation
  or its draft is incompatible, report that blocker. Run a separate stock-27B comparison
  only with explicit model identity and quality labels; it cannot pass the current-model gate.
  **Partial:** the counterbalanced client series and separately frozen native-emission
  series are complete within their published scopes. Representative quality precision and
  broader secondary/adoption qualification remain outstanding; old failures are retained.
- [x] 🟩 Publish the benchmark report and a pass/fail/inconclusive decision with raw-sample
  references, distributions and settings. Only then decide whether Phases 1–3 are justified.
- [x] 🟩 Always stop Splash and verify its process group/port are gone and memory is released;
  restore the exact previous engine/pack/model, sidecar intent and recovery state on success,
  failure or interruption. Check original serving readiness and host/config drift. If an
  external operator changed intent, do not overwrite it; report incomplete restoration.

### Benchmark matrix and measurement contract

Use one frozen, public-safe corpus of varied coding, instruction and prose tasks. Include
realistic multi-turn/tool histories. Avoid only repetitive filler, which may artificially
favor speculation. Hold temperature, supported seed, output cap, reasoning behavior and
context limits fixed; record effective settings, not just requested JSON fields. Use
512- and 1024-token generation tasks with actual output lengths reported. Match weights,
KV precision and supported settings where possible; label any remaining precision/feature
tradeoff. Compare operational defaults separately rather than hiding their differences.

| Scenario | Purpose and metrics |
|---|---|
| Fresh short, ~2K and ~8K prompts, concurrency 1 | Primary 27B interactive decode rate, end-to-end latency, first output token and first visible answer |
| Fresh ~32K prompt | Uncached TTFT and prefill; separately measure native prefill if exposed |
| Replayed ~8K/~32K prefix and multi-turn extension | Cache benefit, measured reused tokens and cache policy; do not mix with uncached samples |
| Concurrency 2 and 4 | Secondary aggregate tokens/wall time, per-request latency, queueing/errors; never substitute aggregate speed for single-user speed |
| Reasoning off, then representative reasoning-on tasks | Verify thinking settings take effect; report answer latency and reasoning tokens separately |
| Cold start, idle resume, stop, restart x3 | Time to ready, first request latency, reliable shutdown and memory release |
| Required long context | Probe 128K and the configured production limit, up to 262K, if used; retrieval correctness and memory, separate from the short-context speed gate |

Start with one excluded warm-up per condition and five measured repetitions per prompt
in at least three alternating MLX/Splash sessions (counterbalance order to reduce thermal
bias). Keep replay sequences together and reset/isolate caches for fresh-prefix cases.
Report medians, p95, paired speed ratios and a confidence interval; increase samples when
the interval overlaps the decision boundary. Do not draw a p95 conclusion from a handful
of startup samples. Record host temperature/thermal state, pressure, swap delta, process
footprint/RSS, available memory and cache configuration in every session. After the isolated
result qualifies, repeat representative tests with the intended sidecar/service mix;
sidecar coexistence is a separate opt-in decision.

Client timestamps measure TTFT to the first nonempty reasoning/content event, time to first
visible answer and completion latency. Token counts come from validated usage or exact
matching tokenization, never SSE event counts: speculative output can contain several
tokens in one event. Missing counts mean null rate and an ineligible sample, not zero or
an estimate. Use server-reported decode timing when available; label a client-observed
output-token/wall-time rate separately and use the same definition for both engines.
Prompt tokens / TTFT is an effective input rate, not native prefill throughput. Report
uncached input and reused input separately. Include the memory used by the draft and
runtime children; a Metal allocation cap is not a whole-process memory cap.

### Proposed adoption gates

These are proposed project targets, not upstream guarantees. Freeze them before measurement.

| Gate | Pass condition |
|---|---|
| Exact model | Same target checkpoint/tokenizer/quantization as the current 27B baseline; any intentional differences are a separately named comparison |
| Useful speedup | At least **25% higher median single-request decode throughput** on the predeclared mixed short/2K/8K workload, using the median of per-prompt paired ratios; lower 95% confidence bound above 1.0; no primary workload category regresses |
| Interactive latency | No more than **10% p95 TTFT regression** on matched fresh prompts or replayed prompts, assessed separately; no regression in median end-to-end time on fixed-length tasks |
| Correctness | Every required deterministic/tool/schema/cancellation smoke case passes; task-eval score falls by no more than 2 percentage points on the fixed suite, with uncertainty reported; no new crashes, timeouts or malformed streams |
| Resources | No new swap growth attributable to steady-state tests, no critical pressure, at least 6 GiB measured host headroom under the intended service mix; three successful start/stop cycles |
| Context and operations | Required production context works, identity and model alias checks pass, clean shutdown/restore works, and no unresolved required capability is missing |

If there is a narrow win (e.g. coding only or lower context), report an optional profile
with that limitation rather than a general replacement recommendation. If samples are
incomplete or a model mismatch remains, decision = inconclusive. A failure stops full
integration under this plan until the user chooses a revised scope.

## Phase 1 — Shared backend contract (development authorized 2026-10-27)

The [focused Phase 1 plan](20261027_splash-phase1-backend-contract.md) is the current
implementation sequence and supersedes the earlier all-gates prerequisite for development.
Keep this a local, typed adapter design, not a framework. MLX remains the default.

| Concept | Responsibility |
|---|---|
| `ModelAsset` | Logical checkpoint, revision/digest, tokenizer/template and physical format/location; never implies the runtime |
| `EngineSpec` / registry | Stable ID (`mlx`, `strata`, `splash`, plus existing `comfyui`), label, enabled state, capabilities, deadlines and resource/coexistence policy |
| `EngineTarget` | Engine ID + validated backend profile/model reference; preserve existing `mlx:<name>`, `strata:<pack>`, `comfyui`, `off`; add `splash:<profile>` |
| Runtime profile | Engine-specific validated settings and asset references: MLX flags, Strata pack, or Splash target/draft/context/cache settings; do not pass one engine-specific set of flags to another |
| `EngineBackend` protocol | Observe process/identity/generation/readiness, list allowed targets, construct start/stop steps, expose logs and optional inference/telemetry adapters |
| Inference capability | `ChatTarget`, public model aliases, request-setting translation, supported APIs/features and effective context; ComfyUI has no LLM capability |
| Normalized observation | Backend/profile/model, process generation, running/ready/loading/released/recovering state, capabilities, optional metrics and telemetry source/coverage |

- [x] 🟩 Add characterization tests for current MLX/Strata/ComfyUI/off transitions and intent,
  including failed stop/readiness, rollback boundaries, sidecar restoration and unknown
  listeners; retain all current behavior before changing the coordinator.
- [x] 🟩 Introduce a registry and adapters around existing clients. Migrate observation,
  target parsing and transition construction first, then chat/quality/status consumers.
  Replace boolean parameters per engine with backend observations; do not add a growing
  list of `splash_running`, `splash_ready`, etc. Unknown backend IDs must fail explicitly,
  never fall through to MLX stop/start.
- [x] 🟩 Test engine-pair transitions parametrically, profile switches within an engine,
  stale/reused PIDs, another engine on the same port, duplicate/conflicting ownership,
  cancellation and an external switch during a quality run. Then migrate callers to the
  shared interface without altering public target strings or existing stored identities.
- [x] 🟩 Resolve recovery and resource policy through capabilities: initially Splash excludes
  GPU sidecars; readiness distinguishes alive from loaded/released/recovering. Keep
  automatic restart opt-in and avoid fighting Splash internal recovery. Preserve desired
  intent and prevent recovery during maintenance or lifecycle/quality jobs.

## Phase 2 — Splash adapter and host integration, only after Phase 1

The [focused Phase 2 plan](20261027_splash-phase2-adapter-and-host-integration.md)
starts from Phase 1 commit `d1ab6d6` and defines six test-first implementation packages.
Phase 2 repository integration is implemented: private contracts, whole-bundle ownership,
owning wrapper, reciprocal guards, adapter, inference/current-only quality and capability
presentation. Full regression passes (3,189 passed, one skipped, 98.20%). Browser rendering is closed by the Phase 3 Chromium checks; actual gateway qualification
remains open; live installation and rollout stay in Phase 3.

- [x] 🟩 Write host-wrapper tests first for start/stop/status, validated profile/env parsing,
  lock contention, port conflict, process ownership, missing volume/assets, failed startup,
  SIGTERM timeout and readiness. Then add `host/splash/bin/splash`, env/profile examples,
  README, `ops/host-files.list` entries and pinned runtime inventory.
- [x] 🟩 Add a `SplashBackend` using only its host wrapper and HTTP; the admin never imports
  Splash or uses its Python environment. Start one configured profile; track parent and
  native child generation; stop only verified owned processes and await release. Reuse
  shared lifecycle/maintenance locking. Add reciprocal guards to MLX, Strata, ComfyUI
  and GPU service wrappers so direct CLI operations enforce the same exclusion policy.
- [x] 🟩 Implement the serving contract with explicit model identity and actual authenticated
  gateway verification. Preserve the original MLX route; use correctly named Splash profile
  routes and native aliases on a dedicated endpoint. Keep8080 as the compatibility default.
  Verify bind/Host/auth, complete streams, context, cancellation and inactive-route refusal.
  This recorded endpoint decision replaces the unsafe shared-port deployment assumption:
  native absolute aliases are invalid and MLX can unload on an unknown alias. No new proxy;
  original public model identity/scopes stay unchanged and secrets remain private.
- [x] 🟩 Test and add registry-driven selector/status/badges/logs, inference target resolution,
  reasoning settings, compatibility/eval runs, context reporting and engine/profile history.
  Feature-gate Splash off by default. Show unsupported capabilities and loading/idle states
  accurately, and preserve existing API fields while adding backend descriptions.

## Phase 3 — Telemetry, qualification and rollout

The [focused Phase 3 plan](20261028_splash-phase3-telemetry-qualification-and-rollout.md)
starts from Phase 2 commit `c25078c`. Supported repository functionality is implemented,
including profile/epoch-bound cumulative snapshots, explicit coverage/gaps, independent
bounded collection, scoped availability and backend-aware monitoring/process metrics.
The [operating guide](../ops/splash-phase3/README.md) documents schema-16 rollback and
the frozen prospective measurement worksheet. Supported Mac/admin/gateway functionality is now deployed on a dedicated endpoint, with
MLX default. All twelve directed switches, both recovery/admin windows, streaming,32K,
cancellation, key scope and inactive-route safety pass. Performance/adoption evidence
remains partial/inconclusive. The parent counts21/23tasks (91%).

- [x] 🟩 Test captured Splash telemetry before adding its adapter. Normalize only available
  counters/timings into existing performance and daily token storage, with backend/profile,
  process generation and source identity. Handle restarts, polling duplicates, missing
  fields and unavailable endpoints; keep old MLX/Strata records and rollups unchanged.
  Audited execution counters are stored separately from daily billed token totals;
  controlled native snapshot/reset attribution now passes; all-client population/billing
  reconciliation and final-stop capture remain unqualified.
- [x] 🟩 Verify whether complete per-request usage for direct/LiteLLM traffic can be obtained
  from supported responses, logs or counters. Do not invent request rows or infer token
  counts from chunks; aggregate-only telemetry must be labeled as such. Document lost
  intervals and unknown cached input, and avoid double-counting gateway and engine data.
- [x] 🟩 Add bounded probes, backend-aware health alerts, availability and last-ingest status;
  test stale and recovering engines. Keep inference HTTP streaming direct and avoid new
  per-token copies, synchronous DB writes, heavy polling or logging on the hot path.
- [ ] 🟥 Run all gates below, then repeat the frozen benchmark through the integrated setup
  and actual LiteLLM route. Compare against the isolated result; investigate material
  overhead before rollout. Update `docs/DESIGN.md` with measured results and limitations.
- [x] 🟩 Present the exact deploy diff, backup, verification and rollback commands for live
  approval. Deploy only after that approval. Smoke-test all engine-pair switches, the
  gateway, required context, sidecars, telemetry and restoration of the original backend.

## Validation and rollback

All repository commands execute inside the active devcontainer as `vscode`, with
`-w /workspaces/local-llm-admin`; Python tools use the existing uv-managed `.venv`.
Every new behavior gets a failing test before implementation. Test early phases separately;
full code integration must pass:

```bash
uv run pytest --cov --cov-fail-under=80
uv run ruff check src tests scripts
uv run mypy src scripts
cmp AGENTS.md CLAUDE.md
uv run python scripts/check_public.py --patterns-file ~/.config/local-llm-admin/public-denylist.txt --git .
```

Also lint/type-check new Python benchmark tooling under `ops/splash-phase0` explicitly,
since the standard commands do not include `ops/`. Require complete coverage of ownership,
transition safety and token accounting decisions. Use fake HTTP and host wrappers in CI;
Mac-only performance tests are explicitly scheduled, not silently skipped as proof.
Scan newly created untracked documents/results explicitly before staging, since `--git`
scans tracked files. Append public-safe devlogs after changes; stage only, never commit.

Rollback: stop the verified Splash process group, wait for endpoint closure and memory
release, select the saved MLX model or Strata pack through the existing control path,
restore service intent and verify the unchanged LiteLLM route. Keep old runtime/env/profile
backups for deployment rollback; do not delete shared model assets or rewrite historical
records. If a new storage field is needed, use an append-only migration with explicit
compatibility/restore tests; pick the next migration number at implementation time.

## Open questions and decision record

Resolve these in Phase 0 without blocking creation of this plan:

1. Which selected 27B checkpoint/revision and profile are the real baseline? Does the
   pinned Splash release/draft support that exact checkpoint, including local modifications?
2. Which reasoning, tools, vision and long-context features are required in daily use?
   Benchmark them explicitly; opt-in service coexistence requires its own resource evidence.
3. What does pinned Splash expose for process identity, idle release and complete usage
   collection, and which stable LiteLLM model aliases must it accept?
4. Do measured gains pass the proposed gates? If not, stop at the report; any different
   quantization/model/context tradeoff needs its own named decision.

Related repository references: [Strata integration plan](20261015_strata-qwen38-flash-next.md),
[token accounting plan](20261022_token-usage.md), [design](../docs/DESIGN.md).

### Near-limit 32K retrieval smoke — 2026-10-26

The user confirmed 32K is sufficient for now. Both selected checkpoints passed six distinct
near-limit retrieval cases per engine, all 24 correct at exact 31,858–31,864 prompt tokens,
with no HTTP errors, reuse or truncation. All declared warmups and independent original-
service restoration checks pass. See the [context report](../ops/splash-phase0/context-report.md).
This fixed synthetic check does not qualify the broader quality margin, native speed,
replay, concurrency, idle/restart, sidecar coexistence or authenticated gateway route.

### Modified-MLX collector overhead follow-up — 2026-10-26

The [separate expanded audit](../ops/splash-phase0/overhead-followup-report.md) meets the
frozen 5% collector-overhead criterion overall and for every category after four complete
independently restored windows. It captures 144 valid requests, 48 measured pairs and 72
accounted native traces; the 8K upper 95% bound is 1.002261. The initial reviewer-failure
attempt and earlier inconclusive pilot remain unchanged and are not pooled. Original
service, assets, configuration/intent and sidecars are restored; temporary owned helpers
are closed. This resolves modified-MLX collector overhead for that profile. Full native
speed, quality precision and secondary requirements still gate integration and deployment;
overall project progress remains 9/23 tasks (39%).

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

Full-project progress remains 9/23 (39%); the primary comparison task still includes secondary qualification. See the [new report](../ops/splash-phase0/native-comparison-report.md).

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

## Scope decision — proceed beyond Phase 0, 2026-10-27

The user accepted baseline feasibility and deferred further Phase 0 experiments. Development
of Phases 1–2 may proceed without resolving every original adoption criterion. Historical
failed/inconclusive/unassessed gates remain unchanged; production rollout is still separate.
Follow the [Phase 1 implementation plan](20261027_splash-phase1-backend-contract.md): typed
registry and existing-engine adapters, observation-based lifecycle planning, consumer
migration and validation. No live change or extra benchmark run is part of that refactor.
Phase 0 progress is committed as `7405b83`; unrelated working-tree changes were excluded.

## Phase 1 implementation — 2026-10-27

The shared registry, existing-client adapters, observation-based planner, execution-boundary
ownership guards and consumer migrations are implemented. Validation is complete; results are in the [focused plan](20261027_splash-phase1-backend-contract.md). The concrete extension
contract and Phase 2 wrapper/profile/ownership handoff are in
[Design §13](../docs/DESIGN.md#13-shared-backend-contract-2026-10-27). No new model-asset
inventory, schema, runtime installation, benchmark campaign or deployment was introduced.
Historical Phase 0 evidence and progress statements above remain historical.

Phase 1 validation: 2,882 passed, one skipped; 98.15% coverage. Contracts, registry/adapters
and new ownership/planning decisions have 100% statement/branch coverage. Ruff, strict mypy,
instruction-file comparison and the private-pattern public scan pass. Changes are staged;
no commit or live deployment was performed. Parent checklist progress is 13/23 (57%).


## Phase 3 Stage A deployment — 2026-10-28

The explicitly approved Stage A installed verified independent runtime/assets/receipts
and the paired host/admin update with schema16. The original MLX generation, selected
model, intentions and gateway configuration are preserved; pre/post authenticated route
checks and deployed code hashes pass. Splash remains disabled with no serving processes.
The matched schema15 backup has integrity `ok`; full app/interpreter/env/plist/host backups
are retained. No rollback was needed, and the temporary SSH relay was closed.

See the [deployment report](../ops/splash-phase3/stage-a-report.md). Phase3 is 18/23 (78%);
full native/gateway/context/cancellation qualification, integrated measurements and
production adoption remain pending. Parent rollout/qualification checkboxes remain open;
MLX stays the default and frozen Phase0 outcomes are unchanged. No commit was created.


## Phase3 functional evidence and lifecycle failure — 2026-10-28

The approved [functional window](../ops/splash-phase3/gateway-contract-report.md) passed29
HTTP checks across both profiles: strict direct/gateway identity, reasoning/content/usage,
near-limit retrieval, security refusals and observed disconnect cancellation. Explicit
temporary upstream routes were used; the original absolute-path production route remains
unchanged and is not claimed to serve Splash. Stock restart and profile switch separated
three epochs. A final owning-stop failure retained a dead receipt; after absence/generation/
listener proof and preserved metadata archival, original MLX/services/intents, disabled
Splash and original authenticated inference were verified.

Phase3 closes only HTTP qualification:19/23 (83%). Parent remains19/23 (83%); original
route/adoption, complete lifecycle/recovery/admin acceptance and frozen performance gates
remain open. The new local stop-race reproduction does not establish the redacted Mac
exception or authorize another live apply. Historical Phase0 failures/inconclusive results
and all earlier approved windows remain unchanged.


## Host shutdown correction attempt — 2026-10-28

The [approved execution](../ops/splash-phase3/shutdown-fix-report.md) deployed only the host
member-inspection correction. Original route checks and two stock streams passed; first
off passed, second off failed. Both modified cycles were aborted. Two independent epochs,
four new readings and idle deduplication passed, but lifecycle remains failed. The sealed
emergency archive refused a macOS trailing-space timestamp mismatch before any metadata
change or signal. Admin is reachable with inference stopped and temporary opt-in/dead
receipt retained; a separate restoration-only packet awaits approval. Ten archive safety
tests pass after one padded-time regression failed first. No previous failure is rescored.
Phase3 and parent progress remain19/23 (83%); no further checkbox closes.


The separately approved restoration-only packet subsequently passed its normalized
parent timestamp and repeated absence proofs, preserved/archived the dead receipt on
its original volume without signals, restored original MLX/actual services/intents and
reloaded disabled Splash. Final health/schema16/integrity, source/config/selection,
no-job/member/listener/receipt/lock cleanup, original gateway inference and unchanged
routing all pass. Five total inference attempts remain counted in the original budget;
25 readings/41 availability rows remain preserved. No qualification restarted and the
failed lifecycle/archive attempts remain failed. Progress stays19/23 (83%). Next work is
private exception evidence for the unresolved Mac stop failure before another lifecycle
proposal; frozen comparison and adoption remain pending.


## Full integration matrix — 2026-10-29

All twelve engine-pair switches and original service/gateway restoration passed under
ongoing user session approval; see the [acceptance report](../ops/splash-phase3/integration-acceptance.md).
The live deployment/smoke checkbox closes:20/23 (87%). Native recovery/admin acceptance,
frozen integrated comparison, measured documentation and final support rollout decision
remain pending. Historical failed attempts and Phase0 outcomes remain unchanged.


## Final functional deployment decision — 2026-10-29

The user's full-support/deployment goal is fulfilled for the supported workflows. The
[rollout report](../ops/splash-phase3/rollout-report.md) records the explicit endpoint/model
identity decision, complete functional tests, matching backups and rollback. The original
absolute MLX upstream is retained; two qualified profile routes use an isolated native
endpoint, avoiding unknown-alias cache displacement. Native controllers/children and
GPU exclusions remain guarded even with a closed shared port. MLX remains default.

The primary broader Phase0qualification and full integrated benchmark/adoption tasks remain
open/partial. The frozen comparison stopped/refused within its scope and is not retried
or rescored; no commissioning sample supplies missing performance conditions. Progress21/23
(91%) reflects completed functionality and the remaining evidence gates honestly.
