# Splash 1.4.0 telemetry contract — revision 2

Phase 3 evidence audit, 2026-10-28. Revision 1 preceded ingestion; revision 2 documents the implemented snapshot collector.
The bounded live evidence below is partial; it establishes no adoption result. See the
[implementation plan](../plans/archive/splash/20261028_splash-phase3-telemetry-qualification-and-rollout.md).

## Sources and coverage

| Surface | Supported data | Coverage and limits |
|---|---|---|
| Authenticated `/status` | Native request counters, execution totals, cache counters, transport freshness/restarts, controller instance | Aggregate native activity, not request history. A successful HTTP read can contain a stale native snapshot. Rejected HTTP requests need not enter native counters. |
| Authenticated `/metrics` | Prometheus rendering of status, including native counters | Overlaps `/status`; never add the two. It does not expose Strata's request list. |
| Chat/text JSON and final SSE usage with `include_usage=true` | Request counts, cache/reasoning details; response metrics/timings | Only the recipient's request. Admin collection would cover admin traffic only; it cannot recover direct/gateway requests. Errors/disconnects may have no final usage. |
| Console `Started` / `Done` / `Cancelled` / `Error` | Rounded diagnostics, token counts on some outcomes | Time of day only; no request ID, date or profile fingerprint. Concurrent/interleaved and rotated logs cannot establish complete, attributable request history. Log viewing never inserts inference rows. |
| Stored Responses API | Individual stored responses by known ID | Neither an all-client history cursor nor a general Chat request ledger. Do not collect prompts or outputs for telemetry. |

No supported durable final-counter file/endpoint or server request-history cursor is
established. Do not add a pre-stop capture guarantee. Missing live shutdown intervals
remain gaps. All-client **per-request** accounting is unsupported by these surfaces;
all-client aggregate population/billing reconciliation remains unqualified. Controlled
native restart/epoch attribution is verified within the bounded windows below. The
canonical source is `splash`; neither an enabled setting nor readiness is proof of
collection. Coverage starts at `not_collected` (or `disabled`) and becomes
`aggregate_snapshots` only after an attributed fresh poll commits. Successful poll,
last new/ingested data, stale/error and bounded gap history are reported separately.

## Field/source matrix

A = authoritative within the stated source boundary; D = derived; U = unavailable;
V = unverified for accounting. A does not mean production-qualified.

| Field | Classification | Boundary |
|---|---|---|
| Response `id` | A | Opaque public request ID, response only; no cross-endpoint uniqueness assumption |
| `instance.id`, `pid`, `started_at` | A / D | Controller identity; must match locally verified PID/create time. HTTP is not process ownership. |
| `transport.restarts` | A / V | Controller-maintained native restart count; validate continuity against the owned native generation before accounting |
| Native generation | D | Verified complete controller/native bundle, including PID/create time; an unchanged controller is insufficient |
| Profile/fingerprint | D | Captured owning record plus matching verified configuration/generation; never today's selection applied to old data |
| Response `created` | A | Request creation, not native admission or completion. Existing scrubbed captures replace it with zero. |
| Completion timestamp | U / D | Not a native absolute timestamp in these metrics. Client receipt time needs its own name and boundary. |
| `usage.prompt_tokens`, `completion_tokens` | A | Final response totals when supplied; preserve unknowns, including on cancellation/error |
| Cached input / reasoning tokens | A | Explicit nested usage fields only. Missing is unknown, not zero; reject counts exceeding parent totals. |
| `metrics.request_latency.ttft_ms` | A | Request lifecycle TTFT, includes queue interval as defined by runtime |
| `start_to_first_token_ms` | A | Admission/start to first emission; excludes queue and is not interchangeable with TTFT |
| `first_token_to_done_ms`, `stream_tokens_per_second` | A | Request stream interval/rate; excludes the first emission's entire speculative block, not just one token |
| `wall_ms` | A | Runtime request wall interval; not client elapsed time or isolated GPU execution |
| Native prefill/decode totals/rates in status | A / V | Execution counters; not proven equivalent to billed response tokens, cached input or successful requests |
| `requests.submitted/completed/cancelled/failed` | A / V | Native population/outcomes, distinct from frontend admission/HTTP errors |
| Counter reset timestamp / lost interval tokens | U | No synthetic start/completion timestamp or made-up request row |

