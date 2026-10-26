# Splash Phase 0 experiment

Implements the isolated experiment from [the Splash plan](../../plans/20261024_splash-27b-benchmark-and-engine-backends.md).
The admin does not import this code. Inventory and measurement commands are read-only
with respect to service lifecycle; `window_guard.py` performs only the separately approved
maintenance procedure from a private, reviewed configuration. On 2026-10-25 the user approved
three separate maintenance windows, each capped at two hours, for stock 8-bit and modified
4-bit comparisons; see [the initial window](benchmark-window.md) and
[approved continuation](continuation-windows.md). Those windows are complete and do not
approve future runs of these tools.
Each checkpoint completed three paired timing sessions. All windows restored the original
service within their bounds; see the [measured report](report.md) and
[scrubbed evidence](evidence.json). A separately frozen [native emission comparison](native-comparison-report.md)
now passes emission speed, p95 TTFT and completion criteria for both checkpoints, with 216
requests and accounted traces. Original-service restoration and owned cleanup are verified.
The adoption decision remains **inconclusive** while broader quality and secondary qualification
remain open. The backend refactor and deployment remain gated.
The offline continuation planner preserves pairs and budgets restoration.
The [pinned timing audit](native-timing-audit.md) explains the original timing limitations;
the [callback boundary review](native-comparison-boundaries.md) defines the new emission scope.
The [qualification guide](qualification.md) records completed evidence and remaining requirements.

See the [public label cleanup and provenance](publication-cleanup.md) for renamed historical
labels and preserved original hashes. Further live qualification is deferred for later.

Run repository commands in the devcontainer as `vscode`, through `uv run` for Python.
Remote commands below are macOS operations, invoked through SSH from that container.
`preflight.sh` resolves `MAC_HOST`/`MAC_DEST` through `ops/deploy-env.sh`; it prints no target.
If the devcontainer shell lacks `SSH_AUTH_SOCK`, use its existing VS Code forwarded agent:

```bash
export SSH_AUTH_SOCK=$(find /tmp -maxdepth 1 -type s -name 'vscode-ssh-auth-*.sock' -print -quit)
```

## Read-only inventory

```bash
bash ops/splash-phase0/preflight.sh /tmp/splash-phase0/before.json
# After choosing a registered 27B target; hashing reads all its weights without inference:
bash ops/splash-phase0/preflight.sh /tmp/splash-phase0/target.json \
  --model qwen3.8-27b --hash-weights
```

The example target is a placeholder selection, not an assertion about the live model.
The inventory separates the currently selected MLX asset, intended serving engine and
explicit benchmark target. It includes the private registry, OS/hardware, disk, pressure,
swap, thermal observations, processes with start times/RSS, listeners, configuration hashes,
model config/tokenizer/template hashes, optional weight hashes and read-only admin intent/jobs.
Unavailable fields remain explicit. Asset hashes cover sorted `(relative filename, SHA-256)`
pairs using compact JSON, so relocation does not change their identity. The full quantization
configuration remains in private evidence, including mixed-precision exceptions.

Every raw artifact stays outside git with mode 0600; new output directories use 0700.
The preflight reads only allowlisted active-env fields and hashes configuration files instead
of exporting their contents. Process arguments and hardware inventory can still contain
private data: never stage raw snapshots. The script refuses to overwrite an artifact.

A successful SSH capture is **not a completed preflight**. It deliberately records
`complete: false`, `listener_identity_requires_review` and `serving_ready: null`: inventory
cannot prove process ownership, required capabilities or real inference. Review the listener
PID/start time against the host wrapper, served model and effective profile. Audit and annotate
private before/after copies only after those checks; retain the original captures and a record
of the review. Do not turn missing evidence into zero, false or an assumed model match.

## Manifest and measurements

