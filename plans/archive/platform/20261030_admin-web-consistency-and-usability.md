# Admin website consistency and usability review

Status: **complete; all 28 findings implemented and locally verified**.
Date: 2026-10-30. Reviewed repository revision: `da30005`.
Implementation follows phases 1–5 below. Local fixture validation precedes any live rollout.

## Implementation progress

- [x] Phase 1: shared identity, context, state and trustworthy actions.
- [x] Phase 2: integrated Performance layout, filtering, pagination and source definitions.
- [x] Phase 3: Dashboard, model library, services and section freshness.
- [x] Phase 4: Chat defaults/attribution, Quality identity, Logs and timezone/accounting.
- [x] Phase 5: accessibility, responsive checks and full regression gates.

Original review evidence and limitations below remain historical observations. Implementation
results will be recorded here with test evidence, including any unresolved acceptance items.

### Implementation checkpoint — local verification in progress

The original review below is preserved. Implementation now covers all five phases in the
working tree; acceptance remains open until the audit and full gates finish.

- F01–F03/F12–F14/F19: shared human labels, configured vs verified loaded context,
  semantic tones, current-target defaults, busy/stale action policy, response/native/gateway
  scope labels and request-path Chat gating. Historical wire IDs remain in disclosures.
- F04–F10: engine-scoped request and retained-token queries; shared MLX/Splash/Strata
  availability; generation-checked new Strata observations; collapsed 20-row native history,
  stable `(observed,id)` cursors, independent profile/epoch catalogs, immutable query
  snapshots, obsolete-response guards and cached resize rendering.
- F11/F15–F18: available Strata live metrics, process vs bundle RSS, adjacent current job,
  compact backend/storage disclosures, explicit MLX library, registry-owned service pause
  explanations and readiness/freshness-aware Decision/TTS/Embeddings pages.
- F20–F23/F25/F27: resolved Chat defaults and request-captured reply identity; safe Clear
  during streaming; supported Quality defaults/scopes; stored scorecard provenance;
  active-engine Logs defaults, equivalent server-side tail/follow filters and connection
  state; operations-timezone dates/timestamps and independent auxiliary usage scope labels.
- F24/F26/F28: isolated status renderers, essential-status expiry, section-level update
  timestamps/errors, chart observation gaps/data tables/focus tooltips, page headings,
  labelled message/embedding inputs, compact grouped navigation and keyboard Usage tabs.

Verified so far: 81 Performance API/store tests; 91 eval-store/chat/backend tests; 16
embedding/sidecar API tests; strict mypy and Ruff pass. Browser regressions grew from 34
original checks to 78 checks. The first full run had 77 passing and one ambiguous test
locator (two stored-ID disclosures); the locator has been narrowed and is being rerun.
The required full coverage suite is running; no coverage result is claimed yet.

Remaining audit work includes per-target stored Quality context/configuration identity,
explicit token-row source labels, additional all-service/backend/failure/long-name browser
cases, final documentation and the full acceptance/gate audit. Keep all 28 findings in scope;
these are outstanding verification/implementation tasks, not a deferred scope reduction.

### Acceptance audit — complete

All 28 findings have implementation and regression evidence. The earlier checkpoint above
records its actual partial state; its failed locator and incomplete gate status are retained
as historical outcomes. No finding has been silently deferred.

| Findings | Delivered behavior | Local evidence |
|---|---|---|
| F01–F03 | Human labels, unchanged wire values, independent configured/verified context, distinct response/native/gateway scope | 32K/128K, mismatch, released, missing metadata and unready MLX/Strata browser regressions; inference metadata API tests |
| F04–F05 | Engine-scoped request/token sources and reusable MLX/Splash/Strata availability | Performance API/store source/folding tests; stable vs replaced Strata generation tests; exact range metadata regression |
| F06–F08 | Common monitoring before collapsed native diagnostics, scoped filters, 20-row stable cursor pages and independent catalogs | 200/500-row desktop/mobile layout and paging checks; equal-timestamp cursor and invalid-cursor tests |
| F09–F10 | Explicit source/measurement definitions, immutable query snapshots, obsolete-response guards, resize without fetch | Source-switch/partial-failure, resize, and older-request vs pending-summary browser interactions |
| F11–F12 | Available Strata rates/queue, process vs bundle memory scope, semantic status vocabulary | Strata Dashboard and partial-bundle tests; backend/state matrix |
| F13–F16 | Shared action policy, deliberate target changes, nearby job feedback, compact read-only targets and MLX library | Busy/stale/conflict/unknown action checks; empty installation; long target names; disclosure-state retention |
| F17–F18 | Shared pause owner/readiness, fresh service checks on engine transitions and visible history errors | All exclusive pause owners with desired-running/manual-stop API tests; Decision/TTS/Embed readiness, failure and transition interactions |
| F19–F21 | Unified keyboard/click guards, resolved defaults/capacity, immutable reply identity and retained/cleared history semantics | Reservation/completion, engine-change attribution, Clear midstream and obsolete-default-response browser tests; SSE identity test |
| F22–F23 | Supported initial Quality kind/scopes, empty-install refusal and stored per-target historical identity | Quality/eval browser tests; stored 32K/128K per-target labels; quality-runner context and scorecard provenance tests |
| F24–F25 | Isolated transport/render failures, expiring essential status, section feedback, contextual Logs and equivalent Follow filters | Failed status renderer, transport, Alerts, services and auxiliary poll regressions; log filter API equivalence and Follow connection tests |
| F26–F28 | Chart gaps/data alternatives, exact unknown timeline intervals, operations-timezone accounting, responsive/keyboard semantics | Gap/keyboard chart checks; different browser timezone and both ops offsets; light/dark contrast, 200% zoom, mobile navigation/touch and Usage tab checks |

