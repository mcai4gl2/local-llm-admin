# Splash functional window with endpoint-specific identity

Date: 2026-10-28. Outcome: **both-profile HTTP checks pass; lifecycle failed; original service restored**.
See the [numeric results](gateway-contract-results.json). The user approved the sealed
functional-only packet. No host/admin source, runtime, model asset, schema or original
gateway route was changed during the window. Previous failed attempts remain intact.

## Results and limits

All29 scheduled inference attempts ran in approximately13 minutes18 seconds, within the
45-minute/40-attempt bound. Concurrency stayed one; request deadlines were120 seconds.
Each profile passed13 cases; stock restart added one direct request, and original gateway
checks before/after added two. Eleven unused request slots were not carried forward.

| Check | Stock | Modified |
|---|---|---|
| Direct and actual authenticated gateway complete streams | Pass | Pass |
| Direct primary / exact gateway public response identity | Pass | Pass |
| Reasoning, content, normal finish, EOF and final usage | Pass | Pass |
| Direct and gateway near-limit retrieval | Both pass | Both pass |
| Invalid auth, unknown model, invalid Host refusals | Pass | Pass |
| Direct/gateway disconnect propagation and follow-up | Both pass | Both pass |

Every near-limit request reported exactly31,873 prompt tokens and a correct synthetic
retrieval answer, with132 completion tokens for stock and117/120 for modified, below the
512 cap. Refusals were direct auth401/model404/Host403, gateway auth401/model403.
Restricted-key unknown-model403 proves gateway policy refusal, separately from native
alias refusal. No arbitrary response-model name was accepted.

All four cancellations disconnected after nonempty emission. Separate native observations
showed a one-count cancelled increment, no active request and unchanged complete owned
generation within0.80–0.92 seconds; subsequent requests passed. Raw client cancellation
records remain inconclusive by design; the separate runtime evidence establishes these
passes. No final token usage was invented for disconnected requests.

Stock off/restart and the stock-to-modified transition passed. Stock restart preserved its
fingerprint, changed process generation and began a new telemetry epoch. Eighteen new
cumulative native readings span three epochs (two stock, one modified); all match captured
profile fingerprints. Idle stock reading IDs remained unchanged over two poll intervals.
Twelve new availability observations are separated from the ten historical observations.
Calendar API responses retain broad date scope; exact-window exports remain separate.
The database retains21 native readings and22 availability observations, schema16,
integrity`ok`. Cumulative counters are never summed into request/gateway usage.

Owned bundle RSS/primed CPU were observed; summed RSS is not physical memory reclamation.
GPU services were excluded during Splash serving. These functional requests are not the
frozen performance comparison, reset/recovery qualification or an overhead measurement.

## Failures retained and restoration

Activation initially stopped on an inherited deployment-backup marker dependency. The
admin was restored with Splash disabled; independent schema16 backup integrity and saved
env/plist digests were verified. The same approved temporary opt-in was then completed
from the sealed environment without changing code, resetting the budget or retrying
inference. This operator dependency remains recorded.

The final owning stop returned failure after both recorded Splash processes exited and
left its ownership receipt. MLX restoration succeeded, but the stale receipt classified
its known listener as unknown and blocked the recorded embedding service from starting.
The lifecycle result remains failed; later cleanup does not erase it.

Read-only checks proved both recorded PIDs absent, no rediscovered Splash processes, no
running job, released locks and the sole listener owned by the restored healthy original
MLX generation/executable. An initial cross-volume rename was refused without changing
the receipt. After repeating those checks, the receipt was copied to private evidence and
atomically archived on its original volume with matching digests. No process was signalled
by this metadata cleanup. Original service running states/intents were then restored,
the saved disabled environment reloaded, temporary routes/key removed, source/config hashes
and health verified, and final original authenticated gateway inference passed. The
original route projection is unchanged. No database snapshot or WAL/journal was removed.
The temporary SSH relay is closed.

## Local follow-up and remaining gates

The redacted live CLI does not identify its inner stop exception. A local deterministic
reproduction shows that exit between macOS process and executable checks aborts stop and
leaks its dead receipt. A narrow correction requires an independent absence observation
before tolerating that mismatch. Live/reused/denied identities still refuse; escaped
runtime paths still refuse. That proven race matches the observed symptom, but exact
live attribution and a corrected Mac lifecycle rerun remain unverified. Final isolated
local validation passes3,350 tests/two skipped/98.14% coverage; the identity gate has100%
statement/branch coverage. A concurrent-diagnostic full-run failure is retained and was
superseded by that clean run. The [host-only proposal](shutdown-fix.md) and45 private
operator checks prepare the next reviewed step. See the
[debug plan](../../plans/20261028_splash-shutdown-race.md).

HTTP compatibility is qualified for the explicit temporary gateway routes. Original
production routing, automatic adoption, forced native recovery/PID-reuse injection,
admin live compatibility/eval acceptance and the frozen integrated comparison remain
separate work. MLX remains active with Splash disabled. No overall lifecycle/production
success is claimed.