[manifest.schema.json](manifest.schema.json) is generated from the executable Pydantic
contract; [manifest.example.json](manifest.example.json) is intentionally unfinished.
Its zero digests, placeholder IDs and one non-primary short case cannot qualify an engine.
The initial text corpus is [corpus.json](corpus.json), with short instruction, multi-turn
code review and 8K design-review tasks. Scrubbed [native streaming captures](../../tests/fixtures/splash-phase0/README.md)
exercise actual protocol/accounting behavior; they are functional smoke evidence, not timed
benchmark samples. Effective precision and rendered prompt counts must still be checked
for every selected target/runtime. The example is separate from that workload.

```bash
uv run python ops/splash-phase0/bench.py schema --out /tmp/splash-manifest.schema.json
uv run python ops/splash-phase0/bench.py run --manifest /tmp/27b-mlx-01.json --out /tmp/27b-results
uv run python ops/splash-phase0/bench.py run --manifest /tmp/27b-splash-01.json --out /tmp/27b-results
uv run python ops/splash-phase0/bench.py compare --results /tmp/27b-results --out /tmp/27b-report.md
```

Manifests pin target checkpoint/revision/weight, tokenizer and template digests, quantization,
runtime version/digest, the separate Splash draft revision/digest, effective context/KV
precision, generation settings, cache conditions, corpus, repetitions and session order.
For a locally modified checkpoint, use its content digest and recorded provenance; an upstream
name alone is not its identity. A new draft/model/setting needs a separately named experiment.

Use loopback SSH tunnels, for example `ssh -N -L 18086:127.0.0.1:8086 "$MAC_HOST"` after
sourcing `ops/deploy-env.sh`. Do not tunnel the production engine to the benchmark runner
until the maintenance window is approved. Authentication is read only from the environment
variable named by `api_key_env`; never put a key in a URL or manifest. The runner ignores
ambient HTTP proxies, verifies `/v1/models`, and checks the expected response model on streams.
HTTP discovery complements the reviewed process identity; it does not replace it.

`corpus_sha256` is SHA-256 of the literal `cases` list serialized as sorted compact UTF-8
JSON (`ensure_ascii=False`, no NaN). Every case explicitly includes `id`, `category`, `kind`,
`max_tokens`, `messages`, and `primary`. Use varied coding, instruction and prose prompts,
both 512- and 1024-token tasks, plus realistic multi-turn/tool histories. Primary prompt-size
bands are 1–1024, 1536–3072 and 6144–10240 **reported input tokens**, not word estimates.
Audit sizes with the exact tokenizer before freezing; a mislabeled prompt cannot pass.

Fresh and replay conditions require separate manifests and reports. Fresh requests insert
a deterministic unique system prefix before the conversation, paired across runtimes for
that session/case/repetition. In contract v2, isolated fresh requests additionally set an audited per-request cache
namespace: MLX `X-APC-Tenant` or Splash `cache_salt`. Still require reported zero reused
tokens; namespaces do not replace validation of the effective server behavior. Replay preserves message bytes and runs each
ordered history sequentially. Its excluded warm-up primes the prefix. Do not mix independent
replay histories in one sequence or label a fresh extension with no reused tokens as warm.
Concurrency 2/4 schedules bounded parallel requests for secondary fresh tests; concurrency 1
is required for the primary gate. The runner cannot configure server-side cache policies.

The HTTP stream parser handles fragmented UTF-8/SSE, comments, multiple data lines, reasoning
and multi-token events. It requires a final finish reason and `[DONE]`; malformed events,
wrong models, HTTP/SSE failures, missing/invalid counts, timeouts and transport failures remain
sample errors. Final usage validates integer input/output/reasoning/reused counts. Events are
never counted as tokens. TTFT includes reasoning; `first_answer_s` starts at visible content.
`total_s` ends on stream completion. Cancellation closes HTTP, records the cancelled sample,
checkpoints an interrupted run and propagates to the caller. It does not prove that the server
released its decode slot; inspect status/processes during the required cancellation smoke.

`client_output_wall_tok_s = completion_tokens / total_s` includes prefill and HTTP overhead.
It cannot pass the native decode gate. `backend_decode_tok_s` reads the server's positive
finite `timings.predicted_per_second`, only alongside valid token accounting. Before selecting
that metric, save an audit proving the two pinned runtimes count equivalent tokens/time
intervals, and put its digest in `evidence.native_timing_audit_sha256`. A common field name
is not proof of common semantics. In the audited MLX runtime, `timings.prompt_n` counts
uncached input; Splash counts total input. The runner validates these against total usage
and known cached counts using the backend-specific definition, without rewriting usage. No unavailable native rate is derived from chunk count,
TTFT or a full-output count divided by a post-first-emission interval.

