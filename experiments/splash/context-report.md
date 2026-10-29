# Near-limit 32K retrieval smoke

Date: 2026-10-26. Fixed synthetic context decision: **pass on both checkpoints**.
Native speed and representative quality gates remain unassessed by this test.
See [allowlisted evidence and receipt hashes](context-evidence.json),
[reviewer](context_validation.py), [input generator](context_inputs.py) and
[execution plan](../../plans/archive/splash/20261026_splash-32k-context-and-overhead.md).

| Checkpoint | MLX correct | Splash correct | Fixed context decision |
|---|---:|---:|---|
| stock-8bit | 6/6 | 6/6 | pass |
| modified-4bit | 6/6 | 6/6 | pass |

## Frozen condition and observed capture

The user confirmed 32K is sufficient for current context testing. Each checkpoint uses
six distinct synthetic documents: two seeds each with one unique retrieval value near
10%, 50% and 90% input depth. Expected values appear once in each document. Exact JSON
with a single correct `value` field is required; duplicate keys, extra text and wrong
answers fail. No model-generated code is executed.

The effective context is 32,768 tokens. All 24 paired long request bodies were frozen
after offline rendering exactly reproduced four saved real native-input fixtures, including
MLX rendered strings and Splash token IDs. Expected prompt tokens are 31,858–31,864, within
the predeclared [31,744, 32,000] band, leaving the 512-token output cap and additional room.
Every actual response must match its exact frozen prompt count, model identity and usage,
report zero reused input, reasoning off and a successful natural stop.

The two cohorts retained 28 requests, including four excluded short 64-token warmups.
There are 24/24 correct long-context answers. Missing, duplicate, truncated or errored
captures cannot pass, and incorrect answers are retained. Stock ran MLX then Splash;
modified ran Splash then MLX. Temperature 0, seed 0, concurrency 1, BF16 KV/FP32 recurrent
settings and the existing 40 GiB Metal guideline are preserved. That guideline is not an
OS RSS ceiling. GPU sidecars remain stopped.

Requests have 300-second deadlines. Each 14-request cohort budgets 90 minutes including
setup/restoration, inside an independent 110-minute collection cutoff and 120-minute
restoration deadline. No failed request was replaced or token band relaxed. TTFT and total
durations are preserved in the JSON as diagnostics; these early-stop replies do not
qualify the primary fixed-output speed gate.

## Resources and restoration

| Checkpoint | Minimum available GiB | Maximum new swap MiB | Maximum pressure level |
|---|---:|---:|---:|
| stock-8bit | 10.12 | 0.00 | 1 |
| modified-4bit | 15.88 | 0.00 | 1 |

Both attempts restored the original service and passed real inference, loaded-admin,
exact configuration/intent, target assets, experiment cleanup and independent sidecar
checks within their bounds. Runtime, target metadata, draft and prepared-link reviews
preceded each window. Listener process ownership, 57 installed runtime files and 20 target
metadata files were independently reviewed before every phase, outside timed requests.
The initial stock thermal observer started late; mandatory memory, swap and restoration
guarding ran continuously. Original installed sources, defaults and registries are unchanged.

Validation: 52 new context tests; 42 expanded overhead tests; full suite 2,579 passed,
one skipped, 98.10% application coverage. Ruff and strict mypy on 61 source files pass.
Existing client timing, quality and collector-pilot evidence remains frozen.

## Scope of the result

This is a fixed synthetic near-limit retrieval/resource smoke. Six related documents per
checkpoint do not establish the broad two-percentage-point quality margin, general long
context reasoning, replay or concurrent serving. No rate is inserted into the prior
timing artifacts. The [fixed expanded MLX audit](mlx-overhead-design.json) addresses the
remaining collector-overhead uncertainty separately; it cannot establish cross-engine
native speed. Replay, concurrency, deliberate operations, representative quality precision
and authenticated gateway inference remain open. Sidecar coexistence is unqualified.
