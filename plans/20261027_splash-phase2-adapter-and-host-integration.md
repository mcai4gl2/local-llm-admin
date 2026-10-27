Overall Progress: 0%

# Splash Phase 2 — adapter and host integration

Date: 2026-10-27
Status: Planned; implementation has not started. Baseline: Phase 1 commit `d1ab6d6`.

## Summary and settled decisions

Add Splash as an opt-in engine using the shared backend contract, an owning Mac wrapper,
validated private profiles and the existing direct inference path. Deliver a reviewable,
offline-tested integration; keep MLX the default and `splash_enabled=False`.

This continues the accepted development decision in the
[Phase 1 plan](20261027_splash-phase1-backend-contract.md) and the
[parent plan](20261024_splash-27b-benchmark-and-engine-backends.md). Further Phase 0
experiments are deferred, not prerequisites for implementation. Earlier reports retain
their original failed, inconclusive and unassessed outcomes; their historical integration
gates do not supersede that later decision. No performance or production-adoption pass is
implied by completing repository integration.

Initial profiles use 32,768-token context, reasoning on, BF16 KV and FP32 recurrent state.
Keep stock 8-bit and modified 4-bit assets, prepared outputs and profile identities separate.
Neither checkpoint is silently selected as a replacement for the other. GPU sidecars and
other full engines are excluded. Start with automatic idle release off and disk cache
disabled; observe released/recovering states accurately if encountered. Do not add an
admin watchdog for Splash or override its internal recovery.

## Scope and boundaries

In scope: pinned runtime inventory; host start/stop/status; private env/profile validation;
process-bundle ownership; reciprocal CLI exclusion; Splash client/adapter; feature-gated
selector/status/logs; chat and current-engine compatibility/eval; context and history.
Keep the lifecycle job claim, Step executor, rollback, intent store and sidecar coordination
in their existing modules. Splash is accessed through its wrapper and HTTP only.

Out of scope: live installation/deployment, another benchmark campaign, runtime source
patches, a downloader, new inference proxy, generalized model sweeps, simultaneous engine
residency, sidecar qualification, usage ingestion, availability/alert redesign or database
migration. Phase 3 owns token accounting, telemetry coverage and production rollout.
Basic allowlisted log viewing belongs here; reading logs must not create request rows.

## Evidence and integration touchpoints