Each request is checkpointed to a private JSON artifact using atomic replacement. Existing
run IDs are refused; errors and interrupted runs are never silently resumed/replaced. Keep
only run artifacts in the results directory. Malformed files reject comparison with a nonzero
exit; parseable partial/error runs produce an inconclusive report with visible sample accounting.
The report references raw runs by ID and canonical digest, without publishing deployment paths.
Review and leak-scan even aggregate reports before publication; IDs are user-supplied.

## Comparison and limits

Freeze at least three paired sessions in alternating order (`mlx-first`, `splash-first`,
`mlx-first`, or the reverse), one excluded warm-up and five measured repetitions per prompt.
Pair by session/case/repetition. The report takes per-prompt medians of paired speed ratios,
then their median over sessions. Its deterministic 2000-draw bootstrap resamples paired
sessions and prompts, retaining pairing rather than treating tokens as independent samples.
Three sessions are a starting point; collect more when confidence remains uncertain.

The comparator rejects differing model assets, settings, corpus, context/KV/cache policies,
concurrency or measurement contracts. It rejects runtime/draft changes within a backend,
missing/duplicate samples, unpaired sessions, unmatched prompt counts, unknown cache reuse,
early-stopped fixed-length tasks, invalid metrics and any request failures, including warm-up
failures. Those rows remain in the raw evidence and accounting. It does not pool incompatible
runs or remove failures to improve a ratio.

The decode gate needs at least a 1.25 median paired ratio, lower 95% bound above 1.0 and no
primary category regression. It assesses the primary concurrency-1 short/2K/8K matrix only.
The latency gate needs at least 40 measured TTFT values per backend/condition and p95 no more
than 1.10 times baseline. Fixed-length median end-to-end time must not regress for any task
in any paired session. The report also shows actual output lengths and per-backend latency.
Pooled TTFT p95 is for the frozen mix; it is not a per-category p95 claim.

Even a passing speed report has **adoption decision `inconclusive`** until separate correctness,
resource/headroom, required-context, ownership/operations and service-mix evidence passes the
plan. A measured regression fails its gate. A client wall-rate comparison can describe a result
but leaves native decode unassessed. `compare` exits 0 only for complete, non-failing measurement
evidence; that exit code is not permission for integration or deployment.

## Maintenance worksheet — complete privately, then request approval

Before Phase 0B, provide the user a private worksheet with resolved values for:

- Exact registered 27B target, checkpoint/derivative, target/tokenizer/template hashes and
  intended production context. Do not silently select a stock 4-bit target in place of a derivative.
- Pinned release/archive checksum, draft revision/digest, disk budget, runtime inventory,
  effective MLX/Splash options and a native timing-definition audit.
- Original engine/pack, selected MLX asset/profile, sidecar **intent and observed state**,
  recovery state/config hashes, admin service label/UID, active-job check and listener generations.
- Exact service stop/start and restoration commands from the deployed wrappers, exclusion of
  other clients, start/stop deadlines, experiment process ownership checks and abort conditions.
- Frozen manifests/corpus, resource sampling command/interval, output paths, session order,
  duration, memory/context cap and required compatibility/eval cases.

Proposed initial envelope: loopback port 8086 after verifying it is free, 32K context and
40 GiB Metal allocation budget, GPU sidecars stopped, idle release off, no persistent cache,
and a two-hour maximum window including restoration. These are review inputs, not validated
capacity or permission. Whole-process memory includes the draft, native children and Python;
abort for critical pressure, sustained new swap growth, or less than 6 GiB measured host
headroom. Check the intended production context and service mix in later approved blocks.

