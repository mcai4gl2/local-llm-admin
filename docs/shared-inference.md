# One unchanged LiteLLM endpoint for every LLM engine

The shared OpenAI entry point listens on `http://mac-mini.local:8080/v1`, the existing
MLX/Strata client URL. It follows the selected verified, ready engine. LiteLLM keeps its
existing API base, public route name and upstream model ID when switching between MLX,
Strata and either Splash profile.

## Internal topology

The shared listener owns port 8080. Only native engine addresses change, behind it:

| Service | Example internal address |
|---|---|
| MLX | `http://127.0.0.1:8086` |
| Strata | `http://127.0.0.1:8087` |
| Splash | `http://127.0.0.1:8085` |

This separation prevents an inactive Splash or Strata model request from reaching MLX,
which can unload its selected model before rejecting a foreign model. No inference
runtime patch is needed. Owning-wrapper exclusion still permits only one GPU engine.
The listener itself is not a native/GPU owner and is never stopped by engine switching.

## Private configuration

Copy the [native-port example](../host/etc/engine-ports.example.json) to the Mac's
`/Volumes/Models/etc/engine-ports.json`, mode 600. The owning wrappers parse this file;
they never source it. With no file, legacy native port 8080 behavior is preserved.
A configured topology requires distinct valid ports and rejects existing service ports.
The file is private deployment configuration; the versioned deploy manifest includes
only the example and helper. It never installs or overwrites the actual configuration.

Set matching private admin settings:

```dotenv
INFERENCE_API_KEY=<the-key-already-sent-by-LiteLLM>
INFERENCE_PORT=8080
INFERENCE_HOST=0.0.0.0
INFERENCE_MODEL_ID=local-model
MLX_PORT=8086
MLX_API_BASE=http://127.0.0.1:8086
STRATA_PORT=8087
STRATA_API_BASE=http://127.0.0.1:8087
SPLASH_PORT=8085
SPLASH_API_BASE=http://127.0.0.1:8085
```

Match Splash's private `etc/splash.env` port to the topology and use a loopback native
bind. Its native API key remains the private admin `SPLASH_API_KEY`; the shared listener
supplies it. The frontend key must match the key already configured on the LiteLLM route,
so credentials do not need to change there either. An empty frontend key disables this
listener. Startup refuses a collision with a native/admin/sidecar port or a native API
base that disagrees with its configured port. It also checks the installed host helper
and private native-port file before binding; missing, invalid or mismatched host
configuration refuses startup rather than leaving wrappers aimed at the public listener.

The existing upstream ID `/Volumes/Models/models/active` remains accepted for all three
engines. `local-model` is an optional stable alias for new clients. The entry point
translates either to the selected native model and uses the caller-facing ID in responses.
`GET /v1/models` lists the stable IDs and the currently active native model. Inactive
engine-specific IDs return 404 before inference dispatch. Clients never request a new
weight load through this API.

## API behavior and bounds

Supported routes: authenticated `GET /v1/models`, `POST /v1/chat/completions` and
`POST /v1/completions`. Streaming, reasoning, usage and tool-call fields supplied by the
selected runtime pass through. Native runtimes remain responsible for capabilities and
context limits; selecting Splash does not add vision. Admin cookies do not authorize
inference, and an inference key grants no access to admin control routes.

No ready engine, unknown ownership, conflicting engines, a disabled backend or an active
engine operation returns 503. There is no engine start, fallback, replay or automatic
retry. Native errors retain their HTTP status with sanitized text. Streamed model fields
are translated incrementally; the response is never collected before streaming begins.
Disconnect closes upstream and releases admission. Each backend has a configurable
concurrency limit (`INFERENCE_MAX_INFLIGHT_MLX`, `INFERENCE_MAX_INFLIGHT_STRATA`,
`INFERENCE_MAX_INFLIGHT_SPLASH`); the compatibility default is two for each. Excess
requests receive 429. Bodies/non-streaming responses are limited to 8 MiB and an
individual streamed event to 2 MiB. No per-token database writes or duplicated billed
usage records are introduced.

## Shared gateway request timeline

The Performance page has a separate Shared gateway timeline. It records metadata for
`POST /v1/chat/completions` and `POST /v1/completions`, including rejected requests.
This population excludes direct native requests, admin chat and quality runs. A missing
row is never evidence that the native engine was idle. The page displays the durable
coverage start and current-process pending, dropped and failed write counts. The
authenticated APIs are `GET /api/inference/timeline`,
`GET /api/inference/timeline/{request_id}` and
`GET /api/inference/timeline/health`. The list accepts operations-calendar `from` and
`to` dates, exact `request_id`, `backend`, `outcome`, `limit` (1–200) and integer `before`
cursor. Rows are ordered by insertion ID, so later inserts cannot shift an older page.