Current explicit Chromium suite: **114 passed** (all navigation and APIs fulfilled locally).
This includes all optional services enabled/disabled and MLX, Strata, Splash, ComfyUI, off,
unknown and conflict at mobile width. Representative muted text and primary controls pass
4.5:1 contrast in light/dark; the result is not a claim that every possible color pair was
measured. Desktop/mobile dense history checks keep Availability above diagnostics, within
the first desktop viewport, without mobile page overflow. Empty installations, long labels,
partial/missing/unsupported metrics, manual stops and paused services remain distinguishable.

The first complete Python gate passed **3,550 tests, two skipped, 98.16% coverage**. The final
full coverage gate, including per-target Quality and range/cursor regressions, passed
**3,557 tests, two skipped, 98.17% coverage** in 679.02 seconds. Ruff and strict mypy pass.
The final polling audit also covers failed daily service traffic and operations-timezone
refresh timestamps. One intermediate browser run had 112 passing and two failed tests
because the new service-history fixtures had not enabled optional services; enabling those
fixtures produced the final 114 passing checks. Earlier failing-first regressions and the
original review limitations remain recorded above. Node remains absent in the devcontainer;
the dependency-free Node script is not claimed as run. Shared EngineUI decisions and real
DOM interactions are exercised in Chromium. Current operations are documented in
[DESIGN.md](../../../docs/DESIGN.md). No live deployment, engine qualification or Mac change
was performed or authorized by this implementation.

## Goal and scope

Make the admin coherent across MLX, Strata and Splash: an operator should immediately know
what is serving, which model/profile it uses, its verified context, which actions are
available, and what the displayed measurements actually cover. Keep backend-specific
capabilities without making every new backend a separate, permanently expanded interface.

This is a review and an improvement proposal, not a runtime change or a new qualification
campaign. It covers the shared shell, Dashboard, Models, Performance, Chat, Quality/evals,
Usage, Logs, Alerts, Decision, TTS, Embeddings, Outputs and login. Source inspection was
broad; browser checks concentrated on the backend-sensitive screens. No live deployment,
engine switch, inference request, private configuration inspection or Mac mutation was
performed. Source findings do not establish the exact current live browser state.

### Evidence and limits

- Reviewed templates, shared CSS/JavaScript, relevant status/telemetry/control APIs, model
  inventory and evaluation persistence, and recent backend commits. Key changes include
  `d1ab6d6` (backend contracts), `c25078c` (Splash integration), `91e9270` (telemetry/128K),
  and the subsequent shared-serving/error-reporting work.
- The [128K report](../../../docs/reports/splash/context-128k-report.md) and
  [profile example](../../../host/splash/etc/profiles.example.json) agree on **131072 total
  context tokens**, including generated output. Both legacy profile IDs retain `-32k` for
  compatibility. Historical 32K records remain valid historical evidence.
- Ran the existing isolated Chromium suite in the devcontainer:
  `uv run --with playwright pytest tests/browser -q`: **34 passed**. APIs and navigation
  were fulfilled locally; this is browser behavior evidence, not live-service qualification.
- Additional temporary Chromium probes used the same fixture with 128K targets, 200 native
  snapshots, and a recovering engine. These reproduced F01/F06/F12/F13/F19/F22 below, with
  no browser exceptions or unexpected API mutations. The chat probe intercepted its one
  deliberate synthetic POST locally and returned a synthetic 409.
- Local, untracked screenshots are in `.smoke/webpage-review-20261030/`; they are supporting
  review artifacts, not permanent rollout evidence. The observations and reproduction steps
  below are sufficient to retain the findings without committing browser artifacts.
- Browser checks used 1440×900 and 390×900 viewports. No page-level horizontal overflow was
  observed in the 200-row Performance probe; the confirmed defect is excessive vertical
  space. Do not claim a general mobile overflow or contrast failure without measuring it.
- Full Python/host qualification suites were not rerun for this documentation-only review.
  They will be required for implementation according to the repository gates.

## Priorities and finding index

P1 = correctness or a major operational obstruction; address in the first implementation
waves. P2 = substantial clarity, consistency or workflow improvement. P3 = broader polish.
There are **28 findings: eight P1, nineteen P2 and one P3**. These are the issues identified
by this review, not a guarantee that every possible browser/runtime combination is covered.

| ID | Priority | Surface | Issue |
|---|---|---|---|
| F01 | P1 | Shared identity / Models | Legacy `-32k` IDs dominate 128K presentation; context repeats |
| F02 | P1 | Models / Dashboard | Configured context is not distinguished from verified loaded context |
| F03 | P2 | Models / Chat / Performance | Blanket “usage unavailable” hides supported response/snapshot data |
| F04 | P1 | Performance | Main summary looks universal but excludes Splash request history |
| F05 | P1 | Performance | Main availability is MLX-only; Strata has no equivalent selectable view |
| F06 | P1 | Performance | Expanded Splash snapshot table pushes normal monitoring far below view |
| F07 | P2 | Performance | Global-looking filters have different, implicit scopes |
| F08 | P2 | Performance | Snapshot history is silently bounded and has no paging affordance |
| F09 | P2 | Performance | Source and metric definitions are incomplete or hidden |
| F10 | P2 | Performance | Overlapping reloads and resize fetching can show mixed filter results |
| F11 | P2 | Dashboard | Available non-MLX data is discarded while MLX-only fields occupy space |
| F12 | P2 | Shared status | State wording, badge colors and conflict wording are inconsistent |
| F13 | P1 | Models | MLX-specific and shared lifecycle controls have inconsistent gating |
| F14 | P2 | Models | Initial target silently proposes switching away from the active engine |
| F15 | P2 | Models | Routine controls and job progress are buried among unrelated panels |
| F16 | P2 | Models / inventory | Model-file management is presented as the complete serving catalog |
| F17 | P2 | Models / services | Sidecar/embedding copy and paused status still assume ComfyUI |
| F18 | P2 | Sidecar test pages | Missing Splash pause explanation, readiness and refresh inconsistencies |
| F19 | P1 | Chat | Keyboard send bypasses disabled state; completion restores Send blindly |
| F20 | P2 | Chat | Defaults, output limit and endpoint copy do not describe active backend |
| F21 | P2 | Chat | Conversation history lacks persistent backend/profile attribution |
| F22 | P2 | Quality | Non-MLX users land on an unsupported benchmark with enabled invalid scopes |
| F23 | P2 | Quality / evals | Run lists and scorecards hide backend/configuration distinctions |
| F24 | P1 | Shared polling / Alerts | Failures leave stale values; renderer errors look like connectivity loss |
| F25 | P2 | Logs | Active backend is not selected; follow filter/reconnect state is unclear |
| F26 | P2 | Charts / monitoring | Hover-only details and zero-shaped missing measurements obscure meaning |
| F27 | P2 | Usage / dates | Timezone, range scope and accounting labels differ across surfaces |
| F28 | P3 | Shell / accessibility | Navigation density and form/section semantics need a measured pass |

