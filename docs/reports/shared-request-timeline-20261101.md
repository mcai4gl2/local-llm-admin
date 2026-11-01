# Shared gateway request timeline rollout

Date: 2026-11-01
Result: Deployed and qualified for the bounded shared-gateway workload.

The approved rollout ran the fixed workload in
[`experiments/gateway_timeline`](../../experiments/gateway_timeline/README.md)
before and after deploying migration 18. Both phases used the same payload and
schedule: two warmups, 12 serial streams, six concurrent pairs, and four streams
closed after the first data event. Raw samples and the verified pre-upgrade schema 17
snapshot remain in private storage. The snapshot passed SQLite integrity checking.
Five sampled installed files (timeline, gateway and admin modules, template, and
script) matched the staged revision byte for byte. A checksum dry run found no
differences in the deployed source tree, project metadata or lockfile.

| Case and measure | Before | After |
|---|---:|---:|
| Serial first content, p50 / p95 | 0.1321 / 0.1345 s | 0.1322 / 0.1375 s |
| Serial total, p50 / p95 | 0.2258 / 0.2283 s | 0.2260 / 0.2330 s |
| Concurrent first content, p50 / p95 | 0.2486 / 0.3359 s | 0.2474 / 0.3337 s |
| Concurrent total, p50 / p95 | 0.3436 / 0.4300 s | 0.3424 / 0.4276 s |

Each phase completed all 24 measured serial/concurrent requests without request
failure; all four planned stream closures returned a successful response before
closing. First-event and first-content timings were equal for this short text workload.
These are observed client latencies, not a causal estimate of tracing overhead. Engine
load, cache state, restart state, and other traffic may have differed between phases.

The deployed admin passed `/healthz` on schema 18. The authenticated timeline list,
detail, health API, Performance page and script responded. Anonymous timeline API
access returned 401. All 28 post-deploy sample IDs were unique and resolved in both
list and detail APIs: 24 completed and four disconnected. All 28 recorded first-event
and first-content times. Cursor paging, exact request-ID filtering, backend/outcome
filtering and the gateway-only coverage label passed. A non-streaming completion and a
model rejection returned distinct server-generated IDs and correct terminal records;
their detail records contained neither the test prompt nor a caller-provided ID. The
writer reported zero pending, dropped and failed writes. MLX reported zero in-flight
requests and zero request/audio queue depth after the run.

The initial verification script assumed a `rows` list key and a route URL where the
API uses `requests` and a symbolic route value. Its script check also looked for a
generic `fetch` call, while the page uses the shared `api` helper. A first rejection
probe expected 400 while the served-model contract returns 404. Those checks failed
as written; corrected checks passed against the same deployed revision. No application
rollback was needed. Direct native, admin-chat and quality traffic remain outside the
timeline's population; the native idle reading is an independent point-in-time check.
