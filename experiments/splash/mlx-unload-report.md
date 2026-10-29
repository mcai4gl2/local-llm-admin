# MLX manual-unload memory investigation

**Cause confirmed:** allocator-cache cleanup runs before the last resource reference expires.
**One-line isolated fix: pass on both checkpoints.**

Retained 8 inference attempts and 8 captures, with 8 unload cycles. All attempted windows independently restored and owned transports/helpers closed.

## Cause and minimal fix

The original unload loop builds a snapshot containing resource-owning cache dictionaries and leaves its last dictionary bound to a loop local. It runs garbage collection and the allocator purge while that reference still owns model buffers. When the function returns, the last buffers become free allocator-cache entries after the final purge has already happened. Loaded-model metadata can be empty while RSS remains high.

The isolated one-line change snapshots only group names, allowing resource dictionaries and buffers to expire before the final GC/cache purge. Worker stop/drain behavior is unchanged. The original pinned function reproduces this order defect with a deterministic allocator model; nine regression/preparation tests pass, including multiple caches, idempotence and a still-retained worker.

```diff
-    for cache_group, _ in list(registry.items()):
+    for cache_group in [kind for kind, _ in registry.items()]:
```

## Actual Mac observations

Two independently restored cohort windows compare original/fixed code in reversed order. Each process runs two unload cycles with a successful first inference and request-triggered resume. Identical process-local diagnostics hold only weak model/generator references. No purge or inference occurs between unload and the fixed 2/10/30/90-second observations. After 90 seconds, an explicitly labeled diagnostic GC/cache purge tests the retained-cache explanation; it is never counted as the automatic effect of `/unload`.

| Checkpoint | Variant | Cycle | Loaded RSS (GiB) | RSS after 90s (GiB) | Active after 90s (GiB) | Cached after 90s (GiB) | RSS after explicit purge (GiB) | Matched predicted signature |
|---|---|---:|---:|---:|---:|---:|---:|---|
| stock-8bit | original | 1 | 27.829 | 27.812 | 0.000 | 27.483 | 0.257 | pass |
| stock-8bit | original | 2 | 28.027 | 28.023 | 0.000 | 27.483 | 0.245 | pass |
| stock-8bit | fixed | 1 | 27.829 | 0.256 | 0.000 | 0.000 | 0.256 | pass |
| stock-8bit | fixed | 2 | 28.025 | 0.240 | 0.000 | 0.000 | 0.239 | pass |
| modified-4bit | fixed | 1 | 15.307 | 0.258 | 0.000 | 0.000 | 0.258 | pass |
| modified-4bit | fixed | 2 | 15.503 | 0.242 | 0.000 | 0.000 | 0.240 | pass |
| modified-4bit | original | 1 | 15.307 | 15.290 | 0.000 | 14.960 | 0.258 | pass |
| modified-4bit | original | 2 | 15.503 | 15.500 | 0.000 | 14.960 | 0.243 | pass |

The original rows match the predicted failure signature; they do not indicate healthy
unload behavior. Fixed rows meet the release criterion before the explicitly delayed purge.
Active MLX memory was exactly 1,032 bytes in every post-unload sample; the GiB table rounds
that value to 0.000. Fixed cached bytes were exactly zero throughout observation. RSS fell
more slowly in a few early samples, but all fixed cycles released more than 98% by 90 seconds.

Confirmation requires dead model/generator weak references and active bytes below 10% of loaded active memory. Original code must retain at least 50% of loaded active bytes as allocator cache and 80% of loaded RSS; the explicit delayed purge must reduce cached bytes below 10% and RSS below 50%. Fixed code must meet both low-cache and 50% RSS-release bounds before any diagnostic purge, on both cycles and checkpoints. The frozen design defines these thresholds; no post-result relaxation or retries were used.

## Scope, restoration and preservation

The fix is prepared and exercised only in a new isolated source copy. Installed MLX, older isolated original/instrumented copies, production defaults and original service selection/configuration/intent/assets remain unchanged. The complete repeated-unload result does not qualify active-request cancellation, automatic idle release, model replacement, APC-enabled unloading, audio/vision models or broader coexistence. Earlier failed lifecycle evidence remains unchanged.

All 61 earlier tracked experiment publications are byte-identical. Source checks review 57 installed runtime files, 1,476 installed MLX Python sources, both 1,477-file diagnostic trees and every custom launch receipt. Original inference/admin and sidecars are independently restored; owned processes/listeners/tunnel/observers/agent relay are closed and the previous shared agent environment is restored byte-for-byte.

Overall adoption remains inconclusive: quality precision, replay p95, concurrency, stock Splash host reclamation and authenticated gateway inference remain open. Unmodified MLX still has the previously measured unload failure. No commit or deployment was performed.

## Validation

Full suite: 2,754 passed, one skipped, 98.10% application coverage. Nine focused tests and 100% statement/branch preparer coverage; Ruff and strict mypy pass (65 source files). Existing unrelated working-tree maintenance/setup fixes are preserved and included in full-suite validation. Publication scan/formatting results are recorded after final review; remote pre-commit bootstrap/Gitleaks are not represented as passed.

See [frozen design](mlx-unload-design.json), [evidence](mlx-unload-evidence.json), [isolated patch preparer](mlx_unload.py), and [plan](../../plans/archive/platform/20261027_mlx-unload-memory.md).

Final publication checks: pinned formatting/JSON/conflict/size checks, instruction parity,
whitespace and the private-pattern public scan pass. Intended files are staged only; unrelated
pending changes remain untouched. Remote hook bootstrap/Gitleaks were not rerun.
