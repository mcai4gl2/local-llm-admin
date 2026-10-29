Overall Progress: 100%

# Splash shutdown ownership race

Local investigation and corrective proposal after the approved functional window.
Follow the [debug plan](20261028_splash-shutdown-race/DEBUG-PLAN.md). HTTP qualification
passed; lifecycle remains failed until a separately approved correction and Mac rerun.


Local scope complete: two pre-fix failures; six new regressions;3,350 passed/two skipped,
98.14% coverage; identity gate100% statement/branch coverage;45 private operator checks.
The [host-only correction and lifecycle proposal](../ops/splash-phase3/shutdown-fix.md)
was deployed under its separate approval. Exact redacted Mac attribution remains
unproven and the lifecycle rerun failed at its second stock off. The clean full suite supersedes a preserved concurrent-diagnostic failure.


The approved host-only deployment was subsequently executed. First stock cycle passed;
second stock off failed and retained a dead receipt. Exact live exception remains
unproven; the local fix is insufficient to claim Mac lifecycle qualification. See the
[execution report](../ops/splash-phase3/shutdown-fix-report.md). This plan's100% remains
limited to its original local investigation/proposal scope. A separate timestamp-padding
archive correction has10 passing safety tests and executed under separate approval.
Original MLX/services/intents, disabled Splash, schema16 health/integrity, gateway
inference/routing and receipt/member/listener/lock cleanup are verified. The lifecycle
failure is preserved; no qualification resumed.
