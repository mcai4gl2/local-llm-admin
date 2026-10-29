Overall Progress: 100%

# MLX unload memory investigation

Date: 2026-10-27
Status: Cause confirmed and one-line isolated fix validated on both checkpoints.
Both windows restored; installed runtime and earlier lifecycle evidence unchanged.

## Problem, known facts and ranked hypotheses

The fixed lifecycle experiment observed loaded-model state cleared by MLX `/unload`, but
only 0.065%/0.116% owned-RSS reduction within 90 seconds. Whole-process shutdown reclaimed
memory. There were no active requests or APC entries. Preserve that evidence unchanged.

Leading hypothesis: `unload_model_sync` snapshots cache dictionaries and retains the last
one in its loop binding while running GC and `mx.clear_cache`. Final native buffers become
free only after that allocator purge, leaving them cached. A deterministic fake-allocator
reproduction executing the actual pinned function confirms this ownership/order defect:
registry empty, active zero, allocator cache still populated after return. Competing causes:
a still-running generation worker or other strong model references; active-vs-cached Metal
telemetry and weak references must distinguish them. Source-level proof alone does not
prove the actual retained RSS cause.

## Minimal fix and test plan

Keep only cache-group names in the outer unload loop, so resource-owning dictionaries can
expire before the final GC/cache purge. Preserve existing stop/drain semantics and all
other code. Add a source-pinned preparation utility that writes a new isolated output
bundle, rejects changed/ambiguous source and refuses overwrites. Never modify installed
MLX, the earlier original/instrumented copies or earlier experiment tooling/publications.

Tests first: deterministic allocator lifetime tests for one/multiple caches, empty/idempotent
unload, active-worker references and source-pin/anchor/overwrite checks. Execute the actual
pinned function privately against the same fake allocator before and after the patch.
Use `uv run pytest`, Ruff and strict mypy inside the existing devcontainer; full coverage
>=80%, critical preparation/review paths 100%. Keep current branch and dated-plan convention.
The user's continuation authorizes the bounded isolated investigation; no production patch,
deployment, commit, dependency change or worktree.

## Frozen live diagnostic protocol

Two independently guarded/restored windows, stock 8-bit then modified 4-bit. Each window
runs a freshly loaded original and fixed MLX process; reverse variant order for the second
checkpoint. Source trees differ only by the one-line unload fix. Both use identical
process-local diagnostic wrappers: expose active/cached MLX bytes and weak model/generator
references without retaining resources or flushing memory during observation. Keep 32K,
concurrency one, reasoning off, zero temperature/seed, disabled APC/disk cache, stopped GPU
sidecars and the existing 40 GiB Metal guideline. Custom isolated bootstrap and source-tree
inventories are pinned separately; original experiment/import/source histories stay unchanged.

Per process: loaded readiness, one correct short JSON inference, manual unload, then fixed
observations at 2/10/30/90 seconds without intervening inference or purge. Record unloaded
metadata, RSS, active/cached bytes, weak-reference liveness and worker state. At 90 seconds,
perform one explicitly labeled diagnostic GC/cache purge, observe again after two seconds,
and make a duplicate unload call to verify idempotence. Resume with one correct fresh JSON
inference, unload a second time, repeat the fixed observation/purge sequence, then stop and
verify owned generations/listener gone and host reclamation. Total: four process launches,
eight inference requests, eight unload cycles, four duplicate unload calls and eight
explicit post-observation diagnostic purges; no inference warmups or hidden retries.

Expected confirmation: original metadata empty and model weak references dead, active
memory low but allocator cached bytes/RSS retained; the explicitly delayed purge removes
those bytes. Fixed version releases at least 50% owned RSS by 90 seconds on both repeated
cycles/checkpoints, with low active/cache bytes, dead weak references, correct resume and
clean stop. Baseline signature that contradicts the hypothesis is retained as a falsification;
it cannot support a fix claim. Timing is diagnostic, not native/p95 qualification. Existing
lifecycle failed outcomes remain frozen; fixed-copy proof does not qualify unmodified MLX.

Budgets per process: 120 startup + 2*90 inference + 3*30 unload APIs + 2*90 observation +
2*30 diagnostic purge + 90 stop/reclaim = 720 seconds. Per paired window 1,440 + 540 setup
+ 600 restoration = 2,580 seconds. Keep independent 110-minute collection/120-minute
restoration deadlines, remaining worst-case reserve, generation-safe control, pressure/swap/
6 GiB headroom aborts, original real-inference/admin/jobs/configuration/intent/assets checks,
source pins and independent restoration/owned-helper cleanup after every attempted window.
Stop on transport/accounting/resource/ownership failure; retain every partial record. No
post-result extension or pooling with previous series.

## Tasks

- [x] 🟩 Add failing lifetime/source-pinning tests and reproduce the actual pinned function.
- [x] 🟩 Implement the minimal isolated patch preparer; verify unit and actual-source proofs.
- [x] 🟩 Freeze diagnostic wrappers, source/command/helper pins and fresh preflight; complete
  repository quality and public-safety checks before live capture.
- [x] 🟩 Run the fixed original/fixed diagnostic windows, retaining all observations and
  independently restoring the original service after each attempted window.
- [x] 🟩 Publish the confirmed/falsified/inconclusive cause, patch proof and limitations;
  verify source preservation/cleanup, append devlog, scan and stage intended files only.

## Reproduction and evidence

The private actual-source AST reproduction records active=0 and cached=100 after the
original unload returns, against an expected cached=0. Public tests use small resource
objects to model native buffers becoming allocator-cache entries on final reference release.
Private raw streams/telemetry and source copies remain private. Link the final public report
here once the live signature is measured; never publish real deployment paths or identifiers.

## MLX unload cause and isolated fix confirmed — 2026-10-27

A separate fixed diagnostic series confirms the manual-unload RSS cause: the unload loop
holds its last resource-owning cache dictionary through GC and allocator purge, so model
buffers enter the allocator cache only after that purge. After original unload, weak model/
generator references are dead and active bytes are only 1,032, but cached memory remains
27.483 GiB (stock) / 14.960 GiB (modified). An explicitly delayed purge removes it.

A one-line source-pinned change snapshots only cache-group names. Across two cycles per
variant/checkpoint, all four fixed unloads leave zero cached bytes before any diagnostic
purge, with 90-second RSS 0.240–0.256 GiB for stock and 0.242–0.258 GiB for modified.
Original 90-second RSS remains 27.812–28.023 / 15.290–15.500 GiB. All eight inference
requests, repeated unload/idempotence/resume and owned stops pass. Both original-service
restorations, final source/import checks and owned transport/helper cleanup pass. All 61
prior tracked experiment files are unchanged; installed runtimes are unchanged.

This resolves the cause and proves the fix in an isolated copy, while the earlier unmodified
MLX lifecycle failure remains valid. No installed patch, deployment or commit was made.
Active/draining requests, model replacement, APC-enabled unload, audio/vision and automatic
idle release remain outside this fix's measured scope. Overall adoption remains inconclusive;
other quality/replay/concurrency/gateway/coexistence and stock Splash reclamation conditions
remain open. Full suite 2,754 passed, one skipped, 98.10% coverage; nine focused preparer
regressions at 100% statement/branch coverage; Ruff and strict mypy pass (65 source files).
See the [investigation report](../../../experiments/splash/mlx-unload-report.md) and
[patch preparer](../../../experiments/splash/mlx_unload.py).
