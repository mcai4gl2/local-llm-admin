# Stock Splash host-memory reclamation

Fixed follow-up outcome: **inconclusive**. Overall adoption remains **inconclusive**.

Retained 8 inference attempts and 8 captures across the fixed original-source default/GPU-only pairs. All attempted windows independently restored and owned transports/helpers closed.

## Protocol and metric

The first paired window runs default then GPU-only; the second reverses the order. Only the documented process-local `--disable-ane` flag differs. Each launch has three fixed engine-free baseline samples, loaded readiness, correct first inference, automatic idle release with a 20-second timer, correct fresh resume and owned shutdown. No source/default was changed. Whole-host observations continue to 300 seconds without purge or pressure injection.

The inspected Darwin available metric is free plus inactive at its low-level interface; the returned free value excludes speculative pages. It does not count active file-backed pages as available. File-backed/anonymous and active/inactive are overlapping views, and process RSS sums include shared mappings. Native allocations, process ownership, wired memory, compression and available memory must be interpreted separately. Missing readings remain unknown.

Qualification retains baseline minus max(2 GiB, 10% baseline available), with three consecutive declared samples ending within 90 actual seconds and spanning >=4 seconds. Baseline drift >1 GiB is a confound. Extended observations cannot retroactively pass this condition. Actual timestamps and spans remain in the evidence; capture is sequential, not atomic.

| Window | Mode | Baseline available (GiB) | Available within 90s (GiB) | Gap (GiB) | Wired delta (GiB) | Active delta (GiB) | File-backed delta (GiB) | Available at 300s (GiB) | Gate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| reclaim-stock-w1 | default | 58.035 | 47.066 | 10.968 | 0.012 | 10.957 | -2.119 | 46.939 | inconclusive |
| reclaim-stock-w1 | gpu-only | 46.940 | 46.921 | 0.019 | 0.000 | 0.028 | 0.033 | 46.917 | pass |
| reclaim-stock-w2 | gpu-only | 58.391 | 46.472 | 11.919 | 0.010 | 11.917 | -0.393 | 46.438 | inconclusive |
| reclaim-stock-w2 | default | 46.442 | 47.154 | -0.712 | 0.008 | -0.714 | -1.460 | 47.147 | pass |

## Attribution and coverage

The first launch in each independent window misses the available-memory threshold: default
by 10.968 GiB and GPU-only by 11.919 GiB at the final sample within 90 seconds. Active pages
increase by 10.957/11.917 GiB, while wired memory rises only 0.012/0.010 GiB. Anonymous and
physical compressor occupancy do not show a corresponding increase. The total file-backed
pool is smaller after stopping, rather than growing by the apparent gap. These overlapping
VM views support a change in active file-backed page classification as the main explanation
for the available-memory drop; they do not identify a unique cache or allocation owner.
The gap persists at 300 seconds in both first-position launches.

Both second-position launches pass their own relative threshold, starting from the lower
available-memory baseline left by the preceding launch. Reversing mode order reproduces
this pattern. Disabling ANE therefore does not prevent the gap, and the second-position
GPU-only pass cannot be interpreted as a remedy. Both default launches report native ANE
state `split` and share 0.4705882353; both GPU-only launches report `off`, share zero and the
native `--ane off` argument. All four mode contrasts are verified.

All eight fresh requests return the expected strict JSON. Four automatic idle releases
reduce native allocated bytes by approximately 94%, followed by successful fresh resumes;
all four owned engine generations and listeners are gone after shutdown. The read-only
pressure query reports 92–93% free before and after shutdown, despite the available-memory
gap. Across the guarded resource history, pressure level never exceeds 1, there is no new
swap and minimum observed available headroom is 14.853 GiB. The pressure query percentage
and psutil available are separate measures and must not be treated as interchangeable.

Other-process raw names, IDs and generations remain private. Published RSS-growth ranks contain only matched readable generations; incomplete coverage cannot rule out inaccessible processes or kernel/driver owners. USS observations are not fabricated when the session lacks access. No engine leak or specific kernel owner is inferred merely from a lower available-memory reading.

## Preservation and validation

All 65 earlier tracked experiment files are byte-identical. Installed runtime files, 1,476 installed MLX Python files, isolated original/instrumented sources and the native bootstrap remain unchanged. Original real inference/admin/configuration/intent/assets/sidecars are independently restored after every attempted window. Owned processes/listeners/thermal observers/tunnel/agent relay are closed and the shared agent environment is restored byte-for-byte.

The fixed evidence review confirms eight inference captures, 56 whole-host snapshots, two
independent restorations, recomputed gate outcomes, valid mode contrasts and unchanged
capture helpers. Full suite: **2,784 passed, one skipped**, **98.11% coverage**. All 30 focused
parser/reviewer tests pass at **100% statement and branch coverage**; Ruff and strict mypy
pass (66 source files). Full-suite results include unrelated existing working-tree fixes.
No commit or deployment was performed.

## Qualification decision and next step

Keep the original and follow-up reclamation gates **inconclusive**. No current result is
rescored, and no runtime patch or default change is justified by this diagnostic. The
available-memory threshold is confounded by active file-backed pages and baseline carryover.
This series does not show retained wired/anonymous allocations matching the apparent gap;
restricted process coverage and absence of a pressure test prevent a general leak-free claim.

Before another qualification run, define a prospective host-reclamation contract that
separates owned-process/listener cleanup, native idle release, wired/anonymous/physical
compressor return, normal pressure and swap, and absolute operating headroom. Freeze any new
thresholds and sampling rules before fresh measurements; the existing available-memory
trajectory should remain visible as a diagnostic. This is a proposed next milestone, not a
new pass rule applied to these captures. Broader quality precision, replay p95, concurrency,
authenticated gateway inference and the required sidecar mix remain separate Phase 0 work.

See [frozen design](reclamation-design.json), [evidence](reclamation-evidence.json), [reviewer](reclamation.py) and [plan](../../plans/archive/splash/20261027_splash-stock-reclamation.md).