| Location | Existing contract or concrete Phase 2 change |
|---|---|
| [Design §13](../docs/DESIGN.md#13-shared-backend-contract-2026-10-27) | Per-instance registry, normalized observations and adapter handoff |
| `backend_types.py`, `backends.py` | Listener attribution currently recognizes one primary generation PID; extend ownership to include verified parent/native-child members |
| `control.py` | Execution guards compare generations and identify lifecycle commands by stable argv; cover child replacement and all selectable/rollback profiles |
| `engines.py`, `jobs.py` | Reuse pure planner and existing executor; adapters build steps without claiming jobs or persisting intent |
| `config.py`, `app.py` | Disabled settings and dependency injection; add backend descriptions while preserving legacy status fields |
| `static/models.js`, `static/app.js`, templates | Selector, badges, confirmation text and exclusions still contain explicit engine lists; consume registry descriptions/capabilities |
| `logs.py`, `chat.py`, `quality_runner.py` | Allowlisted log IDs; direct streaming; current-only inference, metadata and quality scope |
| `host/bin/mlx-model`, other owning wrappers | Existing PID/port guards and atomic owner-bearing maintenance/MLX locks; add reciprocal Splash guards and serialize competing starts |
| `ops/host-files.list`, `host/environment/` | Deploy only versioned wrapper/helpers/examples; record runtime provenance without installing it |

Paths in the first seven rows are under `src/local_llm_admin/` unless stated otherwise.

Use [the isolated runtime notes](../ops/splash-phase0/README.md),
[scrubbed protocol captures](../tests/fixtures/splash-phase0/README.md),
[native functional evidence](../ops/splash-phase0/native-validation-report.md) and
[reasoning/lifecycle evidence](../ops/splash-phase0/reasoning-lifecycle-report.md). The audited
runtime is Splash 1.4.0, asset `splash-1.4.0-arm64-macos26.tar.gz`, SHA-256
`4c0f032a5392a6c559f081a0052278fbc0ea5abee2d16b820a015cc9b1826a2d`.
Use original runtime sources, not the instrumented timing copies. The isolated launch
flags and 40 GiB Metal guideline are evidence inputs, not an established production
RSS/memory cap. Freeze supported flags and authentication interfaces before using them.

## Implementation tasks

Execute packages in order on the current checkout. Each package is a review boundary;
commits require a user request. Write failing tests before each new behavior, demonstrate
the relevant failure, then implement and run the focused checks. Keep all existing target
strings, defaults, stored identities and public API fields compatible.

### 1. Freeze the runtime, profile and HTTP contracts

Files: new `host/splash/README.md`, `host/environment/splash.txt`,
`host/splash/etc/splash.env.example`, `host/splash/etc/profiles.example.json`,
`tests/fixtures/splash/`, `tests/test_splash.py`, `tests/test_host_splash.py`.

- [ ] 🟥 Add failing profile/parser tests using public-safe fixtures for two explicit IDs
  (`stock-8bit-32k`, `modified-4bit-32k`). Separate target/draft assets and revisions,
  prepared-output identity, profile settings, advertised aliases and runtime pin.
  Reject missing/unknown keys, invalid IDs, path traversal, malformed types, incompatible
  formats and incomplete prepared assets. A bad profile must never fall back to MLX.
- [ ] 🟥 Document the pinned launch/readiness/status/model-list/authentication contract from
  available audited artifacts. Record exactly which fields prove loaded model/profile and
  effective context. Preserve original provenance when deriving fixtures; do not relabel
  the old 96-token, reasoning-off captures as new reasoning-on or gateway qualification.
  Missing auth/alias/Host details remain explicit verification tasks, not invented flags.
- [ ] 🟥 Implement a typed admin profile reader and equivalent host validation of the same
  non-executable data format; neither process imports the other runtime environment.
  Env files are parsed, never sourced; private files require mode 600. Public examples
  contain placeholders only. Reject arbitrary command/flag injection and resolve paths
  against explicit managed/asset roots, including symlink checks. Keep secrets out of
  argv, status, logs and job output. Runtime/model preparation is an operator step.
  Use fixed 32K limits, explicit bounded memory settings, reasoning on and no cache/idle
  policy expansion. Hash full weights at preparation/audit boundaries, not each status poll.

Exit: fixture-backed inputs and endpoint/argv expectations are sufficient to implement
the wrapper and client offline. An unavailable upstream interface blocks that capability;
it does not justify an unauthenticated fallback or another open-ended benchmark campaign.

### 2. Extend ownership to the whole Splash process bundle

Files: `backend_types.py`, `backends.py`, `control.py`, `quality_runner.py`,
`metrics.py`/`diagnostics.py` only where generation consumers require changes;
`tests/test_backends.py`, `test_engine_plan.py`, `test_engine_api.py`,
`test_quality_runner.py`, `test_diagnostics.py` and new Splash tests.

- [ ] 🟥 Add failing tests for a Python parent with a native listener child, restricted
  socket visibility, a dead parent with a surviving child, stale/reused PIDs, an unrelated
  process with a matching argument, another backend sharing the port and a disabled
  Splash owner. Unknown ownership must block activation; a verified child must not be
  misclassified as a foreign listener.
- [ ] 🟥 Introduce the smallest typed ownership record/helper needed for parent and child
  PID + creation-time + executable/root identity. Preserve legacy primary `pid`/`started`
  fields where callers require them. Migrate listener attribution, socket fallback,
  generation comparisons, quality ownership and diagnostic/metric selection together.
  A PID-file timestamp or an HTTP success alone cannot prove the whole bundle is owned.
- [ ] 🟥 Test and implement execution revalidation before every signal/start/restore and
  after activation. A same-parent native-child replacement must be verified and reflected
  in the bundle, never silently authorized by the parent's unchanged PID. Refuse stale
  planned generations and operator profile changes; retain existing failed-rollback
  suspension when a failed launch leaves an unrecorded process.
- [ ] 🟥 Keep adapter command construction stable for start, profile restart, stop and
  rollback references. Add tests that every mutating command is guarded. Extend Step
  metadata only if concrete argv matching cannot cover the wrapper; retain one executor.

Exit: existing three-backend ownership/transition tests pass, and the fourth adapter can
represent its native listener without weakening unknown-owner refusal.

### 3. Add the owning wrapper and reciprocal host guards

Files: new `host/splash/bin/splash`, profiles/env examples and README; existing
`host/bin/mlx-model`, `host/comfyui/bin/comfyui`, `host/strata/bin/strata`,
`host/decision/bin/openjev`, `host/tts/bin/qwen-tts`, `host/embed/bin/qwen-embed`;
`ops/host-files.list`, host documentation and existing `tests/host_harness.py`.

- [ ] 🟥 Add failing real-zsh harness tests in `tests/test_host_splash.py` for start,
  explicit profile restart, idempotent stop and read-only status. Include missing volume,
  runtime/draft/target/prepared assets, bad private file modes, invalid config, occupied
  ports, startup failure, readiness timeout, SIGTERM timeout, child survival and PID reuse.
  Tests use temporary roots and fake processes/HTTP; no Metal runtime or remote host.
- [ ] 🟥 Implement `splash start <profile>`, `splash stop`, `splash status` and a stable
  machine-readable status form. Launch only the pinned private runtime/prepared model;
  set writable HOME/HF/XDG/temp locations deliberately and exclude ambient Python path
  contamination. Record profile identity and owned generations atomically in private run
  files. Human and JSON status must redact credentials and avoid configuration mutation.
- [ ] 🟥 Stop only verified owned generations, rechecking immediately before TERM and
  bounded KILL escalation. Track children before parent exit; parent death cannot erase
  a surviving native owner. Never use broad process-name matching or kill a port owner
  merely because it listens on 8080. Require owned parent/children and owned listeners
  to exit before reporting resource release; foreign/ambiguous listeners cause refusal.
- [ ] 🟥 Add failing reciprocal-guard and concurrent-start tests across MLX, Strata,
  ComfyUI, decision, TTS and embeddings. Check Splash ownership even while its HTTP port
  is closed, released, recovering or administratively disabled. Cover the reverse start
  direction, stale records, missing helper dependencies and restoration attempts.
- [ ] 🟥 Implement guards and atomic lifecycle serialization using the existing lock
  namespace/owner convention. A port check alone cannot serialize two starts. Hold the
  lifecycle reservation across validate/start/stop, use `maintenance.lock` only for its
  short acquisition gate, and never invoke another owning wrapper while holding a lock
  it must acquire. Handle stale/unknown owners conservatively, including legacy `unlock`.
  Factor a small installed helper only where needed; list it in `ops/host-files.list`.
  Keep MLX-compatible sidecar coexistence unchanged.
- [ ] 🟥 Add wrapper/examples/inventory to deployment documentation and allowlists.
  Versioned deployment must not overwrite private env/profiles, install runtime/assets,
  start Splash or enable it. Test missing/new helper file handling in the deploy diff.

Exit: harness checks show no signals to unrelated generations, no competing GPU starts,
bounded cleanup and no source restoration while Splash resources remain owned.

### 4. Implement SplashClient and SplashBackend

Files: new `src/local_llm_admin/splash.py`, `config.py`, `backends.py`, `app.py`;
`tests/test_splash.py`, `test_backends.py`, `test_engine_plan.py`, `test_engine_api.py`.

- [ ] 🟥 Add failing client/registry tests for per-instance injection, live enable changes,
  invalid/disabled targets, exact `splash:<profile>` intent/history, profile restart and
  rollback to the actually observed source profile. Do not add ambiguous plain `splash`
  activation, a second job slot or implicit MLX fallback.
- [ ] 🟥 Add settings for disabled enablement, root/wrapper/profile paths, API base/port,
  SecretStr credentials and bounded readiness/stop/job deadlines. Implement the client
  using existing subprocess/HTTP transport patterns and the wrapper's ownership record.
  Register one adapter over that injected client; admin never imports runtime modules.
- [ ] 🟥 Test readiness with owned process identity plus `/ready`, `/status` and model
  metadata as supported by the pin. Reject wrong aliases/assets/profile/context, malformed
  bodies, auth errors and another engine's valid HTTP response. Implement absent, starting,
  loaded, released, recovering and unknown mappings without equating alive with loaded.
  Released processes still exclude other engines; ready is not asserted during recovery.
- [ ] 🟥 Implement exclusive resource policy, no GPU sidecars, no auto-recovery, current-only
  inference and optional profile restart through existing Step plans. Disablement removes
  selector/status HTTP probes and blocks activation; cheap local lifecycle checks must
  still detect an already-running disabled owner. Add all backend-pair/off and failed
  stop/readiness/rollback tests, including external changes between execution boundaries.

Exit: Splash participates in shared lifecycle planning and intent/history with no coordinator
`splash_running` flag or engine-specific fallback. Initial idle release remains off; expose
request-triggered resume only when the pinned contract and tests establish its semantics.

### 5. Wire inference, quality and capability-driven presentation

Files: `app.py`, `chat.py`, `compatibility.py`, `quality_runner.py`, `logs.py`,
`static/models.js`, `static/app.js`, relevant templates/styles and
`tests/test_app.py`, `test_chat.py`, `test_compatibility.py`, `test_quality_runner.py`,
`test_eval_runner.py`, `test_logs.py`, `test_engine_api.py`.

- [ ] 🟥 Add failing API tests for additive backend descriptors: IDs/labels, validated
  public target IDs, operations, resource policy and normalized state. Keep existing
  MLX/Strata/ComfyUI fields. Never expose keys, private paths or full private profiles.
  No disabled-backend HTTP/PID probes for presentation; retain one per-app registry.
- [ ] 🟥 Use those descriptors for selector options, confirmation/exclusion messages,
  conflict banners, badges and legacy MLX action disabling. Show profile/checkpoint and
  effective 32K context, distinguish loading/released/recovering, retain user selection
  during polls and never advertise unsupported operations. Hide Splash while disabled.
- [ ] 🟥 Add failing MockTransport/stream tests for served aliases, authorization,
  reasoning-on defaults, supported request translation, cancellation/disconnect cleanup
  and context limits. Preserve direct SSE forwarding and existing public chat settings.
  Report reasoning-off as unreliable from retained evidence; do not silently rewrite an
  explicit off request into on or advertise it as qualified. Unsupported controls need
  an explicit UI/API explanation rather than silently ignored fields.
- [ ] 🟥 Implement current-engine compatibility/eval through the adapter descriptor and
  effective model/context metadata. Retain exact backend/profile identity and fingerprint
  in existing results/history; test generation/profile change abort, deadlines, cancellation
  and no MLX switch/sweep commands. Missing/unsupported checks remain explicit outcomes.
- [ ] 🟥 Add allowlisted Splash wrapper/native log sources with fixed managed roots,
  source IDs, rotation/truncation tests and no arbitrary path resolution. Display unknown
  throughput/usage as unavailable; do not add Phase 3 counters or attribute Splash
  traffic to MLX. Audit alerts/availability so current defaults cannot report false MLX
  health for Splash, without changing historical denominators or building new telemetry.

Exit: gated UI/API/chat/current-engine quality operate against fakes; existing engine
behavior remains compatible. Browser checks cover disabled, loading, ready, released,
recovering, unknown and conflict states; use existing tooling rather than a new JS framework.

### 6. Validate the integration and prepare the Phase 3 handoff

Files: completed Phase 2 plan, parent plan, `docs/DESIGN.md`, host README, env documentation
and an append-only entry in `devlogs/YYYY-MM-DD.md`.

- [ ] 🟥 Add failing gateway-contract tests with sanitized existing route model strings,
  including unknown-model refusal, Host policy, authorization and stream cancellation.
  Implement only the pinned supported alias/auth mechanisms. Treat admin-to-runtime auth
  and gateway-to-runtime auth as distinct paths; never claim one proves the other.
- [ ] 🟥 Run focused and full quality gates below; document red-to-green evidence and
  100% statement/branch coverage of new ownership, locking, stop and rollback decisions.
  Recheck legacy target/intent/status compatibility and disabled probe counts.
- [ ] 🟥 Update design and operating docs with exact settings, runtime/profile preparation,
  resource policy, limitations and troubleshooting. Preserve historical Phase 0 reports.
  Update parent checkboxes only for work actually completed and append the devlog.
- [ ] 🟥 Prepare a Phase 3 handoff describing installation/diff review, backups, private
  route inputs, exact authenticated gateway request, verification and rollback needs.
  No live action occurs in this phase. Mark authenticated gateway inference unverified
  until a separately approved live test passes; repository fixture tests do not qualify it.

Exit: repository integration is reviewable and disabled by default. Phase 3 still owns
live approval, telemetry, qualification and rollout; incomplete live checks stay visible.

## Test and performance plan

Run all commands in the active devcontainer as `vscode`, working directory
`/workspaces/local-llm-admin`, through the existing uv environment. Suggested focused checks
after their corresponding packages exist:

```bash
uv run pytest -q tests/test_splash.py tests/test_host_splash.py
uv run pytest -q tests/test_host_control.py tests/test_host_strata.py tests/test_host_openjev.py tests/test_host_qwen_tts.py tests/test_host_qwen_embed.py
uv run pytest -q tests/test_backends.py tests/test_engine_plan.py tests/test_engine_api.py tests/test_control.py tests/test_control_strata.py tests/test_control_sidecars.py
uv run pytest -q tests/test_app.py tests/test_chat.py tests/test_compatibility.py tests/test_quality_runner.py tests/test_eval_runner.py tests/test_logs.py tests/test_diagnostics.py
```

Final gates:

```bash
uv run pytest --cov --cov-fail-under=80
uv run ruff check src tests scripts
uv run mypy src scripts
cmp AGENTS.md CLAUDE.md
git diff --check
uv run python scripts/check_public.py --patterns-file ~/.config/local-llm-admin/public-denylist.txt --git .
```

Explicitly scan new untracked files before staging. Include any executable host Python
helper in lint/type checks if one is introduced outside the standard paths. Wrapper tests
run real zsh with fake runtime/ps/lsof/HTTP and temporary roots; supplement with the supported
Mac syntax/platform check during approved live validation. Linux harness success does not
prove a macOS process-query interface.

Phase 1 baseline: 2,882 passed, one skipped, 98.15% coverage; Ruff and strict mypy passed.
Use its existing mock call-count and cancellation tests as regression evidence. Budget:
zero status HTTP requests for disabled Splash, no per-token ownership/HTTP/DB/log work,
and one bounded details snapshot per enabled backend per status cycle (at most three
fixed HTTP endpoints, no duplicate probes, explicit per-request and overall deadlines). Collect
connection/defaults once per chat stream, reuse clients, and avoid added payload copies.
Test endpoint call counts/timeouts and stale/unreachable status without timing-flaky limits.
No new live benchmark is required here. Integrated latency/native-speed comparison remains
unassessed until Phase 3 uses a separately frozen and approved measurement plan.

## Risks, verification inputs and rollback

Process-tree/port observations can race: the wrapper and coordinator both validate
generation identity at execution, and ambiguous ownership blocks rather than grants a stop.
Native recovery can change children under one parent; compare the complete verified bundle.
An owned idle/released controller still reserves the engine, even when GPU allocations fall.
Termination and port exit establish owned-resource release, not a host-wide RSS guarantee.

Before live use, privately verify the runtime's supported auth/Host/alias interfaces and all
upstream model strings actually sent by the existing route. Public fixtures use placeholders
such as `example/qwen-27b` and `http://litellm.example.com:4000`; real route facts stay private.
Confirm memory bounds and prepared-asset/runtime compatibility from the pinned inventory.
These are bounded implementation/live-verification inputs, not unanswered product questions
that block drafting this plan. Do not change the runtime pin silently if a feature is absent.

Rollback preparation must account for direct host guards as well as admin code. Capture
prior versioned files/settings/intent; keep private profiles and prepared models separate.
In a later approved live window, stop the verified Splash bundle, require owned listeners
and children to exit, then restore the previous engine and sidecar intent and prove real
inference. Turning the flag off is insufficient to stop an already-running bundle. Refuse
to restore over an external operator change. Present exact target-derived deploy/rollback
commands for approval in Phase 3; this plan authorizes no live service mutation.
