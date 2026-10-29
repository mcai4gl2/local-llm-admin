Overall Progress: 75% (historical investigation; remaining client scope deferred)

# Explain Strata request refusals without exposing upstream details

## Evidence and hypotheses — 2026-10-29

After the successful shared-listener deployment, the user's Strata switch completed and
both the actual LiteLLM proxy and native health endpoint confirmed a loaded engine. A
bounded text request through LiteLLM returned an answer with normal thinking enabled.
A separate request occupied the only decode slot with a near-context-sized output
allowance and spent five minutes reasoning; a queued admin chat timed out. The long
request was cancelled at the frontend timeout, then an identical-length request started.
Request origin and retry ownership are not established.

The native log also recorded a concrete HTTP 400: this installation lacks a vision
encoder and cannot read image input. A minimal image request reproduced that refusal.
The shared frontend currently replaces its actionable reason with a generic error.
These are distinct request-level failures, not evidence that the engine failed to load.

## Minimal fix and validation

- [x] 🟩 Capture current switch/health/request evidence without restarting Strata.
- [x] 🟩 Add a regression proving the native vision refusal is recognizable to clients
  while arbitrary paths, secrets, malformed data and oversized error bodies stay private.
- [x] 🟩 Translate the recognized refusal to a fixed public-safe explanation, retaining
  HTTP status and existing error code, bounded reads and upstream cleanup.
- [x] 🟩 Run focused and required quality gates and record request-level evidence.
- [x] 🟩 Apply the verified correction with backups in an idle window. The first attempt
  deferred without changes; the later maintenance deployment succeeded.
- [ ] 🟥 Confirm the live Strata image rejection through the shared endpoint; deferred
  while preserving the user's current MLX selection.
- [ ] 🟥 Resolve the client's exact failure and image requirements; deferred to the
  explicit backlog follow-up rather than labeling the client incident resolved.

## Safety and outstanding questions

Keep the user's selected Strata engine. Do not cancel their request, download/install a
vision encoder, change reasoning policy or alter output limits without establishing the
requested client behavior. The user has been asked which client and symptom they observe.
The earlier deployment report remains evidence of its original bounded tests.


## Verification so far

The actionable-error regression failed against the deployed generic-message behavior.
All 55 focused inference tests now pass, with 100% statement and branch coverage of the
shared inference module. Oversized/malformed/unrecognized and interrupted error bodies
retain generic messages and close upstream correctly. Lint and strict type checks pass.
The full coverage gate passed. The patch application waits for an idle Strata slot
and zero queued requests; it must defer rather than interrupt active generation.


## Outcome before the latest-code deployment

The full gate passed: 3532 tests, two skips and 98.18% coverage. The final focused suite
includes one additional malformed-error variant added after full-suite collection;
all 55 cases pass with 100% shared inference statement/branch coverage. The live patch
attempt deferred because Strata had active or queued work. A final read-only snapshot
still showed the model loaded, images unsupported, active reasoning and one queued request.
No live service, installed application file, reasoning policy or vision assets changed
in this follow-up. The existing selected Strata engine remains running. The client and
image-requirement questions are pending; do not label this client incident resolved.


## Delivery outcome — 2026-10-30

The user authorized building and deploying the latest code. Revision `93e1051` matched
remote main and passed the full gate: 3,533 tests, two skips, 98.18% coverage, Ruff,
strict mypy, instruction parity and package builds. Standard host/admin deployment
completed in an idle window with previous-source and consistent database backups.
All 127 installed payload hashes match; host code drift is clear. Admin health,
streaming/non-streaming shared inference, authentication and model refusal checks pass.
The installed module's recognized vision-error translation and generic sanitization pass.
The original MLX process, weights selection, service intents and private configuration
were preserved.

The correction is deployed. No live Strata image probe was performed because the user
currently has MLX selected. The client-specific incident remains unresolved; archive this
partial investigation with its earlier deferred/failed outcomes intact. The remaining
decision and live check are tracked in the [backlog](../../backlog/platform/strata-request-followup.md).
See the [maintenance report](../../../docs/reports/latest-code-deployment-20261030.md).
