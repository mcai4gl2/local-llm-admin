# Splash Phase 0 reasoning and lifecycle

Fixed series: reasoning **fail**; supported lifecycle protocol **fail**.
Overall adoption remains **inconclusive**. MLX automatic idle release is **unsupported/unqualified**; manual unload/resume is reported separately.

Retained 92 attempts and 92 captures: 64 measured reasoning replies, eight excluded warmups
and 20 lifecycle requests. All attempted windows independently restored; owned helpers and
transports are closed.

Reasoning **on** passes 32/32 strict checks, including all eight cases on every engine/checkpoint.
Reasoning **off** passes 5/32. The overall fixed reasoning gate fails because it requires both
modes to pass. Off-mode failures include wrong values and format violations; these counts do
not imply every failed reply has an incorrect numerical answer. The modified off-mode runs
have five non-JSON replies and three wrong JSON values per engine. No looser post-hoc scoring
replaces the frozen one-key JSON requirement.

Settings: 32,768-token effective context, BF16 KV/FP32 recurrent state, concurrency one,
temperature/seed zero, GPU sidecars stopped, original isolated runtime sources, disk cache
disabled and a 40 GiB Metal guideline (not an OS RSS cap). Across all windows, no new swap
was observed, pressure stayed at level 1, and minimum available headroom was 14.571 GiB.

## Reasoning

Eight independently checked cases per mode/backend/cohort, paired reasoning off/on inputs, 2,048-token caps and 240-second deadlines. One excluded short reasoning-off warmup per engine/window. All failures remain in the frozen series. Latencies below are client observations; eight cases do not support p95 or native-speed claims.

| Checkpoint | Mode | Engine | Strict passes | Gate | First event median (s) | First answer median (s) | Total median (s) | Reasoning-count coverage |
|---|---|---|---:|---|---:|---:|---:|---:|
| stock-8bit | off | mlx | 2/8 | fail | 0.676 | 0.676 | 1.771 | 0/8 |
| stock-8bit | off | splash | 3/8 | fail | 0.376 | 0.376 | 0.689 | 8/8 |
| stock-8bit | on | splash | 8/8 | pass | 0.602 | 4.536 | 4.635 | 8/8 |
| stock-8bit | on | mlx | 8/8 | pass | 0.845 | 16.909 | 17.862 | 0/8 |
| modified-4bit | off | mlx | 0/8 | fail | 0.552 | 0.552 | 8.492 | 0/8 |
| modified-4bit | off | splash | 0/8 | fail | 0.352 | 0.352 | 2.448 | 8/8 |
| modified-4bit | on | splash | 8/8 | pass | 0.560 | 3.105 | 3.221 | 8/8 |
| modified-4bit | on | mlx | 8/8 | pass | 0.684 | 9.662 | 10.158 | 0/8 |

Correctness requires strict one-key JSON, the exact answer, natural stop and the requested reasoning mode. Captures require zero reuse and valid usage/timestamps. Explicit reasoning counts remain unknown when the runtime does not emit them; no counts are inferred from text or SSE events. Truncation without an answer is a failure with missing first-answer latency.

## Lifecycle

Each completed backend/checkpoint sequence has one initial model-process cold launch plus three fresh-generation restarts, successful first inference and verified owned shutdown after every launch. Time to ready includes remote control and polling overhead from client launch initiation; it is not isolated model load time.

Splash uses automatic idle release after 20 seconds and checks released/not-restoring state, at least 50% reduction in runtime allocated bytes, a successful next request and an increased restore count. MLX observes 25 seconds of idle residency, then uses its isolated manual unload endpoint and request-triggered reload; at least 50% reduction in owned RSS is required. These memory measures have different meanings and are not compared as equivalent GPU allocations.

MLX release times in the table are observation timeouts, not successful physical release
latencies or the duration of the unload API call.

Host reclamation uses the original-service-paused baseline, tolerance max(2 GiB, 10% baseline), and three qualifying observations spanning at least four seconds within 90 seconds of stop. Shared-host interference yields inconclusive reclamation evidence.

