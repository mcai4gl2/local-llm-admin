# PaddleOCR live rollout and qualification

Status: Completed on 2026-10-31; released and qualified for the bounded measured operating point.
Component: Platform / resident services

Initial preparation notes below are historical. See the completed outcome at the end
and the native report for final operating scope. Failed outcomes remain unchanged.

The [archived implementation](20261030_paddleocr-resident-services.md)
preserves local evidence and failed/partial investigations. The disabled BF16 candidate,
27-file private release, route-create preview, public corpus and fixed acceptance thresholds
are prepared. No native installation, model download, model inference, service change or
LiteLLM route write has occurred. The reservation remains provisional and unmeasured.

Approved window: execute the concrete rollout described in the [host runbook](../../../host/ocr/README.md).
It provisions only the two isolated OCR environments and pinned snapshots, installs paired
private settings/catalog, restarts the admin, qualifies the full pipeline and registers only
`local-ocr`. Existing embeddings/rerank and main chat require before/after checks. Engine
suspend/restore and failure rollback deliberately interrupt service and belong to that window.

Before any mutation, recheck the private target, current route/env/topology and candidate
ports, save consistent DB/config backups and preserve rollback manifests/receipts. Resolve
the existing gateway client key privately in LiteLLM. The current admin viewer key may
preview but route writes need an appropriate management key. The user explicitly approved release to the Mac; host SSH authorization also remains in effect.

Completion evidence: actual image and PDF calls through LiteLLM, frozen-corpus task scores,
measured complete OCR/system peak memory and normal pressure, baseline/combined load within
fixed thresholds, two-service transitions and cleanup, and preserved unrelated routes/history.
Publish sanitized results under [OCR reports](../../../docs/reports/ocr/INDEX.md); leave failed
or inconclusive outcomes intact. See [current OCR operations](../../../docs/ocr.md).

## Progress — 2026-10-31

- [x] Explicit release approval and host SSH authorization.
- [x] Read-only topology/env drift, free candidate ports, schema integrity and active-job checks.
- [x] Consistent pre-release DB/config/admin backups preserved privately.
- [x] Host payload, paired private catalog/runtime/env, and schema-17 admin deployed and verified healthy.
- [x] Both isolated CPython 3.12 environments and pinned snapshots provisioned; receipts remain unqualified.
- [x] Existing chat/embedding/rerank proxy calls pass; initial 30+30 baseline has zero errors.
- [x] Dedicated gateway credential created privately and OCR-only route-create preview prepared.
- [x] Native warmup, corpus, actual proxy image/PDF, full memory and joint-load evidence.
- [x] Suspend/restore, active-work handling, failed dependency cleanup and final before/after preservation checks.

Two initial native warmups failed and timed out; scoped cleanup succeeded. Native source
inspection and a clearly labeled stub-recognizer diagnostic isolated two adapter defects:
the internal OpenAI base omitted `/v1/`, and formatted block JSON added Markdown headings
to plain text. Both fixes have tests that failed before correction. Related local tests,
Ruff and strict mypy pass; the canonical full suite is being repeated. Neither failed
run nor the stub diagnostic counts as candidate accuracy evidence.

The pre-existing long-running embedding process held about 49 GiB physical footprint.
Restarting it in the approved window reduced that to about 5.85 GiB and both embedding
and reranking calls passed. Preserve this operational observation; fresh-run qualification
does not establish an unbounded-runtime memory guarantee.

### First real corpus outcome

The adapter-corrected initial candidate passed 7/10 tasks through actual LiteLLM, with
complete ordered three-page PDF output. Columns were row-ordered (24.14% CER), rotation
lost a heading (42.11% CER), and plain math retained delimiters (22.22% CER). Frozen
[initial scores](../../../docs/reports/ocr/20261031-initial-corpus.json) remain a failed outcome.
All 39 existing proxy routes remained identical after the OCR-only route create.

Next candidate keeps the original VLM/layout revisions and acceptance thresholds, adds
the upstream pinned document-orientation classifier, conservatively repairs clear text
column gutters, and removes whole-block math presentation delimiters from plain text.
Geometry must map back to original input pixels after orientation. Reprovision only the
receipt-owned OCR environments/snapshots; retain the first receipt and failed outcomes.

### Second corpus and sparse-page orientation

