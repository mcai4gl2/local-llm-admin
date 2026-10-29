Overall Progress: 100%

# Expanded modified-MLX collector overhead audit

Date: 2026-10-26
Status: The separate corrected four-window follow-up is complete and collector-overhead
eligible. Every window restored the original service; final owned-helper cleanup passed.
The original incomplete expanded attempt, earlier pilot and context inputs stay frozen.

## Scope and constraints

Implement and execute the fixed expanded design on the selected modified 4-bit checkpoint,
using unchanged isolated original/instrumented MLX source copies and the existing independent
host guard. No production source/default/registry changes, no dependency changes and no
sidecar mix changes. Keep the original 5% median/upper-95% requirement overall and for every
category. Do not pool the pilot, drop samples, stop early on eligibility or add trials until
a pass appears. Run one-off source/tokenizer audits before/after phases.

Four independently restored ABBA windows give 48 measured pairs, 16 per category. Every
launch has one excluded 256-token warmup per category plus two measured repetitions; each
window has 36 requests and 58 minutes worst case including setup/restoration. Source/model
pins, limits, bootstrap seed/count/hierarchy and failure rules are fixed in the design.
This estimates collector overhead, not cross-engine native speed or GPU kernel time.

## Tasks

- [x] 🟩 Add failing schedule, trace/HTTP, hash, warmup, pairing and clustered-bound tests.
- [x] 🟩 Implement a strict expanded reviewer and require all 144 records and 72 accounted
  instrumented traces before eligibility. Recompute body/response digests from raw records.
- [x] 🟩 Freeze exact fresh nonce/body/command ledgers, source/import paths and per-window
  deadlines; validate focused/full tests, lint/typing and actual isolated imports.
- [x] 🟩 Execute all four independently guarded windows, restoring and verifying original
  inference/admin/configuration/intent/assets/sidecars between them; preserve every failure.
- [x] 🟩 Publish full observed bounds, accounting, resource/restoration receipts and any
  remaining limitations. Native speed and broader Phase 0 gates remain separate.

## Validation

Use existing devcontainer/uv. Test malformed and incomplete evidence before implementation,
then deterministic uncertainty calculations and actual source-pinned associations. Require
all category warmups, exact cap/counts, zero reuse, matched paired body/response/prompt counts
and unique independent-request nonces. Review final source/asset/owned-helper cleanup proofs.
Run application coverage >=80%, Ruff, strict mypy, private scanner, instruction parity and
unchanged completed-evidence checks; append devlog. No commits or worktrees.

### Warmup validation failure and separate follow-up

The first expanded attempt stopped after two excluded original-MLX warmups because the
reviewer incorrectly required a single user message, while the frozen 2K input includes
user/assistant conversation history. Both HTTP responses were valid; no measured pairs or
instrumented traces were captured. The original service passed all restoration checks.
The [incomplete report](../../../experiments/splash/expanded-overhead-report.md) and all private
campaign inputs/records remain frozen. No eligibility is inferred.

A regression test failed before accepting alternating nonempty user/assistant history,
ending with a user request. Invalid roles, unfinished turns and extra fields remain rejected.
The [separate follow-up design](../../../experiments/splash/mlx-overhead-followup-design.json)
retains the same four-window schedule, 5% criterion, clustered bootstrap and guard budgets.
All 144 exact fresh bodies are checked by the corrected validator before any model launch;
new command names, nonces, receipts and trace directories prevent accidental pooling.

### Completed follow-up outcome

All four independently guarded/restored windows completed: 144 valid requests, 48 excluded
warmups, 48 measured pairs and 72 accounted native traces. The frozen reviewer reports
**eligible** overall and for short/2K/8K inputs. Overall median wall ratio is 0.997389
(95% interval [0.990996, 0.999361]); the 8K upper bound is 1.002261, below 1.05.
No optional extension or pooling occurred. Ratios below one do not establish acceleration.
See the [report](../../../experiments/splash/overhead-followup-report.md) and
[evidence](../../../experiments/splash/overhead-followup-evidence.json).

Final source/import audit verified every isolated source copy, the bootstrap, 57 installed
runtime files and 41 launch receipts against the trusted manifest. New swap was zero;
minimum available memory across windows was 14.83 GiB. Final original generation/readiness,
admin, configuration/intent/assets/sidecars and experiment cleanup proofs pass. Owned SSH,
agent bridge, tunnel and observers are closed; temporary socket/PATH cleanup passed.

Validation after the conversation fix: 51 focused tests; full suite 2,588 passed, one skipped,
98.10% application coverage; Ruff and strict mypy pass. The private scanner still reports
59 existing pattern-23 matches, so staging remains empty. The 15-artifact preservation
baseline includes all earlier published evidence and both frozen designs.

This completes this overhead audit, not Phase 0. Full native speed, broader quality precision,
replay/concurrency, deliberate operations, sidecar coexistence and authenticated gateway
inference remain separate. The stock pilot's disclosed observer noise remains visible.
