# Clean stock collector attempt — incomplete

**Decision: inconclusive.** The first window completed all 36 requests and 18 native traces.
The second window failed its SSH readiness preflight before sending any benchmark request.
Both attempts passed independent original inference/admin/configuration/intent/assets/
sidecar restoration and experiment cleanup. The remaining two windows were not attempted.
No overhead bound, native speed finding or collector eligibility follows from this attempt.

The launcher failed to pass the temporary SSH environment to its child controller; the
controller's readiness preflight attempted authentication through the wrong transport.
This is a launcher/setup failure, not a checkpoint or inference failure. All original
campaign inputs, successful records, the zero-request failure and receipts remain unchanged.
A transport-inheritance check now runs before service activation. A separately frozen
four-window follow-up uses fresh namespaces/commands/trace paths without pooling this data.

See [evidence](stock-overhead-incomplete-evidence.json) and the
[original frozen design](stock-overhead-design.json). The follow-up preserves the original
5% criterion, fixed sample count and statistical analysis. The earlier functional pilot,
modified-MLX audit, primary timings and quality/context evidence remain separate.
