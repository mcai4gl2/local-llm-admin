# Shared inference request timeline

Date: 2026-11-01
Status: Proposed; implementation not started.
Component: Platform / inference observability
Next decision: Confirm the metadata-only v1 scope and retention policy; implement before
drain-before-switch so both features can share admission and request identity primitives.

## Problem and goal

Request evidence currently comes from MLX logs, Strata metrics, Splash native snapshots
and LiteLLM accounting. The shared inference gateway captures the selected backend when
dispatching but does not persist a common request timeline. Operators cannot consistently
explain a slow, rejected, interrupted or failed gateway request across engines.

Provide one metadata-only timeline for traffic traversing the shared inference gateway,
with explicit coverage and no duplicate billed usage. Direct native traffic, direct admin
chat and quality traffic are outside this ledger unless explicitly instrumented later.
An absent ledger row must never imply that the native engine had no work.

## Existing integration points

- `src/local_llm_admin/inference_gateway.py`: admission, backend resolution, streaming,
  response translation and disconnect cleanup; current in-flight state is closure-local.
- `backend_types.py`, `backends.py`: captured inference descriptors and verified ownership.
- `performance.py`, `performance_store.py`, `service_store.py`, `storage.py`: existing
  query, metadata storage and additive migration patterns to inspect before implementation.
- [Shared inference contract](../../../docs/shared-inference.md) and
  [architecture](../../../docs/DESIGN.md): preserve source boundaries and missing-data semantics.

## V1 scope

- Generate an internal request ID and return a correlation ID where the response permits.
  Treat incoming IDs as untrusted correlation hints, never authoritative identity.
- Capture requested public model, resolved backend/model/profile fingerprint and verified
  generation once. Later engine changes must not relabel an earlier request.
- Record admission/dispatch/first upstream event/end times and distinguish rejected,
  completed, upstream error, timeout, disconnected and interrupted outcomes.
- Separate time to headers, time to first event and measured first content token; reasoning,
  empty chunks and tool-call deltas must not silently become text-token measurements.
- Retain response usage only when supplied, with source and completeness labels. This is
  diagnostic usage, not another billing total; do not merge it into existing token rollups.
- Add paginated timeline and detail APIs/UI with range, backend, outcome and request-ID
  filters. Show coverage start and the gateway-only population explicitly.
- Persist bounded records asynchronously, never per token. Bound pending writes and expose
  dropped/failed persistence counts. Observability failure must not stall inference.
- No prompts, completions, image/document bodies, credentials, arbitrary upstream errors,
  raw headers, real client addresses or caller-supplied identifiers in default storage.
- Prefer a 90-day configurable retention default consistent with existing observability;
  prune in bounded batches. Final retention choice is part of scope approval.

## Implementation sequence

- [ ] Inspect gateway tests, migrations, runtime identity and performance UI patterns.
- [ ] Define typed lifecycle events, terminal outcomes, timing semantics and coverage.
- [ ] Write failing tests for completion, rejection, timeout, malformed stream, disconnect,
  backend changes and duplicate cleanup before implementing instrumentation.
- [ ] Introduce a minimal shared admission/request registry suitable for a later drain
  barrier, without adding switching policy or queueing in this change.
- [ ] Add an additive metadata store, bounded writer, startup reconciliation for unfinished
  rows, retention and explicit persistence-health counters.
- [ ] Instrument both streaming and non-streaming paths with idempotent finalization.
  Restart leftovers become interrupted/unknown, never inferred successes.
- [ ] Add paginated APIs and UI that preserve existing source-specific performance views.
- [ ] Document coverage, limits, schema rollback and operating checks.

## Acceptance and validation

- Terminal paths release admission exactly once and produce at most one terminal record.
- Captured identity remains stable across a switch or configuration change.
- Missing usage and unsupported token timings remain null; an error carrying usage remains
  an error. Rejections before backend resolution do not acquire an invented backend.
- Slow/full/failed storage remains bounded and visible; inference continues.
- Cursor paging is stable under concurrent inserts; retention preserves active records.
- Fixture tests establish privacy and streaming correctness. A separate, approved live
  experiment compares tracing off/on for identical workloads, including concurrent and
  cancelled requests; report TTFT and tail latency without claiming unmeasured overhead.
- Run repository quality gates: `uv run pytest --cov --cov-fail-under=80`,
  `uv run ruff check src tests scripts`, `uv run mypy src scripts`, in the devcontainer.

## Dependencies and boundaries

The [drain plan](20261101_drain-before-switch.md) consumes the shared registry, not the
asynchronous database writer. Billing reconciliation, native all-client capture,
OpenTelemetry export and prompt capture are deferred. LiteLLM remains the accounting source.

## Delivery and plan lifecycle

Move this file to active when implementation begins. Keep one evolving plan and record
failed/partial measurements. After local completion, archive it and update indexes.
Live Mac deployment requires separate approval. Append a public-safe devlog, run the
private leak scanner before staging, and do not commit without explicit instruction.

## Reference

[Langfuse API and OpenTelemetry ingestion](https://langfuse.com/docs/api-and-data-platform/features/public-api)
provides an interoperability reference; no external tracing service is required for v1.
