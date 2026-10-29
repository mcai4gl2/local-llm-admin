# Expanded collector overhead attempt — incomplete

The fixed four-window attempt stopped during its first original-MLX launch after two
excluded warmups. Both HTTP requests completed normally at the 256-token cap, with zero
cache reuse. The reviewer accepted the single-turn short case but incorrectly rejected
the frozen 2K case because it includes user/assistant conversation history.

**Decision: inconclusive.** There are no measured pairs or instrumented traces, so no
collector-overhead bounds or eligibility can be established. The remaining windows were
not run. All captured records and the original frozen campaign remain unchanged.

The original service passed real inference, admin, configuration/intent, asset and sidecar
restoration checks. All 57 installed runtime files and all isolated source copies remained
unchanged; the final source audit reviewed 25 launch receipts, including this original launch.
See [machine-readable evidence](expanded-overhead-evidence.json).

A regression test reproduced the validator error before correction. A separately frozen
[follow-up design](mlx-overhead-followup-design.json) retains the same four-window schedule,
5% criterion, deadlines and analysis; it uses fresh nonces, command names and output paths.
The earlier pilot, primary timings and quality results remain separate. Native cross-engine
speed and the broader quality margin remain unassessed by this overhead work.
