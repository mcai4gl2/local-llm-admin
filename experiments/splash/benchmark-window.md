# Selected two-model benchmark window

The user approved this bounded Phase 0B window on 2026-10-25. It is complete and restored;
see the [measured report](report.md). One of three planned paired sessions per checkpoint
finished inside the window. This is the reviewed recipe, not authorization for future live runs.
Use the [main runbook](README.md) for ownership, isolation, capture and restoration rules.
Run repository tooling in the devcontainer as `vscode`; run the indicated engine/OS
commands remotely over SSH using the target from `ops/deploy-env.sh`.

## Selected comparisons

The user selected stock Qwen3.8-27B **8-bit** and modified Qwen3.8-27B **4-bit**.
For each checkpoint, compare MLX and Splash using that checkpoint's identical target,
tokenizer and template. Keep separate result directories and reports. This is not a
stock-versus-modified or an 8-bit-versus-4-bit quality comparison.

Use [corpus.json](corpus.json), frozen text copied from the repository's public design and
engine transition implementation plus an incident-response instruction task. It contains:

| Task | Input category | Output cap |
|---|---|---|
| Incident-response guide | short | 512 tokens |
| Multi-turn code review | approximately 2K | 1024 tokens |
| Operational design review | approximately 8K | 1024 tokens |

The corpus is a small initial mixed workload, not a task-quality suite. Three prompts
cannot represent every workload. Local CPU tokenization checks prompt sizes before the
window; real server usage must independently verify sizes and paired prompt equality.
Freeze any necessary template corrections before measured repetitions. Do not remove or
rewrite a prompt after seeing which runtime performs better. Early stops remain visible
and prevent the fixed-length qualification gate from passing.

## Bounded scope

Approved maximum for this session: **two hours**, including startup checks and cleanup. Stop collecting
samples early enough to restore within the window. If the full matrix cannot finish,
retain partial evidence and report inconclusive rather than extend the interruption.

- Download/install the pinned Splash release and one pinned approximately 3.85 GB draft
  under a dedicated spike directory. No target-weight copies, production venv updates,
  model registrations or model-profile edits. Reserve 10 GiB for runtime/draft/temp/results.
- Pause the current serving engine and admin recovery under the reviewed procedure below.
  Only one inference engine may reside on the GPU. Stop any GPU sidecars actually running;
  retain their exact prior observed states and desired intent for restoration.
- Initial profile: loopback port 8086; 32,768-token **benchmark workload/retained-KV budget**;
  40 GiB Metal allocation budget; concurrency 1; temperature 0; seed 0; reasoning disabled;
  fresh isolated prefixes; one excluded warm-up and five measured repetitions per task.
  Native advertised context limits may differ: record those separately. This window does
  not prove the production long-context gate or turn a KV retention flag into a context API.
- For each model use three paired, counterbalanced sessions: MLX/Splash, Splash/MLX,
  MLX/Splash. The runner emits 216 total requests across both models, including 36 warm-ups.
- Before measured requests, verify exact assets/draft compatibility, both readiness and
  identity, prompt rendering/counts, request settings, native timing definitions and KV
  precision. Draft compatibility with the modified target is unproven until that check.
  Record any precision/runtime tradeoff and keep qualification inconclusive if required
  comparability cannot be established.
- Capture status, readiness, model list, streamed output, native timings, logs and resource
  observations. Check deterministic inference, reasoning, tools/schema and cancellation;
  full task-quality, production-context and intended service-mix gates remain separate.
- Abort for critical pressure, sustained new swap growth, less than 6 GiB OS-reported
  available memory, failed ownership checks, another operator's config/intent change,
  model mismatch, malformed output, failed readiness, or the window deadline.

The private worksheet holds the resolved model paths/hashes, original engine/pack,
service/sidecar state, launchd job/plist, manifest files and runtime checksums. Never
check it or its raw snapshots into this public repository. The prepared manifest drafts
are not frozen execution evidence: confirm draft bytes, precision, rendered prompts and
native timing semantics before finalizing them. The initial draft rate definition uses
client output/wall time; that cannot pass the native decode adoption gate.

## Concrete command sequence to resolve in the private worksheet

Here `SPIKE`, `TARGET`, `PREPARED`, `ADMIN_LABEL`, `ADMIN_PLIST` and `ORIGINAL_PACK` are
resolved/audited values, not literal strings. Capture PID/start-time/command observations
and compare the before snapshot immediately before starting. Abort if it has changed.
The following serving recipe applies when the observed original engine is Strata;
use a different reviewed restoration recipe if that assumption no longer holds.

