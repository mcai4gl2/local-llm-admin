# Mac exiting-process correction and deployment

Status: correction deployed; four normal cycles and original restoration verified.
User session approval covers correction and deployment toward complete Splash support.
The [earlier correction](shutdown-fix-report.md) remains a failed lifecycle attempt with
verified restoration. Its sealed contracts and all earlier observations are preserved.

The new private trace captured the exact failure: macOS `ps` reported `?E` and `(splash)`
after its executable mapping disappeared. The second sample was still present, so the
previous absence-only executable-race fix correctly refused. Apple's
[ps source](https://github.com/apple-oss-distributions/adv_cmds/blob/main/ps/print.c)
appends `E` for `P_WEXIT`. The new Darwin-only path waits at most three seconds, probing
only signal0. Only independent ESRCH proves absence; a present, denied or reused PID
still refuses. Executable, prepared-path, generation and signal checks are unchanged.

Seven regressions failed before correction, including owned stop/receipt cleanup;64
focused tests pass. The isolated full suite passed3,358 tests/two skipped/98.14% coverage;
new wait statements/branches have100% coverage. All45 offline operator checks pass;
a missing-fixture run was corrected before any live operation and remains preserved.
Only `host/splash/lib/control.py` will differ in the new host deployment; admin code,
schema16, runtime/model/profile receipts,32K/reasoning/BF16/FP32/cache/idle settings and
original gateway remain unchanged. The full matched backup and exact source/process/
config preflight are sealed privately before apply.

The concrete verification window is45minutes with ten minutes reserved for restoration,
at most12 counted inference attempts and seven scheduled: original gateway before and
postdeploy; four complete direct streams across two stock and two modified cycles; final
restored original gateway. Each start verifies complete owned generations and excluded
GPU services. Each off must succeed as an admin job and leave no PID/listener/active
receipt/job/lock. Independent native epochs, profile attribution and idle deduplication
must pass. Failed checks abort remaining cycles and remain failed. Restore actual recorded
MLX/services/intents, disabled env and original gateway; preserve all schema16 records.
The reviewed normalized dead-receipt archive requires complete repeated absence proof,
parent lifecycle lock ownership and stable hashes; it archives on the original volume
without signals and never turns a failed lifecycle check into a pass.

Remaining native recovery/admin acceptance, frozen integrated comparison and deployment
rollout decision follow with explicit bounded operations. Successful normal cycles alone
will not close those gates or change the production default engine.


## Executed normal lifecycle verification

The matched host-only apply succeeded and its manifest differed only in control.py.
Both original gateway checks passed before qualification. Two stock and two modified
normal admin-owned start/complete-stream/off cycles then passed. Each off job succeeded;
all eight recorded PIDs are independently absent, with no active receipt/listener/job/lock.
Four independent native epochs have correct profile/fingerprint/instance attribution,
configured32K/BF16/FP32 and unchanged idle reading IDs. Six new native readings and eight
availability rows remain scoped separately from history. Final schema16 integrity is
`ok`, with32 readings/51 availability observations. Seven total inference attempts passed
in about489seconds. Original explicit MLX, actual services/intents, disabled env and
original gateway inference/routing are restored and verified. No emergency cleanup,
rollback, runtime/model rebuild or gateway mutation was needed in this successful window.
See [structured results](macos-exit-fix-results.json).

This closes the normal shutdown defect's live regression gate. Remaining full engine-pair
matrix, native recovery/admin workflows, frozen integrated comparison and final support
rollout decision stay open. Earlier failed stop/archive windows remain failed and retained.
