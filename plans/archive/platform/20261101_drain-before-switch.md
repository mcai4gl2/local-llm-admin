# Drain-before-switch and configurable inference admission

Date: 2026-11-01
Status: Completed and archived; admin deployed and bounded live qualification passed.
Component: Platform / engine lifecycle
Decision: A drain timeout aborts the switch and preserves the serving engine. V1
guarantees registered shared-gateway traffic; native bypass remains explicitly unobserved.

## Problem and goal

The shared inference gateway has a fixed two-request limit and rejects new requests while
an engine operation is active. Its in-flight counter is internal to the gateway.
Engine control cannot use that counter as a coordinated barrier before stopping an engine.

Add an explicit lifecycle sequence: reserve the lifecycle operation, close inference
admission, await admitted work, execute the existing guarded transition, verify its outcome,
and reopen admission only when safe. Preserve ownership checks and mandatory target exit
before restoration.

## Pre-implementation integration points

- `inference_gateway.py`: previously fixed `MAX_INFLIGHT = 2`, busy checks and idempotent cleanup.
- `control.py`, `jobs.py`, `engines.py`: lifecycle reservation, execution and rollback.
- `backend_types.py`, `backends.py`: readiness and generation ownership.
- [Shared inference contract](../../../docs/shared-inference.md).
- [Request timeline implementation](20261101_shared-request-timeline.md): shared admission
  registry prerequisite; persistence must not participate in the barrier.

## Scope and invariants

- Introduce an atomic admission gate shared by gateway and lifecycle coordinator.
  The drain reservation and request acquisition must have no check-then-dispatch gap.
- Requests already admitted count until cleanup is complete; include requests resolving a
  backend or opening an upstream response. New requests receive a clear temporary refusal.
- Make per-backend concurrency configurable, preserving two as the compatibility default
  until a backend-specific value is qualified. Defer queues and priority scheduling.
- Expose draining state, active count, elapsed time, deadline and cancellation in Models.
  Return bounded retry guidance without implying an exact switch completion time.
- On timeout, abort before stopping the source and release the reservation safely.
  An explicit force action is separate and requires a reviewed cancellation/cleanup policy;
  it is not included in v1.
- Cancelling while waiting leaves the source intact. Cancelling after transition begins
  follows existing job and rollback semantics, not an unconditional reopening of admission.
- Never hold a synchronous lock while awaiting network I/O or stream completion.
- Reopening admission requires consistent lifecycle state. Failed/unknown restoration must
  remain visibly unavailable rather than admitting into an uncertain engine.

## Coverage constraint

Gateway connection completion does not prove that a native runtime has stopped computing.
Client disconnect may close transport before GPU work ends. Identify backend-specific
evidence required for cleanup, and refuse to label unknown work as fully drained.

Inventory admin chat, quality runners, resident work and direct native callers during design.
Use the same admission primitive for compatible internal paths where practical; retain
existing lifecycle reservations for quality jobs and resident-service policies.
V1 guarantees only its explicitly registered traffic. If native bypass traffic cannot be
observed, the UI and operating guide must state that limitation. Do not claim all-client
quiescence from a zero gateway count.

## Implementation sequence

- [x] Map every lifecycle entry point and inference caller; specify lock ordering and the
  state machine for accepting, draining, transitioning and unavailable states.
- [x] Add failing race tests: request versus drain, backend resolution versus reservation,
  last-request cleanup versus timeout, and two competing lifecycle operations.
- [x] Implement the shared barrier and configurable limits using the request registry.
- [x] Apply it to manual switches, stops/restarts and quality-run entry as appropriate;
  explicitly document recovery/watchdog behavior to avoid blocking emergency recovery.
- [x] Add cancellation and deadline handling with bounded cleanup and no silent force-stop.
- [x] Add Models progress and API refusal details, preserving existing job history.
- [x] Update operating instructions, rollout and rollback guidance.

### Integration and lock order

`JobRunner.claim` reserves lifecycle ownership before preflight. Gateway admission checks
runner busy state, then changes the `Admission` registry under its own short lock. Control
closes the registry only after preflight and before job submission; requests admitted
earlier keep leases through resolution, upstream open and close. Control awaits an empty
registry without holding either synchronous lock. The job completion hook first records
intent and ownership, then reopens admission only for a recorded, stable successful job
or verified rollback. The runner releases its slot afterward. A failed/uncertain outcome
sets `unavailable`; a later manual or watchdog recovery job can still reserve the gate.
An unavailable marker is written after the drain empties but before the job is submitted,
closing the crash window between job persistence and its completion hook. The concurrent
inference listener begins closed and opens only after startup reconciliation.

Covered entry points: Models start/stop/switch, Engine selection, quality-run entry and
watchdog `start` through `ControlService.lifecycle`. Quality keeps the gateway closed for
its full held job while its direct-native requests run. Admin chat, resident APIs,
ComfyUI and external direct-native callers bypass this registry; their activity cannot
be inferred from its count. No asynchronous request-timeline writer participates in admission.

## Acceptance and validation

Local implementation and repository gates passed. The admin was deployed and bounded live
stream-before-switch, cancel, drain-timeout and failed target-readiness rollback checks
passed. The [rollout record](../../../docs/reports/drain-before-switch-20261101.md)
preserves the initial resource-policy refusal and final live outcomes. The failed target
readiness job timed out, reported successful rollback, restored MLX and both residents,
and reopened admission. The private override was restored, and post-restore gateway and
native-idle checks passed. The final devcontainer gate passed with 3,864 tests, 4 skipped,
97.20% coverage, Ruff and strict mypy. The private-pattern scan passed. Direct native
callers remain outside gateway admission scope.

- No newly admitted request reaches an engine after its drain barrier closes.
- An admitted stream finishes normally before a planned stop in the controlled case.
- Timeout/cancellation before transition leaves source identity and intent intact.
- Disconnect, failed upstream open, malformed stream and duplicate close cannot leak or
  double-release slots. Unknown native cleanup is surfaced explicitly.
- Concurrent jobs cannot reopen one another's admission gate.
- Existing cross-engine ownership, resource-release, rollback and quality tests still pass.
- Run the repository coverage, Ruff and strict mypy gates in the devcontainer.
- A separately approved live qualification covers streaming, cancellation, long generation,
  timeout and failed target readiness. Record bypass coverage and unresolved cleanup.

## Non-goals and delivery

No automatic model selection, request replay, silent fallback, unbounded queues or broader
resident-sharing policy. Move to active when work starts and archive after completion,
updating linked plans/indexes. Deployment requires approval; append a public-safe devlog,
scan before staging and do not commit without explicit instruction.

## References

[Ollama serving controls](https://docs.ollama.com/faq) and
[llama-swap lifecycle configuration](https://github.com/mostlygeek/llama-swap)
are design references; existing engine ownership rules remain authoritative.