The repository has no all-backend maintenance/suspend API. The short host `maintenance.lock`
gate alone does not suspend admin recovery, and holding it while invoking wrappers deadlocks
their lifecycle lock acquisition. After checking there are no jobs, the proposed maintenance
procedure unloads the admin's actual launchd job during isolated serving; record its label,
UID, plist path and prior loaded state first. Then use the existing verified wrappers to stop
engines/sidecars and check PID/start-time exit plus port and memory release. Do not edit the
admin database to create a pretend maintenance mode. Prepare exact unload/load and wrapper
commands in the private worksheet before asking for live approval.

### Independent window supervisor

`window_guard.py` runs on the Mac independently of the SSH session. Its private configuration
pins the approved collection deadline, exact allowlisted launch argument lists, original
wrapper process generation, admin launchd state, semantic service intent, selected symlink
and configuration hashes. The reviewed worksheet must resolve these values; this repository
does not contain a deployment configuration.

The supervisor accepts serialized `activate`, `launch`, `stop` and `restore` requests through
an atomic private mailbox. Wait for each command's ID to be acknowledged before submitting
another. Remove/archive an old mailbox before arming a new supervisor. `serving` means a
process was launched; callers must independently verify loaded identity and real inference.

Every two seconds it records available memory, swap, pressure and owned process-tree RSS.
It ends collection and attempts restoration on deadline, critical pressure, sustained swap
growth, less than 6 GiB available memory, or an external intent/configuration change. It identifies processes
by PID, creation time and complete command before every termination signal; an unrecorded
experiment process or open experiment port blocks restoration. It refuses to overwrite new
operator intent. The original wrapper starts before the original admin job is reloaded.

A `restored` supervisor state proves that restoration commands completed, not serving
readiness. Verify original engine identity, real inference, gateway route, sidecar state,
configuration and the after snapshot separately. Keep at least ten minutes for those checks.

### Audited isolated launch recipe (only after that approval)

