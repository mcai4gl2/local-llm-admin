# PaddleOCR-VL and generic resident model services

Date: 2026-10-30
Status: Code complete; local validation passed. Live rollout/qualification awaits approval.
Component: Platform / resident services
Requested outcome: Run PaddleOCR-VL alongside embeddings as an independently managed,
always-on service, accessible through LiteLLM for image and PDF recognition. Make the
shared service infrastructure reusable for additional small models.

## Scope and completion boundary

This change includes the shared resident-service model, migration of embeddings onto it,
an isolated PaddleOCR-VL host runtime and full document pipeline, authenticated gateway
and LiteLLM integration, OCR UI, observability, deployment tooling, tests and qualification.

Planning is complete; no implementation, package installation, model download, live
inference, LiteLLM write or Mac deployment is authorized by this planning document alone.
Implementation can proceed when requested; approval for live service changes remains
subject to AGENTS.md. Local implementation completion and live qualification are separate
milestones. Preserve failed or inconclusive results rather than declaring readiness.

Keep this proposal in backlog while planning. On implementation, move this same file to
plans/active/20261030_paddleocr-resident-services.md and update the indexes and links.
When code is complete, archive it under plans/archive/platform even if deployment or
live qualification is pending; record those outcomes explicitly.

## User-visible result

- Models shows a Resident services section with independent cards for embeddings and OCR,
  and automatically renders further registered services.
- Embeddings and OCR can remain loaded together beside an allowed MLX model; OCR does not
  occupy or replace the exclusive OpenJev/TTS sidecar slot.
- OCR accepts PNG/JPEG images and PDFs, including scanned multi-page PDFs, and returns
  text, Markdown or structured page/block results.
- LiteLLM exposes a stable local-ocr route to the supported OCR API, with image and PDF
  examples. Applications do not need to know model weight paths or internal ports.
- An OCR page supports upload, progress/busy/error states, page-ordered results, and
  text/Markdown/JSON download. It uses the same admission control as gateway clients.
- Each service has its own status, intended state, readiness, resource estimate,
  availability history, request counts, diagnostics and allow-listed logs.
- Existing embedding/rerank clients, model IDs, keys, history and operator controls retain
  their contracts.

## Repository findings

| Existing implementation | Consequence |
|---|---|
| sidecars.py already has SERVICE_BUILDERS, kind=service, per-service intents and process hooks | Extend this registry; avoid a second parallel lifecycle framework |
| control.py uses generic service start/stop jobs with warmup and rollback | Reuse its lifecycle lock and job runner for OCR and dependency cleanup |
| plan_service_start counts the candidate, MLX and the slot sidecar, but has no sibling-services argument | Starting a second resident service requires aggregate budgeting first |
| app.py _service_view returns embedding/reranker IDs for every service | Model and capability metadata must come from each registration |
| models.js and models.html assume one embed-card; monitor alerts mention the Embeddings card | Generalize rendering, actions and operator guidance |
| Embeddings has its own venv, loopback server and authenticated sidecar gateway | Preserve this isolation and use the gateway as LiteLLM's ingress |
| gateway.py has specialized routes and special-case /v1/models composition | Add capability dispatch and discovery without dropping other enabled service IDs |
| inference_gateway.py separately serves the selected chat engine | OCR routing must not change selected-engine ownership or route back through LiteLLM |
| ComfyUI, Strata and Splash host guards explicitly enumerate embeddings and other processes | Generic host guards must see every configured resident service and dependency |
| Existing watchdog is alert-only after a service crash | Keep restart policy explicit and preserve embedding behavior |
| Existing service request stores contain counts and timings, not client content | OCR history must not persist images, PDFs, filenames or recognized document text |

Reviewed paths: src/local_llm_admin/{sidecars,control,app,gateway,config,engines,
backends,logs,diagnostics,embed,embed_store,storage,inference_gateway}.py;
src/local_llm_admin/static/models.js; host/embed/; host/bin/engine-guard.sh;
host/comfyui/bin/comfyui; host/strata/bin/strata; host/splash/lib/control.py;
ops/host-files.list and host/environment/embed-app.txt.

## External evidence and qualification requirements

Consulted 2026-10-30. These are upstream claims and interfaces, not local qualification.

- [PaddleOCR-VL model](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6):
  the initial candidate is version 1.6, approximately 0.9B parameters. Pin a model revision,
  dependency versions and precision after qualification; do not float latest.
