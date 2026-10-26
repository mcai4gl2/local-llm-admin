# Fresh native comparison: reviewed boundary and decision scope

Date: 2026-10-26. Applies only to the separately frozen new series, never the completed
client-timing or collector-pilot series. Source and collector hashes remain those in
[native pilot evidence](native-validation-evidence.json). No installed code is changed.

Both adapters timestamp entry to delivery of explicit target token IDs before text
streaming. MLX observes a scalar engine token before detokenization; Splash observes the
native Tokens event before its Python streamer receives the batch. Final independent IDs
must exactly match callback IDs, final HTTP completion count and length reason. Every
actual native input must match the frozen tokenizer/template rendering or token-ID digest,
settings, source identity and unique request nonce. Missing or ambiguous data is ineligible.

Use (total completion IDs minus the complete first batch) divided by elapsed monotonic
time from first callback entry to last callback entry. A speculative first batch may contain
multiple target IDs. Native Done time, reconstructed predicted_ms, HTTP chunk count and
client TTFT are never used for this emission numerator or interval. Native intervals must
be positive and fit inside the corresponding full HTTP duration.

This is a common logical engine-token-delivery metric. MLX in-process delivery and Splash
native IPC/wire-reader delivery retain different queue and transport costs. The finding is
native emission throughput for the complete selected runtime paths, not isolated GPU kernel
compute or a quantified decomposition of queue latency. GPU compute remains unassessed.
The existing pinned collectors' accounting, EOS, reasoning and cancellation pilots apply;
source inventories, bootstrap, installed files and actual import receipts are reverified.

Require source/model/runtime-matched collector eligibility before reviewing a complete
three-session comparison. The clean stock-MLX overhead prerequisite is separate from the
old statistically eligible but audit-confounded stock pilot. Modified-MLX uses its completed
four-window eligible audit; Splash uses its source-matched eligible functional pilots.
Preserve their uncertainty and scope, do not pool them with native speed samples.

The reviewer recomputes rates from traces, then reuses the existing median-of-per-prompt-
paired-ratios and paired-session/prompt bootstrap. Freeze >=1.25 median, lower 95% bound >1,
no category regression, p95 TTFT <=1.10 times reference, and nonregressing median fixed-length
completion for each prompt/session. The existing 2,000-draw seed-0 bootstrap is unchanged.
Only new private projections carry recomputed native rates; original raw measurements and
all prior published reports remain unchanged. Five repetitions are paired observations,
not independent sessions or independent task diversity.

Reports keep the emission speed, client latency and completion criteria distinct. Even
when those pass, broader adoption remains inconclusive until the other Phase 0 quality,
context/operations, service-mix and authenticated-route requirements are resolved.