## Detailed findings

### Identity, model configuration and services

**F01 — Legacy identity leaks into the product language.**
[engine_ui.js](../../../src/local_llm_admin/static/engine_ui.js) `label()` appends the raw
reference; [models.js](../../../src/local_llm_admin/static/models.js) `renderBackendCards()`
uses it for Profile; history also prints stored identifiers. With a current 128K fixture,
the Engine card reads `Splash · stock-8bit-32k` while the Context fact reads `128K`.
`EngineUI.options()` appends context even when the profile's own label already contains it,
producing `Splash · stock-8bit · 128K · 128K context` from the shipped example labels.
Use a shared presentation label from backend/profile metadata, show context exactly once,
and put raw IDs in expandable technical details. Preserve target values, aliases, prepared
names and history. Never infer context from the ID suffix or relabel old telemetry as 128K.

**F02 — “Context” conflates configured and loaded facts.**
The Models backend card uses `status.max_context || target.max_context`, silently replacing
missing loaded evidence with configuration; Dashboard uses raw active status instead, so
pages may disagree. A stopped backend can also lose its current reference and show dashes
despite configured targets being available. Show `Configured context` independently and
`Loaded context` only when runtime verification supplies it. Mark pending/unverified/mismatch
states explicitly. Context is the total prompt-plus-output window, not an output-token limit.
This review reproduced the naming defect, not a hard-coded 32768 effective-context bug:
131072 status data already renders as 128K. If the live numeric Context field still says
32K, implementation should compare the public status response and loaded/configured fields
before changing presentation; do not blindly replace every occurrence of 32K.
Sources: `renderBackendCards()`,
[dashboard.js](../../../src/local_llm_admin/static/dashboard.js),
[Splash readiness/profile contracts](../../../src/local_llm_admin/backends.py).

**F03 — Usage has several meanings but one blanket label.**
Models hard-codes `Usage: unavailable`. Runtime notes say usage depends on reports; Chat can
show final response usage; Performance has cumulative execution snapshots; Usage counts
LiteLLM traffic. Replace the blanket fact with compact capability labels: response usage
when reported, native counters available/stale/not collected, all-client request history
unsupported, gateway accounting scoped separately. Absence of request history must remain
visible. Do not imply that a native count is a billed-token or all-client request ledger.
Sources: `renderBackendCards()`, [chat.js](../../../src/local_llm_admin/static/chat.js),
[telemetry contract](../../../docs/splash-telemetry.md).

**F12 — State presentation does not share a stable vocabulary.**
The header, Engine card, Strata card, Splash card, Dashboard and availability use different
names and `data-state` values. The broad `.badge[data-state^="splash"]` rule colors stopped
(`splash-absent`) amber, just like loading; the browser probe confirmed the warning color.
`STATE_LABEL.conflict` still says “two engines” although there can be more. Use a shared
semantic tone (`ready`, `busy`, `stopped`, `unknown`, `error`) alongside an explicit lifecycle
label; retain released/recovering as meaningful states. Separate engine identity from status
so MLX ready and Splash ready have equivalent visual treatment. Engine-card badges should
not rely on a legacy top-level state to color a different backend's facts.
Sources: [app.js](../../../src/local_llm_admin/static/app.js) `STATE_LABEL`,
[app.css](../../../src/local_llm_admin/static/app.css), `EngineUI.badge()`, `renderStrata()`.

**F13 — Two control paths communicate different rules.**
Models has the shared Run/Apply selector plus Registered models' Start selected/Stop and
row Switch/Restart actions. Row controls consult `canUseModelActions()`; Start selected and
Stop do not. The 128K Splash browser fixture leaves Start selected enabled while the MLX row's
Restart is disabled. Apply also lacks a pending-job/fresh-status gate. This is a UI defect;
server-side lifecycle protections still apply. Give primary lifecycle actions one capability-
and freshness-aware policy; explicitly label any retained controls as MLX-only. Disable
unavailable actions with a visible explanation and a path to the engine selector. Cover
unknown/conflict/busy states and duplicate submission, while preserving deliberate conflict
recovery controls and all backend checks.
Sources: [models.html](../../../src/local_llm_admin/templates/models.html), `loadModels()`,
`applyEngine()`, `onStatus()` in models.js, `EngineUI.canUseModelActions()`.

**F14 — The proposed target is surprising.**
`EngineUI.defaultTarget()` chooses the last-used MLX model when an exclusive engine is active,
and otherwise seeks an option from another backend. The page can greet an operator running
Splash with `Run MLX …` and a prominent Apply button. Initialize to the actual current target
when known and require an explicit changed selection before Apply. Keep any convenient
“Return to MLX” action explicit, with its own target and effects. Preserve deliberate user
selection through polling and define a clear initial state when nothing is running.
Source: `defaultTarget()`, `renderEngineOptions()`.

