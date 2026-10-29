Overall Progress: 100%

# Splash Phase 0 replay/cache at 8K and 32K

Date: 2026-10-27
Status: Fixed campaign reached its declared terminal outcome on 2026-10-27: pass.
All attempted windows are restored; overall adoption remains inconclusive.

## Scope and constraints

Run a separate fixed synthetic replay/cache correctness pilot for stock 8-bit and modified
4-bit. Preserve every committed timing, quality, context, collector and native comparison
artifact. Keep the existing branch and devcontainer; leave unrelated pending edits alone.
Use original isolated runtime copies, 32,768-token context, BF16 KV/FP32 recurrent state,
temperature/seed zero, reasoning off, concurrency one and GPU sidecars stopped. Preserve
40 GiB Metal guideline and disabled disk persistence. Explicitly enable APC only in the
isolated MLX launch, with a 4 GiB resident cache budget, 6 GiB memory reserve, two resident
checkpoint entries and a 2,048-token checkpoint interval. Audit the actual effective values. No deployment or default change.

## Frozen workload and interpretation

Four independently guarded/restored paired windows: stock 8K, modified 8K, stock 32K,
modified 32K. Alternate engine order across windows. Each backend uses three synthetic
retrieval trajectories at 10%, 50% and 90% input depth (seed zero), each with cold prime,
identical replay and a canonical fixed-history continuation. Freeze messages, canonical
assistant history, expected answer, per-trajectory namespace/salt, exact native rendering,
prompt counts and shared-prefix token counts before capture. Never construct cross-engine
histories from potentially differing generated replies.

8K prompts must be in [7,936, 8,192]; near-limit prompts in [31,744, 32,000]. All prompts
plus the 128-token cap must fit the context, including the growing history. Each cold
namespace is unique and must report zero reused tokens; repeated and extended requests
must reuse at least 80% of the exact shared prompt prefix. All replies need a natural
stop and the exact expected JSON value, valid model/usage/timestamps and disabled reasoning.
Unknown reuse, wrong/missing/reordered samples, failed warmups, truncation, incompatible
model/settings/source or missing restoration cannot pass. Retain all failures and partial
captures; no retries, optional extension or dropping failed trajectories.

There are 72 long requests (24 cold and 48 cached), plus eight excluded short warmups,
across four windows. Report per-step reuse, median/min/max TTFT and completion, and paired
cached-versus-cold observations separately per cohort/context/backend. Small fixed synthetic
samples do not qualify p95 latency, native decode speed, broad quality or overall adoption.
Any measured functional regression fails its scoped gate; incomplete evidence is inconclusive.

## Budgets and operations

Each window has two backends, one 30-second short warmup each and nine long requests per
backend. Long deadlines are 150 seconds at 8K and 300 seconds at 32K. The largest timeout
budget is 5,400 + 60 + 540 setup + 600 restoration = 6,600 seconds, inside independently
supervised 110-minute collection/120-minute restoration deadlines. Keep reserve before every
request. Abort on critical pressure, sustained new swap, under 6 GiB headroom, ownership or
intent changes, active jobs, request/accounting failure or interruption. Source/tokenizer
inspection and full target hashing happen outside timed phases. Stable resource/thermal
observers remain active. Restore and independently verify original real inference, admin,
configuration/intent/assets/sidecars and experiment cleanup after every attempted window.

## Tasks

- [x] 🟩 Add failing replay input/schedule/accounting, reuse/correctness, incomplete-evidence
  and safe-output tests without changing the committed measurement contracts.
- [x] 🟩 Implement a separate strict replay contract/reviewer and deterministic request
  construction, reusing existing HTTP parsing and context input grading.
- [x] 🟩 Freeze exact private inputs/tokenization/source/runtime/command/helper pins and
  public design; verify budgets, transport startup environment and repository quality gates.
- [x] 🟩 Run the fixed series to completion or its declared first-failure stop, with complete
  private receipts in independently guarded/restored windows,
  raw responses and fixed-schedule accounting; retain every failure without silent retries.
- [x] 🟩 Publish allowlisted per-condition results and limitations, verify restoration/source
  and owned cleanup, update campaign status and append the devlog.

## Validation and integration

Use test-first development and the existing uv environment inside the devcontainer. Run
focused replay/context tests, full pytest coverage >=80%, Ruff and strict mypy including
the standalone tooling, shell syntax, instruction parity and whitespace checks. Snapshot
all committed publication bytes before capture and require unchanged hashes afterward.
Scan tracked and nonignored untracked files with the unchanged private denylist before
staging. No commit is authorized by this milestone request. No new dependencies or worktrees.

The user resumed Phase 0 testing after the earlier deferral. This authorizes the chosen
bounded experiment; it does not authorize deployment or production configuration changes.
Replay is one component of the still-open secondary qualification checklist; concurrency,
reasoning, quality precision, deliberate lifecycle/memory checks, required service mix and
authenticated gateway inference remain separate.

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
See the [replay report](../../../experiments/splash/replay-report.md) and
[evidence](../../../experiments/splash/replay-evidence.json).
