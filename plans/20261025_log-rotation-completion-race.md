Overall Progress: 100%

# Preserve log-rotation submission and completion state

## Problem and evidence

A full Splash qualification-tooling regression run failed two existing log-maintenance
scheduler tests: daily rotation submitted twice, and a deferred retry did not run.
There were 2,271 passes, one skip, and 98.09% application coverage in that run.
The scheduler saves submission metadata after awaiting control submission. A worker
completion hook independently reads and writes the same persisted state. A fast worker
can finish before metadata is saved, or its older read can overwrite a newer submission.

## Hypotheses and reproduction

Leading hypothesis: non-atomic state merging loses the day/attempt timestamp or completion
outcome/result. A deterministic regression will invoke completion before submission returns,
for success, deferral and failure. Existing ordinary worker tests cover completion afterward.
The job runner intentionally holds its slot through hooks, but that does not order the
scheduler's own write after control submission. No evidence suggests a timezone/date issue.

Run in the devcontainer:

```bash
uv run pytest tests/test_log_maintenance.py -q
```

## Minimal fix and critique

Serialize the scheduler and completion hook's read/modify/write operations with one instance
lock. Never hold it across an await, subprocess, job submission or alert update. When submission
metadata arrives after completion, merge the same job's final outcome/result/finish time.
Preserve the configured clock, daily/retry policy, alerts, intent, job slot and wrappers.
This affects low-frequency maintenance metadata, not inference. Do not deploy during this work.

- [x] 🟩 Add deterministic failing success/deferred/failure ordering regressions.
- [x] 🟩 Implement locked state merging for submission, completion and refusal.
- [x] 🟩 Run maintenance/job/control regressions and the full quality gates.
- [x] 🟩 Append public-safe results to the devlog; keep live deployment separate.

## Validation and rollback

Check daily deduplication, deferred retries, alerts and preservation of attempt/completion
metadata in both callback orders. Run Ruff, strict mypy, instruction parity and public-safety
scans. Revert only this focused change if validation fails; do not hide failures with longer
sleeps or relaxed scheduler assertions. Keep all live benchmark approval bounds unchanged.

## Results

Three deterministic callback-order tests failed before the fix. Afterward, 115 focused
maintenance/job/control tests pass at 100% log-rotation statement/branch coverage. The final
full suite passes: 2,276 tests, one skip, 98.11% application coverage. Ruff and strict mypy
pass. Public-safe results are appended to the daily devlog. No live deployment was performed.
The configured full private denylist is unavailable; changes remain unstaged/uncommitted.
