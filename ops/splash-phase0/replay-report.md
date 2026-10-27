# 8K/32K replay and cache correctness pilot

Date: 2026-10-27. **Fixed replay pilot: pass. Overall adoption: inconclusive.**

Fixed synthetic 8K/32K replay correctness and substantial reported shared-prefix reuse; descriptive client latency only.

| Checkpoint | Context | Attempted / captured | Long / cached replies | Replay gate | Restored |
| --- | --- | ---: | ---: | --- | --- |
| stock-8bit | 8k | 20 / 20 | 18 / 12 | pass | True |
| modified-4bit | 8k | 20 / 20 | 18 / 12 | pass | True |
| stock-8bit | 32k | 20 / 20 | 18 / 12 | pass | True |
| modified-4bit | 32k | 20 / 20 | 18 / 12 | pass | True |

## Frozen behavior and accounting

Each engine receives three different retrieval documents with the target near 10%, 50% and 90%
of input depth. Each trajectory sends a cold prime, an identical replay and an extension with
a predeclared canonical assistant answer followed by a new user turn. Paired histories never
depend on potentially different generated replies. Exact template/token receipts match four
saved native fixtures. Input bands are 7,936–8,192 and 31,744–32,000 tokens within the effective
32,768-token context, including the growing history and 128-token completion allowance.

Stable per-trajectory APC tenant/salt and system prefixes isolate different documents. Cold
requests must report zero reuse; repeats/extensions must reuse at least 80% of the exact shared
prompt prefix. Every reply needs the expected JSON value, natural stop, matching prompt count,
valid model/usage/timestamps and disabled reasoning. Each backend starts with an excluded
32-token short warmup. Here cold means no input-cache reuse in a loaded, warmed engine;
process/OS cold-start and idle-resume remain separate tests. Error, truncation, unknown reuse
or wrong answers cannot pass. The fixed extension asks for the same value already present
in canonical assistant history, so it checks continuation consistency rather than independent
retrieval of a new fact.

MLX uses original isolated source with process-local APC enabled, 4 GiB resident cache budget,
6 GiB reserve, two checkpoint entries and 2,048-token interval. Effective settings and process
environment are independently checked. Splash uses original isolated source with RAM prefix
reuse and zero disk-cache budget. The existing 40 GiB Metal guideline applies to both; it is
not an OS RSS cap. Disk persistence is disabled. No installed source or production setting changes.

## Descriptive client latency and reported reuse

Only fully validated complete conditions have distributions below. Partial/failed conditions
retain their counts, issues and raw private receipts and cannot imply a pass. These small
samples do not support a p95 or confidence-bound claim. The parent replay p95 latency gate
remains **unassessed**; this passing functional pilot does not close that gate.

| Checkpoint | Context | Engine | Step | Replies | Median TTFT (s) | TTFT min–max (s) | Median completion (s) | Reused tokens min–max |
| --- | --- | --- | --- | ---: | ---: | --- | ---: | --- |
| stock-8bit | 8k | mlx | prime | 3 | 21.432 | 21.382–22.275 | 23.593 | 0–0 |
| stock-8bit | 8k | mlx | repeat | 3 | 0.388 | 0.361–0.494 | 2.683 | 8004–8006 |
| stock-8bit | 8k | mlx | extend | 3 | 0.798 | 0.693–0.801 | 3.009 | 8004–8006 |
| stock-8bit | 8k | splash | prime | 3 | 15.076 | 14.704–15.259 | 15.501 | 0–0 |
| stock-8bit | 8k | splash | repeat | 3 | 0.266 | 0.196–0.274 | 0.851 | 7968–8000 |
| stock-8bit | 8k | splash | extend | 3 | 0.427 | 0.347–0.478 | 0.816 | 7968–8000 |
| modified-4bit | 8k | mlx | prime | 3 | 21.591 | 20.520–21.668 | 22.614 | 0–0 |
| modified-4bit | 8k | mlx | repeat | 3 | 0.287 | 0.252–0.356 | 1.634 | 8003–8007 |
| modified-4bit | 8k | mlx | extend | 3 | 0.595 | 0.574–0.649 | 1.931 | 8003–8007 |
| modified-4bit | 8k | splash | prime | 3 | 13.789 | 13.666–14.155 | 14.151 | 0–0 |
| modified-4bit | 8k | splash | repeat | 3 | 0.249 | 0.140–0.274 | 0.616 | 7968–8000 |
| modified-4bit | 8k | splash | extend | 3 | 0.350 | 0.329–0.388 | 0.542 | 7968–8000 |
| stock-8bit | 32k | mlx | prime | 3 | 95.078 | 94.909–95.455 | 97.376 | 0–0 |
| stock-8bit | 32k | mlx | repeat | 3 | 0.544 | 0.538–0.579 | 3.360 | 31861–31863 |
| stock-8bit | 32k | mlx | extend | 3 | 1.110 | 1.097–1.223 | 3.903 | 31861–31863 |
| stock-8bit | 32k | splash | prime | 3 | 67.278 | 66.427–67.611 | 68.117 | 0–0 |
| stock-8bit | 32k | splash | repeat | 3 | 0.257 | 0.247–0.351 | 1.117 | 31840–31840 |
| stock-8bit | 32k | splash | extend | 3 | 0.446 | 0.414–0.466 | 0.860 | 31840–31840 |
| modified-4bit | 32k | mlx | prime | 3 | 89.556 | 89.215–89.687 | 90.825 | 0–0 |
| modified-4bit | 32k | mlx | repeat | 3 | 0.553 | 0.529–0.563 | 2.475 | 31860–31861 |
| modified-4bit | 32k | mlx | extend | 3 | 1.007 | 0.951–1.211 | 2.881 | 31860–31861 |
| modified-4bit | 32k | splash | prime | 3 | 64.437 | 64.436–64.564 | 64.913 | 0–0 |
| modified-4bit | 32k | splash | repeat | 3 | 0.211 | 0.201–0.355 | 0.721 | 31840–31840 |
| modified-4bit | 32k | splash | extend | 3 | 0.424 | 0.373–0.492 | 0.675 | 31840–31840 |

## Resources, restoration and scope

Each independently guarded window reserves restoration time and aborts on pressure, new swap,
under 6 GiB headroom, ownership/intent changes or capture failure. One-off tokenizer/source/full
target inspections precede timing; stable resource/thermal observation remains active. Original
real inference, admin, intent/configuration/assets, sidecars and experiment cleanup are verified
after each attempted window. Owned temporary observers, SSH transports and sockets are closed,
and the previous private agent environment is restored. Source inventories and launch/import
receipts pass final checks; all 53 prior committed experiment files remain byte-for-byte unchanged.

- stock-8bit / 8k: minimum available 14.50 GiB, maximum new swap 0.00 MiB, maximum pressure level 1.
- modified-4bit / 8k: minimum available 16.37 GiB, maximum new swap 0.00 MiB, maximum pressure level 1.
- stock-8bit / 32k: minimum available 11.06 GiB, maximum new swap 0.00 MiB, maximum pressure level 1.
- modified-4bit / 32k: minimum available 15.88 GiB, maximum new swap 0.00 MiB, maximum pressure level 1.

Replay p95 latency qualification, representative quality precision, concurrency 2/4,
representative reasoning, deliberate cold/
idle/restart and memory-release cycles, any required selected sidecar mix and authenticated
gateway inference remain separate Phase 0 requirements. Integration/deployment remain gated.

See [design](replay-design.json), [evidence](replay-evidence.json), [reviewer](replay_validation.py) and [execution plan](../../plans/20261027_splash-replay-cache.md).
