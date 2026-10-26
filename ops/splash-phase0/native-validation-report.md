# Native collector live pilot

Date: 2026-10-26. **Accounting and functional checks pass on both checkpoints;
collector overhead remains inconclusive. Native speed gate: unassessed.**

See the [allowlisted evidence and receipt hashes](native-validation-evidence.json),
[tested review implementation](native_validation.py) and
[execution plan](../../plans/20261026_splash-native-live-validation.md).
The completed [client timing](report.md) and [quality](quality-report.md) evidence is unchanged.

## Observed accounting and behavior

The complete modified 4-bit pilot and separate stock 8-bit follow-up each contain 68
requests: 64 successful HTTP replies and four intentional cancellations. Every successful
instrumented request has an associated native-input companion and trace: 36 per checkpoint.
All four instrumented launches per checkpoint passed natural stopping, reasoning and
cancellation. Each cancelled request created exactly one ineligible failed trace; subsequent
real inference proved that the request slot was released.

Across retained attempts, accounting totals are 159 successful HTTP replies, 10
intentional cancellations, 1 HTTP timeout and 90 accounted successful native traces.
These totals describe artifact completeness; they are not pooled performance samples.
Actual token IDs and native batch boundaries are retained privately. The rate numerator
excludes the whole first batch, including multiple speculative tokens. EOS handling is
reviewed against the pinned target tokenizer/configuration; final HTTP counts include the
observed terminal token consistently.

## Predeclared overhead result

Each engine uses original, instrumented, instrumented, original launches, one excluded
64-token warmup per launch and two paired repetitions each of short/2K/8K prompts with
256-token completion caps. Paired requests must have matching body/response hashes and
prompt counts, zero reused input and valid full-length accounting. No failed sample is
dropped. Twelve paired requests per engine and checkpoint remain a pilot.

Ratios are instrumented/original client wall time. The frozen criterion requires both
the median and upper 95% bound <=1.05 overall and in **every** prompt category. Bounds use
2,000 seed-0 hierarchical bootstrap draws over two launch-pair blocks, categories and
paired repetitions. `eligible` below means that statistical pilot criterion is met.

| Checkpoint | Engine | Overall median ratio | Overall 95% interval | Largest category upper bound | Statistical decision |
|---|---|---:|---|---:|---|
| modified-4bit | mlx | 0.9984 | [0.9883, 1.0276] | 1.0764 | inconclusive |
| modified-4bit | splash | 0.9984 | [0.9864, 1.0070] | 1.0471 | eligible |
| stock-8bit | mlx | 0.9998 | [0.9778, 1.0037] | 1.0343 | eligible |
| stock-8bit | splash | 0.9990 | [0.9922, 1.0075] | 1.0173 | eligible |

Modified MLX's 8K interval remains too wide to establish the 5% limit. The overall median
does not override that category requirement. No threshold, interval method or sample was
changed to obtain a pass. Read-only source hashing and EOS/tokenizer inspections overlapped
MLX segments and are possible CPU/disk noise; these exploratory pilot statistics do not
establish an isolated overhead or cross-engine compute finding. Perform one-off audits
between phases in a future timing campaign.

## Retained interruption and separate follow-up

The initial stock pilot stopped on its 34th request: the final original-MLX 8K reply
lacked final HTTP accounting when its 60-second client deadline elapsed. The server logged
completion near 50 seconds. Its 18 accounted native traces, two intentional cancellations,
all preceding responses and timed-out response remain in the failed series. Cause is
unresolved; a server log does not repair the client record. Its overhead result stays
inconclusive. Splash had not run in that attempt.

A separately frozen stock follow-up uses fresh nonces and trace directories, allowing
90 seconds only for 8K requests and retaining 60 seconds elsewhere. Its maximum
request/setup/restore budget is 98 minutes; the original pilots use 90 minutes. Each
window has its own independent 110-minute collection cutoff and 120-minute restoration
deadline. No earlier cutoff is extended, and no data is spliced or pooled across versions.

## Runtime safety and verification

Original and instrumented packages are separate copies; installed runtimes, defaults and
registries were unchanged. Final source/import audits verify all four isolated package
inventories, the bootstrap and 57 installed runtime files. Source, adapter, collector,
launch/profile, request, native-input and private restoration receipt hashes are preserved.
Both target assets and the pinned draft were verified before each window and target
weights/configuration again after restoration.

Every attempt restored the original service and passed real inference, loaded-admin,
intent/configuration, target-asset, experiment-cleanup and independent sidecar checks within
its bound. Independent resource monitoring recorded at least 13.22 GiB available,
maximum new swap 0.00 MiB, and no critical pressure abort. The optional modified
pilot thermal observer started late after an environment error; continuous memory, swap
and restoration guarding remained active. Temporary owned transports are closed afterward.

Validation before live capture: 35 new review tests; 374 focused tooling tests; full suite
2,485 passed, one skipped, 98.10% application coverage. Ruff and strict mypy pass.

## Remaining qualification

MLX overhead needs a separately frozen, more precise audit. A full native comparison
requires equivalent reviewed emission endpoints, adequate independent launches and new
per-request traces; this pilot supplies no native speed verdict. The in-process MLX and
Splash wire-reader paths include different queue/transport behavior and do not isolate
GPU kernel time. Existing server-reported rates keep their original definitions.

The user confirmed 32K is sufficient for current context testing. Actual near-limit 32K
correctness/resources, replay, concurrency, operations, representative quality precision
and authenticated gateway inference remain separate work. The current profile keeps GPU
sidecars stopped; coexistence is unqualified. Integration and deployment remain gated.
