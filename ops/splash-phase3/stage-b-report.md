# Splash Phase 3 — Stage B attempt

Date: 2026-10-28. Outcome: **qualification aborted; original service restored**.

The user approved the sealed functional packet. Fresh source/schema16/process/intent
and gateway preflights passed. The pre-window SQLite backup has schema16 and integrity
`ok`; environment, LaunchAgent and service-state backups are retained. Temporary opt-in
and two isolated gateway routes plus one restricted key were created.

Stock startup exposed a readiness-parser bug before any Splash qualification inference.
Pinned1.4.0 reported ready with the expected controller/model, aliases and32K serving
limit. The wrapper/admin checked nonexistent `kv.format` and top-level `state_format`.
The captured configuration instead uses `identity.kv.format` and
`memory_plan.model.{kv_format,state_format}`. Top-level `kv` and `state` hold allocation
counters; the memory planner's context capacity differs from the serving limit.

The controller stopped qualification, removed the temporary gateway key/routes and waited
for the bounded owning startup job. That job timed out and cleaned up its owned resources;
MLX was restored. No process-name or port-based kill was used. The disabled environment,
original explicit MLX selection, service running states and semantic intents were verified.
The original gateway route identity/routing parameters match the baseline. Authenticated
original-route inference passes before and after. The temporary SSH relay is closed.

## Results

| Check | Result |
|---|---|
| Fresh baseline and schema16 online backup | Pass |
| Original authenticated gateway route before/after | Both pass |
| Temporary route/key creation and owned removal | Pass |
| Stock loaded-readiness verification | Fail; parser field mismatch, owning job timeout |
| Splash qualification inference attempts | Zero |
| Modified-profile serving and all direct/gateway/32K/cancellation checks | Not run |
| Owned Splash processes/listener after cleanup | Absent |
| Original MLX/services/intents and disabled environment | Restored |
| Native readings | Zero; no counter-population qualification |
| Splash availability records | Six observations retained; no availability pass claimed |
| Database after cleanup | Schema16 retained; no snapshot restore |

Two attempts were used from the40-request budget, both original-route checks capped at
32 output tokens. The attempt closed in approximately seven minutes15 seconds, within
the45-minute window. Failures and private job/status/backup/restore evidence are retained.
The approved source packet is immutable; a subsequent source correction requires a new
reviewed deployment/qualification packet. The live deployment remains the Stage A code
with Splash disabled. No performance comparison or production decision occurred.

## Repository correction

The captured allowlisted status projection now drives wrapper/admin/inference tests.
A shared stdlib precision validator checks runtime identity against a valid memory plan
and the pinned profile; both owning and admin paths use the same byte-identical contract.
Missing, malformed, conflicting or legacy-only metadata still refuses readiness.
Process identity, aliases,32K serving limits and generation checks retain their meaning.
Regression validation and a corrective deployment proposal are recorded in the plan and
devlog; local test success is separate from a successful live rerun.


The local correction also resolves a deterministically reproduced signal re-entry
race during lifecycle reservation release. Final validation passes 3,344 tests
(two skipped), 98.14% coverage and eight injected TERM/INT cleanup cases. The shared
precision validator has 100% statement/branch coverage. The
[corrective packet](readiness-fix.md) awaits approval; the live source is unchanged.
