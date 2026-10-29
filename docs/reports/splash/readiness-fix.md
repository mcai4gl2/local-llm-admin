# Splash readiness correction and controlled rerun

Status: **prepared; new approval required before live apply**. The
[previous attempt](stage-b-report.md) was aborted and restored. The new packet binds
the corrected source and a fresh original-service baseline; it preserves the previous
approval, request budget and evidence as immutable history.

## Correction

Three admin files and three host files change. A shared stdlib validator reads the
pinned runtime's precision from `identity.kv.format` and the valid memory plan's
`model.kv_format` / `model.state_format`. The wrapper, status monitor and quality
readiness use the same contract. Allocation counters and planner context capacity
are separate from the configured 32K serving limit. Missing/conflicting precision
remains unknown; ownership, aliases and generation checks are preserved.

The shared lifecycle guard also defers TERM/INT during the verified owner-unlink /
directory-removal sequence. It then releases this process's remaining reservations
and re-raises the signal. Eight deterministic before/after-unlink tests cover both
maintenance and lifecycle locks with both signals; foreign ownership is still refused.
This resolves a reproduced gate-cleanup race found during regression validation.

## Proposed window

The [machine-readable scope](readiness-fix.json) permits one combined 45-minute
window including a final ten-minute restoration reserve, cap 40 inference attempts,
concurrency one and 120-second request deadlines. Thirty attempts are scheduled;
ten remain for reviewed retry/restoration. This replaces the unsuccessful packet
for the new attempt; its unused budget is not reused.

After fresh before-source/process/intent/env/gateway checks and original-route inference,
stop the admin writer and make a matched schema16/app/environment/LaunchAgent/whole-host
backup. Apply the paired host/admin deployment with Splash disabled. Verify exact source
hashes, schema16, health, original MLX generation and authenticated gateway inference.
No runtime/assets are installed again and no schema migration is introduced.

Then run the original two-profile functional sequence under the new source manifests:
streaming/reasoning/content/usage, near-limit direct/gateway context, auth/model/Host
refusals, cancellation with runtime evidence and follow-up, owned off/restart/profile
switch, scoped telemetry and bundle metrics. Pinned runtime source confirms that alias requests report the configured primary
response model when served-name announcement is disabled. The capture client checks
that primary identity for both direct and gateway streams; request aliases remain
explicit in the reviewed route bodies. The two offline context inputs still
render to 31,873 prompt tokens and allow at most 512 output tokens. Request guard and
stop criteria retain their original limits. Export new-window availability separately
from the six retained observations in the aborted attempt.

## Restoration

Normal cleanup stops Splash through its owner, proves all members/listeners absent,
restores original MLX/services/intents and the disabled environment, removes only the
new temporary routes/key and verifies original authenticated gateway inference. A new
MLX process generation is expected after the functional transitions.

For corrective deployment failure, the ignored packet supplies exact matched-code
resume/rollback commands. They refuse unknown/live Splash ownership or external code
changes, stop the admin writer and preserve the post-attempt database before restoring
the prior app/interpreter/env/LaunchAgent/whole host unit. Both revisions use schema16;
normal fallback retains the database and its observations. No WAL companions are deleted
and no schema15 database is restored.

The executable `.private/splash-phase3/readiness-fix/REVIEW.md` resolves every target,
backup, route/auth-file and command path. Its manifests bind the review and source.
Live deployment and renewed serving require approval of that new packet. The frozen
comparison and production/default decision remain separate.
