Overall Progress: 100%

# Isolate Splash gateway requests from the selected MLX runtime

## Summary and context
The shared serving port is unsafe for persistent distinct model routes. The pinned MLX
server clears its text-generation cache before loading a different requested model.
A Splash alias sent while MLX is selected can therefore displace the original model.
Installed source inspection established this ordering; no destructive live request was sent.
Linux devcontainer pytest exercises the stdlib Mac host helper and asynchronous FastAPI
admin. This is a networking/configuration correctness fix with operational risk. Existing
session approval covers implementation and deployment; no new approval checkpoint is needed.

## Scope and constraints
Add an optional private Splash port, retaining the existing 8080 default. New ownership
records retain their serving port; old records retain 8080 semantics. Readiness and cleanup
use the recorded endpoint, and admin observation refuses a configured/recorded mismatch.
Start continues to exclude MLX's 8080 and every existing GPU service even on another port.
Deploy Splash on a verified unused dedicated port, with matching private admin/gateway URLs.
Keep MLX default and preserve all earlier bounded measurements and failures. No new proxy,
runtime patch, database schema change, inference-path work or benchmark retries.

## Tasks
- [x] 🟩 Add failing tests for strict port/env/record validation, old-record compatibility,
  custom-port start/probes/cleanup, MLX exclusion and admin mismatch refusal.
- [x] 🟩 Implement the minimal shared host/admin contract and lifecycle port binding.
- [x] 🟩 Run focused tests, isolated full coverage, Ruff/mypy, parity and public scan.
- [x] 🟩 Publish a new source-pinned deployment/backup packet and deploy the paired change.
- [x] 🟩 Verify both profiles, actual gateway streams/inactive-route refusal, native recovery,
  engine exclusion, telemetry and owned cleanup; retain original MLX/services/intents.
- [x] 🟩 Publish final functional support status, scoped performance limits and rollback.

## Validation and performance
Run `uv run pytest tests/test_splash.py tests/test_host_splash.py
 tests/test_splash_host_failures.py tests/test_splash_backend.py -q`, then isolated
`uv run pytest --cov --cov-fail-under=80`, `uv run ruff check src tests scripts`,
`uv run mypy src scripts`, standalone host checks and instruction/contract parity.
No serving/process diagnostic runs alongside the full suite. Port parsing and record
validation are control-plane operations; inference still streams directly to the runtime.
No added token copies, DB writes or HTTP probes enter the inference path. Do not repeat the
frozen stock/modified comparison; publish its partial results and unassessed conditions.

## Observability and integration
Shared profile/env/ownership contract; host start/observe/readiness/stop; admin ownership
observation; private native/admin/gateway endpoint settings; operating documentation.
Use complete PID/create-time/executable/receipt/listener and model-identity evidence.
An inactive Splash route must fail while the original MLX generation and inference survive.

## Rollback and critique
Before changing configuration prove Splash absent, no queued/running jobs, both locks clear,
and the new port unused. Preserve current app/host/env/plist/native config and online schema16
backup. A failure removes only recorded gateway resources and restores the matched disabled
configuration/source bundle. Never restore a live SQLite main file or erase observations.
Do not infer identity from an empty port. Old receipts must remain stoppable on their recorded
port, regardless of changed configuration. Keep gateway key creation/cleanup receipts private;
the temporary comparison key rejection remains a failed setup, with no gateway samples.

## Open questions
None. The separate endpoint resolves the observed cache-displacement hazard without changing
installed inference runtime sources or the original gateway's model identity.

## Implementation checkpoint
The new regressions first failed (18 failures plus three start/guard failures). Shared
contracts now accept strict optional ports and record the endpoint. Probe/stop use the
recorded port; start checks both its configured port and legacy engine/GPU ports; admin
observation rejects mismatches. The custom real-socket harness test passes after correcting
its MLX test target setup. The broader focused run had401 passes before that test-only
fixture correction. Ruff and strict types pass. Full isolated validation follows.

## Validation checkpoint
Standalone host validation passes321tests; control has100% statement/branch coverage.
All new port-contract paths are covered, and both app ownership/profile modules have100%
statement/branch coverage in the first full run. That run preserved3failures/3405passes
caused by September fixture dates aging outside the performance API's default30day view.
The fixture copy now shifts both recorded days together; all26performance API tests pass.
Production filtering and historical fixture bytes remain unchanged. Five private operator
checks pass after first exposing ambiguous lsof absence and a queued-job installation gap.
Final isolated full coverage is running. Neither comparison is retried or rescored; see
[partial report](../../../docs/reports/splash/integrated-comparison-report.md).

## Final local gate
Final isolated suite:3408passed,2skipped,98.14% coverage in574.23seconds.
Ruff, strict mypy58app/script and2host files, source/instruction parity, diff checks
and the23-pattern public scan pass. The original failing full run remains preserved.
App ownership/profile contracts are100%; standalone host control is100% statements/branches.
No production source changed after this gate. The paired packet is sealed and ready for
existing session deployment approval; live endpoint/routing verification is still pending.


## Completed deployment and commissioning

Paired source and dedicated native configuration deployed with matched whole backup and
unchanged original MLX generation. Four normal cycles, twelve directed pairs and both
native/admin recovery windows pass. Final18of20commissioning attempts qualify via16complete/
expected-refusal records plus2emitted-only cancellation captures with separate runtime
propagation/idle assessments. Four31873-token context checks pass. Both inactive routes
fail500 while the exact original MLX generation stays healthy; the new scoped key refuses
the original model403. Routes are database-backed, support is enabled and MLX default,
original actual services/intents preserved, schema16/integrityok/85readings/109availability,
no native receipt/member/listener/jobs/locks. An initial read-only final preflight refused
a stale deployment PID; its unstarted packet is retained and revision2 binds the fresh
restored generation, with no old budget/result mutation. See the
[rollout evidence](../../../docs/reports/splash/rollout-report.md). Functionality is complete;
performance adoption remains explicitly partial/inconclusive in the unchanged parent scope.