The gateway assigns a random internal request ID and returns it as `X-Request-ID` on
completion responses, including refusals. Incoming request-ID headers are ignored.
Resolution captures the backend, a safe model label, profile fingerprint and a digest
of the verified generation once. The ledger stores no prompts, output, image/document
bodies, credentials, caller identifiers, raw headers, client addresses or arbitrary
upstream error text. The requested model is stored only after it matches a served public
ID. Path-shaped model IDs are reduced to their final name. No raw native model path or
process generation is stored.

`started`, `admitted_at`, `dispatched_at`, `headers_at`, `first_event_at`,
`first_content_at` and `ended_at` are wall-clock timestamps. Durations use a monotonic
clock. The UI shows time from dispatch to upstream headers, first parsed SSE data event
and first nonempty text delta.
The last is a first-content observation, not a tokenizer-level timing. Reasoning,
tool-call and empty deltas leave it null; non-streaming responses cannot expose this
timing. Usage contains only nonnegative integer prompt/completion counts supplied by the
upstream response. `usage_source=upstream_response` and `usage_completeness` distinguish
complete from partial counts. These figures are diagnostic and are never added to LiteLLM
billing or the existing native token rollups. A failing response with usage remains an
error. A request left running at admin startup is marked `interrupted`, without inventing
an end time or success.

The writer queues only a start and terminal snapshot per request, with a bounded default
capacity of 256. Full or failed writes increment health counters and do not block
inference. Set `INFERENCE_TIMELINE_QUEUE_SIZE` (16–4096) and
`INFERENCE_TIMELINE_RETENTION_DAYS` (7–365, default 90) in the private admin environment
if needed. Terminal records older than retention are pruned in batches of at most 500;
active rows are retained. The health counters reset when the admin restarts. Check the
health API and correlate the returned `X-Request-ID` with the detail API when diagnosing
a request. If dropped or failed writes rise, expect gaps and inspect the admin log and
SQLite disk state; the timeline never proves full native quiescence.

The additive schema is migration 18 in `admin.sqlite`. Before deploying over a v17 app,
use the installed admin environment's `python -m local_llm_admin.db_snapshot backup`
with explicit `--database` and a new private `--output` path. This online backup includes
committed WAL pages and verifies integrity. The existing `ops/rollback-mac.sh` restores
application code, not the database: a v17 app refuses a v18 database. For rollback,
boot out the admin LaunchAgent and wait for all database writers to stop. Run
`python -m local_llm_admin.db_snapshot restore --expected-version 17 --offline` with
explicit `--snapshot`, `--database` and a new private `--preserve-current` path. Restore
the previous application source and bootstrap its LaunchAgent, then verify `/healthz`,
gateway inference and native ownership. The code-only rollback script assumes a loaded
LaunchAgent, so use the manual source/bootstrap steps for this offline schema rollback.
Restoring the snapshot discards later database writes from the active copy; retain the
preserved v18 database separately for investigation or history recovery. Never present a
code-only rollback as complete when the matching database has not been restored.

## Drain before engine changes

Manual model start, stop and switch, engine selection, and quality-run entry reserve the
job slot, then close shared inference admission. Already admitted requests count through
backend resolution, upstream opening, response streaming and upstream cleanup. The job
waits up to `INFERENCE_DRAIN_TIMEOUT_S` (120 seconds by default) before starting any
engine-changing step. New requests receive HTTP 503 with a bounded `Retry-After` hint;
the hint is a retry interval, not a promised switch completion time. A full backend
concurrency pool still receives 429.

The Models page shows the admission state, active gateway count, elapsed wait and
deadline. `POST /api/inference/drain/cancel` cancels only a pending drain. A timeout or
cancel before transition leaves the source engine and intent intact. After a job begins,
its existing rollback and quality cancellation rules apply. Failed or unrecorded
restoration holds admission unavailable until a later verified engine action succeeds.
An unavailable marker is persisted before an engine-changing job is submitted and
cleared only after a verified result. It is restored after an admin restart;
interrupted engine jobs also start with admission unavailable.
The Engine selector's existing `force` flag is for backend queue policy; it does not
bypass this gateway drain.
An operator can inspect `/api/inference/drain`, the job record, observed ownership and
native metrics before retrying. Restarting the admin does not clear a recorded
unavailable state and cannot prove that native work has ended.

