# Splash integration fixtures

Reuse ../splash-phase0/native-probes.json for recorded, scrubbed 1.4.0 readiness,
context and model-list projections. Its original reasoning-off, 96-token provenance
remains unchanged. No new model responses or gateway captures were recorded.

Private profile tests generate temporary config/assets/receipts; their byte content
is synthetic and never represents a usable checkpoint. Synthetic ownership/state
fixtures test refusals and protocol handling, not measured memory or performance.

## Phase 3 telemetry evidence

`telemetry-captures.json` contains only numeric request/execution counter and
transport freshness/restart projections from the retained Phase 0 session-3
stock-8bit and modified-4bit initial idle status captures (zero counters; not
workload or reset evidence). Each projection includes the original
status byte digest. It deliberately omits device, model-path, instance, client and
request metadata. These are retained observations, not new integrated measurements.

The normalizer tests also reuse both existing final SSE usage captures; their
reasoning-off, 96-token provenance remains unchanged. Malformed/missing documents,
partial/error/cancelled outcomes and uncollected/disabled coverage are synthetic
tests, not recordings. No unavailable-endpoint or recovery/reset capture is claimed.
The [versioned telemetry contract](../../../docs/splash-telemetry.md) records the
field matrix, source audit digests and pending live capture worksheet.


## Stage B readiness projection

`readiness-capture.json` is an allowlisted projection of the unsuccessful Stage B stock
startup. It retains actual pinned1.4.0 schema/configuration paths and a digest of the
canonical parsed status. Instance/model/process/path metadata are omitted; tests insert
synthetic identities. KV/state allocation values are omitted, retaining empty object
containers. Planner capacity is distinct from the configured32K serving limit.
This capture corrects synthetic fixtures that incorrectly put precision on allocation
counters; it proves no inference, gateway, native-reset or availability qualification.
