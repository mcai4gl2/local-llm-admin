# Resource-fit previews and qualified operating profiles

Date: 2026-11-01
Status: Proposed; implementation not started.
Component: Platform / resource admission and resident services
Next decision: Confirm advisory previews as v1 and choose one representative engine,
context, concurrency and resident-service combination for bounded qualification.

## Problem and goal

Existing resident-service admission accounts for owners, reservations and pending restores.
Operators still need a clear explanation of what a proposed model/context/service
combination requires, what can run together, and which estimates have measured support.

Add a read-only resource-fit preview and explicit qualification records. Preserve current
conservative admission rules; a favorable estimate must not authorize an unqualified
sharing combination or override unknown ownership and abnormal memory pressure.

## Existing integration points

- `sidecars.py`, `resident_manifest.py`, `control.py`: resident descriptors,
  reservation accounting, compatibility and restore sequencing.
- `metrics.py`, `backend_types.py`, `backends.py`: process identity and memory evidence.
- `profiles.py`, `splash_profiles.py`, quality/eval stores: profile identities and
  workload evidence to inspect for reuse.
- [Architecture](../../../docs/DESIGN.md).
- [Resident sharing follow-up](20261031_resident-sharing-followup.md): broader OCR sharing
  remains unqualified and long-running embedding allocator drift remains unresolved.
- [Candidate qualification](20261101_regression-cases-and-candidate-qualification.md):
  potential producer of scoped evidence, not an admission-policy override.

## V1 scope

- Preview proposed engine/model/profile, total context, concurrency and resident services.
- Explain active owners, reservations, pending restores, proposed additions, exclusions,
  headroom policy, evidence freshness and refusal reasons.
- Reuse the actual admission calculation as the source of policy truth. Avoid a second
  UI-only calculator that can disagree with a real start decision.
- Clearly distinguish measured process footprint/RSS, configured reservations, estimates,
  system memory pressure and unavailable values. Apple unified memory figures are not
  independent GPU and CPU pools and must not be summed blindly.
- Show predicted headroom only where inputs support it, with assumptions and uncertainty.
  Display "unknown" rather than manufacturing a fit score.
- Bind qualified records to hardware class, runtime and model/profile fingerprints,
  context, concurrency, resident versions, workload, duration and measurement method.
- Label records as qualified within bounds, unqualified, stale, failed or inconclusive.
  A cold-start peak does not establish long-running stability.
- Provide named operating-profile proposals referencing existing identities and evidence.
  Initially preview-only; do not introduce automatic switching or a parallel source of
  engine/service intent.

## Implementation sequence

- [ ] Trace current accounting and qualification artifacts; define one reusable pure
  preview result with explicit evidence provenance and freshness.
- [ ] Write failing tests for shared-owner deduplication, pending restores, disabled-but-live
  owners, unknown identity, abnormal pressure, stale snapshots and mutually exclusive engines.
- [ ] Expose a read-only preview API using existing admission policy and bounded observations.
- [ ] Add preview UI to Models and resident-service actions, including reasons and links to
  relevant qualification evidence.
- [ ] Define local qualified-profile metadata and invalidation rules without storing private
  machine identities or deployment facts in tracked files.
- [ ] Design a bounded measurement protocol before running it: idle baseline, cold/warm
  phases, representative maximum inputs, concurrent work, cancellation and sustained load.
- [ ] Following separate approval for live work, collect one combination's evidence and
  preserve raw/private data separately from sanitized reports.
- [ ] Document interpretation, limitations, stale evidence and manual operating workflow.

## Acceptance and validation

- Preview and actual admission agree for identical captured inputs; real starts revalidate
  current state rather than trusting an earlier preview.
- All active owners and pending restores count once; unknowns cannot become zero.
- Context/concurrency/runtime changes invalidate or narrow qualification visibly.
- Estimates never expand the OCR sharing allow-list or authorize additional residency.
- Memory measures name their scope and cannot be confused with additive system totals.
- Failed, partial and inconclusive experiments remain intact, including existing drift.
- Define numerical pass criteria before live measurements: normal pressure, explicit
  headroom, bounded latency degradation, cleanup and sustained-memory behavior.
  Unmeasured windows remain unqualified.
- Run repository coverage, Ruff and strict mypy gates in the devcontainer.

## Dependencies and deferred work

Advisory previews can proceed independently of the request timeline and drain barrier.
A later one-action profile application would depend on guarded lifecycle orchestration
and should be scoped separately. Multi-host placement, automatic packing, speculative
overcommit and automatic resident restarts are out of scope.

The resident-sharing follow-up owns the existing drift investigation. Link its eventual
evidence instead of duplicating or prematurely closing that work.

## Delivery and plan lifecycle

Move to active when implementation begins; archive after completion and update indexes.
Live measurements and service changes require the appropriate separate authorization.
Append a public-safe devlog, run the private leak scanner before staging, and do not commit
without explicit instruction.

## Reference

[GPUStack resource-fit scheduling](https://docs.gpustack.ai/2.0/scheduler/) motivates
explainable resource compatibility. This proposal adapts the idea to one Apple-silicon
host and does not assume GPUStack's estimates qualify MLX or resident-service sharing.
