# Splash support deployed — 2026-10-29

Both qualified profiles are enabled/selectable in the admin, with dedicated authenticated,
database-backed gateway routes and a new key scoped only to those routes. **MLX remains
the selected default.** Existing original routing, key scopes, model selection, actual
services/sidecars and semantic intents are preserved. Choose the matching Splash profile
before using its route; one full GPU engine/profile runs at a time. Actual connection
parameters and the mode600 client key remain in the ignored commissioning packet.

## Delivered and verified

The paired endpoint correction preserves the8080 compatibility default while allowing a
private dedicated Splash endpoint. New ownership records bind their serving port; old
records retain8080 semantics. Probes/cleanup follow recorded ownership, admin configuration
mismatches refuse readiness, and the new port continues to exclude every other GPU engine.
The pinned MLX server clears its cache before attempting another requested model, so
persistent Splash aliases must not reach the selected MLX listener. Endpoint isolation
avoids that displacement without an inference proxy or installed runtime patch.

- Four normal lifecycle cycles and all twelve directed engine-pair switches passed on
  the deployed source. Every owning job succeeded; prior members and listeners cleared.
- Both profiles passed admin chat, seven supported compatibility cases, current-profile
  eval/history/reported usage, held-run cancellation and source logs. Vision is unsupported.
- Each profile recovered one verified native child under the lifecycle lock, retained
  its controller/instance, invalidated readiness, incremented restart count and emitted
  a successful followup. Two correctly attributed epochs, current member bundles and
  idle deduplication passed. Normal owning off removed all pre/post-recovery members.
- Final direct and actual gateway streams passed for both profiles, including reasoning,
  content, model identity, final usage and four exact31873-token context/retrieval checks.
- Both gateway disconnects propagated: the native cancellation count increased once,
  no work remained, the same generation survived, and followups passed. Raw emitted-only
  capture records remain partial; separate runtime assessments supply propagation proof.
- After MLX restoration, both inactive Splash routes returned500 instead of reaching MLX.
  Its exact generation/selection/readiness stayed unchanged. The new key received403 for
  the original model. Three original authenticated gateway checks and unchanged routing
  projection passed. Both new routes report database persistence; key expiry is null.

The final commissioning window used18of20 HTTP slots,45minutes maximum/final10minutes for
restoration. No retries, cap increase or source changes occurred inside it. An earlier
read-only preflight refused an obsolete deployment PID before any traffic/window or live
mutation; that packet remains preserved. A new packet binds the current restored generation.

## Final state and evidence

Splash is enabled, original MLX is healthy/selected, actual services/intents match their
baseline, no native receipt/member/listener or pending job remains, and both lifecycle
locks are clear. Schema16 integrity is `ok`:85 native readings and109 backend availability
rows are retained. No SQLite main-file restoration, WAL deletion, model installation or
inference-runtime modification occurred. Whole app/venv/Python/host/env/plist/native-config
and consistent schema16 backups remain available, with exact private manifests.

Final isolated repository gate: **3408 passed,2skipped,98.14% coverage**. Standalone host
control and app ownership/profile contracts have100% statement/branch coverage. Ruff,
strict mypy58app/script plus2host files, parity, diff and23-pattern public checks pass.
The previously recorded34 real Chromium DOM checks cover the unchanged UI. The first
endpoint full run's three calendar-aged fixture failures remain recorded; test-copy dates
were corrected and all26performance API tests passed before the final full run.

See [structured rollout evidence](rollout-results.json), [endpoint plan](../../plans/20261029_splash-endpoint-isolation.md),
[telemetry contract](../../docs/splash-telemetry.md), and the unchanged [partial comparison](integrated-comparison-report.md).
Native execution counters remain separate from billed/daily request usage. Cancelled
streams can lack usage; no all-client ledger, final-stop counter, every-interleaving
recovery or physical memory-reclamation guarantee is claimed. Reasoning-off quality,
broader Phase0 precision and statistical preferred-backend adoption remain unqualified.
The final dedicated endpoint has functional qualification, not a new performance benchmark.

## Operating decision, monitoring and rollback

Deploy supported selectable functionality under the user's explicit session approval;
retain MLX default and decline preferred-backend/performance adoption. The frozen benchmark
is still partial/inconclusive; no old result is rescored and no missing condition is filled
from commissioning traffic. Monitor profile/fingerprint/generation, ready/recovering state,
telemetry freshness/gaps, memory/headroom/swap, job outcomes and owned member/listener cleanup.
Recovery of the native child belongs to the pinned runtime; the admin adds no watchdog.
Unknown ownership, source/config drift, overlapping full engines, pressure, counter/identity
mismatch or failed cleanup stops further transitions and requires preserved evidence.

Routine rollback keeps schema16/history. Through the reviewed private runner, stop only
known Splash ownership, restore/verify original MLX/services/intents, remove only recorded
new route IDs/key with `gateway-remove`, then apply the saved disabled environment using
`disable` and `verify`. Confirm original authenticated inference/routing and no owned
resources/jobs/locks. Exact target-derived paths and credential references are private.
To undo the paired source/endpoint correction, use its matched `deployment/deploy.sh rollback`
after proving native absence; it preserves post-attempt DB data and restores matched prior
source/native/admin configuration. Schema15 rollback remains a different matched bundle
operation described in the [operating guide](README.md); never copy a live SQLite main file.
