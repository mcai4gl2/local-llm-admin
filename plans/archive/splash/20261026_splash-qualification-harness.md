Overall Progress: 100%

# Splash qualification harness

Date: 2026-10-26
Status: Offline implementation, private worksheets and validation complete.
Staging is blocked by a public-label collision in the configured private scan.
Additional live maintenance is not authorized.

## Summary

Prepare the next Phase 0 qualification work for stock 27B 8-bit and modified 27B 4-bit.
Keep the completed three-session client timing evidence and contract unchanged. Continue on
`splash-phase0` in the existing checkout, preserving pending changes from the preceding work.

## Scope and non-goals

- Validate a separately versioned native emission trace contract with explicit engine token
  batches, monotonic callback timestamps, exact final accounting and pinned adapter/runtime.
  Validation establishes structural consistency; actual runtime instrumentation and its
  equivalent-boundary audit remain prerequisites for native qualification.
- Freeze a deterministic quality pilot from the existing core eval suite plus coding cases.
  Reuse existing graders without executing model output. Add an isolated nonstreaming HTTP
  runner, private checkpoints, exact paired comparison and conservative uncertainty.
- Generate private manifest drafts and a deadline-based, whole-pair maintenance worksheet.
  Missing fresh preflight evidence prevents running drafts. No lifecycle or SSH commands.
- Document the remaining context/replay/concurrency/operational matrix with prerequisites.
- No production integration, runtime patch installation, live requests, deployment, gateway
  credential changes, default changes, regrading old timing runs or new benchmark claims.

## Runtime and integration

Linux devcontainer, uv/pytest/Ruff/strict mypy. Pure offline validators surround an async
HTTPX quality runner. Network boundary is an explicitly configured loopback tunnel; private
artifacts are the persistence boundary. No admin hot path or database changes. Reuse
`bench.py` strict models, canonical hashes, request translation, usage validation and private
writes, plus `eval_suites.py` validation and `eval_graders.py` deterministic grading.

## Risks and constraints

A native timestamp label cannot prove equivalent collection points; no fallback to SSE event
counts, reconstructed native duration or client timing. A small all-tied quality pilot cannot
establish a 2-point population margin: show a conservative paired confidence interval and
keep its gate inconclusive when the lower bound crosses -0.02. Required tool cases must all
pass. Failures, truncations, missing cases and cache reuse stay visible and prevent a pass.
Keep cohort identities, suite/grader hashes, settings and paired prompt counts exact.

## Task list

- [x] 🟩 Add failing tests for trace accounting, boundaries and private/sanitized CLI output.
- [x] 🟩 Implement the separate native trace validator and instrumentation specification.
- [x] 🟩 Add failing quality tests for frozen suites, request/response handling, cancellation,
  private checkpoints, paired grading, uncertainty and evidence rejection.
- [x] 🟩 Implement the isolated quality runner/comparator and manifest preparation worksheet.
- [x] 🟩 Freeze the quality pilot and document the remaining live qualification matrix.
- [x] 🟩 Validate offline tooling and application regressions; inspect public-safe output.
- [x] 🟩 Update the parent plan and append the devlog with concrete results and limitations.

## Test plan

Run project commands in the existing devcontainer as `vscode` through uv. First prove the
new tests fail before adding their modules. Use HTTPX fake transports only; tests make no
Mac or external-network calls. Exercise invalid token types, multi-token batches, partial
captures, wrong models, malformed tool calls, errors/timeouts, missing credentials,
cancellation, tampered profiles, duplicate/missing cases and degenerate confidence intervals.

```bash
uv run pytest tests/test_splash_native_timing.py tests/test_splash_quality.py
uv run pytest tests/test_splash_phase0.py tests/test_splash_continuation.py tests/test_splash_window.py
uv run pytest --cov --cov-fail-under=80
uv run ruff check src tests scripts ops/splash-phase0
uv run mypy src scripts ops/splash-phase0
cmp AGENTS.md CLAUDE.md
uv run python scripts/check_public.py --patterns-file ~/.config/local-llm-admin/public-denylist.txt --git .
```

Explicitly scan untracked files too. If the required private denylist is unavailable,
supplemental scans cannot replace it and changes remain unstaged. No commit is authorized.

## Performance and observability

Quality results do not enter the speed comparator. Use one sequential request per case,
record validated usage and raw answer/tool data privately, checkpoint each completion and
retain interrupted/error captures. A worksheet budgets all requests at their deadlines,
reserves setup/switch and restoration separately, and refuses to split a paired cohort.
Synthetic traces and fake HTTP timing are never performance evidence. No per-token I/O or
instrumentation is installed on the Mac in this work.

## Validation and rollback

All changes are isolated tooling/docs/tests. Run the full regression suite once after focused
checks pass. Existing timing report digests must remain unchanged. Rollback is removal of the
new offline files; no live service requires restoration because none is changed.

## Open questions and next campaign

Actual native callback adapters need an audited, pinned implementation before collecting a
new native series. Exact tokenization is required before labeling approximately 32K prompts;
actual required production context and service coexistence remain separate qualification
choices. Present concrete manifests, supervisor bounds, restoration recipe and remaining
prerequisites before seeking new live-window approval.

## Results — 2026-10-26

- 113 new native/quality tests pass; the full isolated tooling selection passes 278 tests
  at 87.68% branch-aware coverage. Quality capture/comparison is 98%; native inspection 97%.
- Full suite: 2,389 passed, one skipped, 98.10% application coverage. Ruff and strict mypy
  pass, including the isolated tooling (55 source files). Instruction-file parity and
  whitespace checks pass. Targeted tests pass after the synthetic marker-only cleanup.
- Both private preparation worksheets validate: 35 cases per backend, 70 requests per cohort,
  95-minute bounds including reserves, two separate proposed windows, and no runnable drafts.
- Completed timing report/evidence/comparison bytes are unchanged. No live inference,
  service transition, runtime adapter installation, deployment or commit occurred.
- The configured full private denylist is available. Its scan reports 59 matches of an
  existing public cohort label against one pattern; a separate synthetic test-marker
  collision was removed. This scan is not clean; no pattern was weakened and staging stays
  blocked. Offline implementation completion does not imply an adoption or staging pass.
