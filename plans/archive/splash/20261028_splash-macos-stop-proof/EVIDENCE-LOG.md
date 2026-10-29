# Evidence

Initial baseline: separately approved restoration passed; original MLX/services/intents
and gateway restored, Splash disabled, schema16 integrityok,25 readings/41 availability
observations. Earlier failed second stock stop, archive refusal and dead receipt evidence
are preserved privately. Current local source and live hashes must be reverified.


## Captured Mac cause and failing regressions

The first traced native stop failed at `_member` during `stop` membership polling.
Actual Mac ps returned `?E` with `(splash)` command/argv twice, while lsof returned no
text mapping. Independent read-only inspection then proved both recorded PIDs absent,
no candidate/listener/job and released locks. Preserved same-volume dead receipt archive
and original MLX/services/disabled-env/gateway restoration passed; no diagnostic inference
was sent to Splash. Two original-route checks were counted in this separate window.

Apple's [ps implementation](https://github.com/apple-oss-distributions/adv_cmds/blob/main/ps/print.c)
appends E for P_WEXIT before zombie state. This confirms the captured exit flag, not a
live-executable identity. Seven regressions failed first, including real owned-stop
membership. The fix recognizes only Darwin's E flag and waits at most60x50ms for signal0
probes to independently prove ESRCH. Still-exiting, permission-denied or reused present
PIDs refuse; the flag alone authorizes neither absence nor a signal. No executable or
launch identity checks change. All64 focused tests pass; exact host coverage remains
pending. Full isolated suite is running without concurrent process/listener diagnostics.


## Normal owning-job regression passed

Isolated full source validation:3358passed/2skipped/98.14%,616.10seconds; new exit wait
statement/branch coverage100%;64host failure and45operator tests passed. One host file
deployed after matched backup. All four normal stock/modified cycles passed, including
all owning off jobs, all8recorded PIDs absent, no receipt/listener/job/lock and independent
attribution/idle deduplication. Seven requests passed in489seconds;6new readings/8availability
rows leave final32/51. Original MLX/services/intents/disabled env/gateway restored. Full
engine-pair matrix/native recovery/admin acceptance and frozen comparison remain required.
