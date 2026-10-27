# Splash integration fixtures

Reuse ../splash-phase0/native-probes.json for recorded, scrubbed 1.4.0 readiness,
context and model-list projections. Its original reasoning-off, 96-token provenance
remains unchanged. No new model responses or gateway captures were recorded.

Private profile tests generate temporary config/assets/receipts; their byte content
is synthetic and never represents a usable checkpoint. Synthetic ownership/state
fixtures test refusals and protocol handling, not measured memory or performance.
