Overall Progress: 0%

# Splash: prove the 27B speedup, then integrate another engine backend

Date: 2026-10-24
Status: Proposed. Planning only; no benchmarks, installations or service changes performed.

## Summary

Improve the speed of the existing Qwen3.8-27B workload by evaluating
[incoai/Splash](https://github.com/incoai/splash) against the current MLX server on the
same Mac. First deliver an isolated experiment and a measured go/no-go report. Full
integration and the backend refactor depend on that report passing the gates below.
A negative result is a valid completed experiment; keep MLX and retain the findings.

If Splash qualifies, add it beside MLX and Strata as a separately managed engine.
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
- Do not change the default engine based on upstream benchmark claims. No implementation
  in this planning change; no automatic rollout after a successful experiment.
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

- [ ] 🟥 Record a read-only preflight: actual engine/model/profile, hardware/OS, free disk,
  memory pressure/swap, model asset hashes and tokenizer/template, serving configuration,
  running sidecars and recovery intent. Resolve SSH target through `MAC_HOST`/`MAC_DEST`
  and the existing deploy-env mechanism; never put deployment facts in tracked output.
- [ ] 🟥 Add failing tests in `tests/test_splash_phase0.py` for fragmented/multi-token SSE,
  reasoning-only deltas, final usage, missing counts, HTTP/SSE failures, cancellation,
  timeout, settings translation, result comparability and restoring experiment state.
- [ ] 🟥 Add `ops/splash-phase0/` with an audited launch/capture runbook, a small HTTP
  benchmark runner and a manifest schema. Reuse measurement concepts from `quality.py`
  and existing Phase 0 scripts; do not copy the old Strata runner unchanged: it uses
  Strata-specific model/process handling and falls back to counting chunks as tokens.
- [ ] 🟥 Test malformed/partial samples and the comparison calculation first, then add a
  report command that rejects incompatible manifests or insufficient valid samples.
  Errors, early stops and missing metrics stay visible; never discard them to improve
  the reported speed. Run the tooling tests before touching the Mac.

Proposed tooling interface (to implement, not existing commands):

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

## Phase 0B — Isolated Mac benchmark and decision

This phase temporarily stops live inference and sidecars. Request approval for its concrete
maintenance window, exact commands, memory cap and restoration procedure before execution,
as required by `AGENTS.md` for changes to a running Mac service. The plan itself needs no
live changes. Run project tooling from the devcontainer; only engine/OS operations execute
remotely on the Mac. Docker/VM GPU emulation is not a benchmark substitute.

- [ ] 🟥 Prepare an isolated directory, e.g. `/Volumes/Models/splash-spike`, containing the
  verified runtime, private configuration, draft, caches, logs and results. Audit writes
  and redirect supported caches/temp paths under it. Keep the existing MLX environment
  and model registry untouched; read shared target weights without modifying them.
  Account for target copies, draft and caches in the disk budget. Stop if required writes
  cannot be confined and document the prerequisite before proceeding.
- [ ] 🟥 Capture the original engine/model/pack, sidecar intent and recovery state; prevent
  watchdog/reconciliation from restarting another engine during the experiment using the
  supported maintenance mechanism. Stop through existing controls and wait for verified
  process exit before starting Splash. Use loopback and an unused experiment port (8086
  after checking availability), not the production endpoint. Start with all GPU sidecars
  stopped and an explicit memory/context cap and idle-release policy.
- [ ] 🟥 Verify exact target compatibility and real inference, then capture scrubbed health,
  readiness, identity, model list, status/metrics, streamed response and log fixtures from
  the pinned version. Explicitly test reasoning off/on, tools, structured output,
  cancellation and image input if used by the current workload.
- [ ] 🟥 Run the primary comparison below with the same 27B checkpoint. If local preparation
  or its draft is incompatible, report that blocker. Run a separate stock-27B comparison
  only with explicit model identity and quality labels; it cannot pass the current-model gate.
- [ ] 🟥 Publish the benchmark report and a pass/fail/inconclusive decision with raw-sample
  references, distributions and settings. Only then decide whether Phases 1–3 are justified.
- [ ] 🟥 Always stop Splash and verify its process group/port are gone and memory is released;
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

## Phase 1 — Shared backend contract, only after a pass

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

- [ ] 🟥 Add characterization tests for current MLX/Strata/ComfyUI/off transitions and intent,
  including failed stop/readiness, rollback boundaries, sidecar restoration and unknown
  listeners; retain all current behavior before changing the coordinator.
- [ ] 🟥 Introduce a registry and adapters around existing clients. Migrate observation,
  target parsing and transition construction first, then chat/quality/status consumers.
  Replace boolean parameters per engine with backend observations; do not add a growing
  list of `splash_running`, `splash_ready`, etc. Unknown backend IDs must fail explicitly,
  never fall through to MLX stop/start.
- [ ] 🟥 Test engine-pair transitions parametrically, profile switches within an engine,
  stale/reused PIDs, another engine on the same port, duplicate/conflicting ownership,
  cancellation and an external switch during a quality run. Then migrate callers to the
  shared interface without altering public target strings or existing stored identities.
- [ ] 🟥 Resolve recovery and resource policy through capabilities: initially Splash excludes
  GPU sidecars; readiness distinguishes alive from loaded/released/recovering. Keep
  automatic restart opt-in and avoid fighting Splash internal recovery. Preserve desired
  intent and prevent recovery during maintenance or lifecycle/quality jobs.

## Phase 2 — Splash adapter and host integration, only after Phase 1

- [ ] 🟥 Write host-wrapper tests first for start/stop/status, validated profile/env parsing,
  lock contention, port conflict, process ownership, missing volume/assets, failed startup,
  SIGTERM timeout and readiness. Then add `host/splash/bin/splash`, env/profile examples,
  README, `ops/host-files.list` entries and pinned runtime inventory.
- [ ] 🟥 Add a `SplashBackend` using only its host wrapper and HTTP; the admin never imports
  Splash or uses its Python environment. Start one configured profile; track parent and
  native child generation; stop only verified owned processes and await release. Reuse
  shared lifecycle/maintenance locking. Add reciprocal guards to MLX, Strata, ComfyUI
  and GPU service wrappers so direct CLI operations enforce the same exclusion policy.
- [ ] 🟥 Test and implement the existing serving contract on port 8080. Configure Splash
  aliases for every upstream model string actually used by the existing LiteLLM route;
  exercise that exact request through the gateway before claiming compatibility. Verify
  bind address, Host allowlist, authentication and cancellation without assuming Strata
  accepts-any-model behavior. Secrets remain private configuration. No new gateway proxy.
- [ ] 🟥 Test and add registry-driven selector/status/badges/logs, inference target resolution,
  reasoning settings, compatibility/eval runs, context reporting and engine/profile history.
  Feature-gate Splash off by default. Show unsupported capabilities and loading/idle states
  accurately, and preserve existing API fields while adding backend descriptions.

## Phase 3 — Telemetry, qualification and rollout

- [ ] 🟥 Test captured Splash telemetry before adding its adapter. Normalize only available
  counters/timings into existing performance and daily token storage, with backend/profile,
  process generation and source identity. Handle restarts, polling duplicates, missing
  fields and unavailable endpoints; keep old MLX/Strata records and rollups unchanged.
- [ ] 🟥 Verify whether complete per-request usage for direct/LiteLLM traffic can be obtained
  from supported responses, logs or counters. Do not invent request rows or infer token
  counts from chunks; aggregate-only telemetry must be labeled as such. Document lost
  intervals and unknown cached input, and avoid double-counting gateway and engine data.
- [ ] 🟥 Add bounded probes, backend-aware health alerts, availability and last-ingest status;
  test stale and recovering engines. Keep inference HTTP streaming direct and avoid new
  per-token copies, synchronous DB writes, heavy polling or logging on the hot path.
- [ ] 🟥 Run all gates below, then repeat the frozen benchmark through the integrated setup
  and actual LiteLLM route. Compare against the isolated result; investigate material
  overhead before rollout. Update `docs/DESIGN.md` with measured results and limitations.
- [ ] 🟥 Present the exact deploy diff, backup, verification and rollback commands for live
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
