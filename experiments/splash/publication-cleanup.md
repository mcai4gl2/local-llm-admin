# Public label cleanup — 2026-10-27

Further live Phase 0 qualification is deferred at the user's request. The completed
comparison remains available in [the native report](native-comparison-report.md).

The derivative checkpoint uses the neutral public label `modified-4bit`. Earlier public
labels and three filenames were normalized for the configured private denylist. This is a
publication cleanup: measured numbers, decisions, confidence intervals, source identities,
input schedules, thresholds and the raw private measurements have not changed.

[publication-redactions.json](publication-redactions.json) records the before/after byte
hashes for each redacted public file and canonical JSON hashes where applicable. Original
public snapshots are retained privately outside version control. Existing hashes inside
historical reports/evidence continue to identify the original frozen source, code, comparison
or private run; they are not relabeled as hashes of the cleaned public projections. New
quality preparations use the neutral CLI label and the current grader-source digest.

The callback collectors and fresh native comparison reviewer were unchanged. Their pinned
source and runtime hashes still match. No benchmark was rerun and no adoption gate was
changed. Historical stage reports remain historical; overall adoption is inconclusive.

## Commit validation

The user approved a label-only redaction of the historical daily log; its original bytes
and cleaned hash are included in the provenance record. The private denylist is unchanged.

- Full suite: 2,630 passed, one skipped; 98.10% application coverage.
- Focused label/preparation/protocol/continuation suite: 234 passed, including two cases
  that failed before neutral-label support was applied.
- Ruff and strict mypy pass; shell syntax, JSON validity, instruction parity and Git
  whitespace checks pass.
- Pinned pre-commit-hooks 4.6.0 formatting, JSON/YAML, merge-conflict and size checks pass.
- The private denylist scan passes across tracked and all nonignored untracked files.
- GitHub connections time out from the devcontainer. The normal remote hook bootstrap
  could not complete, so Gitleaks has not run. Hook revisions and secret checks remain
  enabled and unchanged; rerun the standard hooks when connectivity is available.

The Splash set is staged without a commit. Existing devcontainer/private-denylist setup,
log-rotation fix and download-test edits are kept unstaged for separate review/commits.
The full-suite result describes the current workspace, including those pre-existing edits.