Numeric normalization accepts nonnegative integers for counts and finite nonnegative
numbers for timings/rates; booleans are rejected. No chunk counting, fallback zero,
wall-time decode estimate, prompt reconstruction or request-body retention is allowed.
Normalization reads an already decoded final document once, outside the token path.
Its outcome is supplied by the caller: usage alone cannot prove a completed stream.

## Attribution and persistence boundary

The implemented snapshot epoch binds `splash`, captured profile ID/fingerprint,
controller PID/create time and instance ID, native PID/create time and transport
restart count. Recheck the bundle around collection. Reject ambiguous transitions,
stale native snapshots, missing restart information and mixed-generation reads.
Treat native replacement as a new execution-counter baseline; controller HTTP
admission counters have a different lifetime. Reset/continuity validation remains
pending. Never share their baselines.

First readings, resets, disabled intervals, failed polls and stop gaps need explicit
coverage intervals. Do not reconcile native execution totals with response counts
without evidence of the same fields/population. Schema 16 adds independent engine snapshot and backend availability tables. Existing
request rows and daily folding remain unchanged; execution snapshots never enter
billed token totals. Profiles/fingerprints/epochs remain distinct in retention and
queries, avoiding the existing model-only rollup boundary. The
[operating runbook](reports/splash/README.md) specifies matching database/app
rollback and tested WAL-consistent backup/offline restore tooling.

## Provenance and remaining captures

`tests/fixtures/splash/telemetry-captures.json` contains allowlisted numeric status
projections from the retained Phase 0 session-3 initial idle captures, with original
byte digests. All projected counters are zero; these samples establish field shapes,
not workload population, reconciliation or reset continuity.
The existing [SSE captures](../tests/fixtures/splash-phase0/README.md) retain their
reasoning-off, 96-token provenance. Synthetic malformed records in tests are not live
observations. No full production status, logs, model paths, prompts or instance IDs
are copied into the fixtures.

