# Admin website consistency deployment — 2026-10-30

## Scope and installation

Committed the complete admin consistency implementation as `0302459` and deployed its
application payload with the standard admin deployment script to the configured private
target. This delivers all 28 findings in the
[completed platform plan](../../plans/archive/platform/20261030_admin-web-consistency-and-usability.md).
The user explicitly authorized the commit and live deployment.

An initial SSH preflight could not authenticate from the devcontainer. No Mac writes
occurred on that attempt. Temporarily forwarded the existing host SSH agent into the
container without copying keys; all project/deployment commands ran as the container's
regular user. Removed the temporary forwarding after verification.

A successful preflight found no inference, lifecycle, quality or download work and verified
known MLX ownership. Created an integrity-checked online SQLite backup. The deployment kept
the standard previous application copy, synchronized the source, installed frozen production
dependencies and reloaded the admin LaunchAgent. Health verification passed. No host wrapper,
engine profile, model registration or engine selection change was applied.

## Validation

- Implementation gate: 3,557 Python tests passed, two skipped; 98.17% coverage. The isolated
  Chromium suite passed 114 checks. Ruff, strict mypy, instruction parity, Markdown links,
  whitespace and private public-safety scans passed. Node was absent; no Node pass is claimed.
- All 98 installed application payload hashes match the implementation commit.
- Admin health and 23 authenticated read-only page/API checks passed, covering the main
  pages, enabled test-service pages and availability contracts, source-scoped Performance,
  exact availability ranges/timezone metadata, bounded native history, resolved Chat
  defaults and the authenticated shared model listing.
- The new Strata availability collector has fresh observations. MLX remained selected;
  these observations do not qualify serving Strata or backfill historical availability.
- The native MLX PID and creation time, selected model, saved engine/sidecar/service intents
  and six private configuration fingerprints were preserved.
- Final database integrity is `ok`, schema version remains 16, and the service is idle.

Raw receipts, backup location, configuration fingerprints and deployment logs remain
private. No new inference generation, engine switching or qualification campaign ran.
Documentation records added after deployment do not alter the installed application payload.