**F15 — Models has too much expanded content and late feedback.**
Engine selection, Splash/Strata, ComfyUI weights/queue, services, registered models, inventory,
downloads and raw disk discovery precede the job panel. A switch can update progress far
below the initiating control; the page is long even when most services are stopped. Place
active-engine status, controls and current job together at the top; group inactive engines
compactly; collapse storage/download/advanced configuration and inactive ComfyUI details.
Keep queue controls prominent when ComfyUI is active. Provide section links, clear result
feedback and focus/scroll handling after a deliberate action. Do not collapse away a failure
or a running job on a polling update. Sources: models.html section order, `showJob()` and
backend rendering in models.js.

**F16 — “Registered models” is really MLX registration.**
Models' main table, Settings, registration/download instructions and inventory are driven by
`/api/models`/`mlx-model`, not a unified backend catalog. Splash profiles and Strata packs are
separate read-only mechanisms, yet labels and the shared disk directory suggest that a
downloaded/registered model becomes usable everywhere. Keep an explicit serving-target
catalog (MLX models, Strata packs, Splash profiles), and label the file-management section
`MLX model library` unless it truly covers the others. Show which operations are supported
and whether settings are editable or configured externally. Inventory request counts already
have an MLX-source caveat; surface that scope near the counts rather than treating missing
Splash counts as zero usage. Do not introduce a new Splash installer/profile editor here.
Sources: [inventory.py](../../../src/local_llm_admin/inventory.py) `inventory()` and attribution
text, models.js `loadInventory()`, models.html registration/settings/download sections.

**F17 — Service descriptions and pause status disagree with exclusivity.**
Models says only one engine may use the GPU even though supported MLX/sidecar combinations
share it; sidecar text excludes ComfyUI only, and embeddings are described as “Always on”
except during ComfyUI. `_service_view()` computes `paused` from ComfyUI alone, and models.js
hard-codes “paused while ComfyUI runs”; an intentionally paused service under Strata/Splash
can appear down instead. Return a generic pause reason/owning backend from the existing
registry and render it everywhere. Distinguish desired intent, paused by policy, stopped,
starting, ready and failed. Derive restoration text from actual policy, not an unconditional
promise. Sources: [app.py](../../../src/local_llm_admin/app.py) `_service_view()`, models.html
service copy, models.js `loadServices()`, [sidecars.py](../../../src/local_llm_admin/sidecars.py).

**F18 — Test-page availability is incomplete and becomes stale.**
Embeddings handles `comfyui_active` and `strata_active` but not the existing `splash_active`
reason; it tells users to start a service that policy forbids starting. Its buttons check
`running` while the API also reports `ready`. Decision/TTS give generic stopped messages;
these pages do not consistently refresh availability from shared status while engines change
elsewhere. Show the actual blocker, a Models link and readiness-aware button state; refresh
on relevant status changes without resetting user input. Sources:
[embed.js](../../../src/local_llm_admin/static/embed.js) `load()`,
[tts.js](../../../src/local_llm_admin/static/tts.js),
[decision.js](../../../src/local_llm_admin/static/decision.js), `service_unavailable_reason()`.

### Performance and Dashboard

**F04 — Summary coverage looks broader than it is.**
Requests/TTFT/decode/tokens are backed by MLX/Strata inference rows; Splash is only disclosed
by a separate coverage paragraph and native card. During Splash operation, historic MLX
numbers can look like the current engine's performance, and zeros/no rows can look like no
traffic. Introduce an Engine selector with explicit source coverage next to each summary.
A combined view must say which sources it includes; unsupported Splash request metrics must
say `Not collected` or `Unsupported`, not `0`. Do not use native counters to fill request
history, percentiles or token-accounting gaps. Sources: performance.js `loadSummary()` and
`loadTokens()`, [app.py](../../../src/local_llm_admin/app.py) performance routes,
[performance.html](../../../src/local_llm_admin/templates/performance.html).

**F05 — Availability is split across incompatible presentations.**
The main hero/timeline/table calls `/api/availability` without source and therefore measures
MLX intended time. Strata is a separate colored period, grouped into an `Off / ComfyUI` table
column. Splash has only a sentence below its native table. The legacy availability states
have no Splash state, and profile-qualified non-MLX intents do not map cleanly into that
legacy classifier. There is no selectable Strata availability API (`source` currently permits
only MLX/Splash). Give availability the same backend selection and visual component, with
its denominator and observation coverage visible. Reuse Splash's existing serving/released/
recovering/maintenance distinctions. Add Strata collection through the backend contract if
in scope; label unavailable historical coverage honestly. Preserve the legacy MLX series and
never reinterpret old unknown periods as measured availability. Sources: performance.js
`loadSummary()`/`renderAvailability()`/`loadNative()`,
[availability.py](../../../src/local_llm_admin/availability.py),
[backend_availability.py](../../../src/local_llm_admin/backend_availability.py), availability API.

**F06 — Native history consumes the page.**
`loadNative()` renders every returned snapshot in an always-expanded card directly before
Availability. At the endpoint's default 200 rows, the isolated browser measured:

| Viewport | Native card height | Availability strip top | Full page height |
|---|---:|---:|---:|
| 1440×900 | 6,699 px | 7,077 px | 8,500 px |
| 390×900 | 10,668 px | 11,605 px | 14,201 px |

Place backend-aware summary and Availability first. Show a compact native-counter summary
inside the shared performance hierarchy, with at most one latest snapshot per explicitly
selected epoch and clear cumulative scope. Raw snapshots, fingerprints, epochs, gap codes
and reset qualification belong in collapsed diagnostics with bounded, paged history.
Do not invent period totals/deltas while reset/population qualification is unverified.
A page with 200/500 snapshots must remain usable before diagnostics are opened.
Sources: performance.html `#native-telemetry`, performance.js `loadNative()`.