This barrier covers only requests entering the shared `/v1` gateway. The admin chat and
quality runner call native APIs directly; quality takes the barrier before its own run.
Resident services use separate APIs and policies. Direct native callers bypass the
gateway and cannot be counted. A zero gateway count therefore means only that registered
gateway transport cleanup completed. A disconnected or malformed native stream may keep
computing after transport close; the Models page counts such unverified cleanups, but the
admin cannot establish native quiescence across MLX, Strata and Splash from that count.
For MLX, inspect `/metrics` `summary.in_flight`, server queue depth, and the native
log's cancellation markers. For
Strata, inspect `/metrics` live state and queued work; its exposed fields do not prove
that a cancelled decode stopped. Splash currently has no comparable authoritative
per-request idle signal in the admin. Check native process state during a live
qualification or incident, and treat missing evidence as unknown. The watchdog uses
the same bounded drain for a restart; a failed drain leaves the existing engine alone
and reports the refusal through the watchdog job path. It does not force a stop.

For a drain-only rollout, inspect `/api/inference/drain` and native activity before
`ops/deploy-mac.sh`. Deploy the admin with its existing private listener settings, then
check `/healthz`, gateway inference, Models progress and job history. Qualify a held
stream against a planned same-engine restart, a cancelled wait, a deadline refusal and
a target-readiness failure in a controlled window. Record which native metrics could
confirm cleanup and which traffic bypassed the listener. If the new admin fails to
start or cannot route inference, use `ops/rollback-mac.sh` with the saved prior app;
verify the original engine and the shared listener afterward. Rollback cannot undo an
engine transition that already ran, so inspect current ownership before retrying.

## Migration and rollback

During an approved quiet window, record the original engine/service intent, freeze new
jobs and stop the admin/watchdog so it cannot restart an engine during migration. Stop
the current native engine through its owner and prove its processes and listener absent.
Back up the deployed source, private admin/native environments and native port configuration.
Deploy the paired host/admin changes, install the private topology and matching admin
settings. Start the original selected native engine on its internal port, then start
the admin and shared listener before unfreezing jobs. The native engine moves
behind the listener while the external URL, request model and LiteLLM route stay unchanged.

Verify original inference, each selected engine/profile, both streaming and non-streaming
responses, invalid-key/model refusal, cancellation and owned cleanup. Keep existing
qualification failures and old results scoped to their original deployed revision.
A branch update performs none of these live changes. Roll back by stopping the new
listeners/owners and restoring the matched source/configuration and original native port
before restarting the original engine. Do not bind the shared listener and a native
engine on 8080 simultaneously. The historical rollout reports do not qualify this new
entry point live.


## Verified deployment record

The [incident and deployment verification](reports/shared-inference-20261029.md) records
activation of the paired shared listener, the prior foreign-model unload failure, and
bounded functional checks across MLX, Strata and both Splash profiles. Use the configuration
and migration instructions above for current operations; the dated report is evidence for
its pinned revision, not a substitute for a later deployment preflight.

The [latest-code maintenance record](reports/latest-code-deployment-20261030.md) documents
the subsequent vision-error correction deployment, payload verification and current MLX
smoke tests. Its installed-module check does not establish live Strata image support.

The [drain-before-switch rollout record](reports/drain-before-switch-20261101.md) documents
the deployed barrier and bounded live streaming, cancellation, drain-timeout and
failed-readiness rollback checks. Its gateway-only coverage limit still applies.

The [shared gateway timeline rollout record](reports/shared-request-timeline-20261101.md)
documents the schema backup, paired workload, correlation checks and persistence health.
Its client latency comparison does not isolate tracing overhead.


## Strata request troubleshooting

A successful switch and healthy listener confirm loading; they do not guarantee that
all request types are supported. A Strata installation without its vision encoder
rejects image input with HTTP 400. The frontend translates that recognized rejection
into a fixed explanation without forwarding arbitrary upstream error text. Enabling
vision is a separate native installation/configuration step.

Strata can also spend minutes reasoning while later requests wait for its decode slot.
Without an output cap, its native default can permit most of the remaining context as
output. Automatic client retries can repeat that work after the frontend read timeout.
Use a bounded text-only smoke test to distinguish loading/routing from generation policy:

```json
{
  "model": "/Volumes/Models/models/active",
  "messages": [{"role": "user", "content": "Reply with OK."}],
  "max_tokens": 32,
  "chat_template_kwargs": {"enable_thinking": false}
}
```

For a reasoning request, choose an explicit output limit and, when appropriate,
`reasoning_budget_tokens` to reserve room for an answer. A five-token reasoning test can
finish without answer text. Neither the frontend nor this troubleshooting guidance
silently changes the caller's generation settings or installs model components.
