# Fresh native emission comparison

Date: 2026-10-27. **Overall adoption: inconclusive.** Emission speed and client latency criteria below are scoped to this new frozen series; quality and operational gates remain separate.

Rates are recomputed from explicit target-token batches at engine delivery callbacks. The whole first batch is excluded. MLX in-process delivery and Splash IPC/wire-reader delivery costs remain included; this is not isolated GPU kernel throughput.

| Checkpoint | Native emission ratio | 95% interval | Emission speed | p95 TTFT | Fixed-length completion |
| --- | ---: | --- | --- | --- | --- |
| stock-8bit | 2.494996 | [2.389975934189482, 3.328202089354354] | pass | pass | pass |
| modified-4bit | 2.365861 | [2.2750806109009956, 2.9449391336223347] | pass | pass | pass |

## Captured evidence

6 independently guarded/restored paired windows, 216 attempted requests and 216 fully accounted native traces. The complete frozen schedule has 180 measured responses and 36 excluded category warmups across both checkpoints, with five measured repetitions per case/backend in each of three alternating sessions.

All fresh inputs are individually rendered/tokenized before capture. Every response must match expected prompt count, output cap, zero reuse and disabled reasoning; every native input/settings/source and final token-ID count must reconcile. Different answer text between engines is permitted. Raw replies, prompts, token IDs, deployment facts and launch arguments remain private.

The speed criterion is >=1.25 median of per-prompt paired ratios, lower 95% bound >1 and no category regression. Bounds retain the original 2,000-draw seed-0 session/prompt bootstrap; TTFT and fixed-length completion use the unchanged parent criteria. No prior samples are pooled, no eligible subset is selected, and no extra sessions are added after seeing bounds.

## Native emission distributions

Per-category p95 rates are descriptive, not additional tail-latency gate claims.

| Checkpoint | Engine | Input | Requests | Median tokens/s | p95 tokens/s | First batch tokens |
| --- | --- | --- | ---: | ---: | ---: | --- |
| stock-8bit | mlx | short | 15 | 10.015 | 10.016 | 1–1 |
| stock-8bit | mlx | 2k | 15 | 9.942 | 9.943 | 1–1 |
| stock-8bit | mlx | 8k | 15 | 9.742 | 9.743 | 1–1 |
| stock-8bit | splash | short | 15 | 24.982 | 26.406 | 1–1 |
| stock-8bit | splash | 2k | 15 | 33.090 | 33.815 | 1–1 |
| stock-8bit | splash | 8k | 15 | 23.380 | 24.467 | 1–1 |
| modified-4bit | mlx | short | 15 | 18.203 | 18.215 | 1–1 |
| modified-4bit | mlx | 2k | 15 | 17.960 | 17.971 | 1–1 |
| modified-4bit | mlx | 8k | 15 | 17.322 | 17.331 | 1–1 |
| modified-4bit | splash | short | 15 | 42.144 | 43.671 | 1–1 |
| modified-4bit | splash | 2k | 15 | 53.811 | 58.869 | 1–1 |
| modified-4bit | splash | 8k | 15 | 39.885 | 41.532 | 1–1 |

## Category ratios and client latency

| Checkpoint | Short emission ratio | 2K emission ratio | 8K emission ratio |
| --- | ---: | ---: | ---: |
| stock-8bit | 2.495× | 3.328× | 2.390× |
| modified-4bit | 2.283× | 2.945× | 2.303× |

The latency values below describe the frozen mix of 512/1024-token tasks. Pooled p95 TTFT
is not a per-category tail claim; the median output length is 1,024 tokens for every row.

| Checkpoint | Engine | p95 TTFT (s) | Median completion time (s) |
| --- | --- | ---: | ---: |
| stock-8bit | mlx | 23.295 | 108.077 |
| stock-8bit | splash | 14.930 | 34.206 |
| modified-4bit | mlx | 21.585 | 61.609 |
| modified-4bit | splash | 14.398 | 22.179 |

## Restoration and provenance

Every started window passed original real inference/admin, unchanged configuration/intent/assets/sidecars and experiment cleanup within its independent bound. Final live generation/readiness and no-active-job checks passed. Generation-checked cleanup closed owned transports, observers, temporary sockets and PATH; the original host agent was untouched.
Final source audit: all isolated source inventories and bootstrap unchanged, 57 installed runtime files unchanged and 74 import receipts reviewed. See source/model/runtime/collector pins and private receipt digests in the machine-readable evidence.

## Scope and remaining Phase 0 work

- Different in-process and native wire-reader delivery paths are included; GPU kernel compute and queue-latency decomposition are not isolated.
- Collector overhead calibration used 256-token length caps; the fresh native series uses the original 512/1024-token workload caps. No longer-cap overhead bound or correction is invented.
- Three paired sessions per checkpoint are independent top-level bootstrap units; repetitions and tokens are dependent.
- Earlier timing, quality, context, collector pilots and overhead audits remain separate and unchanged.
- Broader representative quality precision, replay/concurrency/reasoning, deliberate cold/idle/restart/memory-release cycles, any required sidecar coexistence and actual authenticated gateway inference remain open.

The clean [stock overhead audit](stock-overhead-report.md), completed [modified overhead follow-up](overhead-followup-report.md), and unchanged [functional collector pilot](native-validation-report.md) supply the scoped collector evidence. The higher output caps are explicitly disclosed above; no calibration at those caps is claimed. See [frozen native design](native-comparison-design.json), [reviewed callback boundary](native-comparison-boundaries.md), [machine-readable evidence](native-comparison-evidence.json) and [execution plan](../../plans/archive/splash/20261026_splash-native-comparison.md). Integration/deployment remain gated by the overall Phase 0 result.