**F07 — Filter scopes are implicit.**
Model filters inference rows, but `loadNative()` deliberately strips `model` and main
availability strips it too. The profile filter affects only native snapshots/availability,
while Traffic affects only tokens. An MLX model can be selected while an unrelated Splash
table remains. Profile options are inferred from limited returned readings and rebuilt only
when no profile is selected; changing ranges can retain a profile with no data. Define a
single scope model: range → engine → target/profile, with explicitly local traffic/state
filters. Populate target choices independently of a bounded snapshot page. Preserve valid
choices and clearly report invalid/no-data selections rather than silently changing scope.
Source: performance.js `params()`, `loadSummary()`, `loadNative()`, filter handlers.

**F08 — Snapshot history is incomplete without saying so.**
The API defaults to 200 and caps at 500 readings. The UI has no cursor, next page, displayed
limit or truncation signal, unlike its Requests table. Options derived from those rows can
also hide older profiles. Add stable paging and a limit/coverage description to advanced
history. Make global current collector health distinct from the selected historical range;
`coverage()` currently returns current collector state, not range-specific completeness.
Sources: [engine_telemetry.py](../../../src/local_llm_admin/engine_telemetry.py) `readings()`/
`coverage()`, app.py `performance_engines()`, performance.js `loadNative()`.

**F09 — Metrics need provenance at the point of use.**
The summary says `MLX log source` even when Strata rows contribute; request/token tables do
not show source. Strata prompt time is used as TTFT, but that distinction is in footer prose.
Native execution tokens, final response tokens and LiteLLM-accounted tokens have different
populations and timing boundaries. Add compact source/scope badges and definition help to
metrics and rows. Keep unlike TTFT definitions separate or clearly annotated in combined
views; do not claim directly comparable backend speedups. Preserve the telemetry contract's
restrictions and the existing inconclusive benchmark outcome. Sources: performance.js
`loadSummary()`/`reqRow()`, performance.html footnotes,
[telemetry field matrix](../../../docs/splash-telemetry.md).

**F10 — Rendering can race between scopes.**
`reload()` starts four independent loads; rapid selection changes can let older responses
replace newer ones. `loadSummary()` also rebuilds the model selector while other loads are
in flight. Window resize fetches summary data again instead of redrawing its cached value.
Use an immutable query snapshot and generation/abort guard; discard obsolete responses,
redraw cached charts on resize, and expose section-level loading/error state. Preserve
last-known values only when marked stale. This is a source-confirmed race opportunity;
out-of-order arrival was not separately injected during this review.
Source: performance.js `reload()`, `loadSummary()`, resize handler.

**F11 — Dashboard still equates useful metrics with “multi-model”.**
`modelMetrics` gates almost all inference/model facts on the multi-model operation, leaving
Strata's provided live decode/prompt/queue data unused. Splash shows many MLX-specific dashes
for tool parser, architecture, allocator-related metadata and last request; Engine process
and Engine bundle tiles can be confusing together. Adapt the existing descriptors into a
small common current-engine summary, map verified backend measurements with their semantic
labels, and hide unsupported facts behind details. Label primary-process RSS and bundle RSS
explicitly, preserving partial/unavailable states and shared-page caveats. Do not substitute
cumulative native execution rates for last-request decode/TTFT. Sources:
[dashboard.js](../../../src/local_llm_admin/static/dashboard.js),
[dashboard.html](../../../src/local_llm_admin/templates/dashboard.html),
[engines.py](../../../src/local_llm_admin/engines.py) `parse_strata_metrics()`.

### Chat and Quality

**F19 — Disabled Chat can still submit.**
The status callback disables Send, but the textarea Enter handler directly invokes `send()`;
that function only checks empty text and an existing controller. `finally` unconditionally
re-enables Send. In the recovering fixture, Enter produced **one intercepted POST** despite
a disabled button, and Send became enabled after its synthetic 409. Active quality-run
blocking is enforced by the API but not included in the client availability policy either.
Maintain one current eligibility state (readiness, quality reservation, fresh status and
in-flight request) and consult it from click, submit, keyboard and completion paths. Keep
server enforcement and show its refusal accurately. This is a usability/control-consistency
bug, not evidence of a server ownership bypass. Sources: chat.js status/send/key handlers,
`EngineUI.chat()`, app.py `/api/chat`.

**F20 — Chat help describes the old model path.**
Chat says requests go to `:8080`, use a single decode slot and use saved Models → Settings
defaults, else 0.7/512. Splash uses its own endpoint, Strata has an 8192 output default, and
Splash profiles carry their own defaults/reasoning caveat. The 32768 max-tokens field is a
separate output cap, not evidence that total context is 32K. Show resolved backend defaults,
loaded total context and output allowance/cap separately; make reasoning capabilities and
caveats contextual. Label accounting as bypassing **LiteLLM Usage**, because MLX/Strata chat
can still contribute to Performance. Sources:
[chat.html](../../../src/local_llm_admin/templates/chat.html),
[chat.py](../../../src/local_llm_admin/chat.py) defaults/ChatRequest,
[backends.py](../../../src/local_llm_admin/backends.py) inference descriptors.

**F21 — Chats are not tied visibly to the engine that answered.**
Chat retains one messages/history array while shared status can change to another backend
or profile. Replies have token/timing metadata but no durable displayed serving identity;
clearing is not coordinated with an active stream. Capture the actual response's engine,
model/profile and settings identity when available, show a compact conversation header and
reply attribution, and prompt for or visibly mark a new engine segment when selection
changes. Do not silently erase a conversation on polling. Define Clear during generation
so hidden/cleared streams cannot repopulate confusing history. Source: chat.js history,
`addMessage()`, done handling and Clear listener; any API identity addition must reflect the
request's resolved descriptor, not status sampled after it finished.