The second candidate passed 9/10 tasks: text columns and math passed with zero CER;
rotation still lost the heading. [Second scores](../../../docs/reports/ocr/20261031-second-corpus.json)
remain frozen. A native CPU diagnostic showed the classifier chose no rotation at low
confidence on the mostly blank full page, but selected the correct rotation on its text
region. The next adapter trims only exact-white margins, keeps padding and every
non-white pixel, and adds crop offsets after inverse rotation. Corpus inputs, model pins
and acceptance thresholds remain unchanged. Decode and adapter regressions pass.

### Native stop investigation

Repro established: idle, ready OCR stops report failure; worker and VLM are gone and the
API exits, but its unchanged generation record remains. A generation-checked Stop retry
now runs native cleanup instead of submitting an empty job. The first-stop failure is
not yet fixed. The sparse-margin adapter is locally tested but was not deployed after
the stop failure.

Leading hypotheses: (1) process identity changes while macOS tears down the API after
TERM/KILL; (2) an unscoped child blocks group ownership; (3) concurrent cleanup races on
records. Capture exception and inventory evidence around the actual stop under the
shared lifecycle lock before choosing another fix. Never signal an ambiguous generation.
Validate with a deterministic failing regression, native first-stop repetitions and
active-work stop/exclusive-engine transitions. Keep one active plan for this rollout,
including its debug evidence, as required by the repository lifecycle.

Evidence gathered: two instrumented native stops under the shared lifecycle lock
reported `unverified process group member`; the actual offending command was the API
shutdown observer `ps -axo pid=,pgid=,stat=,lstart=,args=`. macOS also exposed transient
argv changes as a process exited. The observer inherited the service group, causing
its parent cleanup to fail closed. A deterministic real-subprocess regression failed
before isolating inventory observers in a new session. The verified-disappearance
regression also failed before adding a bounded, signal-free wait for ambiguous exits.
Existing-generation mismatch protection remains intact. Validate native first-stop,
active-work and exclusive-engine transitions before closing this investigation.

The isolated observer and bounded verification fixed the actual first-stop repro.
Third-candidate recognition passed rotation but margin-cropping the layout input changed
column grouping. Its failed [scores](../../../docs/reports/ocr/20261031-third-corpus.json) remain
frozen. Separate preprocessing: classify a padded content view with the pinned CPU
orientation model, then rotate the complete original page for layout/recognition. A real
array test proves that the classifier sees the crop, layout sees the full rotated page,
and boxes map into original pixels. This replaces full-pipeline cropping; thresholds and
corpus remain unchanged. Forty-three focused tests passed.

### Joint-load performance

The fourth candidate passed all ten fixed corpus tasks with zero CER and exact numeric,
table and PDF checks. Its unpaced 30+30 joint load had zero errors and 33 OCR documents,
but chat p95 was 1.326x baseline and throughput 0.872x, failing the unchanged 1.25x/0.90x
limits. Embedding p95 was 0.993x. Preserve [unpaced scores](../../../docs/reports/ocr/20261031-unpaced-load.json).

The evidence is consistent with GPU contention: chat degraded while embedding latency
stayed stable. Add explicit serial-region pacing across document boundaries: idle for
the prior call duration, capped at one second, within existing deadlines. Recheck
admission after any wait. This intentionally trades OCR throughput for interactive
capacity, rather than altering the baseline or acceptance thresholds. Both pacing and
cancellation regressions failed before implementation; 33 related tests passed.
The observer fix has now passed another native first-stop test.

## Completed outcome — 2026-10-31

The [native report](../../../docs/reports/ocr/20261031-native-rollout.md) and
[frozen qualified evidence](../../../docs/reports/ocr/20261031-qualified.json) record the
passed outcome. Ten tasks passed with zero CER; the unchanged 30+30 joint-load thresholds
passed with zero errors and 32 OCR documents. OCR peak physical-footprint bound was
5.32 GiB within 12 GiB; aggregate managed bound was 37.42 GiB, pressure normal in all
459 samples. Maximum image/PDF, rejection, force-stop and actual exclusive-engine
suspend/restore checks passed. Original chat selection/profile, clients, proxy routes
and all original rows in 19 historical tables were preserved.

Final serial suite: 3,844 passed, 4 skipped, 97.24% coverage; Ruff and strict mypy passed.
Earlier failures, inconclusive runs and the obsolete interrupted gate remain historical.
No commit was made. Private backup/provision/route receipts remain available.

The native OCR allow-list contains only the measured chat model; defaults remain opt-in.
Additional sharing models and observed pre-existing embedding memory drift are explicitly
deferred in the [resident follow-up](../../backlog/platform/20261031_resident-sharing-followup.md).
Do not reinterpret the fresh bounded workload as proof of indefinite allocator stability.
