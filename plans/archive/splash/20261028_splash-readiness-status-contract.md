Overall Progress: 100%

# Splash readiness status contract correction

## Problem and evidence

The approved Stage B stock startup exposed a reproducible readiness-schema mismatch.
Pinned Splash1.4.0 returns ready with matching controller/model, aliases and32K context.
The owning wrapper/admin adapters expect `kv.format` and top-level `state_format`; actual
configuration is at `identity.kv.format` and `memory_plan.model.{kv_format,state_format}`.
Runtime `kv` and `state` sections contain allocation counters. Synthetic readiness
fixtures mirrored the wrong shape. Qualification stopped before Splash inference;
temporary gateway resources were removed while the owning startup job settles.

## Hypothesis and confirmation

The field-path mismatch alone prevents loaded readiness. Confirm using an allowlisted
projection of the captured response, with synthetic instance identity, first against the
unchanged wrapper/admin and then the corrected parsers. Preserve mismatched/missing/
malformed precision fields as unknown. Do not weaken process ownership, context, aliases
or generation checks. Require runtime identity and valid memory-plan precision to agree.

## Tasks

- [x] 🟩 Finish owned cleanup and verify original MLX/services, disabled env and gateway.
- [x] 🟩 Add capture-based failing readiness tests and malformed/conflicting field cases.
- [x] 🟩 Correct all readiness/quality validators and replace synthetic fixtures with the pinned shape.
- [x] 🟩 Run relevant and full regression/coverage, lint/type/parity/public checks.
- [x] 🟩 Prepare the exact corrective deployment and qualification continuation proposal.

## Validation

Run `uv run pytest tests/test_splash_backend.py tests/test_host_splash.py` for the failing
repro and fix; then all affected monitoring/ownership/quality tests and the full required
`uv run pytest --cov --cov-fail-under=80`, Ruff and strict mypy. Check the host and admin
parsers independently refuse mismatched/absent precision and invalid memory-plan state.
No inference-path work, caching or performance boundary changes are needed.

## Safety and restoration

Restore the approved unchanged live deployment before editing runtime source locally.
Retain the sealed approved packet and unsuccessful attempt; new code invalidates its
source manifests. Any corrective live apply or renewed qualification must have a complete
reviewable new revision/manifests and restoration commands before approval. Schema16
telemetry is preserved; the current window needs no schema15 deployment rollback.


## Attempt closure and reproduction

The owning startup job timed out; all Splash resources were released and original
MLX/services/intents plus disabled environment were verified. Both original gateway
checks pass; two attempts total and zero Splash inference. The current schema16 backup
has integrity`ok`; zero native readings and six availability observations are retained.

The unchanged implementation failed21 focused capture-based readiness cases before
correction. The capture also distinguishes memory-plan capacity from the32K serving
limit; no allocator-capacity equality is invented. The quality adapter had a third
copy of the stale precision checks. A shared stdlib validator now supplies all three
paths, mirrored byte-for-byte to the standalone host contract. Private offline replay
of the full captured document verifies corrected readiness and native snapshot binding.


## Signal-cleanup finding

A focused run also exposed an inherited empty maintenance-gate leak under SIGINT.
Four explicit before/after-unlink TERM/INT injections reproduced it; eight cases now
cover both gate and lifecycle release. The guard defers signal delivery only during
verified owner removal, then releases the remaining owned reservations and re-raises
the signal. No unknown ownership is acquired or deleted.

The readiness-only full checkpoint passed 3,336 tests/two skipped/98.15% coverage.
The final full run includes the additional guard correction and eight deterministic
signal cases; its result will be recorded separately. The new combined corrective
packet changes three admin and three host files, retains schema16 and uses a fresh
45-minute/40-attempt budget (30 scheduled) for cutover and qualification.


## Final validation and handoff

Final complete regression: **3,344 passed, two skipped, 98.14% coverage** in544.24s.
The shared precision helper has100% statement/branch coverage; all eight injected
signal cases pass. Ruff, strict mypy (58 app/script plus two host files), shell
syntax and host/admin contract parity pass. Private operating safeguards pass34
checks, including configured-primary response identity for alias requests, service
state drift and new-window export boundaries. A read-only pinned-source audit
confirms response identity; no extra inference was sent for that inspection.

The [corrective deployment/rerun proposal](../../../docs/reports/splash/readiness-fix.md) and
ignored executable packet bind the changed source, matching schema16 backups/fallback
and a fresh45-minute/40-attempt window (30 scheduled). The original approved packet
and its aborted execution are preserved. New live approval is pending; this debug
plan's completed scope is the local fix, validation and concrete deployment proposal.


## Approved deployment result

The approved paired correction is installed and its post-cutover hashes, schema16, health
and original MLX generation/inference passed. Stock now reaches owned loaded readiness
and direct complete streaming passes. The next gateway request exposed a separate client
identity assumption; qualification aborted and original MLX/services/intents plus disabled
Splash were restored. The [execution report](../../../docs/reports/splash/readiness-fix-report.md)
preserves five counted attempts and all failures. The separate functional rerun proposal
changes operator contracts only; no additional runtime correction is proposed.
