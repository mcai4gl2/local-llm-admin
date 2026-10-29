Overall Progress: 100%

# Near-limit 32K validation and precise MLX overhead preparation

Date: 2026-10-26
Status: User requested continuation after the collector pilot and confirmed 32K is enough
for current context testing. Existing reports remain frozen; integration stays gated.

## Scope and integration

Reuse the pinned source profiles, isolated experiment services, existing HTTP measurement
parser and independent host guard. No production source, registry, default, dependency or
sidecar mix changes. Work in the existing branch and devcontainer. Add synthetic retrieval
inputs and strict artifact review under `experiments/splash/`, with tests before implementation.

Near-limit inputs contain six distinct documents: two seeds at needle positions 10%, 50%
and 90%. Rendered prompt tokens must lie in [31,744, 32,000], leaving a 512-token completion
cap within 32,768. Exact JSON retrieval is required; silently truncated, reused, mismatched,
missing, duplicated and malformed responses cannot pass. Offline rendering must reproduce
actual saved MLX/Splash native inputs before calibrating/fixing new cases.

One short 64-token excluded warmup plus six long requests per engine gives 14 requests per
checkpoint. Each has a 300-second deadline, with ten minutes setup and ten minutes restore:
90-minute worst-case budget. Separate 110-minute collection / 120-minute restoration guards
per checkpoint retain existing pressure, swap, headroom and generation controls. All GPU
sidecars remain stopped and original state is independently verified afterward.

This is fixed synthetic context correctness/resource qualification, not a primary speed or
representative quality verdict. Per-request TTFT/completion are diagnostic. Wrong answers
remain recorded; HTTP/accounting/context failures stop capture for restoration. No retries
replace failures or loosen token bands. Keep one-off CPU/disk audits between timing phases.

## Tasks

- [x] 🟩 Write failing deterministic-input, context-band, paired-accounting and grading tests.
- [x] 🟩 Implement bounded synthetic input generation and strict context artifact review.
- [x] 🟩 Verify actual template/tokenizer equivalence; calibrate and freeze paired inputs.
- [x] 🟩 Pass focused/full tests, lint and typing; refresh original service/assets/ownership.
- [x] 🟩 Run stock 8-bit and modified 4-bit in separate guarded windows; retain all outcomes
  and independently verify original inference, assets, configuration/intent and sidecars.
- [x] 🟩 Publish context results and prepare a separately frozen, better-powered MLX overhead
  design, retaining the original 5% overall/per-category criterion and prior inconclusive result.

## Overhead design constraints

The prior modified MLX 8K upper bound was 1.0764. More independent launch pairs, balanced
order and declared per-category warmups are needed before another precision claim. Determine
a bounded fixed sample count and cluster interval before data; do not repeatedly extend
until eligible. Source/EOS inspection runs before/after phases, with stable resource observer
load. Actual execution requires complete reviewed inputs and a worst-case bounded schedule.

## Validation

Use `uv run pytest` with meaningful synthetic artifacts and actual tokenizer receipts.
Run full application coverage >=80%, Ruff, strict mypy, instruction parity, whitespace,
unchanged completed-evidence checks and the configured private scanner. Append devlog.
No commits; staging remains blocked by the existing label-pattern collision.

## Fixed expanded overhead design

The [design artifact](../../../experiments/splash/mlx-overhead-design.json) fixes four independent
modified-MLX ABBA windows: 48 measured pairs, 16 per prompt category, with a declared
256-token excluded warmup for each category in every launch. Each 36-request window
budgets 58 minutes worst case including setup and restoration, inside its own existing
110/120-minute independent bounds. The bootstrap clusters whole windows before launch
pairs, categories and repetitions. All data and every category remain required; the
original 5% criterion is unchanged. More replication does not guarantee eligibility.

The design is frozen, but private request/command ledgers and a tested expanded reviewer
are still required. It is not runnable and no new overhead measurements are claimed. The
prior four-phase comparator cannot analyze this larger clustered schedule.

## Completed context outcome

The [context report](../../../experiments/splash/context-report.md) records both complete pairs:
all 24 distinct long-context requests answered correctly and matched exact frozen prompt
counts, with no HTTP errors, truncation or reused input. All four declared short warmups
completed. Both independently bounded windows restored original inference, admin, preserved
state/configuration, target assets and sidecars. This passes the fixed synthetic near-limit
context smoke; native speed, broad quality, replay/concurrency and operations remain open.

The fixed expanded design now has a tested reviewer and complete private tokenized request/
command ledgers. Its separate [execution plan](20261026_splash-mlx-overhead-audit.md) tracks
the four independently restored windows; no pilot data is pooled into it.