**F22 — Quality opens with a dead end for Strata/Splash.**
The default kind is Benchmark; current non-multi-model engines only support compatibility
and evals. The ready Splash browser fixture lands on a disabled Start, with all model-scope
options still enabled. Evals already narrows scope, while the compatibility form merely
rejects the choice after selection. Choose an available initial kind, consistently disable
unsupported scope/kind options with a short reason, and preserve deliberate valid choices.
Label “current” with the actual engine/profile. Keep the existing restriction on native
benchmarking and self-judging; do not add support just to make a button enabled. Sources:
[quality.html](../../../src/local_llm_admin/templates/quality.html), quality.js `renderEffect()`,
[evals.js](../../../src/local_llm_admin/static/evals.js) `renderEvalForm()`, `EngineUI.quality()`.

**F23 — Result identity is too weak in summaries.**
`runModels()` returns `current`, `all` or a model name, so run lists and compare pickers do
not identify the backend/profile. Details expose runtime/fingerprint later, but scorecards
can have distinct fingerprint-keyed rows with the same displayed model name. Render engine,
target, context, reasoning and a compact settings/version indicator wherever results are
chosen or compared; offer full identity on expansion. Preserve existing comparison guards,
confidence intervals and historical identities; this finding does not claim persistence
already merges distinct fingerprints. Sources: quality.js `runModels()`/`loadRuns()`/`showRun()`,
evals.js `loadEvalRuns()`/`loadScorecard()`,
[eval_store.py](../../../src/local_llm_admin/eval_store.py) `scorecard()`.

### Reliability, logs, accounting and access

**F24 — Failures look like valid old data or the wrong failure.**
Many pollers swallow errors (`catch(() => {})`): Dashboard metrics/problems, Models inventory/
services, Quality runs and Alerts can remain populated without a visible stale indicator.
`refreshStatus()` wraps both the API request and every renderer in one try/catch, so a
listener exception incorrectly sets `Admin unreachable` and prevents later listeners from
updating. Separate transport failure from render failure, isolate listeners, record last
success and show refreshing/stale/unavailable states per section. Invalidate lifecycle/chat
eligibility when essential status is stale. Retain useful last-known data with a timestamp
instead of erasing it or silently treating it as current. Sources: app.js `refreshStatus()`,
dashboard.js `tick()`, models.js pollers, quality.js `watchRun()`,
[alerts.js](../../../src/local_llm_admin/static/alerts.js) `refreshAll()`.

**F25 — Logs defaults and Follow do not reflect intent.**
Logs defaults to the first source (MLX) regardless of active backend, and engine cards have
no direct contextual log action. Follow snapshots the text filter when started, ignores the
selected level, and has no visible `onopen`/`onerror` reconnect state. Add backend log links,
default intelligently unless a source was explicitly selected, apply the same text/level
semantics to tail and follow, and expose connection/stale/pause state. Keep allowlisted log
sources and bounded rendering. Sources: [logs.js](../../../src/local_llm_admin/static/logs.js)
`load()`/`startFollow()`, [logs.py](../../../src/local_llm_admin/logs.py) source IDs.

**F26 — Charts obscure absent data and keyboard details.**
SVG hover targets have pointer handlers but no equivalent focus interaction. The availability
strip relies on segment titles; its generic aria label is not a time/state alternative.
Latency/decode bars use `?? 0`, giving missing measurements a zero-shaped presentation, and
line charts connect the remaining non-null points across missing intervals. Add accessible
summaries/tables or keyboard-focusable details, identify missing values, and break lines at
observation gaps. Preserve measured zero as distinct from missing. Do not infer unsupported
Splash history from blank charts. Sources: app.js `lineChart()`/`stackedBars()`, performance.js
`bars()`/`renderAvailability()`, performance.html strip.

**F27 — Range and accounting scope require too much interpretation.**
Usage is LiteLLM-based but named generically; Performance is engine-derived, with different
coverage. Browser-local calendar dates are sent to APIs interpreted in the configured ops
timezone, while timestamps are formatted in browser local time and Usage sessions show UTC.
Usage displays a hard-coded UTC+8 explanation even though the setting is configurable.
Its main range may be 7/30/90 days or custom, but sidecar usage modules independently fetch
30 days. Adopt one explicit range/timezone convention, return/use server timezone metadata,
name the accounting scope near totals, and either synchronize auxiliary ranges or visibly
mark their independent scope. Do not silently change existing token inclusion rules or
split gateway aliases among historical engines without attribution evidence. Sources:
[usage.html](../../../src/local_llm_admin/templates/usage.html),
[usage.js](../../../src/local_llm_admin/static/usage.js), `performance.js.params()`, app.py
`_epoch_range()`, decision_usage.js/tts_usage.js/embed_usage.js.

**F28 — The expanded application needs a navigation/accessibility pass.**
The shared header can include twelve top-level destinations plus status/theme/logout, all
wrapping in a sticky header. Several content screens begin at h2 without a page h1, some
textareas rely on placeholder text, chart/table details rely on title, and Usage tab roles
lack a complete linked-panel/keyboard pattern. Group secondary destinations, provide a
compact mobile navigation and page title/landmark, label inputs, and use native accessible
controls or complete tab semantics. Measure light/dark contrast, focus order, zoom and touch
targets rather than claiming unmeasured failures. Keep Outputs explicitly about ComfyUI;
its existing source naming and paged gallery are useful patterns. Login's native password
label and error alert need no backend-specific redesign. Sources:
[base.html](../../../src/local_llm_admin/templates/base.html), app.css, Chat/Embeddings/Usage
forms and templates. This is polish after the operational defects, not a site rewrite.

## Proposed information and data model

### One public presentation contract over existing adapters

Extend the existing backend description/inference contracts only where necessary. Do not
create a second frontend-only engine registry with duplicated policy. Expose the smallest
public-safe facts needed by shared components:

- Engine ID/label, stable target value, human target label, model/cohort where supported.
- Lifecycle state, semantic tone, readiness, checked timestamp and reason for unavailability.
- Configured total context, verified loaded total context and verification state; effective
  generation defaults/output cap separately. Raw IDs and settings fingerprints in details.