| Checkpoint | Engine | Gate | Ready times, initial + 3 restarts (s) | First inference times (s) | Release observation / timeout (s) | Resume inference (s) | Released memory fraction |
|---|---|---|---|---|---:|---:|---:|
| stock-8bit | mlx | fail | 13.025, 4.533, 4.981, 4.875 | 1.189, 1.121, 1.112, 1.181 | 90.46731358500256 | 2.074900854000589 | 0.0006530253276230136 |
| stock-8bit | splash | inconclusive | 15.519, 16.222, 18.750, 18.331 | 0.355, 0.350, 0.365, 0.344 | 21.296050807999563 | 6.354004342996632 | 0.9393083455859843 |
| modified-4bit | splash | pass | 12.699, 13.757, 14.226, 12.189 | 0.299, 0.282, 0.281, 0.316 | 21.103260281000985 | 3.1911343290121295 | 0.8956337773642511 |
| modified-4bit | mlx | fail | 4.216, 4.077, 4.531, 3.633 | 0.712, 0.704, 0.701, 0.697 | 90.49532612400071 | 1.4594213670061436 | 0.0011613128317591315 |

## Lifecycle outcome details

All 16 initial/restart launches reached loaded readiness and completed first inference;
all 16 shutdowns removed owned generations and listeners. Three restart cycles completed
for each engine/checkpoint. These operational checks pass independently of idle memory release.

- **Splash automatic idle release passes on both checkpoints.** Stock runtime allocated
  memory fell from 30.535 GiB to 1.853 GiB (93.93%); modified fell from 18.038 GiB to
  1.883 GiB (89.56%). Both kept the same owned process generations and incremented the
  restore count on successful resume. Resume requests took 6.354 s and 3.191 s respectively.
- **MLX manual-unload memory release fails on both checkpoints.** The endpoint reported
  successful unload and metrics showed no loaded model, but owned RSS remained 27.811 GiB
  from 27.829 GiB for stock, and 15.289 GiB from 15.307 GiB for modified. The observed drops
  were only 0.065% and 0.116% after approximately 90 seconds, below the frozen 50% criterion.
  Request-triggered reload/inference succeeded. This is an observed RSS retention result;
  it does not identify the retaining reference or establish a GPU allocation leak. Whole-process
  shutdown recovered host memory within the frozen tolerance on all MLX cycles.
- **Stock Splash host reclamation is inconclusive.** After its first stop, all owned
  generations and the listener were gone, but host available memory was 51.106 GiB at
  88.448 s against a 59.805 GiB baseline and 53.825 GiB qualifying threshold. Shared-host
  memory observations do not identify the cause. The other three stock stop cycles met
  their local baseline tolerance; this does not erase the first outcome.
- **Modified Splash passes the full supported lifecycle protocol**, including all four
  stop/reclamation observations. No OS-cold start or unsupported MLX automatic idle pass is claimed.

## Restoration, preservation and remaining gates

All 6 attempted windows have independent original inference/admin/configuration/intent/assets/sidecar restoration proofs. The previous private agent environment is restored byte-for-byte, the host agent is untouched, and owned tunnels/observers/agent relay are closed. All 57 prior publication files remain byte-identical. Installed runtime/source and launch/import checks pass; no installed source or production default changed.

Remaining adoption work includes representative quality precision, replay p95 latency, concurrency 2/4, authenticated gateway inference and any required selected sidecar mix. Unsupported automatic MLX idle release remains explicit. No integration/deployment pass is implied.

See [frozen design](reasoning-lifecycle-design.json), [evidence](reasoning-lifecycle-evidence.json) and [plan](../../plans/20261027_splash-reasoning-and-lifecycle.md).

## Repository validation

Final full suite: **2,745 passed, one skipped**, 98.10% application coverage. All 60 focused
reviewer tests pass with 100% statement/branch coverage. Ruff and strict mypy pass (64 source
files), as do pinned formatting/JSON/conflict/size checks, instruction parity and whitespace
checks. Public scan is clean. Existing unrelated local setup/maintenance edits were preserved;
the full suite includes those working-tree fixes. Remote pre-commit bootstrap/Gitleaks were
not rerun; their earlier network limitation is not reported as a pass. Changes are staged;
no commit or deployment was performed.