Pin [Splash 1.4.0](https://github.com/incoai/splash/releases/tag/1.4.0), not a moving branch.
The standalone asset is `splash-1.4.0-arm64-macos26.tar.gz`; verify it against release checksums
(expected archive SHA-256 `4c0f032a5392a6c559f081a0052278fbc0ea5abee2d16b820a015cc9b1826a2d`).
Audit the extracted layout, imports, writes and supported options before execution. Do not
install with Homebrew or reuse the production MLX Python environment.

Prepare a dedicated existing directory such as `/Volumes/Models/splash-spike`, with the
release extracted to `runtime/`, draft to `draft/`, and private logs/results/prepared assets
inside it. Copy `sandbox.sh` there. The wrapper clears ambient variables and redirects home,
HF/XDG/Python/uv/temp paths to that directory. It redirects writable defaults, not OS access;
abort for any unexplained writes outside it. Recheck files before/after, including per-user
config/cache/LaunchAgents and system package inventory. Do not invoke agent integration commands.

The [pinned local-preparation interface](https://github.com/incoai/splash/blob/1.4.0/install/local.py)
accepts existing MLX files and an explicit draft, with a separate output directory. Run using
Splash-owned Python; both preparation and all engine operations below execute on the Mac:

```bash
# SPIKE and TARGET are resolved private worksheet values; TARGET is read-only shared weights.
PREPARED=$(bash "$SPIKE/sandbox.sh" "$SPIKE" "$SPIKE/runtime/python/bin/python3" -P - \
  "$TARGET" "$SPIKE/draft" <<'PY'
import os
import sys
from pathlib import Path
from install.local import prepare_local_model
spike = Path(os.environ['SPLASH_SPIKE_ROOT'])
print(prepare_local_model(sys.argv[1], draft=sys.argv[2], models_dir=spike / 'prepared'))
PY
)
# Run in a supervised foreground session; record parent/native children and start times.
bash "$SPIKE/sandbox.sh" "$SPIKE" "$SPIKE/runtime/python/bin/python3" -P -m server.server \
  "$PREPARED" --model example/qwen-27b --binary "$SPIKE/runtime/engine/splash" \
  --host 127.0.0.1 --port 8086 --max-memory 40G --max-context 32768 \
  --idle-release off --max-cache-disk 0 --no-webui \
  --kv-format bf16 --state-format fp32
```

The API model ID is a worksheet value, not an upstream weight substitution. Inspect
`--help` on the pinned server before finalizing flags. Confirm readiness with `/ready`, then
review `/status` identity/effective limits, `/v1/models` aliases and real inference. Capture
private status JSON, Prometheus metrics, raw streams and logs. Scrub actual captures before
adding compatibility fixtures; the test suite's synthetic events are not captured evidence.

Exercise reasoning off/on, multi-turn/tools/schema, cancellation/slot release, image input
when required, production context and three clean restart cycles. Review engine timing
semantics and usage against the captured data before freezing native-rate manifests. Capture
thermal state, pressure, swap delta, available memory and parent/child footprint throughout
each session; preflight endpoints alone do not provide continuous resource qualification.
Stop the verified owned Splash generation before every MLX session; never run both full
engines together. Alternate sessions, preserve replay order and keep other clients excluded.

### Restore on completion, failure, timeout or interruption

The supervising maintenance session must run its reviewed cleanup on every exit. Record
owned parent/native PID **and start time/command**, send SIGTERM only after re-verifying that
generation, await children/port closure and memory release. Escalation after a deadline needs
a fresh ownership check; never signal a reused PID or kill all Python processes. If ownership
is uncertain, stop and report incomplete restoration instead of signalling an unrelated process.

Before restoring, compare current intent/config generation to the expected experimental
state. If another operator changed it, do not overwrite that change. Restore the saved engine
pack and inactive MLX selection/profile, sidecar states/intents and recovery configuration
through the supported control paths, then restore the admin launchd job's original loaded
state. Do not resume recovery if it was suspended before the experiment. Verify original
serving model/readiness, LiteLLM route, sidecars and host/config drift; do not delete model assets.

```bash
# From the devcontainer, capture an after snapshot and retain a reviewed annotated copy.
bash ops/splash-phase0/preflight.sh /tmp/splash-phase0/after.json
uv run python ops/splash-phase0/bench.py verify-restoration \
  --before /tmp/splash-phase0/before-reviewed.json --after /tmp/splash-phase0/after-reviewed.json
```

The verifier only compares reviewed snapshots; it performs no restoration itself. It requires
complete inventory, the same semantic intent/selection/config and recovery state, a verified
ready original service, no experiment processes and a closed experiment port. Job IDs and
intent timestamps are preserved in raw admin evidence but excluded from semantic restoration
comparison, since legitimate lifecycle operations change them. Missing proof fails verification.

## Development checks

```bash
uv run pytest tests/test_splash_phase0.py
uv run pytest --cov --cov-fail-under=80
uv run ruff check src tests scripts ops/splash-phase0
uv run mypy src scripts ops/splash-phase0
cmp AGENTS.md CLAUDE.md
```

Use a separate `COVERAGE_FILE` when measuring tooling coverage alongside a full test run:
`COVERAGE_FILE=/tmp/splash-tooling.coverage uv run pytest tests/test_splash_phase0.py --cov=ops/splash-phase0 --cov-fail-under=80`.
Run the private-pattern leak scanner over new untracked files explicitly before staging.
No benchmark result, installation or rollout is implied by passing Linux tests.

## Deterministic quality follow-up

The [quality report](quality-report.md) records the two real 35-case paired captures,
retained failed pilot, resource/restoration proofs and fixed-margin uncertainty. Both
engines have equal per-case outcomes on each selected checkpoint; the quality decision
remains inconclusive. See [evidence](quality-evidence.json) and [follow-up suite](quality-suite-512.json).

### Native collector live pilot

The [measured report](native-validation-report.md) and [evidence](native-validation-evidence.json)
retain both complete checkpoint pilots and the interrupted initial stock series. The
[native validation reviewer](native_validation.py) enforces native-input/HTTP association
and the original/instrumented overhead criterion. Accounting and functional checks pass;
modified MLX overhead remains inconclusive, so the native speed gate remains unassessed.
See [remaining qualification](qualification.md) for the confirmed 32K scope and open work.

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
