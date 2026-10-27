Overall Progress: 100%

# Stock Splash host-memory reclamation

Date: 2026-10-27
Status: Fixed diagnostic follow-up complete; attribution supports active file-backed accounting. Qualification remains inconclusive.

## Problem and known facts

The earlier stock 8-bit lifecycle window stopped all owned engine processes/listeners, but
host available memory was 51.106 GiB against a 59.805 GiB baseline and 53.825 GiB threshold
at 88.448 seconds. The original evidence remains frozen. It lacks a comparable paused-host
VM/process breakdown, so the missing headroom cannot be attributed retrospectively.

The pinned Darwin psutil implementation reports available as its raw free plus inactive;
its public free value excludes speculative pages. Available therefore excludes active
file-backed pages and is not an engine-ownership counter. Splash reports Neural Engine
prefill offload enabled by default. The pinned CLI supports process-local `--disable-ane`.

## Ranked hypotheses and distinguishing observations

1. File-backed cache/active-page classification changes the available metric after model
   mapping/loading/stopping. Measure free/speculative/inactive/active/file-backed pages;
   overlapping VM categories must never be added as independent allocations.
2. Native/driver memory remains wired after Neural Engine use. Compare unchanged default
   and GPU-only launches, native allocation/release counters, wired pages and other-process
   USS. Consistent differences are an association, not proof of a specific kernel owner.
3. Other host processes, compression or ordinary delayed recovery cause the gap. Record
   process generations/RSS/USS with unavailable readings explicit, compressor and swap/page
   counters, a fixed engine-free baseline observation, and post-stop samples through 300 s.

## Frozen diagnostic protocol

Two independently guarded/restored paired windows, stock 8-bit only. First order: default
then GPU-only; second reverses it. Both use original isolated runtime sources, 32K context,
BF16 KV/FP32 recurrent state, 40 GiB Metal guideline, disk cache disabled, concurrency one,
reasoning off, temperature/seed zero and GPU sidecars stopped. The only variant difference
is the documented `--disable-ane` flag. No installed source/default/selection change.

Per launch, capture three engine-free whole-host baseline samples at 0/10/30 s; the final
sample is the predeclared primary baseline, with all three retained to expose drift. Launch
and verify loaded readiness/native ownership and effective mode. Send one short, strict JSON
inference (128-token cap, 90 s deadline, zero reuse), observe automatic idle release with the
isolated 20 s timer, send one fresh matched resume request, verify restore count and loaded
state, then stop. Capture whole-host snapshots before load, loaded, released, resumed and
at fixed post-stop offsets 2/10/30/60/75/88/180/300 s. Record actual start/end timestamps and
observation spans; sampling is sequential and not atomic. No cache purge, pressure injection,
reboot, privileged mutation or hidden retry. Four launches, eight inference requests, four
automatic idle/release/resume sequences, four owned stops; no inference warmups.

Keep the earlier tolerance max(2 GiB, 10% baseline available). A fresh scoped 90 s recovery
pass requires three consecutive declared post-stop observations with actual memory timestamps no later than
90 s, spanning >=4 s, all above threshold; source/ownership/inference/release/stop
proofs are independently required. Missing/incomplete observations are inconclusive; failed
functional checks fail their scoped outcome. 180/300 s recovery is diagnostic and cannot
retroactively pass the 90 s check. Baseline drift >1 GiB is a confound; host recovery stays
inconclusive in that case. Nonreproduction does not erase the earlier inconclusive window.

Capture psutil VM and `vm_stat`, read-only pressure query and pressure level, cumulative
swap/page counters, owned RSS/USS, and generation-keyed host process RSS/USS. Missing USS
permission is unknown, not zero. Raw identifiers/names/commands remain private. Publish only
allowlisted VM bytes/deltas, anonymized aggregate growth and coverage counts. Process RSS
sums include shared mappings and cannot be summed with VM or GPU counters as disjoint memory.
File-backed/anonymous pages overlap active/inactive; compressor stored and occupied pages
have different meanings. Freeze parsers and arithmetic before capture, preserve raw outputs.

## Budgets and supervision

Per launch budget 840 s: baseline 30 + startup 120 + two inference deadlines 180 + idle 90 +
stop 60 + observation 300 + diagnostics overhead 60. Per paired window 1,680 + 540 setup +
600 restoration = 2,820 s, within independent 110-minute collection/120-minute restoration
bounds. Reserve remaining worst-case launch budgets and restoration before each phase and
inference. Use generation-safe supervisor, pressure/swap/6 GiB headroom aborts, fresh original
real-inference/admin/jobs/configuration/intent/assets/sidecar and source/transport preflight.
Restore and independently verify original service and owned cleanup after every attempted
window, including failure; stop campaign on transport/accounting/resource/ownership failure.

## Implementation and validation

Use the existing devcontainer/uv environment and branch; repository dated-plan and direct
user continuation override generic debug-skill branch/directory/checkpoint conventions.
No dependencies, worktrees, commits or deployment. Do not patch logic before attribution.

- [x] 🟩 Add failing VM parser, accounting, missing-data, 90 s/extended recovery and safe
  publication tests, including duplicate/malformed counters and overlapping categories.
- [x] 🟩 Implement minimal standard-library VM parsing and fail-closed diagnostic review.
- [x] 🟩 Freeze source/command/helper pins and public design; validate focused/full tests,
  Ruff, strict mypy, instruction parity, whitespace and public safety before capture.
- [x] 🟩 Run both fixed paired windows with independent restoration and raw host/native
  evidence; retain failures, confounds and nonreproduction without optional extension.
- [x] 🟩 Publish measured trajectories and attribution limits, verify source preservation
  and owned cleanup, update campaign/parent plans, append devlog, scan and stage intended files.

Run `uv run pytest --cov --cov-fail-under=80`, `uv run ruff check src tests scripts`, and
strict `uv run mypy src scripts` plus standalone tooling checks. Target parser/review critical
paths at 100% coverage. Full-suite results include preexisting unrelated local fixes, which
remain untouched. Earlier experiment publications are byte-pinned before capture.

## Terminal outcome

Both fixed paired windows completed: four launches, eight correct fresh requests, four
idle releases/resumes and 56 whole-host snapshots. The first launch in each window misses
the 90-second relative available-memory threshold in both modes; second-position launches
pass from a lower inherited baseline. First-position gaps of 10.968/11.919 GiB track active
page increases of 10.957/11.917 GiB; wired deltas are only 0.012/0.010 GiB, without a matching
anonymous or physical compressor increase. Pressure-query free remains 92–93% before/after
shutdown. Disabling ANE is not a remedy. The measured pattern supports active file-backed
page accounting; inaccessible process readings prevent unique-owner or general leak-free
claims. No purge or pressure test was performed.

All original-service restorations and owned cleanup proofs pass; installed sources and all
65 earlier tracked experiment files are unchanged. Pressure level remains 1, new swap zero,
minimum observed available headroom 14.853 GiB. The original and follow-up qualification
gates remain inconclusive. Propose a separately frozen prospective reclamation contract
before fresh qualification; do not retroactively rescore these observations.

Validation: 2,784 passed, one skipped, 98.11% application coverage; 30 focused parser/reviewer
tests at 100% statement/branch coverage; Ruff and strict mypy pass (66 source files). Final
public-safety and publication checks accompany staging. No commit or deployment performed.
See the [report](../ops/splash-phase0/reclamation-report.md) and
[evidence](../ops/splash-phase0/reclamation-evidence.json).
