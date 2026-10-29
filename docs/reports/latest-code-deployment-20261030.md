# Latest-code maintenance deployment — 2026-10-30

## Scope and installation

Built and deployed revision `93e1051`, which matched remote main after fetching.
The pending application change was the shared inference gateway's explanation for a
recognized native refusal when vision support is absent. The other application payload
contents already matched. Updated three host operating guides and installed the missing
native-port example; every deployed host executable already matched.

Used the standard host and admin deployment scripts with the configured private target.
An immediate idle preflight found no inference, lifecycle, quality or download work.
Retained the standard previous application backup and created an integrity-checked
SQLite backup. Backup receipts, configuration fingerprints and raw logs remain private.

## Validation

- Frozen dependency sync, source distribution and wheel build passed.
- Full suite: 3,533 passed, two skipped; 98.18% coverage. The shared inference gateway
  has 100% statement and branch coverage.
- Ruff, strict mypy and agent-instruction parity passed.
- All 98 application and 29 host payload hashes match the revision.
- Host drift check passed; the model registry remains separately managed state.
- Admin health and authenticated model listing passed. Bounded streaming and
  non-streaming requests through the shared endpoint returned HTTP 200 with answers.
- Missing credentials, foreign-model refusal and isolation of admin APIs passed.
- The installed gateway module correctly translated a representative vision refusal
  and retained generic sanitization for unrecognized upstream errors.
- The same native MLX process, selected weights, engine/sidecar intents and six private
  configuration fingerprints were preserved. Final database integrity and idle checks passed.

## Remaining investigation

The user-selected MLX engine remained running throughout this maintenance deployment.
The installed-module error check is not a live Strata image request. A live Strata vision
refusal check and the earlier client's exact failure/image requirements remain deferred
in the [follow-up](../../plans/backlog/platform/strata-request-followup.md).
The [earlier incident record](shared-inference-20261029.md) retains its original outcomes;
this maintenance does not add engine-switching or performance qualification.
