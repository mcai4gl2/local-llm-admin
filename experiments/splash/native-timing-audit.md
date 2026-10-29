# Native timing audit for the pinned Phase 0 runtimes

Audit date: 2026-10-25. Result: **native rate comparability is unproven**.
This audit does not authorize selecting `backend_decode_tok_s` for the existing experiment.
The saved session-1 manifests and results remain unchanged.

## Source identity

These are hashes of the actual installed MLX source and Python modules extracted from the
verified Splash release archive, inspected locally. They pin this finding to those bytes;
upstream changes and another installation require another audit.

| Runtime / source | SHA-256 |
|---|---|
| MLX VLM 0.7.3 `server/generation.py` | `6c0b663d7adafbc686c6049649953fd78bea1572d2a6280532fc063dda511b77` |
| MLX VLM 0.7.3 `server/openai.py` | `fd5dafaba30c5779390657110aa16c1fa2409335853d58bf4ceec1fa701fe6c7` |
| MLX VLM 0.7.3 `server/schemas.py` | `80c863e32dd543ca878876e3b183559adfa1a27848d44599ae0852efe74fea68` |
| Splash 1.4.0 `server/backend.py` | `0ce85023177d4029942e9505a4b0f8c67dddb29a7fef37ac9c0fd15ce1b0bcd6` |
| Splash 1.4.0 `server/metrics.py` | `d010c426dcb275a32795c1222fa26e484f4633cf89943fe5ed8aea73cf8986a4` |
| Splash 1.4.0 `server/runtime.py` | `6733f8003c4f52dfbb09b5ab885c7445f12e9c62c1cdd93eb05323362d0eeb5b` |

The archive/native binary/draft fingerprints and real protocol fixture source digests are
recorded in [evidence.json](evidence.json) and the [fixture directory](../../tests/fixtures/splash-phase0/README.md).
Raw installed source snapshots remain private; deployment paths are not published here.

## Definitions found in the source

MLX `GenerationMetrics.record_chunk` in `generation.py:780` records token counts using the
chunk's explicit `token_count`, a cumulative generation-token difference, or its single-token
fallback. Its timestamps use the chunk's `emitted_at` when present, otherwise the server's
performance clock. The selected MLX path is ordinary generation without a speculative draft.
`GenerationMetrics.rate` at line 804 computes:

```text
(generated_tokens - first_chunk_tokens) /
(last_recorded_emission_time - first_recorded_emission_time)
```

The streaming chat finish and usage path reads that metrics object. This is an emission
interval, not a sum of isolated GPU kernel times. `GenerationTimings.from_metrics` in
`schemas.py:489` prefers this rate and derives `predicted_ms` as
`output_tokens / generation_tps * 1000`. Thus MLX's reported `predicted_ms` is reconstructed
from the total output and that rate; it is not an independently recorded decode interval.
Fallback rates are also present in the source and must not be assumed to have the preferred
path's semantics when emission timing is unavailable.

Splash `NativeBackend` in `backend.py:865` records the size of the first native token batch
when `sequence_offset == 0`. Its result finalization at line 926 maps native Done-event
`decode_micros` to `first_token_to_done_ms`. In `metrics.py:272`, `metrics_dict` computes:

```text
decode_tokens = completion_tokens - first_token_batch_tokens
stream_tokens_per_second = decode_tokens * 1000 / first_token_to_done_ms
```

`timings_dict` in `metrics.py` exposes that stream rate as
`timings.predicted_per_second`. The source explicitly calls the native Done-event interval
request lifecycle timing and distinguishes it from native batch throughput in `/status`.

Both rates exclude the first emitted token batch. That similarity does not prove equivalent
timing endpoints: MLX ends at its last recorded emission; Splash uses native Done timing.
The Python/native transport and completion finalization can contribute differently. The
Splash first emission may also contain multiple speculative tokens. The available sources
and captures do not bound those differences across every timed request.

## What the captures establish

The real 96-token stream fixtures have validated final usage, rather than token counts
inferred from SSE events. Splash's stock fixture reports 95 post-first-emission decode tokens
and 3,442.553 ms for first-token-to-Done, giving 27.5958 tok/s. MLX's stock fixture reports
its final rate of 10.0268 tok/s. These are smoke captures, not extra benchmark repetitions.

`timings.predicted_n` is a total completion count where present; it is not automatically the
native decode numerator. Dividing it by `predicted_ms`, or subtracting client TTFT from request
wall time and using all output tokens, cannot repair the missing common interval. Chunk
counts cannot identify the first speculative batch's token count.

Prompt timing also differs: MLX's native `prompt_n` counts uncached input; Splash's counts
total input. Contract v2 validates those definitions separately against final usage and
cached-token counts, without rewriting usage.

## Consequence for continuation

Keep the frozen `client_output_wall_tok_s` metric for sessions 2 and 3. Completing them can
assess the client latency/end-to-end gates and replication of the observed wall-rate result;
it will **not** satisfy the native decode adoption gate.

A qualifying native comparison needs a separately frozen, audited contract with equivalent
token and timing boundaries, and corresponding per-request evidence from both runtimes.
That may require supported instrumentation beyond the current responses. Do not retrofit
that contract onto session 1, reinterpret these diagnostics as a pass, change production
runtimes implicitly, or loosen the plan's decode requirement after seeing the speedup.

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

## Live collector pilot — 2026-10-26

See the [measured collector report](native-validation-report.md). Both checkpoints now
have live source-pinned accounting, natural-stop/EOS, reasoning and cancellation evidence
from isolated source copies. All 90 retained successful traces reconcile with native inputs
and final HTTP counts; 20 natural stops include exactly one terminal EOS, and length-capped
traces include none. Installed production runtimes remain unchanged.

This does not resolve native speed comparability. Modified MLX's 8K overhead upper bound
is 1.0764, above the frozen 1.05 criterion; its result is inconclusive. MLX segments also
include disclosed read-only audit activity, a possible timing confound. Equivalent logical
callback endpoints still include different in-process/wire-reader queue paths. A separately
frozen audit and full native series are required. Existing client timing files and
server-reported rate definitions remain unchanged.