1. Verify the local release archive's SHA-256, transfer it to `SPIKE`, inspect the extraction
   paths and extract only within `SPIKE/runtime`. Download the pinned draft config/weights
   there, verify their expected digests, and prepare each exact local target with
   `install.local.prepare_local_model(..., models_dir=SPIKE/prepared)`, using the release's
   Python through `sandbox.sh`. Preserve all original model files.
2. Ensure no admin/lifecycle/quality/download job is active. Record the original launchd
   loaded state and service intent, then remotely execute:

   ```bash
   launchctl bootout "gui/$(id -u)/$ADMIN_LABEL"
   /Volumes/Models/strata/bin/strata stop
   ```

   Stop other actually running GPU components through their audited wrappers. Verify
   owned generations exited, production/experiment listeners closed and memory released.
   Do not hold the short host maintenance lock while invoking lifecycle wrappers.
3. Start a supervised, foreground MLX experiment using its existing installed Python:

   ```bash
   bash "$SPIKE/sandbox.sh" "$SPIKE" /usr/bin/env -u PYTHONPATH \
     /Volumes/Models/venvs/mlx/bin/python -c \
     'import mlx.core as mx, runpy; mx.set_memory_limit(40 * 1024**3); runpy.run_module("mlx_vlm.server", run_name="__main__")' \
     --model "$TARGET" --host 127.0.0.1 --port 8086 --max-num-seqs 1 \
     --max-kv-size 32768 --max-tokens 1024
   ```

   Confirm the allocator API/options against the installed version first, record actual
   limits and observe whole-process memory. The installed MLX allocator documents its
   limit as a guideline, not a hard cap; monitor available memory/pressure and abort at
   the declared thresholds. Neither runtime budget is a whole-process RSS cap.
   The direct experiment leaves the production registry, selected symlink and profiles
   unchanged. Require inference/model identity before measurement, not health alone.
4. After verified MLX exit/port closure/memory release, launch Splash using the prepared
   target and a model ID matching its private manifest:

   ```bash
   bash "$SPIKE/sandbox.sh" "$SPIKE" "$SPIKE/runtime/python/bin/python3" -P -m server.server \
     "$PREPARED" --model "$SERVED_ID" --binary "$SPIKE/runtime/engine/splash" \
     --host 127.0.0.1 --port 8086 --max-memory 40G --max-context 32768 \
     --idle-release off --max-cache-disk 0 --no-webui \
     --kv-format bf16 --state-format fp32
   ```

   Capture parent/native children and start times, then check `/ready`, `/status`, model
   identity and real inference. Fresh requests use Splash `cache_salt`; MLX uses the
   audited `X-APC-Tenant` header. Also require reported zero cached tokens. Rediscovery
   after a crash/recovery must reverify identity and settings before any further samples.
5. Run the appropriate frozen manifest from the devcontainer through an SSH tunnel to
   port 8086. Stop the verified generation between sessions; never overlap full engines.
   Repeat the declared order independently for the other checkpoint. Compare directories
   separately; preserve failures, cancellations, early stops and unknown native rates.
6. On every exit path, close the owned experiment stream/processes and tunnel, verify
   parent/native child generations exited, port 8086 closed and memory released, then
   restore the original pack with:

   ```bash
   /Volumes/Models/strata/bin/strata start "$ORIGINAL_PACK"
   launchctl bootstrap "gui/$(id -u)" "$ADMIN_PLIST"
   ```

   Re-enable only components originally running, preserve sidecar desired intent, and
   restore the admin job only if it was originally loaded and no external operator
   changed the expected state. Respect pre-existing recovery suspension. Do not overwrite
   new operator intent or blindly repeat a start command after an ownership conflict.
7. Verify the original listener's generation, pack/model/readiness and unchanged LiteLLM
   route, sidecar intent/state and configuration hashes. Capture/review the after snapshot
   and run `bench.py verify-restoration`. Report incomplete restoration explicitly.

A passing speed observation does not authorize backend integration, production deployment
or a default engine change. Those still depend on the full plan's measured gates and approval.

## Subsequent approved timing sessions

Two separately approved continuation windows completed sessions 2 and 3, restoring and
verifying the original service between them and before each two-hour deadline. See the
[continuation record](continuation-windows.md) and [three-session report](report.md).
The original first-window evidence is preserved; the measurement contract is unchanged.