Audit references at tag `1.4.0` in [the upstream server directory](https://github.com/incoai/splash/tree/1.4.0/server):

| File | Audited bytes SHA-256 | Relevant interface |
|---|---|---|
| `metrics.py` (retained original release audit) | `d010c426dcb275a32795c1222fa26e484f4633cf89943fe5ed8aea73cf8986a4` | `PROMETHEUS_SERIES`, `usage_dict`, `metrics_dict`, `timings_dict` |
| `backend.py` (retained original release audit) | `0ce85023177d4029942e9505a4b0f8c67dddb29a7fef37ac9c0fd15ce1b0bcd6` | `status`, `_record`, `_log`, native recovery |
| `runtime.py` (retained original release audit) | `6733f8003c4f52dfbb09b5ab885c7445f12e9c62c1cdd93eb05323362d0eeb5b` | Native process and restart lifetime |
| `server.py` (retained tagged source audit) | `32a1d5171eef82d593a8f6faa5811d3e2b67a85390640e5fa624814eb020db7f` | `do_GET`, `_openai_stream`, `FrontendServer.status` |
| `diagnostics.py` (tagged source fetched for this audit) | `e71c4753e1eeb4c445eac29f2e9b18fa9f443c272230b05aff3dfc25fa4bcfea` | `print_request`, time-of-day diagnostics |

A later approved capture worksheet must cover both profiles with reasoning on,
complete/partial/error/cancelled responses, native recovery under the same controller,
counter continuity/reset, stale status, unavailable/auth-refused endpoints and final
shutdown gaps. Verify actual authenticated gateway traffic separately. Those missing
observations stay unverified; synthetic tests cannot close them.

## Browser closeout

Chromium 156.0.8078.4 executed 34 local, fake-backed DOM scenarios using the real
Jinja templates, CSS and JavaScript at 390px and 1440px. Checks cover disabled,
starting, ready, released, recovering, unknown and conflict states; selector
retention, stock/modified profile and 32K labels, exclusion confirmation/cancel,
chat/quality/eval controls, reasoning defaults, log selection/download and enabled/
disabled Performance coverage notices. All network requests are fulfilled locally;
unexpected requests, uncaught JS errors and mutations fail the harness. This is
presentation evidence, not live browser-to-runtime/gateway compatibility.

The narrow released-state check failed before wrapping the header controls; it
passes after the CSS fix. Narrow released and wide conflict screenshots were
visually reviewed; synthetic screenshots stay in ignored `.smoke/splash-phase3-browser/`.

Run with the existing uv project environment plus an ephemeral tool dependency:

```bash
uv run --with playwright playwright install --with-deps chromium
uv run --with playwright pytest tests/browser -q -s
```

The ordinary pytest run skips this optional browser module when Playwright is absent.
Browser validation must therefore also be run explicitly with the command above.

## Collector and monitor implementation

`SplashClient` binds the native reading only after locally verified observations
on both sides of HTTP agree, instance/model match, native freshness is explicit and
exactly one controller/native pair is owned. `SplashMonitor` polls independently;
one pending operation survives timeouts, preventing replacement workers from
accumulating. Disabled mode starts no task or HTTP calls. Readiness expires after
three intervals and is matched to the supervisor's own generation. Storage failure
is a telemetry error, separate from loaded serving readiness.

`EngineTelemetryStore` commits changed snapshots and bounded cursors/gaps atomically,
rejects late polls and same-epoch regressions, and retains profile identity in JSON
exports. Identical snapshots are deduplicated across restart/replay. Changed native
generations start new baselines; admin observation gaps remain visible. It makes no
reset-population reconciliation or complete-usage claim. Retention/query/worker
bounds and the separate availability denominator are specified in the runbook.

Performance exposes profile-filtered cumulative history, unmeasured counts as dashes,
stale/error/gaps and source-scoped availability. Diagnostics/alerts use registry
labels and the cached monitor result, with grace for transitions and no Splash admin
automatic restart. The dashboard identifies verified process RSS sums and partial
bundles while preserving legacy primary-process metrics. No token-path DB work,
extra per-token copies, response collection or request-body logging was added.

Stage A installed the revision-pinned runtime/profiles and schema16 admin. The subsequent
[corrective window](reports/splash/readiness-fix-report.md) verified actual stock
controller/native ownership, loaded readiness and direct complete streaming. Three stock
cumulative readings share one verified epoch; unchanged idle counters retained the same
reading IDs over57 seconds. Complete process RSS summation and a primed CPU sample were
observed. The aborted gateway stream produced an incidental native cancellation; no
formal cancellation qualification or complete request-population claim follows.

Ten availability observations are retained across attempts. Supplemental exact-window
exports separate the newest observations from the prior six. Calendar API aggregates
retain their date scope; they are not relabelled as the bounded window. Stock restart,
modified serving, recovery/reset continuity,32K integrated checks, full gateway behavior
and measured overhead remain unverified. Source inspection confirms that the installed
gateway restamps stream models to public names, while direct Splash reports its primary
name. A strict endpoint-specific client is prepared for the next approved window.
Private target details, snapshots and source audits remain ignored.

A missing counter field remains null in the snapshot while retaining the last known
monotonic validation baseline. Reappearance below that baseline is refused; missing
fields cannot erase evidence of a same-epoch regression. This baseline is a
validation guard, never a replacement value in exported measurements.

Snapshot `observed` and poll timestamps use the admin poll-start wall clock,
not inferred native admission/completion times. They conservatively include collection
delay in freshness checks. A late/hung operation cannot start a second collection.

Expired HTTP results cannot revive timed-out readiness or insert snapshots. A storage
timeout preserves timely serving readiness and reports a separate telemetry timeout.
Profile/fingerprint availability reports retain all observed switch boundaries; excluded
profiles become unknown intervals rather than extending a prior profile through a switch.


## Approved functional capture — 2026-10-28

The [endpoint-contract window](reports/splash/gateway-contract-report.md) now supplies
both-profile reasoning-on direct/gateway complete streams, four exact near-limit retrieval
checks and four disconnect observations. All29 scheduled HTTP attempts pass with separate
runtime cancellation evidence. Eighteen new cumulative snapshots span stock, restarted
stock and modified epochs, with verified fingerprints and idle deduplication. Twelve new
availability observations remain separate from ten older observations; broad calendar
aggregates retain their original scope. Counts/population/reset reconciliation and
all-client request history remain unqualified.

The final stop failed and left a dead ownership receipt; complete resource absence and
original restoration were established through verified metadata archival. No durable
final-counter guarantee, corrected lifecycle pass, forced-recovery/reset proof or measured
overhead follows. MLX remains active and Splash disabled. Raw synthetic response bodies,
private operator identities and source snapshots remain ignored.


## Shutdown correction attempt — 2026-10-28

The [host correction execution](reports/splash/shutdown-fix-report.md) added two stock
epochs with four fresh readings, correct attribution and unchanged idle reading IDs.
The first normal off passed; the second failed and retained a dead receipt despite both
processes and listener exiting. Modified cycles were not attempted. No corrected lifecycle,
final-counter, forced-recovery/reset or overhead claim follows. The emergency archive
refused a timestamp-format mismatch before mutation. The separately approved helper then
preserved and archived the wholly dead receipt without signals. MLX/services/intents,
disabled Splash, health and original gateway inference/routing are now verified. Final
schema16 integrity is `ok`, with25 readings and41 availability observations. Nineteen new
availability rows include the interrupted interval; historical22 rows retain their scope.
No database restore or observation loss occurred; the original failed stop remains failed.


## Proven Mac exit correction — 2026-10-28

The [new correction](reports/splash/macos-exit-fix.md) captured the actual ?E/P_WEXIT
teardown exception, reproduced it in seven failing tests and waits for independent ESRCH
before treating that exiting PID as absent. Four normal stock/modified lifecycle cycles
passed after deployment. Six fresh readings span four distinct correctly attributed epochs;
idle reading IDs are unchanged. Eight new availability observations are separately scoped.
Final schema16 integrityok,32 readings/51 availability rows, original MLX/services/intents,
disabled Splash and gateway inference/routing are verified. This is normal lifecycle
proof, with full matrix/native recovery/admin acceptance/comparison still pending. It
adds no all-client usage, final-counter, reset or physical reclamation guarantee.


## Native recovery and admin acceptance — 2026-10-29

Both profiles passed [native/admin acceptance](reports/splash/integration-acceptance.md).
Each observed ready invalidation and one runtime-owned native restart under an unchanged
controller/instance, with complete followup and successful normal off. Nine new readings
span four epochs, preserving old/new native identity, profile/fingerprint and restart
boundaries. Idle reading IDs remained unchanged and post-recovery bundles include only
current members. Twelve new availability samples retain their independent window scope;
manual recovery's lifecycle reservation excludes its maintenance interval. Final DB16
integrityok/41readings/67availability and original service/gateway restoration pass.
Current-only chat/compatibility/eval/history, reported usage, cancellation and logs work;
vision is unsupported. No complete all-client usage, final-stop capture, every-interleaving
recovery, physical reclamation or measured overhead claim follows from these windows.


## Final dedicated-endpoint support — 2026-10-29

Both profiles are enabled/selectable; original MLX remains default. The deployed endpoint
binds serving port to ownership and refuses admin mismatches, with every GPU exclusion
preserved. Four normal cycles, twelve directed engine pairs, two native/admin recovery
windows and18commissioning cases pass within their scopes. Direct/gateway reasoning,
content/final usage, four exact31873-token context checks, cancellation propagation and
followup, key-scope refusal, and inactive-route refusal with unchanged original generation
are verified. Native child recovery creates a new attributed epoch with the same controller;
idle snapshots deduplicate and current two-member bundles replace old child generations.

| Supported surface | Final qualification boundary |
|---|---|
| Recipient response usage/reasoning/content | Complete direct/admin/gateway streams; cancelled streams can lack final usage |
| Native cumulative snapshots | Profile/fingerprint/controller-native epoch attribution, observed reset and idle deduplication; never sum overlapping snapshots |
| Backend availability | Observed loaded serving/intended time, explicit unknown gaps; no changed MLX denominator |
| RSS/CPU bundle | Current verified members, summed process metrics; shared pages can double-count |
| All-client request/billing ledger | Unsupported; no inferred rows, chunk-count tokens or daily billing reconciliation |
| Final-stop counters/physical reclamation | No guarantee; missing shutdown intervals stay gaps and stock host reclamation remains inconclusive |

Final schema16 integrity is `ok`:85native readings/109availability rows retained. Feature
support deployment is complete; performance adoption remains inconclusive. The prior
[frozen comparison](reports/splash/integrated-comparison-report.md) is partial and is
not rescored from commissioning traffic. Final dedicated-endpoint performance is unassessed.
Vision remains unsupported and reasoning-off quality unqualified. See the
[rollout report](reports/splash/rollout-report.md) for exact supported scopes and rollback.


The subsequent [128K context rollout](reports/splash/context-128k-report.md) preserves
profile IDs while changing their limits and fingerprints. Keep prior 32K epochs and
measurements attributed to their original fingerprints. This change adds no all-client
usage ledger and does not complete the frozen performance/adoption comparison.