- Operations already supplied by adapters; explicit presentation capabilities for editable
  settings, current-only quality, supported measurements and service coexistence/pause.
- Measurement scope, definition, unit, freshness and missing-data reason. A metric can be
  unavailable, unsupported, not collected, stale, partial, unverified or measured zero;
  those states must not collapse into one dash or fabricated zero.

Keep paths, credentials, native endpoints and deployment-specific details out of the public
presentation contract. Backend-specific advanced diagnostics remain possible. Response
identity must be captured at request resolution; historical identity must come from stored
records, not today's active target.

### Recommended Models hierarchy

1. Current engine: name/profile, verified loaded context, readiness and primary controls.
2. Current job/result adjacent to those controls, with progress and restoration outcome.
3. Serving targets: compact, consistent MLX/Strata/Splash choices; inactive details collapsed.
4. Services: concise current availability/pause reasons; expand budgets/settings as needed.
5. MLX model library/storage/downloads; advanced inventory and disk discovery collapsed.
6. History with readable labels and original IDs available in details.

### Recommended Performance hierarchy

1. Shared range + engine + dependent model/profile selection; explicit timezone and coverage.
2. Compact summary of supported request metrics, with missing capabilities explained.
3. One reusable Availability presentation for the selected scope and denominator.
4. Latency/throughput and token/accounting sections as supported, with source definitions.
5. Request history where collected; unsupported history explained in place.
6. Collapsed `Engine diagnostics`: latest cumulative native snapshot for the selected epoch,
   collector health/gaps, then paged raw history. No repeated full-width Splash-only block
   before the shared monitoring content.

A combined view can place source-labeled summaries side by side, but must not add cumulative
snapshots to request/accounting totals or pool incompatible metric populations. For Splash,
compact cumulative diagnostics are useful; a fabricated 7-day request/decode summary is not.

## Implementation phases

No phase is implemented by this review. Use test-first changes for new behavior and failing
regressions before fixes. Keep this one plan updated rather than creating checkpoint plans.

### Phase 1 — Identity, state and trustworthy actions

Findings: F01–F03, F12–F14, F19, the status-listener portion of F24.

- Add regression cases for 131072 with legacy `-32k` IDs, legacy 32768, stopped/unverified/
  mismatched loaded context, duplicate label context and missing profile metadata.
- Reuse/extend backend descriptions and EngineUI for human labels, configured/loaded context,
  semantic status and capability/freshness-aware actions. Preserve target wire values.
- Unify model controls, require deliberate target selection, and fix Chat click/Enter/submit/
  finally/reservation handling. Isolate status listeners from transport error reporting.
- Replace blanket usage text with supported scopes; keep raw identifiers accessible.

Files: backend_types.py/backends.py/app.py only as needed; engine_ui.js, app.js, models.js,
chat.js, app.css and owning templates. Tests: backend/status contracts, existing UI-decision
checks and browser regressions with meaningful interaction coverage.

Exit: no prominent `-32k` capacity implication for a 128K profile; context shown once;
configured is never claimed verified loaded; click and Enter obey identical eligibility;
one failing renderer does not label a successful status fetch unreachable.

### Phase 2 — Integrated Performance layout and filtering

Findings: F04–F10, F26 (Performance).

- Define the supported source/filter matrix before implementation. Add additive source
  filters/metadata where needed; keep existing request and native stores separate.
- Put common summaries/Availability first and collapse native diagnostics. Default advanced
  history to a small page (for example 20 rows) with explicit older/newer navigation.
- Add stable snapshot pagination/truncation metadata and independent profile choices; label
  current collector health separately from selected-range coverage.
- Use one immutable query state and stale-response protection. Redraw on resize without
  fetching. Render meaningful not-collected/unsupported/empty/failed states.
- Support current MLX/Splash availability through the same component. Add Strata collection
  using existing backend observations, with new coverage beginning at deployment; never
  backfill unknown periods or rewrite legacy MLX availability. If deferred, explicitly show
  `Strata availability not collected` in the shared component and retain a scoped follow-up.

Files: performance.html/js, app.css, performance routes/store queries, engine_telemetry.py,
backend_availability.py and collector wiring as required; preserve source attribution.
Tests: no native/request double counting; engine/target/range/traffic scoping; paging across
same-timestamp rows; empty/stale/disabled sources; out-of-order replies and partial failures.

Exit: with 200 and 500 returned fixture snapshots, raw history is collapsed and does not
change the collapsed page height; on 1440×900, Availability begins within the first 900 px;
on 390×900 it appears before advanced history without page-level horizontal overflow.
No false all-engine total, unsupported zero, or unqualified period delta is displayed.

### Phase 3 — Dashboard, model library and service workflow

Findings: F11, F15–F18, remaining Models aspects of F24.

- Present common current-engine facts and available Strata measurements; show backend-only
  facts in details, with explicit process/bundle memory scope.
- Restructure Models around active operation/job and serving targets; clearly separate MLX
  storage management from Strata packs and Splash profiles. Keep settings read-only where
  editing is unsupported.
- Normalize service pause reason/readiness and reuse it on Models/Decision/TTS/Embeddings.
  Update static coexistence/restoration explanations from backend policy.
- Add visible fresh/stale/loading/error states without discarding in-progress user input.

Tests: MLX/Strata/Splash/ComfyUI/off/unknown/conflict, ready/loading/released/recovering,
paused-by-each-exclusive-backend, manual stop, failed start, job progress and restoration.

Exit: no advice to start a service while policy pauses it; no intentional pause labeled a
crash; current job/result is visible near the triggering control; unsupported fields do not
dominate Dashboard; library actions clearly state their backend scope.

### Phase 4 — Chat defaults, Quality identity, Logs and accounting language

Findings: F20–F23, F25, F27.

- Show resolved Chat defaults/capacity and engine attribution; define history/Clear behavior
  across streams and engine changes without increasing total-context/output limits blindly.