- [PaddleOCR pipeline guide](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL.html):
  the VLM inference endpoint is only one stage. Image/PDF processing and document assembly
  belong to the full pipeline, not merely a raw model chat endpoint.
- [Apple Silicon guide](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html):
  documents manual deployment with a PaddleOCR pipeline and MLX-VLM recognition backend.
  Compatibility of their exact dependencies and model revision must be checked locally.
- [LiteLLM compatible endpoints](https://docs.litellm.ai/docs/providers/openai_compatible):
  an OpenAI-compatible model can be registered with a model name and API base.
- [LiteLLM PDF input](https://docs.litellm.ai/docs/completion/document_understanding):
  documents file content parts and PDF capability metadata. Verify that the installed
  LiteLLM version preserves PDF input for this custom endpoint; documentation examples
  for other providers are not proof of this route.

## Design decisions

### 1. Shared lifecycle and service-specific adapters

Retain the current exclusive sidecar slot. Extend the existing registry with a typed
resident-service descriptor rather than scattering OCR branches through shared code.

The shared descriptor covers stable service ID, label, lifecycle class, enablement,
public model IDs, capabilities, UI links, CLI/PID/health/log metadata, memory reservation,
compatibility policy, intent key and lifecycle/readiness hooks. A logical service may own
multiple models: embeddings already owns both embedding and reranker models; OCR may own
a pipeline API and VLM process.

Keep model loading, request schemas, warmup correctness checks, response validation and
specialized tool pages in typed adapters. A new adapter registers at one construction point;
the shared status, cards, budgets, transitions, logs and discovery iterate the registry.
No arbitrary module imports or shell command strings from browser input or private JSON.

Preserve the qwen-embed service ID and embed_intent key, existing EMBED_* settings, public
embedding/reranker IDs and historical availability identifiers. Add new configuration
through normalized descriptors with legacy settings adapters. Reject duplicate service
IDs, model IDs, conflicting ports and malformed dependency graphs at validation time.

Use a synthetic third resident service in tests to demonstrate that the architecture
does not stop at embeddings plus OCR. This is a fixture, not another deployed model.

### 2. Resident lifecycle and intended state

Always-on means a service remains loaded after startup, independently of the exclusive
sidecar slot, until stopped, suspended by an incompatible engine, or failed. It does not
mean ignoring memory checks or starting a model as a side effect of an inference request.

Preserve desired running/stopped state across admin restarts. On startup, observe and
reconcile actual processes without launching duplicates. Keep current alert-only crash
handling; boot autostart and automatic crash recovery are separate future policies,
not an implicit change to embeddings in this refactor.

Starting an exclusive engine suspends all incompatible resident services, drains or
refuses active work according to the existing force policy, and verifies all child
processes are down before the engine starts. Returning to a compatible MLX target
restores services whose intent remains running, in deterministic order and only if
the aggregate budget passes. An explicit user stop clears that service's running intent.

A failed start stops only the processes it created, releases its reservation and leaves
existing services alone. Partial restoration must produce an accurate job result and
per-service failure state; it must not claim every desired service is ready.

### 3. Resource accounting for any number of services

Use one inventory of running services and reservations for start, restore, model switch
and UI preview. Count MLX weights/context, the exclusive slot occupant when present,
every resident service, the candidate exactly once and existing headroom. OCR's reservation
covers its pipeline, layout models, VLM and peak document-processing working memory.

Recheck under the existing lifecycle lock. Unknown/conflicting ownership, missing memory
estimates and non-normal pressure must not produce a permissive budget. Include active
services even when disabled in config if their processes still own resources. Include
pending restores in the target-state estimate; restore candidates one by one with renewed
checks rather than independently admitting each against the same empty budget.

RSS is diagnostic, not a second reservation to add on top of the service estimate.
Record the estimated total and all contributors so operators can explain a refusal.
Never infer the full runtime footprint solely from parameter count.

Start with one active OCR document, a bounded admission queue and one VLM recognition
request at a time. Include page rendering and pipeline region concurrency in that bound.
Share admission between admin uploads, native API and LiteLLM calls. Qualify the joint
OCR plus embedding workload against chat; adjust pacing from measured evidence.

### 4. Host topology and guards

Proposed layout: host/ocr/bin/paddleocr-vl, host/ocr/app/, host/ocr/etc/,
host/ocr/README.md and host/environment/ocr-app.txt, deployed under the existing model
volume with separate models, venv, cache, logs, run and temporary directories.

The logical OCR service owns the pipeline adapter API and its MLX-VLM dependency. Start
dependencies first; stop in reverse order; verify model readiness with a small recognition
warmup. Liveness and readiness are separate. Health reports which component is missing.

OCR packages stay in the OCR runtime. The admin never imports PaddlePaddle, MLX-VLM or
PDF/image native libraries, and embedding/chat environments are not upgraded to add OCR.
Freeze a dependency set proven to run the complete pipeline on Apple Silicon. If runtime
dependencies conflict, separate the OCR API/pipeline and VLM environments explicitly.

Replace resident-service enumeration in host guards with a shared validated manifest/helper
used by the relevant wrappers. The manifest describes known service IDs, owned process
identities, paths, ports and policy; it does not provide executable shell fragments.
Generate/validate it from the same normalized catalog used by the admin and detect drift.
Missing or invalid ownership metadata must fail closed, including an orphaned OCR child.
Maintain legacy embedding detection through the migration.

All backend wrappers must check both directions of exclusion, including ComfyUI, Strata,
Splash and legacy MLX entry points. Retain process start-time/command identity checks,
shared lifecycle locks and unrelated-process protection. Private configuration chooses
ports after topology discovery; no plan assumes that a new fixed port is free.

### 5. Image/PDF contract and bounded execution

Provide a native OCR endpoint on the authenticated sidecar gateway and an OCR-only
OpenAI-compatible POST /v1/chat/completions route on that same gateway. Reuse existing
client keys and authorization policy; session-authenticated admin routes stay separate.

Initial supported media: PNG, JPEG and PDF. Native upload accepts multipart documents
and an output mode of text, markdown or json. The chat route accepts a single user
request containing image_url data URLs or a file content part with base64 PDF file_data.
Its default assistant content is Markdown; a documented structured mode serializes the
validated OCR result as JSON. Pin the exact supported envelope in contract fixtures.

Dispatch chat by a registered service model ID, never to the selected main engine or
back to LiteLLM. Keep this sidecar route distinct from inference_gateway.py. Preserve
existing embedding, rerank, speech and decision routes and their error contracts.

Reject unsupported remote URLs, filesystem paths, file IDs, multi-turn chat, tools and
unsupported output formats explicitly. Initially support non-streaming completion only;
advertise that restriction and return a clear error for stream=true. Do not silently
drop parameters or claim general image reasoning.

Set validated limits for encoded/decoded bytes, image pixel count, PDF pages, rendering
resolution, per-page and whole-document time, output size and queue depth. Phase 0 fixes
their numeric values using measured worst-case working sets. Enforce ingress limits while
reading the body, including chunked requests; a Content-Length check alone is insufficient.
Process pages incrementally, retain order and avoid rendering a whole PDF into memory.

PDF decoding and rendering run in a terminable worker process with a hard deadline.
A disconnected/timed-out request cancels downstream work, cleans temporary data and does
not release GPU admission while work is still executing. If the VLM backend cannot cancel
a generation safely, keep it busy until completion or terminate its owned worker and
report the resulting readiness loss. A thread timeout is not proof that work stopped.

Use the full PaddleOCR pipeline for layout and region recognition. Results carry a
schema version, stable public model ID, pinned revision/precision, ordered pages, text,
Markdown and available typed block geometry. Define coordinate units and page dimensions.
Do not invent confidence scores that the chosen pipeline does not expose. Record empty
pages, incomplete output and truncation explicitly; never return a partial document as
an unqualified success. Reject corrupt/encrypted/over-limit PDFs with documented errors.

Keep document bytes, extracted text and source filenames out of admin.sqlite, request
history and routine logs. Temporary files are private, request-scoped and deleted on
success, failure, cancellation and startup cleanup of abandoned work. Render OCR output
as escaped content; generated Markdown/HTML must not execute in the admin page.

### 6. Generic UI, discovery and observability

Render resident service cards from descriptors, with service-specific model IDs and
links. Reuse lifecycle actions, freshness checks, keyboard gating, job state and budget
explanations. Show intended state, actual process state, readiness and owner-induced
suspension distinctly; lack of telemetry is not zero traffic.

Compose /v1/models from all ready registered capabilities, including the case when
OpenJev occupies the exclusive slot. One adapter's discovery failure must not hide healthy
resident models. Configured LiteLLM routes remain registered while a service is suspended;
requests receive a clear unavailable response rather than starting it.

Add generic service request metadata keyed by service/model/capability and measured
units, with OCR pages/bytes/output-character counts and latency. Preserve old embedding
tables and query behavior through a compatibility adapter; do not destructively rewrite
history or double-count requests. Reuse service-scoped availability history where suitable.
Unknown token usage remains unknown; do not fabricate LLM token/cost accounting from pages.

Add per-service diagnostics, allow-listed pipeline/VLM log sources, rotation, history
retention and alerts. UI history is metadata-only; OCR output is returned to the current
request and can be downloaded without becoming a stored document collection.

### 7. LiteLLM registration and delivery

Expose local-ocr through the existing authenticated sidecar gateway; internal OCR ports
remain loopback-only. Supply a sanitized configuration template and a reusable ops tool
that renders/validates a route diff by default and applies only the requested route when
explicitly authorized. Preserve unrelated models, keys and embedding routes.

Register it as an OpenAI-compatible chat endpoint with verified image/PDF capabilities.
Qualify PDF file_data forwarding with the installed LiteLLM build. If forwarding requires
a provider hook, implement and test that explicitly; native PDF support alone does not
satisfy the LiteLLM requirement. Check API base, route identity, timeout, payload limits
and retry behavior end to end. Avoid automatic repeated OCR after ambiguous timeouts.

Keep a route snapshot for rollback. No credentials or deployment identifiers enter the
example config, plan, fixtures or reports. Registration, host provisioning and enabling
OCR require an explicit rollout step; a model name in /v1/models is not a successful
registration or proof that image/PDF requests work.

## Implementation phases and evidence

Every behavior is test-first. A refactor starts with compatibility characterization;
a bug fix starts with a regression that fails on the old implementation. Update this
same plan at each phase and append the repository devlog after meaningful changes.

| Phase | Deliverable | Required evidence |
|---|---|---|
| 0 | Contract and runtime qualification design | Exact revisions, dependency plan, API fixtures, limits and fixed acceptance thresholds |
| 1 | Generic resident descriptors and embedding compatibility | Two real registrations plus a synthetic third; old APIs/intent/history remain compatible |
| 2 | Aggregate budgets, lifecycle, transitions and shared host guards | All-service accounting, target/restore checks, suspension, rollback and process ownership tests |
| 3 | Isolated OCR runtime and full pipeline adapter | Host fixtures, dependency lifecycle, bounded image/PDF execution and validated results |
| 4 | Gateway and LiteLLM integration | Authenticated native/chat routes, image/PDF preservation, capability discovery and route diff/apply tool |
| 5 | Generic cards, OCR page and service observability | Browser tests, downloads, metadata-only history, alerts/diagnostics/log coverage |
| 6 | Full validation, operations docs and release bundle | Quality gates, public-safe reports, reproducible provisioning/rollback and archived implementation plan |
| 7 | Separately authorized live rollout and qualification | Actual image/PDF LiteLLM calls, measured memory/latency and multi-service transition evidence |

### Phase 0 checklist

- [x] Move this proposal to active when implementation is requested.
- [x] Inventory native/gateway port topology, current embedding API settings and active
      model compatibility without printing private values into tracked evidence.
- [x] Pin candidate PaddleOCR-VL revision and precision; initially evaluate BF16 or 8-bit
      before choosing more aggressive quantization. Review pipeline/layout model licenses.
- [ ] Prove the full pipeline/MLX-VLM dependency combination in isolation; record runtime
      constraints without upgrading existing chat or embedding environments.
- [x] Freeze native/chat image and PDF request/response/error fixtures, including
      LiteLLM capability metadata and the installed version's PDF forwarding behavior.
- [x] Establish public-safe generated or licensed OCR references: English, simplified
      and traditional Chinese, numbers, punctuation, rotations, multi-column text,
      tables, formulas, empty pages and a multi-page scanned PDF.
- [x] Set character error rate, numeric-field exactness, page-order/completeness and
      structured-table checks before evaluating the candidate. Record results by task.
- [ ] Set maximum bytes/pixels/pages/output/deadlines and memory reservation. Define
      chat latency/throughput and embedding latency regression thresholds from a baseline
      before the combined-load qualification; publish pass/fail against those thresholds.
- [ ] Document whether setup/inference needs a live service window and obtain the
      required live-change approval before that operation.

### Phase 1-2 checklist

- [x] Characterize embedding status, settings, IDs, intent, discovery, history and
      existing lifecycle refusal behavior before moving it behind shared descriptors.
- [x] Add model/capability/dependency metadata and descriptor-driven status/actions.
- [x] Add failing tests for sibling-service omission and correct all budget callers,
      including legacy model switches, force transitions and pending restores.
- [x] Cover duplicate IDs/ports, cyclic dependencies, unknown owners, disabled-but-running
      processes, stale PID reuse, two racing starts and resource-reservation cleanup.
- [x] Make host exclusion checks registry-driven and verify direct wrapper execution,
      not only admin-driven lifecycle. Include orphaned children and manifest drift.
- [x] Cover exclusive-engine suspension, explicit-stop intent, partial restore failure,
      engine-start rollback and admin restart reconciliation without automatic launches.

### Phase 3-4 checklist

- [x] Add OCR wrapper, isolated runtime lock/requirements, dependency start/stop and
      functional readiness warmup using a tiny public-safe reference image.
- [x] Implement native and chat transport adapters over one bounded OCR execution path.
- [x] Test byte/pixel/page limits, malformed base64/MIME, corrupt/encrypted PDFs, blank
      images, multi-page order, partial/truncated output, sanitized errors and busy queues.
- [x] Test deadline/disconnect cancellation, continued upstream work, temp-file cleanup,
      output schema/geometry, escaped output and absence of content in logs/history.
- [x] Extend model discovery, gateway authentication, error mapping and shared admission;
      verify model IDs cannot select arbitrary paths or cross into main-engine routing.
- [x] Add LiteLLM template and preview/apply/rollback tool with exact request fixtures,
      endpoint-level integration tests and no changes to unrelated routes or keys.

### Phase 5-7 checklist

- [x] Replace embedding-only Models rendering with descriptor-based resident cards.
- [x] Add OCR upload/results/download UI and generic service diagnostics/logs/usage.
- [x] Verify disabled/uninstalled/busy/suspended/loading/stale/failed states, mobile
      layout, keyboard controls and safe rendering in isolated browser tests.
- [x] Complete the validation commands and document which runtime checks are still
      pending. Archive the implementation plan once code is complete.
- [x] Prepare exact host payload, environment locks, route diff, DB compatibility
      strategy and operator runbook before requesting live rollout approval.
- [ ] In the authorized window, qualify the frozen corpus through the actual pipeline,
      then image and PDF through LiteLLM; measure idle/peak memory and combined-load impact.
- [ ] Exercise two-service suspend/restore and failure rollback with approved engine
      changes. Verify existing embeddings/rerank and main chat still work.
- [x] Record sanitized outcomes and limitations without rewriting failed evidence.
      Preserve private raw deployment facts outside git.

## Expected file map

Names for new modules are proposed; follow established patterns during implementation.

| Area | Likely paths |
|---|---|
| Shared model/lifecycle | src/local_llm_admin/sidecars.py, control.py, config.py, engines.py, app.py; a small services.py if needed |
| Service APIs | src/local_llm_admin/gateway.py; new ocr.py and service_store.py; storage.py migrations |
| Compatibility | src/local_llm_admin/embed.py, embed_store.py, decision_store.py; existing embedding endpoints |
| UI | templates/models.html, templates/base.html, new templates/ocr.html; static/models.js and new static/ocr.js |
| Visibility | diagnostics.py, logs.py, log_rotation.py, sidecars.py monitoring; existing usage templates/scripts |
| Host | host/ocr/, host/environment/ocr-app.txt; shared resident manifest/helper under host/; affected engine wrappers |
| Operations | ops/host-files.list, ops/env.example, ops/deploy.env.example, provisioning and LiteLLM route tools/examples |
| Documentation | README.md, docs/DESIGN.md, host/ocr/README.md, host/embed/README.md, ops/README.md, docs/reports/ |
| Verification | tests/test_sidecar_plan.py, test_control_embed.py, test_gateway_embed.py, test_host_*.py; new service/OCR tests and browser cases |
| Delivery tracking | This plan, plan indexes and append-only devlogs/YYYY-MM-DD.md |

## Validation and release gates

For implementation, run in the devcontainer as vscode through uv:

~~~sh
uv run pytest --cov --cov-fail-under=80
uv run ruff check src tests scripts
uv run mypy src scripts
~~~

Keep changed lifecycle, budget, authorization, input-validation and cleanup paths at
100% critical-path coverage. Run the repository's isolated Chromium/browser tests for
generic service cards and OCR flows. Host-specific code must have fixture-based
subprocess tests on Linux; those do not substitute for Apple Silicon qualification.

Check AGENTS.md/CLAUDE.md parity, diff whitespace, local Markdown links, host deployment
manifest coverage and the private public-safety scanner before staging. Do not add tests
merely to assert document wording or mirror an implementation.

Definition of done:

- [x] Embeddings and OCR are independent resident registrations; a fixture third service
      needs no new shared-card, budgeting, discovery or lifecycle special cases.
- [x] Every admission and transition counts all services and enforces engine compatibility.
- [ ] Image and PDF recognition work through the full pipeline and the actual LiteLLM
      route; deployment and live qualification status are reported separately from CI.
- [ ] Correctness and combined-load measurements meet the thresholds frozen in Phase 0.
- [x] Existing embedding/rerank behavior and retained histories pass compatibility checks.
- [x] UI, diagnostics, logs, request metadata and availability identify the right service.
- [ ] Provisioning is reproducible; rollout and rollback preserve private settings and
      unrelated models, routes and running services.
- [x] Current operations docs are updated; plan lifecycle and append-only devlog are complete.

## Deferred scope

Unlimited-OCR2 and other future model adapters, boot autostart/crash supervisors, remote URL
fetching, persistent document libraries, retrieval/knowledge-base ingestion, asynchronous
long-document jobs and chat streaming are outside this implementation. Adding another
compatible resident model should reuse the shared descriptors and controls; distinct model
protocols still need explicit adapters and qualification.

## Progress and next decision

- [x] Reviewed the existing resident-service, embedding, gateway, host-guard and UI paths.
- [x] Recorded the architecture, compatibility constraints, image/PDF contract, phases,
      validation and rollout boundary.
- [x] Implementation and local automated validation.
- [ ] Separately authorized host/runtime and LiteLLM rollout with live qualification.

Current decision: approve a live installation/qualification window for the prepared private
release and local-ocr route. Local code is complete; no native accuracy, measured memory
or combined-load pass is claimed. The concise rollout follow-up tracks this approval boundary.


## Implementation evidence

- Runtime: Linux devcontainer; FastAPI asyncio, host CLIs through serialized lifecycle jobs.
- Correctness and operational sensitivity: high; document execution and resident memory
  must be bounded. No live process changes are authorized by implementation approval.
- Phase 1 test sequence: embedding descriptor characterization; synthetic third-service
  validation; failing aggregate-budget tests; implementation; compatibility/control tests.
- At the initial checkpoint, Apple Silicon runtime, measured limits and installed LiteLLM
  forwarding proof were pending. Later SDK-only forwarding evidence is recorded below;
  actual native/proxy qualification is still pending.


### Local implementation checkpoint: contracts and transport

- Descriptor and sibling-accounting regressions were reproduced before implementation.
- Registered embeddings and disabled OCR with typed models, capabilities and dependencies;
  synthetic third registrations exercise catalog validation, discovery and status.
- Added target-state budgets, renewed restore admission, grouped restore cleanup, disabled
  resource inventory, unknown-ownership refusal and busy-service force gating.
- Native multipart and OCR-only chat routes share a single bounded client across admin
  and gateway. PDF data is preserved; unknown model paths, streaming and unsupported
  chat parameters are rejected. Ambiguous cancellation freezes further GPU admission.
- Request history uses additive schema v17 and a legacy embedding read adapter, without
  copying old rows or storing filenames, document bytes or recognized text.
- Added generic resident cards and OCR upload/results/download UI; isolated browser tests
  cover a third service, escaped results, download formats and mobile width.
- Candidate snapshot from the upstream model API:
  c5630abae1d940eafe0697512a0325494b02ab42; BF16 remains an unqualified candidate.
- Contract ceilings (not measured peak guarantees): 10 MiB decoded; 16 million pixels/page;
  16 PDF pages; 144 DPI rendering; 45 seconds/page; 240 seconds/document; 2 MiB output;
  one active document, two queued, 30-second queue wait.
- Initial whole-suite evidence: 3,587 passed, two skipped, two pressure-fixture failures,
  91.9% coverage. Both failures subsequently passed focused reruns. This run began before
  the new OCR tests were added and is not final validation of those modules.
- Focused OCR contract/client tests: 43 passed. Native gateway/history tests: 14 passed.
  Admin/control/entry-point/embedding compatibility tests: 55 passed.

Outstanding: separately approved native installation/inference, actual proxy image/PDF
forwarding, measured memory/accuracy/load and service-transition qualification. Private
configuration, release archive and a read-only route preview are ready for that window.
OCR remains disabled on the Mac; no native package/model provisioning or live writes occurred.

### Local implementation checkpoint: host runtime and delivery

- Added the normalized private resident catalog, admin drift checks and shared host guards.
  Direct wrapper regressions cover orphaned OCR resources; target MLX admission counts all
  reservations. Shared native ports are attributed by listener process, not port number alone.
- OCR owns separate pinned pipeline and MLX-VLM environments, generation-checked process
  records and persistent workers. Linux fixture subprocesses prove hard deadline/cancellation
  cleanup. Full native imports stay in the document worker, outside admin/API processes.
- Candidate macOS 14 arm64 hashed locks resolve; the original macOS 13 target failed MLX
  wheel compatibility. No Apple Silicon execution has qualified these locks yet.
- Installed LiteLLM 1.99.1 SDK image/PDF forwarding: two passing isolated tests. Public
  index lookup failed; proof uses an isolated read-only source copy of the installed SDK.
  Actual proxy route registration and recognition remain untested until rollout approval.
- Preview/apply/rollback manages only local-ocr, saves private receipts before writes and
  refuses concurrent drift/opaque rollback. PATCH updates may not introduce fields that
  cannot be restored by the prior definition.
- Isolated provisioning previews first, refuses live/unrecorded owners and existing
  candidate directories, downloads pinned snapshots only with an explicit flag, and limits
  rollback to directories created by its private receipt. Release bundles include hashes,
  candidate config/catalog and code, excluding credentials and private env files.
- Added descriptor-based resident logs and readiness-loss diagnostics/alerts. Browser suite:
  116 passed, including generic third-service log links and escaped OCR downloads.
- Generated public references and fixed CER/numeric/page/table and combined-load thresholds
  before candidate inference. Native accuracy, pressure and peak memory remain unknown.


## Local delivery outcome

- Final broad gate: 3,794 passed, three skipped; total coverage 96.52%.
- After the final concurrent-start cleanup correction: 264 lifecycle/policy tests passed;
  a further OCR start/restore test proves unique start-token binding and conditional cleanup.
- Critical OCR transport/history: 119 passed; all statements covered (99% branch coverage).
- Isolated Chromium suite: 116 passed. Installed LiteLLM SDK image/PDF tests: two passed.
- Real CPU decode with pinned Pillow/NumPy/PDFium: three passed; no GPU model inference.
- Ruff and strict mypy passed; host manifest coverage, Markdown links, instruction parity,
  whitespace and private public-safety scans passed. Local changes are staged without a commit.
- The prepared private archive contains 27 hashed code/config/lock files and is explicitly
  unqualified. Its reservation is provisional; native measured limits and baseline results
  are still absent. Read-only inventory confirms the candidate ports remain available.
- Failed/partial investigations above remain historical evidence. The original macOS target
  resolution, first broad-suite failures and SDK teardown failure are not relabeled passes.

Phase 7 remains pending explicit approval. See the [rollout follow-up](20261030_paddleocr-rollout-qualification.md),
[current OCR operations](../../../docs/ocr.md), [host runbook](../../../host/ocr/README.md)
and [evidence report](../../../docs/reports/ocr/20261030-local-validation.md).

### Final canonical release gate

The serial devcontainer suite passed after the final package-name, process-disappearance
and fixture corrections: **3,825 passed, four skipped; 97.24% coverage**. Ruff and strict
mypy passed. The unsuccessful optional parallel run remains recorded in the evidence report.
Host-authenticated SSH works through a private relay into the devcontainer; the read-only
deployment preview completed with apply disabled. The current 27-file private archive
matches the host payload, includes explicit v16/v17 DB rollback guidance and is marked
unqualified. All local changes are staged; no commit or live mutation occurred. The live
approval request is pending, so Phase 7 outcomes remain incomplete.

## Subsequent native delivery — 2026-10-31

The [approved live follow-up](20261030_paddleocr-rollout-qualification.md) completed release
and bounded native qualification. Its [report](../../../docs/reports/ocr/20261031-native-rollout.md)
preserves failed candidates and the measured passing result. Earlier unchecked live
items and inconclusive observations above describe this implementation record at its
original close; they are not retroactively rewritten as successful investigations.
