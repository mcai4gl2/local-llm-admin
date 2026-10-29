# Splash shutdown ownership correction and lifecycle rerun

Status: **prepared for separate approval; not deployed**. The
[preceding window](gateway-contract-report.md) passed both-profile HTTP qualification,
but its final stop failed and left a dead receipt. MLX/services are restored and Splash
is disabled. Exact live exception attribution remains unproven because the CLI redacted
its inner error. No previous failure or frozen result is rescored.

## Correction and validation

Only `host/splash/lib/control.py` changes on the target. If a member's executable lookup
fails to match after a process sample, query that PID again. Continue only if it is
independently proven absent; live, reused, denied and malformed identities still refuse.
Runtime path escapes still refuse before this check. No process signal, readiness,
precision, alias, cache, context or inference-path policy is loosened.

Two deterministic tests failed before the fix: a disappearing member and the real
stop/signal/member flow retaining its receipt. Six new tests now cover proven absence,
live/denied/reused refusal, receipt release without signalling the dead child, and path
escape refusal. The first focused run also had an orphan-stop failure; subsequent full
host tests and60 isolated diagnostics passed. A full run contaminated by a concurrent
diagnostic caused quality failures and is retained; final validation must run alone.
Final isolated validation: **3,350 passed, two skipped,98.14% coverage** in592.41 seconds.
The member identity gate has100% statement/branch coverage;45 private operator checks,
Ruff and strict mypy pass. Exact source/operator hashes are sealed in the ignored
`.private/splash-phase3/shutdown-fix/REVIEW.md` before any live approval is acted on.

## Proposed independent window

One45-minute window, including a final ten-minute restoration reserve. At most12 inference
attempts, seven scheduled, concurrency one and120-second request deadlines. Original
route checks cap output at32; four direct profile checks cap at2,048. The native runtime,
profile receipts,32K serving limit, reasoning-on/BF16/FP32 and zero-cache/idle-off policies
remain unchanged. This rerun creates no gateway routes or keys. Resource/ownership guards
and all refusal/abort rules retain their existing limits.

After a fresh pinned source/process/env/intent/service/gateway baseline and original
inference, stop only the admin writer and make the matched schema16/app/interpreter/env/
LaunchAgent/whole-host backup. Apply `ops/deploy-host.sh --apply` through deploy-env; its
full source manifest must differ in this one file. Bootstrap the unchanged admin and
verify schema16/disabled health, original MLX generation and gateway inference. No admin
code deployment, schema migration, runtime or model install is included.

Make an independent online qualification backup, enable only the sealed temporary
opt-in and run two stock and two modified start/direct-stream/owned-off cycles. Each
start must verify complete macOS executable/create-time/controller/native/listener
ownership, expected profile fingerprint and excluded GPU services. Each off must be a
successful owning job with no member/listener, released locks and no active job.
Inspect fresh independent epochs and retain deduplication/availability/bundle evidence.
The four cycles target normal shutdown; they cannot prove every possible race or replace
forced-failure/recovery/PID-reuse qualification.

## Restoration, fallback and completion boundary

On pass or abort, prove complete absence, restore original explicit MLX and recorded
service running states/intents, reload the disabled environment and verify original
authenticated gateway inference and unchanged route projection. Keep schema16 observations.
A failed stop remains failed. Unknown/live ownership permits no metadata removal or kill.
The concrete packet preserves the proven-dead receipt archive procedure: repeated no-job,
all-recorded-PIDs-absent, no-rediscovered-Splash and known-only-listener checks; matching
receipt digests; verified private copy and atomic archive on the original volume; zero
signals. This emergency cleanup does not turn a failed lifecycle check into a pass.

If apply fails, resume before mutation or use the matched schema16 code fallback only
after owned resources/jobs settle and the saved disabled environment is restored. Preserve
the post-attempt database; restore no older schema or WAL/journal files. All helper-only
uploads, target paths, commands, backup/fallback and credentials are resolved and sealed
privately. No source pin may change during execution and no window repeats implicitly.

This packet neither changes production defaults nor runs the frozen comparison, forced
recovery injection or admin live compatibility/eval acceptance. Those remaining gates
need separate concrete work after successful lifecycle validation.