- Make Quality initial choices and scopes capability-aware. Label run/compare/scorecard
  identities from stored metadata and preserve existing comparison restrictions.
- Add contextual log links, consistent follow filtering and connection feedback.
- Align range/timezone/source wording and auxiliary usage scopes. Keep direct Chat,
  engine-native counters and LiteLLM Usage distinct.

Tests: switch engines with an existing chat, actual response identity vs later status,
clear/stop during streaming, Strata defaults, reasoning caveats, current-only compatibility/
evals, ambiguous visible scorecard names, log-level filtering/reconnect and non-ops browser
timezone around midnight.

Exit: operator can identify which engine/config produced a reply or selected result without
opening unrelated pages; unsupported scopes cannot be accidentally chosen; range/accounting
scope is visible and consistent.

### Phase 5 — Accessibility, responsive layout and final regression pass

Findings: F28, remaining F24/F26 and cross-page consistency.

- Add accessible chart alternatives, page/form semantics, navigation behavior and explicit
  section-level error/freshness feedback. Preserve native controls where sufficient.
- Check mobile/desktop, light/dark, keyboard-only use, 200% zoom, long model/profile names,
  empty installations, all optional services enabled/disabled and dense historical data.
- Extend the browser suite beyond legacy 32K fixtures; retain valid historical 32K cases.
  Exercise failure/keyboard/filter transitions, not just presence of text.

Exit: all acceptance checks below pass and the plan can be archived with actual outcomes.

## Acceptance and validation matrix

| Scenario | Required behavior |
|---|---|
| Splash 128K, legacy ID | Human label and 128K shown once; raw ID only in details; target value unchanged |
| Historical/legacy 32K | Remains 32K according to its recorded/configured facts |
| Missing/mismatched loaded context | Configured limit separate; loaded limit unknown/mismatch, no false verification |
| Strata ready | Available live metrics used with correct definitions; current-only quality options clear |
| Splash released/recovering | Explicit lifecycle state; Chat/quality gating correct; native diagnostics scoped |
| Busy quality/lifecycle job | Primary actions and keyboard submissions obey reservation policy; progress visible |
| Engine ownership unknown/conflict | Safe unavailable/recovery UI; no guessed target/profile or false ready |
| One failed status renderer | Other listeners update; render failure is not called network failure |
| Poll/API failure | Last known timestamp and stale reason visible; essential actions fail closed |
| 200/500 native snapshots | Compact collapsed layout; bounded paged diagnostics; truncation/coverage explicit |
| MLX/Splash/Strata filter changes | All sections have correct declared scope; obsolete responses cannot overwrite |
| Collector idle vs stale | Last successful poll distinct from last new counters; quiet engine not called broken |
| Partial/unsupported measurements | Distinct from measured zero; no manufactured per-request Splash metrics |
| Epoch/profile/context changes | No summing snapshots; historical attribution and qualification preserved |
| Service paused by Strata/Splash | Correct owner/reason and Models link; no futile Start advice |
| Chat engine switch / Clear midstream | Preserved, attributed conversation and defined stream cleanup |
| Quality lists/comparisons | Engine/target/settings distinguish results; existing comparability guards retained |
| Follow logs | Tail/follow filter equivalence, selected source retained, reconnect visible |
| Different browser/ops timezone | Same declared day range; timestamps and auxiliary ranges unambiguous |
| Keyboard, themes, mobile, zoom | Readable controls, usable focus/labels/data alternatives; no page overflow |

Implementation commands run in the devcontainer as `vscode`, through the existing uv
environment. Required gates: `uv run pytest --cov --cov-fail-under=80`,
`uv run ruff check src tests scripts`, `uv run mypy src scripts`; relevant critical contracts
must retain full coverage. Run `uv run --with playwright pytest tests/browser -q` explicitly
because the default environment can skip the optional browser dependency. Run
`scripts/check_engine_ui.cjs` with Node when available in the devcontainer; currently Node
is absent, so Python/Chromium checks are the available review validation, not a claimed Node
pass. Verify AGENTS/CLAUDE parity, Markdown links, diff whitespace and public safety before
staging. Do not add a framework or dependency merely to rearrange existing templates.

## Boundaries, risks and deferred scope

- Preserve lifecycle reservations, native ownership verification, service restoration,
  authentication and inference-route contracts. UI gates complement existing server guards.
- No profile ID/alias migration, model installation, engine adoption/default-policy change,
  128K requalification or new benchmark campaign is included. MLX remains the default; the
  [broader qualification follow-up](../../backlog/splash-qualification.md) stays separate.
- Do not merge native snapshots into inference_requests/token_usage_daily, estimate absent
  tokens from chunks or timing, reinterpret historical availability, or rewrite frozen
  report JSON. Retain partial/failed investigation outcomes and original settings identity.
- Prefer additive response fields and read queries over schema changes. Snapshot pagination
  can use existing `(observed, id)` ordering. Audit attribution before adding engine filters
  to folded historical totals; ambiguous history must remain explicitly ambiguous.
- Site-wide visual redesign, profile editors/installers, telemetry export, durable Chat
  history and new all-client per-request Splash collection are outside this scope.
- Deployment and any live validation that switches/stops engines are separate final actions
  requiring the repository's live-change approval. Local fixture verification needs no
  live service and should be completed first.
- Proposed phases may be delivered incrementally. Record actual gate results and remaining
  scope here; archive completed implementation and leave only a concise backlog follow-up
  for genuinely deferred work. Append an appropriately sanitized devlog for each work session.

## Review outcome

The 128K configuration and shared backend contracts are already present. The main problem
is presentation still mixing MLX-centric assumptions, legacy target IDs and separately
bolted-on native diagnostics. Fix identity/action trust first, then integrate Performance
with truthful source boundaries and compact advanced diagnostics, then apply those shared
patterns to Dashboard, services, Chat, Quality and the surrounding workflows.
