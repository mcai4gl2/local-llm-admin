Overall Progress: 100%

# Splash stop failure with a dead ownership receipt

## Problem and known facts
The approved functional window passed29 HTTP attempts, but the final owning stop job
failed and retained its receipt after both recorded processes exited. The restored MLX
listener made that stale receipt appear unknown and blocked a recorded GPU service.
Read-only PID/generation/listener checks proved all Splash processes absent. The dead
receipt was preserved and atomically archived on its original volume without signals;
original MLX/services/intents, disabled env and final gateway inference then passed.

The CLI redacts its underlying exception. The exact live exception is not known.
See [hypotheses](HYPOTHESES.md), [evidence](EVIDENCE-LOG.md) and [repro](REPRO.md).

## Ranked hypotheses and confirmation
1. A recorded member exits between macOS process and executable queries, producing a
false executable mismatch that aborts stop before receipt removal. Inject this boundary
inside the real stop/signal/member flow; require a failing pre-fix test.
2. Other inspection/permission errors or malformed identity. Preserve refusal tests;
a passing exit-race regression alone does not identify the redacted live exception.

## Minimal strategy and safety
Recover only an executable mismatch followed by independently verified process absence.
Live, denied, malformed, reused or wrong-identity processes remain unknown and receive
no signal. No polling retry loop, new telemetry, runtime patch or token-path work.
The live service remains original MLX with Splash disabled. No new live window is
implied. Keep historical evidence/seals intact before editing local source.

## Tasks
- [x] 🟩 Reproduce the disappearing member inside real stop; preserve strict live/reused/denied refusals.
- [x] 🟩 Correct only the proven inspection race and run focused ownership/stop suites.
- [x] 🟩 Run full coverage, lint, types, shell/parity/public gates; publish actual limitations.
- [x] 🟩 Prepare a concrete host-only corrective/lifecycle deployment proposal before approval.

## Validation
Use uv inside the devcontainer. Focused: pytest tests/test_splash_host_failures.py
and tests/test_host_splash.py; then pytest --cov --cov-fail-under=80, Ruff src/tests/scripts,
strict mypy src/scripts and host contracts. Every source change invalidates previous
source seals; preserve their complete execution evidence rather than resealing history.

## Critique
The live cause is bounded, not proven. Injecting the ps/lsof exit boundary proves a
specific bug only; report that distinction and require later approved Mac lifecycle
validation. The fix cannot accept a live foreign/reused PID. Existing HTTP passes and
failed lifecycle outcomes remain historical facts.


## Final local result

Two deterministic regressions failed before the narrow correction; six new cases pass.
The first focused run had134 passes and one orphan-stop failure; subsequent full host
tests and60 isolated diagnostics pass. A concurrent-diagnostic run had eight quality
failures and is retained. Final isolated full run:3,350 passed/two skipped,98.14% coverage
in592.41s. Member identity gate:100% statements/branches;56 host failure tests and45
private operator checks pass. Ruff, strict project/host mypy and shell/parity checks pass.

The [host-only proposal](../../ops/splash-phase3/shutdown-fix.md) and ignored exact packet
bind one changed host file, matched schema16 backup/fallback and four lifecycle cycles
with seven scheduled/12 maximum inference attempts in45 minutes including10min restoration.
No new live action occurred. This completed plan covers local investigation, correction,
validation and concrete proposal; the redacted Mac cause and corrected lifecycle pass
remain unverified until a separate approved window.
