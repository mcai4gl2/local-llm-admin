# Splash CI lifecycle race repair

## Goal and evidence
Finish the authorized push and CI monitoring. After portable fixture interpreters restored
standalone-Python startup, CI passed 3427 tests but exposed two host lifecycle races.
Repeated isolated tests with a uv-managed interpreter reproduced both failures privately:
startup can omit a readiness-discovered native child from its durable ownership receipt;
a child born between discovery and candidate scans can be mistaken for an unrecorded orphan.
The latter correctly refuses ownership, but makes legitimate startup fail intermittently.

## Implementation
1. Persist the verified native processes from the successful readiness observation before
   returning from startup. Preserve the existing generation/executable/launch validation.
2. Capture candidate PIDs before discovering controller children. A child born after both
   relevant snapshots is discovered and validated on the next observation. Revalidate all
   recorded generations; a proven exited recorded process does not become an unknown owner.
3. Keep foreign/unrecorded orphan and reused-generation refusals. Never add retries that
   bypass identity checks or adopt an unverified orphan.

## Validation
Add deterministic regressions before the repair for late readiness persistence, child
birth between scans and a recorded child exiting after the candidate snapshot. Repeat the
two originally failing real-process tests with the standalone interpreter. Run the focused
Splash/privacy suite, full coverage gate, standalone owning-control branch coverage,
Ruff, strict mypy, instruction parity and public/history scanners. Monitor all required
GitHub workflows on the exact pushed revision through terminal results.

## Deployment and rollback
This repair changes the versioned host helper. Existing Mac deployment/128K qualification
receipts describe the previously deployed revision and stay unchanged. No live deployment
is part of the CI monitoring step. Revert this isolated helper change if its gates fail;
retain failed-run evidence and existing process-identity safeguards.
