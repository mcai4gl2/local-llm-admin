# Regression case capture and candidate qualification

Date: 2026-11-01
Status: Proposed; implementation not started.
Component: Platform / quality and evaluation
Next decision: Confirm admin test-chat capture as the v1 entry point, and choose a small
operator-owned task suite and explicit qualification thresholds.

## Problem and goal

The project already supports custom suites, deterministic/judge graders, stored responses,
benchmarks and paired comparisons. Turning an actual application failure into a reusable
test still requires manual suite preparation. Model/profile selection lacks one report
combining relevant correctness checks with operational measurements.

Let an operator turn a reviewed chat example into a regression case, run a candidate
against a pinned baseline, and inspect a qualification report before changing the selected
default. Reuse existing quality machinery and comparability rules.

## Existing integration points

- `eval_suites.py`, `eval_graders.py`, `eval_store.py`, `eval_stats.py`,
  `eval_judge.py`: suite validation, grading, storage and comparison.
- `quality.py`, `quality_runner.py`, `quality_store.py`: lifecycle ownership,
  cancellation/restoration, runtime fingerprints and benchmark evidence.
- `chat.py`, `app.py` and Chat/Quality templates: explicit capture and report workflow.
- [Current eval behavior](../../../README.md#evals-m10).
- [Request timeline](20261101_shared-request-timeline.md): optional correlation only;
  a metadata-only ledger cannot reconstruct prompts or responses.

## V1 scope

- Add an explicit "Save as regression case" action for admin test chat. Present an editable
  preview of input, expected behavior, category and grader; save only after confirmation.
- Reuse JSONL suite validation and size limits. Require an explicit expected answer or
  assertion; never assume the observed failed response is a correct reference.
- Preserve necessary conversation/tool-call structure only where the existing runner can
  replay it correctly. Unsupported multimodal or tool-execution workflows are refused
  clearly or deferred; do not flatten them into misleading text-only tests.
- Store captures in local runtime data, not tracked fixtures. Offer redaction before saving;
  export is explicit. No default production prompt capture or automatic upload.
- Version suite content immutably for completed runs, retaining hashes and provenance.
  Editing a suite must not rewrite historical result meaning.
- Define a qualification specification binding baseline/candidate identity, suite hash,
  generation settings, runtime/profile fingerprints, judge identity and thresholds.
- Combine existing compatibility, task eval, speed and memory evidence in one report.
  Each section declares comparable, incomparable, failed, missing or inconclusive status.
- V1 reports recommendations only; changing defaults remains an explicit existing action.

## Comparison policy

Retain current paired-sample minimums, confidence intervals, self-judge labeling and
promotion restrictions for failed/cancelled runs. Different runtime/profile fingerprints
must not be forced through current paired comparison rules.

A cross-profile or cross-runtime candidate can receive separately labeled acceptance
results against fixed task requirements and operational limits. Present independent
measurements without a claim of matched statistical superiority. Any future relaxation
of comparison rules requires its own methodology decision and tests.

## Implementation sequence

- [ ] Inspect suite schemas, conversation representation and current promotion rules.
- [ ] Define capture, immutable provenance and qualification-report schemas.
- [ ] Add failing tests for invalid assertions, redaction, unsupported input, suite edits,
  stale fingerprints and missing/judge-error evidence.
- [ ] Implement reviewed chat capture using existing suite validation and local storage.
- [ ] Implement candidate specifications and report aggregation around existing quality
  jobs; reuse their lifecycle slot, cancellation, deadlines and restoration behavior.
- [ ] Add report UI with case failures, score uncertainty, performance and memory evidence.
- [ ] Document a repeatable candidate review workflow and example public-safe task suite.

## Acceptance and validation

- A captured text failure can be edited, saved, rerun and compared where eligible.
- Historical results keep original suite and runtime identity after edits.
- Missing or failed evidence cannot yield a passing qualification.
- Thresholds are recorded before candidate execution; no silent retuning after results.
- A profile/runtime change is explicitly incomparable under existing paired rules.
- Cancelled/intervened runs never change defaults or promote incomplete scorecards.
- Use fake providers for deterministic CI tests; no paid judge or live model is required.
- Run the repository coverage, Ruff and mypy gates in the devcontainer.
- Any live candidate study requires a separately scoped workload and deployment approval
  where applicable; keep failed and inconclusive findings.

## Dependencies, delivery and deferred work

This can proceed independently of the timeline. The
[resource-fit plan](20261101_resource-fit-and-qualified-profiles.md) may later consume
qualified memory evidence, preserving workload scope and expiry.

Defer automated traffic capture, autonomous promotion, external evaluation platforms and
a new full tool-execution harness. Move to active on implementation, archive on completion,
update indexes and append a public-safe devlog. Scan before staging; no unauthorized commits.

## References

[Langfuse datasets](https://langfuse.com/docs/evaluation/experiments/datasets) motivates
explicit trace-to-test workflows.
[Promptfoo](https://www.promptfoo.dev/docs/intro/) provides a reference for repeatable
application evaluations; replacing the existing runner is not proposed.
