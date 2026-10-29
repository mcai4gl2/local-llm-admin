Overall Progress: 96%

# Splash Phase 3 — telemetry, qualification and controlled rollout

Current result: supported functionality is deployed/enabled with MLX default and dedicated profile gateway routes. The remaining checkbox is the partial/inconclusive performance comparison; preferred-backend adoption is deferred. See [verified rollout](../ops/splash-phase3/rollout-report.md).

Date: 2026-10-28
Status: Supported functionality is deployed and enabled for both profiles with dedicated gateway routes; MLX remains the default. Lifecycle, engine-pair, native/admin and final commissioning checks pass. The frozen performance comparison remains partial/inconclusive; preferred-backend adoption is deferred.
Baseline: Phase 2 implementation commit `c25078c`; Phase 2 plan commit `f4e3e88`.

## Objective and inherited decisions

Make the opt-in Splash integration observable, establish its actual gateway/platform
contract and prepare a reversible production rollout. Deliver repository changes and a
concrete deployment worksheet before requesting approval for live changes. Writing this
plan authorizes no installation, deployment, runtime launch, gateway mutation or benchmark
window. Read-only inspection and local implementation/test preparation can proceed.

Use the [parent roadmap](20261024_splash-27b-benchmark-and-engine-backends.md),
[Phase 2 plan](20261027_splash-phase2-adapter-and-host-integration.md) and
[host handoff](../host/splash/README.md#phase-3-installation-and-verification-handoff).
Phase 2 code and automated checks are complete: 3,189 passed, one skipped, 98.20% coverage,
plus a 65-case client/adapter follow-up. Browser rendering review is now closed by
32 Chromium DOM checks plus narrow/wide visual inspection; see the [telemetry/browser evidence](../docs/splash-telemetry.md).

Keep MLX the default and Splash disabled until an approved rollout explicitly opts in.
Retain the pinned original Splash 1.4.0 runtime and audited archive digest from
[the inventory](../host/environment/splash.txt). Stock 8-bit and modified 4-bit remain
separate profiles, prepared outputs and result identities. Initial settings stay at 32,768
context, reasoning on, BF16 KV, FP32 state, disk cache zero and idle release off. GPU sidecars
and other full engines remain excluded. No Splash admin watchdog is added; its internal
recovery remains runtime-owned.

The user's decision to proceed beyond Phase 0 remains effective. Do not reopen a broad
benchmark campaign as a prerequisite for offline development. Preserve every failed,
inconclusive and unassessed result in the frozen Phase 0 reports. Reasoning off remains
unreliable; stock host-reclamation observations remain inconclusive. Repository completion
is not production adoption, and this phase cannot silently waive outstanding qualification.

## Scope and completion boundaries

In scope: Phase 2 browser closeout; pinned telemetry evidence; available request/counter
normalization and durable ingestion; truthful source coverage; backend-aware probes,
alerts and availability; owned process metrics; direct/gateway compatibility; a bounded
integrated comparison; reviewed deployment, verification and rollback.

Do not add an inference proxy, runtime patches, generic plugin framework, downloader,
concurrent engine residency, request-body logging or a full rewrite of existing ingestion.
Keep the lifecycle claim, Step executor, ownership guards, sidecar policy and direct SSE
path. Changes to runtime pins, context, cache/idle policy, automatic recovery or default
engine require a separate explicit decision.

Separate milestones:

| Milestone | Required evidence |
|---|---|
| Repository ready | Offline contracts, tests, browser review, operating docs and deploy/rollback worksheet |
| Live integration verified | Approved Mac window; owned lifecycle, both profiles and actual authenticated gateway checks |
| Production rollout decision | Reviewed telemetry coverage, integrated measurements, remaining qualification limits and proven restoration |

Unsupported telemetry can be a completed investigation with an explicit unsupported
capability and coverage gap. It cannot become a complete-usage claim or an invented request
history. If live qualification fails, restore the original service and keep rollout pending.

## Reviewed code and concrete integration points

Paths below are under `src/local_llm_admin/` unless stated otherwise.

| Location | Finding / intended change |
|---|---|
| `splash.py`, `backends.py`, `backend_types.py` | Existing ownership/readiness and inference metadata; reuse the per-app client and complete generation bundle |
| `strata_usage.py` | Transactional rows/cursors and bounded final readings are useful patterns; its `/metrics` fields and reconciliation guarantees are Strata-specific |
| `performance_store.py`, `performance.py`, `ingestion.py` | Source-keyed durable rows, atomic cursor commits, log rotation/gaps and daily folding; preserve MLX/Strata semantics |
| `storage.py` | Current request columns lack explicit profile fingerprint/bundle metadata; older apps reject newer schema versions, so any migration changes rollback requirements |
| `app.py` performance/usage endpoints | Performance source metadata is MLX-specific; expose source-specific coverage and last successful ingestion without claiming a KV poll is a log cursor |
| `availability.py` | Existing denominator describes MLX intent; Splash currently remains unknown and excluded. Add separately scoped semantics without rewriting historical MLX figures |
| `alerts.py`, `diagnostics.py`, supervisor/probes | Legacy engine intent and conflict text need backend descriptions; reuse observations rather than creating another readiness loop |
| `metrics.py`, app sampler binding | Existing process tile measures the primary controller PID; any bundle/native measurement must verify generations and state what is summed |
| `static/performance.js`, `static/usage.js`, `static/dashboard.js`, templates | Inspect actual consumers before editing; distinguish missing, stale, aggregate-only and measured data |
| `host/splash/lib/control.py`, `host/splash/README.md` | Optional bounded final telemetry only if supported and necessary; stopping/ownership must remain reliable when telemetry fails |
| `ops/deploy-host*.sh`, `ops/deploy-mac.sh`, `ops/rollback-mac.sh` | Existing manifests/backups and reload commands; prepare the complete paired host/admin/config/database rollback sequence |

Potential new parser/client modules and tests are proposed locations, not existing
interfaces. Add only the smallest modules required by evidence. In particular, do not
assume Splash has Strata's `/metrics`, request IDs, process epoch or final-counter endpoint.

## Implementation packages

Work on the current checkout without worktrees. Write failing tests for new behavior and
prove bug regressions before fixes. Keep implementation packages reviewable; commit only
when requested. Live stages follow their own concrete worksheet and approval.

### 1. Close Phase 2 presentation and freeze telemetry evidence

- [x] 🟩 Run a real browser against local fake-backed admin scenarios: disabled, starting,
  ready, released, recovering, unknown and conflict. Check selectors and retained selection,
  profile/32K labels, confirmation/exclusion text, chat/quality controls, log access and
  narrow/wide layouts. Reuse existing tooling; an isolated browser tool is sufficient,
  without introducing an application JS framework. Record browser/version and outcomes,
  add regression checks for defects and close the Phase 2 checkbox only after this passes.
- [x] 🟩 Audit the pinned original runtime's supported API/log/response telemetry and existing
  sanitized captures. Prepare `tests/fixtures/splash/` samples with provenance for supported
  forms, malformed/missing data and unavailable endpoints. Any missing live evidence goes
  on the later capture worksheet; never substitute synthetic data as a live observation.
- [x] 🟩 Write a field/source matrix before implementing ingestion: request ID and its scope,
  controller/native epoch, model/profile binding, start/completion timestamps, token counts,
  cached input, reasoning tokens, timings and completion/error/cancel semantics. Classify
  each field as authoritative, derived with a defined boundary, unavailable or unverified.
  Distinguish admin chat/eval responses from all direct and gateway traffic.

Exit: browser closeout and a versioned telemetry contract, including explicit unsupported
paths. Preparation can continue with synthetic tests, but complete-accounting claims require
actual supported evidence. No production logs, prompts, keys or deployment facts are tracked.

### 2. Specify attribution, persistence and reconciliation

- [x] 🟩 Add failing normalization tests for valid/malformed records, booleans masquerading as
  numbers, negative/non-finite values, missing usage, partial streams, errors and cancellation.
  Preserve nulls. Never count chunks as tokens or label client wall time as native decode.
  If counters alone are supported, identify their reset, timestamp and coverage semantics
  before defining any aggregate record; do not fabricate per-request timings or timestamps.
- [x] 🟩 Freeze one canonical engine source such as `splash`, profile identity/fingerprint and
  a verified generation/epoch key. Test same model alias across profiles, profile edits,
  native recovery under an unchanged parent, restart/PID reuse and ambiguous intervals.
  Attribute historical rows from captured generation evidence, never today's selected profile.
  Native recovery invalidates only the counters whose reset scope actually changes.
- [x] 🟩 Test whether existing rows/keys and KV metadata preserve the required identity through
  queries, retention and daily folding. Add a minimal additive migration only if necessary;
  test fresh and upgraded databases, interrupted/repeated migration and unchanged historical
  MLX/Strata results. Do not silently merge profiles/fingerprints in rollups. Specify schema
  rollback before landing a migration: an old binary cannot open a newer schema today.
- [x] 🟩 Add tests for duplicate/out-of-order polls, log rotation/truncation, replay after admin
  restart, bounded histories, late final samples, counter resets and source overlaps. Commit
  rows and cursors atomically. Reconcile aggregates against request rows only where the
  supported contract proves they cover the same epoch, population and fields. Otherwise
  expose separate views and gaps; never add overlapping gateway and engine counts together.

Exit: tested source/identity/storage contracts. Aggregate-only, lost intervals, unknown cache
counts and incomplete per-request coverage remain visible in API/UI and exported results.

### 3. Implement bounded ingestion and source-aware presentation

- [x] 🟩 Add the smallest evidence-backed Splash telemetry adapter, reusing the injected client,
  existing ingestion/store transactions and retention. Bound endpoints, bytes, files,
  records, polling frequency and worker time. Disablement schedules no Splash HTTP polling;
  disabled local ownership checks still remain active. Separate poll success, last new data,
  stale/error state and observed coverage. A telemetry error cannot stop the supervisor.
- [x] 🟩 If a final reading is supported and materially closes a known loss interval, test and
  implement a bounded pre-stop capture with generation/profile binding. A capture failure
  must not prevent verified stop or resource release. Replay must be idempotent, bounded
  on disk and safe after a newer generation starts. Without this interface, document the
  shutdown gap rather than inventing a final-counter guarantee.
- [x] 🟩 Extend performance/token queries and presentation with source/profile identity,
  capability, coverage, last-ingest and stale/error indicators. Preserve existing response
  fields and historical totals; keep LiteLLM usage and engine usage explicitly scoped.
  An admin-only response collector must be labeled admin-only and cannot represent external
  clients. Reading/following logs alone must not insert inference rows.

Exit: available data is durable and attributable; unsupported data stays unavailable.
The direct SSE path gains no per-token DB writes, ownership scans, heavy logging or new
payload copies. If no all-client telemetry exists, that limitation is a reported outcome.

### 4. Make monitoring and process metrics backend-aware

- [x] 🟩 Add failing probe/alert tests for explicit Splash profile intent, missing/disabled
  owners, loading, ready, released, recovering, stale/malformed responses, unknown ownership
  and conflicts. Use registry labels and capabilities rather than extending engine-name
  lists. Reuse bounded monitor observations; keep telemetry freshness separate from serving
  readiness, suppress expected transition noise and preserve MLX-only automatic recovery.
- [x] 🟩 Freeze Splash availability semantics before writing samples: intended profile,
  loaded readiness, recovering/released intervals, deliberate off, jobs/maintenance and
  unknown observation gaps. Add backend-scoped samples/queries or additive fields as
  required. Preserve the existing MLX endpoint meaning and historical denominator; do not
  reinterpret pre-feature time as Splash downtime or silently backfill availability.
- [x] 🟩 Test and implement only supported process measurements. If summing controller/native
  RSS/CPU, revalidate each PID/creation time, handle partial permission failures and child
  replacement, avoid counting a PID twice, and identify the measured bundle in the response.
  RSS sums are not unique physical/unified-memory totals. Native allocator/host pressure
  readings need their own names and provenance; do not claim they resolve reclamation.

Exit: no false MLX health, stale-data success or automatic Splash restart. Unsupported or
partially observed measurements remain unknown rather than zero or healthy.

### 5. Prepare reviewable operating and qualification artifacts

- [x] 🟩 Assemble the offline deploy diff and public-safe runbook with exact versioned host
  files, admin revision and any schema transition. The new guard and all reciprocal owning
  wrappers form one deployment unit. Document private runtime/assets/env/receipts as separate
  operator inputs; versioned deploy does not install or overwrite them. Link this plan from
  design/host docs and record repository validation results.
- [x] 🟩 Prepare a private worksheet resolving `MAC_HOST`/`MAC_DEST` through the existing
  deploy-env mechanism. Read-only inventory/diffs are allowed; keep outputs private.
  Include current deployed hashes, original engine/profile/intent/services, actual gateway
  public model name versus upstream model string, Host policy, both auth paths, runtime and
  asset receipts, disk/memory headroom, exact backup paths and verification requests.
  Reference private env/credential files instead of copying keys into commands, argv,
  shell history or reports; never commit the deployment worksheet.
- [x] 🟩 Freeze the prospective integrated measurement worksheet: corpus/manifest revisions,
  both checkpoint identities, runtime pins, 32K/reasoning/cache settings, warmups/repetitions,
  request caps, endpoint boundaries, sample accounting, comparison thresholds and maximum
  window with restoration reserve. Reuse the retained frozen benchmark where comparable;
  keep direct/admin-enabled and actual gateway observations separate. Freeze an overhead
  comparison with ingestion on/off where feasible. No new statistical or adoption pass is
  inferred from smaller or changed conditions. Defer broader Phase 0 work unless requested.
- [x] 🟩 Make rollback executable and reviewable before approval: host manifest backups,
  admin app/environment/LaunchAgent, private profile/route snapshots and a consistent SQLite
  backup including WAL handling. Test restoring the pre-migration database with the old app.
  State what post-backup records would be lost, preserve them privately before restoration,
  and choose a reviewed rollback method; copying only a live `.sqlite` file is insufficient.
  Include backup retention because deployment scripts may replace their previous-app copy.

Exit: concrete commands, expected results, bounded budget and abort/restore actions are
ready for review. No generic approval request should precede these artifacts.

### 6. Execute approved Mac/gateway verification and comparison

The user explicitly approved completion/deployment in this session. The recorded packets below define each bounded operation; future sessions still require authorization.

- [x] 🟩 Present the completed private worksheet and exact target-derived commands, deployment
  diff, backup/restore points, traffic impact and time budget. Obtain approval before
  `ops/deploy-host.sh --apply`, `ops/deploy-mac.sh`, runtime installation/launch, gateway
  changes or benchmark traffic. Approval covers only the described window and actions.
- [x] 🟩 After approval, verify the fresh baseline and backups, apply the paired host/admin
  changes and opt in deliberately. Validate macOS `ps`/`lsof` executable and creation-time
  checks, locks/signal cleanup, parent/native ownership and resource release. Exercise both
  profiles and engine-pair/off transitions with original-profile rollback; GPU sidecars
  remain excluded. Restore the original service after each independently bounded window.
- [x] 🟩 Run direct and actual gateway authenticated inference for both profiles, reasoning
  on and required 32K checks. Verify unknown-model, invalid auth and Host refusals, streaming
  reasoning/content/final usage when reported, disconnect cancellation and a subsequent
  successful request. Splash cannot alias an absolute MLX path: any upstream-name change
  must be explicit and separately included in the reviewed gateway diff. Restore the
  original route/credentials as well as the engine when closing the window.
- [ ] 🟨 Capture supported telemetry and run the frozen integrated comparison within budget.
  Retain failed/partial attempts, unknown counts and intervals; verify deduplication and
  profile/generation attribution through switches/recovery. Compare like endpoint/timing
  boundaries and investigate material overhead before proposing rollout. Confirm original
  authenticated gateway inference, admin health, intent/services, asset/config fingerprints
  and owned helper/listener cleanup before declaring restoration successful.

Exit: a bounded verification/measurement report with pass/fail/inconclusive/unverified
outcomes and proven restoration. A refusal or failed test leaves rollout pending; it does
not authorize repeated windows, threshold changes or automatic adoption.

### 7. Review results and decide rollout

- [x] 🟩 Publish sanitized implementation/measurement evidence, supported telemetry matrix,
  browser/Mac/gateway results, remaining Phase 0 limits and integrated overhead findings.
  Explain whether per-request coverage is complete, partial, aggregate-only or unsupported.
  Update design, parent/phase checkboxes and append the devlog only for completed work.
- [x] 🟩 Present a separate production rollout decision with exact configuration/route changes,
  operational monitoring, recovery responsibility, stop criteria and rollback commands.
  Keep MLX the default unless the user explicitly chooses otherwise. Declining rollout is
  a valid final outcome; do not mark deployment or adoption complete from repository tests.

## Validation and performance budget

All repository commands run in the active devcontainer as `vscode`, with working directory
`/workspaces/local-llm-admin`, using the existing uv environment. No worktrees or host venvs.
Run focused suites as packages land; new test module names depend on the evidence-backed
adapter chosen. Existing regressions include:

```bash
uv run pytest -q tests/test_splash.py tests/test_splash_backend.py tests/test_splash_ownership.py
uv run pytest -q tests/test_host_splash.py tests/test_splash_host_failures.py tests/test_host_deploy.py
uv run pytest -q tests/test_performance.py tests/test_performance_store.py tests/test_performance_api.py tests/test_strata_usage.py tests/test_ingestion.py
uv run pytest -q tests/test_metrics.py tests/test_diagnostics.py tests/test_alerts.py tests/test_availability.py tests/test_watchdog.py
uv run pytest -q tests/test_chat.py tests/test_eval_api.py tests/test_eval_runner.py tests/test_quality_runner.py
```

Run process/listener suites sequentially when they share the real test port. Browser checks
must execute actual DOM flows, not just the existing Node decision harness. Final gates:

```bash
uv run pytest --cov --cov-fail-under=80
uv run ruff check src tests scripts
uv run mypy src scripts
uv run ruff check host/splash/lib
uv run mypy host/splash/lib
cmp AGENTS.md CLAUDE.md
git diff --check
uv run python scripts/check_public.py --patterns-file ~/.config/local-llm-admin/public-denylist.txt --git .
```

Keep new ownership, stop/rollback, accounting/deduplication and migration decisions at
100% statement/branch coverage; overall coverage remains at least 80%. Check modified
shell and JS syntax plus actual browser behavior. Scan all new untracked files explicitly
before staging. Append public-safe devlogs; no real host/account/key/path facts in plans,
fixtures, report samples or commit messages.

Disabled Splash adds zero telemetry HTTP calls. Reuse one bounded readiness snapshot per
monitor cycle, and schedule telemetry independently with explicit request/overall timeouts
and per-poll record/byte limits. A slow parser/probe cannot delay lifecycle cleanup or the
watchdog. Background batching/atomic commits must not block SSE forwarding; measure added
latency using the frozen comparison rather than assuming it is negligible. Status freshness
and data completeness are separate fields, not one success flag.

## Current status and unresolved inputs

Eighteen of 23 tasks are complete. All supported repository functionality is implemented:
versioned evidence/normalization, verified profile/full-bundle epochs, schema-16 independent
snapshot/availability tables, atomic deduplication and bounded retention, an independent
single-worker monitor, source-aware APIs/UI, registry-driven alerts/diagnostics, scoped
availability and verified partial bundle metrics. Request rows/daily billed token totals
and historical MLX availability remain unchanged. Unsupported request-history/final-stop
interfaces are documented outcomes; no proxy, fabricated request ledger or final capture
was added. Native reset/population qualification is explicitly unverified, so snapshots
are never reconciled with billed tokens or silently summed.

The operating/rollback guide and frozen prospective design are in
[ops/splash-phase3](../ops/splash-phase3/README.md). Tested SQLite backup includes committed
WAL pages; an interrupted migration rolls back and the restored pre-v16 database is
usable by the old app. The paired host guard/wrappers remain one deployment unit.
Before Stage A, read-only inventory found schema15, no managed Splash installation and
an absolute MLX gateway upstream. The verified source commits and explicit tokenizer
overlay were then frozen in a private packet. The user approved Stage A: independent
runtime/assets/assemblies and actual resolved-output receipts are now installed, paired
host/admin code and schema16 are deployed, and both original-route checks pass. Original
MLX generation/selection/intents and gateway configuration are preserved. Exact private
inputs, named matching backups and immutable approval/execution evidence are retained.
See the [Stage A report](../ops/splash-phase3/stage-a-report.md). Candidate temporary
route/key bodies remain prepared for a separately approved qualification window.

The remaining five tasks are full owned-platform verification, actual Splash gateway
qualification, integrated measurement, results publication and a separate rollout decision.
Stage A installation and disabled deployment succeeded; serving launches, gateway route
mutation and benchmark work remain unexecuted. Repository completion is not platform/gateway qualification or production
adoption. MLX remains the default and Splash disabled until an approved opt-in.

Earlier foundation validation checkpoint: full regression **3,246 passed, two skipped, 98.26% coverage**
(527.18 seconds); final focused telemetry/API **72 passed**, including two supplemental
synthetic counter/detail cases. The telemetry normalizer has 100% statement/branch
coverage. The separate browser run passes 32 checks. Ruff, strict mypy, standalone host
Ruff/mypy, all static JS syntax, instruction parity, diff checks and private-pattern
scanning pass. The ordinary full suite skips the optional browser dependency; the
explicit browser command supplies that validation. No live service was changed at that
foundation checkpoint; the later approved Stage A execution is recorded below.

## Implemented repository decisions

The supported all-client surface is cumulative native execution snapshots, not a
request ledger. Schema v16 adds engine snapshots and backend-scoped availability;
inference_requests/token_usage_daily and historical totals remain unchanged.
Profiles/fingerprints/verified controller-native epochs stay distinct.
Snapshot queries expose cumulative values and gaps without summing overlapping polls
or translating execution counts into billed tokens. No final-counter interface is
qualified, so no stop hook or request-history parser will be invented.

Identity, restart/PID reuse, stale/malformed readings, replay, retention and transaction
rollback were tested before implementing the store/poller. The bounded independent
monitor supplies cached readiness for alerts/diagnostics/availability. Bundle sampling
handles partial/unknown observations. SQLite backup/restore tests cover schema migration
rollback. Operating/measurement artifacts are prepared; the private rebuild proposal
records exact source commits and a required tokenizer overlay. Installation receipts and
the Stage A Mac deployment are verified. Actual Splash serving/gateway qualification and
integrated measurement remain pending.

The earlier 3,246-test checkpoint above belongs to the first implementation slice.
The continuation's final validation results are recorded below after the full suite.
Browser coverage now includes 34 scenarios, including populated stale native history,
profile filtering and unknown counts; the narrow native-history view was reviewed.


## Continuation validation — 2026-10-28

- Final uninterrupted regression: **3,306 passed, two skipped, 98.14% coverage**
  (615.32 seconds). The optional browser module is validated separately.
- Final critical package: **101 passed**; snapshot normalization/storage, monitor,
  scoped availability and database backup/restore have 100% statement/branch coverage.
  Bundle sampling decisions also have 100% statement/branch coverage.
- Explicit Chromium DOM checks: **34 passed**. Static JavaScript syntax passes for
  all 19 files. Frozen measurement input digests match.
- Ruff, strict mypy (58 app/script files and two standalone host files), instruction
  parity, staged diff checks and the private-pattern public scan pass (zero hits).
- Regression tests first exposed late HTTP readiness revival, readiness loss from slow
  storage, missing-field counter-baseline erasure and profile-switch interval leakage;
  all now pass. No inference/token-path DB work was added.
- Changes are staged without a commit. Read-only host inventory and source-byte checks
  succeeded; no live deployment, runtime launch, gateway mutation or benchmark occurred.

Phase 3 remains 16/23 (70%) because the private executable live worksheet, approved
verification/measurement, results publication and production decision are unfinished.
All supported repository functionality is implemented. MLX remains the default and
Splash disabled; repository test results do not imply production qualification.


## Deployment preparation — 2026-10-28

A concrete ignored Stage A approval packet is ready: exact immutable source commits,
fresh per-file hashes and explicit tokenizer overlay; independent asset/prepared paths;
profile/env/receipt templates; private native/operator/scoped-client credential references;
app/host payloads and digests; guarded target/process/intent/config checks; matched SQLite,
app/interpreter/env/LaunchAgent/host backup and rollback; exact original-route request
commands with a durable four-attempt cap. Final receipts bind actual resolved assemblies
only after target verification. No prospective receipt is called an installation result.

Read-only Mac and authenticated gateway preflights pass. The original app lock matches
the repository. A temporary existing-host-agent relay allows project deployment scripts
to run inside the devcontainer without copying SSH private keys. Gateway management can
add/remove isolated test routes/key without replacing the public route or restarting the
shared gateway; those writes remain outside Stage A and need later qualification approval.

Stage A deploys paired host/admin code and schema16 with Splash disabled, preserving the
original MLX generation/selection and sidecar intents. Asset copies precede the writer
outage; cutover budgets five minutes plus ten reserved for restoration. It includes only
small bounded original-route checks, no Splash serving or integrated comparison. Stage B
still needs its own approved native/gateway/context/cancellation/measurement window.

This closes private deployment preparation (17/23, 74%); the earlier 16/23 checkpoint
remains historical. No live upload, installation, serving launch, schema change, gateway
mutation or verification inference has been executed during preparation.


## Stage A execution — 2026-10-28

The user explicitly approved the concrete packet. Fresh preflight and the original
route check passed before upload. The pinned runtime and independent stock/modified
assets/assemblies were installed offline and fully verified, with real resolved-output
receipts. After a matched schema15 backup and writer stop, the whole reciprocal host/
guard unit and schema16 admin were deployed. Current file hashes and code drift pass;
admin health/authentication, original MLX PID/create-time/executable, selected model and
engine/sidecar intents are preserved. The post-change original gateway request passes.

Both approved route checks used two attempts from the four-attempt cap. Gateway config
is unchanged, Splash remains disabled, no owned Splash serving processes exist, and
native snapshot/availability rows are zero. The cutover including the post-change check
completed in about 72 seconds; restoration reserve was unused. The pre-schema backup
has integrity `ok`; app/env/plist/interpreter/host backups are retained. No live rollback
was needed or claimed. The temporary SSH agent relay was closed. No commit was created.

See the [Stage A report](../ops/splash-phase3/stage-a-report.md). The approved packet and
original source patch remain immutable private evidence, separate from execution records.
Progress is 18/23 (78%). The larger platform-verification task is in progress because its
opt-in/serving/transition/resource-release checks were outside Stage A approval. Actual
Splash direct/gateway/32K/auth/Host/cancellation tests, integrated measurement, publication
and production/default decisions remain separately gated. Frozen Phase0 evidence is unchanged.


## Stage B preparation — 2026-10-28

A separate [functional window](../ops/splash-phase3/stage-b-qualification.md) is prepared,
with a machine-readable request budget and ignored executable operator packet. Fresh
read-only checks confirm the deployed schema16/source, original MLX generation/selection/
semantic intents, valid receipts, clear lifecycle locks, disabled Splash and zero native
rows. The original gateway route is captured for exact route-parameter comparison. Both
near-limit synthetic inputs were prepared offline with the installed tokenizer/template
at 31,873 prompt tokens.

The proposed window is 45 minutes including a ten-minute restoration reserve, concurrency
one and cap40 attempts (29 scheduled). It covers both profiles' direct/gateway streaming,
near-limit context, refusals, disconnect/follow-up, owned off/restart/profile switch and
telemetry attribution. Normal cleanup retains schema16 records and restores original MLX/
service states and the disabled environment. Operating safeguards pass 21 synthetic tests.

No Stage B enable, serving transition, gateway mutation or inference occurred during
preparation. Forced child recovery/fault injection, admin compatibility/eval acceptance,
the unchanged integrated comparison and production/default decision remain separate work.
Progress remains 18/23 (78%); no live checkbox is completed by preparation.


## Approved Stage B attempt and local correction — 2026-10-28

The approved functional window was attempted, then aborted at stock readiness before
Splash inference. The actual pinned status uses runtime identity/memory-plan precision
fields rather than allocation counters. The owning job timed out and released its
resources; original MLX/services/intents, disabled env and authenticated gateway route
were restored. Two original-route attempts passed; native rows remain zero and six
availability observations are retained. See the [attempt report](../ops/splash-phase3/stage-b-report.md).

The captured-shape regression failed before the fix. A shared precision contract now
supplies wrapper/admin/quality readiness. A separate inherited signal cleanup failure
was reproduced with exact unlink-boundary injection and corrected without deleting
foreign reservations. Final source validation is3,344 passed/two skipped/98.14%
coverage, with100% precision-helper statement/branch coverage and eight signal cases.

A [new corrective packet](../ops/splash-phase3/readiness-fix.md) prepares paired host/
admin apply and a fresh bounded rerun while retaining schema16. Its source changes
require new approval; the previous unused budget is not reused. The changed local
source has not been deployed. Overall Phase3 remains18/23 (78%): successful platform/
gateway qualification, integrated comparison, publication and production decision
remain open. The frozen measurement and historical results are unchanged.


## Corrective live window and next functional packet

The approved paired correction passed matched backup, source/schema16/health and original
inference checks. Stock reached owned readiness and direct complete streaming passed.
Gateway streaming failed the primary-name expectation, so qualification stopped and
owned cleanup restored original MLX/services/intents, disabled environment and gateway.
Five attempts were counted; three native readings and ten total availability observations
remain. See the [execution report](../ops/splash-phase3/readiness-fix-report.md).

Installed gateway source proves public-name stream restamping; the next separate
[functional packet](../ops/splash-phase3/gateway-contract.md) uses endpoint-specific strict
identities, calendar API ranges plus exact-window exports, and correct already-off
restoration. Three contract and two route-binding regressions failed before correction;34 offline
operator checks pass.
This packet deploys no code. Previous failures are preserved. Phase3 remains18/23 (78%):
both-profile lifecycle/compatibility, native/admin acceptance, frozen comparison and
rollout decision are not closed by the partial successful stock evidence.


## Endpoint-contract functional window

All29 scheduled HTTP attempts passed for both profiles, including exact31,873-token
near-limit retrieval, strict native-primary/gateway-public identity, refusals and four
cancellations with native idle/counter/generation evidence. Stock off/restart and profile
switch produced three independent epochs;18 new readings and12 new availability rows
are scoped separately from history. See the [report](../ops/splash-phase3/gateway-contract-report.md).

The final owning stop failed and retained a dead receipt. Verified no-job/PID/executable/
listener checks, preserved same-volume receipt archival and service restoration recovered
original MLX/services/intents, disabled env and gateway inference. Lifecycle remains
pending; a local [shutdown race correction](20261028_splash-shutdown-race.md) needs separate
Mac validation. The exact redacted live cause is unproven. Phase3 is19/23 (83%): only the
HTTP-qualification checkbox closes. Frozen comparison, full publication/overhead findings
and production decision remain open; no previous failure is rescored.


The local shutdown correction now passes3,350 tests/two skipped/98.14% coverage; its
identity gate has100% statement/branch coverage. Six new race/refusal tests,45 private
operator checks and60 isolated diagnostics support the
[host-only proposal](../ops/splash-phase3/shutdown-fix.md). A prior contaminated full run
and focused orphan failure remain recorded. The live CLI's inner exception was redacted;
exact attribution and corrected Mac validation remain pending. This local completion
closes no further Phase3 checkbox and authorizes no new live apply.


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


## Proven Mac exit correction and normal-cycle verification

The [Mac exit fix](../ops/splash-phase3/macos-exit-fix.md) captured ?E/P_WEXIT after executable
teardown. Seven regressions failed first;3358passed/2skipped/98.14% isolated full suite,
100% new wait branch coverage and45operator tests pass. Matched single-host-file deployment
and all four normal profile cycles passed. Seven requests, four epochs/six readings/eight
availability rows were retained; final schema16/integrityok/32readings/51availability and
original MLX/actual services/intents/disabled env/gateway restoration pass. No failed prior
window is rescored. Full engine-pair matrix and native/admin acceptance remain before the
platform checkbox closes; progress stays19/23 (83%) until that broader gate is verified.


## Full engine-pair matrix — 2026-10-29

All twelve directed engine pairs among enabled MLX/Splash/Strata/ComfyUI passed, with
exclusive ready targets, prior PIDs absent and settled owning jobs/locks. Both Splash
profiles were used. Original MLX/actual services/intents/disabled env/gateway restoration
passed; two original inference checks and no other inference/generation were sent. Final
schema16/integrityok/32readings/55availability remain preserved. See the
[acceptance evidence](../ops/splash-phase3/integration-acceptance.md). The platform/lifecycle
checkbox now closes:20/23 (87%). Native recovery/admin acceptance and the frozen comparison,
complete publication and final support rollout remain required.


## Both-profile native/admin acceptance — 2026-10-29

Both profiles passed complete admin chat/reported usage,7supported compatibility cases,
one deterministic current eval/history, expected held-run cancellation and log access;
vision remained unsupported. One controlled native-child termination each invalidated
readiness, preserved the parent/instance, produced a verified new native/restart1 and
complete followup. Four epoch boundaries/9new readings and idle deduplication passed;
normal owning off and all3old/newPIDs/receipt absence passed each time. Original MLX/
actual services/intents/disabled env/gateway restored; finalDB16/integrityok/41readings/
67availability retained. See [evidence](../ops/splash-phase3/integration-acceptance.md).
Frozen comparison, complete measured publication and final support rollout remain;
progress stays20/23 (87%). No earlier failure or Phase0 qualification is rescored.


## Final supported rollout — 2026-10-29

Supported functionality is deployed/enabled, with MLX default. See the
[final evidence](../ops/splash-phase3/rollout-report.md). A necessary endpoint correction
keeps inactive Splash aliases away from MLX's model loader: strict configurable port,
recorded-port cleanup and admin mismatch refusal, preserving the8080 compatibility default
and every GPU exclusion. Paired source/config backup and current-generation checks passed.
Four endpoint cycles, all twelve directed pairs, both complete native/admin recovery windows
and18commissioning cases are qualified. Persistent profile routes are database-backed;
the new key is scoped to those routes, original route/scopes remain unchanged, inactive
calls fail500 without changing the original generation, and its model refuses the key403.
Original MLX/services/intents are restored; Splash stays explicitly selectable. No native
member/receipt/listener, pending job or lifecycle lock remains; schema16 integrityok,
85readings/109availability. Final3408passes/2skips/98.14%, critical100%, lint/types/parity/scan pass.

The remaining comparison checkbox is **partial/inconclusive**, not promoted: stock stopped
at its reserve, modified setup rejected a malformed temporary key, and both restored.
Three-sample stock TTFT+10.4%/70ms was investigated with overlapping ranges, equal wall
medians and guard-call confounding. No causal ingestion-only bound or performance/adoption
pass is inferred. Existing explicit full-support deployment authorization supports the
separate functional rollout decision; preferred/default-backend adoption is declined and
MLX retained. No historical failure, budget, threshold or frozen result is changed. Further
performance qualification needs a new separately defined scope. Progress22/23 (96%).
