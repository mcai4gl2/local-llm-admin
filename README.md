# local-llm-admin

A self-hosted web admin for an Apple-silicon Mac mini that serves local LLMs with [MLX](https://github.com/ml-explore/mlx) (and ComfyUI, an optional decision/TTS sidecar slot and an embedding service). It covers: status, system metrics, model switching, a test chat, logs, and local-model token usage from LiteLLM. The design is in [docs/DESIGN.md](docs/DESIGN.md). Design notes written before this repository existed are not published (cited below as "pre-split design notes"); [docs/DESIGN.md](docs/DESIGN.md) is the design reference. New plans go in [plans/](plans/README.md).

It assumes one Mac with the MLX tooling under `/Volumes/Models` (see [host/](host/README.md)), a LaunchAgent for the admin, and optionally a [LiteLLM](https://github.com/BerriAI/litellm) gateway for usage reports. Addresses in this repository are placeholders: `mac-mini.local` for the Mac, `192.0.2.x` (RFC 5737) for other LAN hosts.

## Develop (in the devcontainer)

```bash
uv sync --all-extras
.venv/bin/pytest --cov          # unit + integration tests
.venv/bin/ruff check src tests && .venv/bin/mypy src
```

## Run

```bash
.venv/bin/local-llm-admin-hash                 # prints ADMIN_PASSWORD_HASH=...
.venv/bin/local-llm-admin --env-file <env>     # serves on :8090
```

Configuration comes from environment variables; see [ops/env.example](ops/env.example). On the Mac the file is `~/.config/local-llm-admin/env`.

## Deploy (needs approval)

The deploy scripts never assume a host: set `MAC_HOST` (ssh target) and `MAC_DEST` (install folder) in the environment, in `ops/deploy.env` (gitignored; copy [ops/deploy.env.example](ops/deploy.env.example)), or in a file named by `DEPLOY_ENV`.

`ops/deploy-mac.sh` rsyncs the source to `/Users/you/local-llm-admin/app`, runs `uv sync --frozen --no-dev`, installs the LaunchAgent, and checks `/healthz`. `ops/rollback-mac.sh` restores `app.prev`.

Host scripts under `host/` deploy separately with `ops/deploy-host.sh` (dry run by default; see [host/README.md](host/README.md), including the one-time upgrade from the legacy `mlx-model`). Deploy the host scripts before an admin version that calls `mlx-model unlock` or, for quality runs that start from off, `mlx-model select`.

Before any deploy, back up the admin database consistently (stop the admin, or use SQLite's backup API):

```bash
ssh you@mac-mini.local 'sqlite3 "$HOME/local-llm-admin/data/admin.sqlite" ".backup $HOME/local-llm-admin/data/admin-$(date +%Y%m%d-%H%M%S).sqlite"'
```

Schema changes are additive and versioned (`PRAGMA user_version`); an older app refuses a newer database instead of guessing. To roll back, cancel any quality run (Quality tab) and wait for it to finish restoring, set `WATCHDOG_ENABLED=false` (or stop the admin), take the backup above, and keep the database and its alert/job/quality history. An M1 app (schema 3) refuses the M2 database (schema 4). Schema 4 only adds the `quality_*` tables, which M1 never reads, so with the admin stopped, lower the version first and then restore the app:

```bash
ssh you@mac-mini.local 'sqlite3 "$HOME/local-llm-admin/data/admin.sqlite" "PRAGMA user_version = 3"'
ops/rollback-mac.sh
```

Re-deploying M2 re-applies schema 4 idempotently and keeps the stored results.

Schema 9 (2026-10-01) only adds `log_cursors.head`, a fingerprint of each log's first 4 KiB, so a drive remount that changes the device number is not mistaken for a new file and replayed. To roll back to schema 8: stop the admin, take the backup, `PRAGMA user_version = 8`, then `ops/rollback-mac.sh`. The older app ignores the column.

M4 (schema 5) only adds `inference_requests` and `availability_samples`, plus `mlx-perf` rows in `log_cursors`/`kv`. To roll back to M2: stop the admin (this stops request ingestion, availability sampling and the rotation scheduler), take the backup, then `PRAGMA user_version = 4` and `ops/rollback-mac.sh`. The tables, cursors and rotated `logs/*.YYYYmmdd-HHMMSS` archives stay for recovery; re-deploying M4 continues from the stored cursor. Deploy the host scripts (`mlx-model rotate-logs`) before an M4 admin, or its daily rotation job fails and raises `log_rotation_failed`.

M3 adds schema 6 (`model_inventory`), 7 (`downloads`) and 8 (`quality_cases.profile_fingerprint`), all additive. Deploy the host files first (`mlx-model` with `remove`, `download*` and `profile`, plus the new `bin/mlx-download` and `bin/mlx-profile`), then the admin. To roll back to M4: cancel or let any download finish (it runs on the Mac without the admin and is not stopped by an admin rollback), stop the admin, take the backup, `PRAGMA user_version = 5`, then `ops/rollback-mac.sh`. The new tables and column stay. The host files can stay: the previous admin never calls the new commands, and `start` without a profile uses the built-in flags. Restoring the previous `mlx-model` (its `.bak-<stamp>` from the deploy manifest) is only needed to undo the start-time profile flags; saved profiles, `downloads/` and `quarantine/` are never deleted by a rollback. Deleted weights cannot be recovered by any rollback.

## Resilience (M1)

- **Alerts** tab: open alerts (acknowledge hides them from the badge, not from the list, until they resolve), the diagnostics list, and 90 days of history.
- **Diagnostics** (`GET /api/diagnostics`): drive, drive access, LiteLLM reachability, `mlx.lock` ownership (with **Unlock**), engine conflict, memory pressure, watchdog, intended engine and grouped request errors. Each check is time-boxed; a hung probe reports `unknown`/`stale` instead of piling up threads.
- **Watchdog**: restarts MLX once after a confirmed crash; a second crash within 30 minutes, a failed restart, or missing log evidence suspends it (**Resume** on the Alerts tab). A graceful stop outside the admin sets the intended engine to off. ComfyUI is alert-only. `WATCHDOG_ENABLED=false` keeps alerts and disables restarts.

## Quality runs (M2)

- **Quality** tab: benchmark (speed and memory) or compatibility (API features) for the current model, all registered models or one model. It shows the effect on the running engine before starting, per-model progress, cancellation and the restoration outcome.
- A run is one job (`source=benchmark`) that holds the lifecycle slot. Manual model actions and test chat return 409 until it ends, and the watchdog does not act on it. Models are switched with `mlx-model` inside the run, and the run returns to the original model, or to off with the original selection (`mlx-model select`, which needs the host scripts deployed).
- Benchmark: cases of about 128, 2k and 8k tokens with a fresh prefix, plus a repeated-prefix and an extended-conversation case. Each runs one warm-up and three measured repeats at temperature 0 with thinking off. Token counts and rates come only from the server's usage report and are shown as unavailable otherwise. Peak RSS is the server process only. A repeat that overlaps another client's request (server `/metrics` counters) is excluded. A run whose counters cannot be read is uncontrolled and never compared.
- Regressions (>15% worse medians) are flagged only against the latest baseline with the same weights fingerprint, case, effective settings, server settings fingerprint and verified cache state.
- If someone changes the engine during a run, the run stops without restoring (`quality_intervention`). A failed restore raises `quality_restore_failed`. Both set the intended engine to unknown and suspend the watchdog until **Resume**.
- API: `POST /api/quality/runs` `{kind, scope, model?, confirm_off?}`, `GET /api/quality/runs`, `GET /api/quality/runs/{id}`, `POST /api/quality/runs/{id}/cancel`, `GET /api/quality/latest`. All require login and CSRF.

## Evals (M10)

Speed is not quality. Evals measure how good each model's answers are, with repeatable suites, and show a per-model scorecard and a paired comparison. They run on the **Quality** tab as a third run kind (`kind=eval`), so they hold the lifecycle slot, switch models, cancel and restore exactly like benchmarks (see "Quality runs (M2)").

- **Suites**: `core-v1` (25 deterministic cases: instruction following, extraction, arithmetic, reasoning, tool calls), `writing-v1` (5 cases graded by a judge on a 1 to 5 rubric) and `needle-v1` (long-context retrieval: an access code hidden at 10%, 50% and 90% depth, generated per run; lengths above the loaded context are skipped, using the running engine's context). Import a custom suite as JSONL (up to `EVAL_IMPORT_MAX_BYTES` and `EVAL_IMPORT_MAX_CASES`) and export any non-generated suite; `needle-v1` cannot be exported.
- **Graders**: `exact`, `regex`, `contains_all`, `choice`, `number`, `json_fields`, `tool_call` (deterministic) and `judge`. Custom regexes are limited (length, applied to a capped prefix of the answer) and refused at import when a repeated group could backtrack badly: a repeat containing an alternation (`(a|aa)+`), a repeat containing a repeat that is unbounded or bounded above 100 (`(a+)+`, `((a+))+`, `(a+){1,999}`), or a bounded one whose body can split more than one way (`(a+){4}`; `(\s+[^\s.]+){4}` is fine). Grading runs off the event loop. A thinking model that stops while thinking scores 0 ("no answer"), including a thinking-on reply cut off by the token limit with no closing `</think>`; its reasoning text is never graded (`<think>` blocks, and everything up to a closing tag the template opened in the prompt).
- **Generation**: temperature 0, seed 0, one sample per case, thinking off unless chosen in the dialog (then up to `EVAL_THINKING_MAX_TOKENS` per answer). Responses are stored, so a run can be re-graded without asking the model again. The per-request timeout is the larger of `QUALITY_REQUEST_TIMEOUT_S` and `max_tokens / 5` seconds, still bounded by `EVAL_RUN_DEADLINE_S` (default 12 h).
- **Estimate**: the start dialog shows the expected duration per model (from the latest benchmark medians, else defaults) and the judge that will be used (or "none"). For `needle-v1` it uses the current engine's context for every target model.
- **Judge** (`EVAL_JUDGE`): `none` (judge cases are left ungraded), `external` (an OpenAI-compatible endpoint: `EVAL_JUDGE_BASE_URL`, `EVAL_JUDGE_MODEL`, `EVAL_JUDGE_API_KEY`, the key goes only into the request's Authorization header) or `mlx:<registered name>` (the run switches to it once, after every candidate has answered). A candidate judged by itself is flagged **self-judged**. A judge that answers prose is retried once, then the case is `judge_error`: excluded from the score and counted, never scored 0. On Strata only an external judge (or none) is possible.
- **Scores**: mean with a 95% bootstrap interval over cases (2000 resamples, seed 0), per category. Cases that failed to run or were not graded are excluded and counted. A run's scores become the model's "latest" only when the whole run finishes cleanly (including the judge phase); a cancelled, timed-out or intervened run keeps its responses but promotes nothing, and a run with no graded case promotes nothing. A model with any `failed` case (a request that timed out or errored) is not promoted either; its summary still shows on the run.
- **Scorecard**: one row per weights fingerprint with the latest score per suite and thinking setting (cells `<suite>` for thinking off and `<suite>+thinking` for thinking on, each naming its suite version, judge and whether it was self-judged), decode and prefill medians and peak memory from the latest benchmark. Strata scores are keyed `strata-<hash of the served model id>` and have no speed. The Models tab inventory shows the thinking-off `core-v1` and `writing-v1` scores in a **Quality** column (MLX models only; Strata scores appear on the Quality tab scorecard, not there).
- **Comparability rule**: two results (any two models or runs) compare only if the suite id and content hash and the effective settings (thinking, launch profile, runtime fingerprint) match, and, for a suite with judge cases, the same judge graded both sides. Otherwise the compare view refuses with the reason. A side graded by itself is flagged self-judged. Strata and MLX results never compare: their runtime fingerprints differ. The paired comparison reports wins, losses and ties, the mean difference with a 95% interval, and the cases where the models disagree; the verdict is "better" or "worse" only when the interval excludes 0 and at least 10 cases are shared (`MIN_PAIRED_N`), otherwise "no clear difference".
- **Settings**: `EVAL_RUN_DEADLINE_S`, `EVAL_THINKING_MAX_TOKENS`, `EVAL_JUDGE`, `EVAL_JUDGE_BASE_URL`, `EVAL_JUDGE_MODEL`, `EVAL_JUDGE_API_KEY`, `EVAL_IMPORT_MAX_BYTES`, `EVAL_IMPORT_MAX_CASES` (see `ops/env.example`).
- API (login and CSRF required): `POST /api/quality/runs` with `{kind: "eval", scope, model?, suite, thinking}`; `GET /api/evals/suites`, `GET /api/evals/suites/{id}`, `GET /api/evals/suites/{id}/export`, `POST /api/evals/suites` (multipart `file`, `id`, `title`), `DELETE /api/evals/suites/{id}` (custom only); `POST /api/evals/estimate`; `GET /api/evals/runs/{id}/results?model=`; `POST /api/evals/runs/{id}/regrade` (re-runs deterministic graders on stored responses); `GET /api/evals/scorecard`; `GET /api/evals/compare?suite&a_run&a_model&b_run&b_model`.
- Schema 14 adds `eval_suites`, `eval_results` and `eval_scores` (additive). Rollback: stop the admin, `PRAGMA user_version = 13`, run `ops/rollback-mac.sh`; the M10 tables stay unused.

## Observability (M4)

- **Performance** tab: per-request history from `mlx-server.log` (TTFT, prefill and decode rates, token counts, elapsed, finish reason, error/cancel/incomplete), daily median/p90 charts, and an availability strip with a per-day table. Days use the ops timezone.
- Requests are read by a separate bounded cursor (`mlx-perf`) in its own probe, so the watchdog's log evidence never waits for it. Lines are correlated by request id; the id-less `Request completed/failed` lines are attached only when exactly one request fits, otherwise the row is marked ambiguous or left without that value. A request whose lines stop (restart, rotation gap, over an hour of silence) becomes `incomplete`, never successful. Missing numbers stay empty. Model names come from the `===== start <name> =====` marker of the serving process; otherwise `unknown`.
- Usage vs Performance: Usage counts only requests that went through LiteLLM. Performance reads the MLX log, so it also counts quality runs, the test chat and clients that call `:8080` directly.
- Availability: one sample a minute from the monitor's own observation. Service availability = serving time / observed time while MLX was the intended engine. Off and ComfyUI time are shown separately, and gaps longer than two minutes (admin down, hung probes) are "not observed" and never count as uptime.
- Log rotation: a daily `maintenance` job (after `LOG_ROTATE_HOUR`, default 04:00 ops time) runs `mlx-model rotate-logs`. Logs whose writer runs are deferred and raise `log_rotation_deferred` until a later run rotates them (retried hourly while MLX is stopped, and `mlx-model start` rotates its own log). Rotation never restarts a model.
- Retention: 90 days of requests and samples (`OBSERVABILITY_RETENTION_DAYS`), pruned daily in bounded batches.
- API: `GET /api/performance/summary`, `GET /api/performance/requests` (`from`, `to`, `model`, `state`, `slow`, `limit` ≤ 200, `before`), `GET /api/performance/requests/{id}/context` (that request's own log lines, redacted, max 64 KiB, only while its log file still exists), `GET /api/availability`, `GET /api/maintenance/log-rotation`. All require login. Date ranges are at most 90 days.

## Model lifecycle (M3)

- **Inventory** (Models tab): each registered model with size, a metadata fingerprint (file names and sizes plus config/index content, not a hash of the weights), readiness, other names for the same directory, selected/loaded, when the admin first saw the directory (or the download date for admin downloads), last start and the requests logged by M4. LiteLLM tokens are per route, so the Usage tab attributes a day's tokens to a model only when it was the only model active that day; mixed days stay unallocated. Scans run in a time-boxed probe and are cached for 30 s.
- **Remove**: unregister (files kept) or also delete the weights. The preview shows the canonical directory, its size and why a kind of removal is refused (selected or loaded, behind `models/active`, registered twice, symlinked, outside `models/`, shared with another name). Deleting needs the typed model name; the CLI rechecks everything under `mlx.lock` against the previewed path and inode. See [host/bin/README.md](host/bin/README.md#remove-a-model).
- **Download a model**: Hugging Face repository ids from `DOWNLOAD_ALLOWED_OWNERS` (default `mlx-community`; `*` allows any). Preview pins the commit and shows files, sizes, checksums, free space and the model card as text. One download at a time runs on the Mac, independent of the admin (an admin restart reconnects to it). Every file is checked against its published checksum before the directory is renamed into `models/`; it is never registered automatically and never overwrites a directory. Cancel keeps partial files; Resume continues them. One alert per finished run comes from the run's log.
- **Settings** per model (profiles): launch flags that `mlx-model` passes to `mlx_vlm.server` at the next start, and admin test-chat defaults. Only fields on the host helper's allow-list are accepted; a `reasoning_effort` default only if that value passed the model's compatibility check. Saving never restarts the model; the dialog shows when the running flags differ and offers a separate Restart. Server defaults (`max_tokens`, `enable_thinking`, `thinking_budget`) apply to every client that does not set them, LiteLLM included; the chat defaults apply to the admin's chat only (built-in, then saved defaults, then what the chat form sets). The profile fingerprint is part of the benchmark comparability key (the default profile keeps older keys) and is recorded with compatibility checks, which show as stale after a settings change.
- API: `GET /api/models/inventory`, `GET /api/models/{name}/remove-preview`, `POST /api/models/remove` `{name, delete_files, confirmation}`, `POST /api/downloads/preview` `{repo, revision}`, `POST /api/downloads` `{repo, revision (commit), dest}`, `GET /api/downloads/runs`, `GET /api/downloads/{id}`, `POST /api/downloads/{id}/cancel`, `POST /api/downloads/{id}/resume`, `GET|POST /api/models/{name}/profile`, `POST /api/models/{name}/profile/reset|revert`. All require login; changes require CSRF. Removal and profile changes run as jobs, so they wait for lifecycle jobs and quality runs.


## ComfyUI control (M5)

Built on the routes of the installed ComfyUI 0.37.0 (commit `a716932`, see `host/environment/comfyui-app.txt`); prompts and workflows never leave the adapter, only ids, times and node counts.

- **Queue** (Models tab, ComfyUI card, polled every 3 s while ComfyUI is ready): remove selected pending items (`POST /queue {"delete": [...]}`, which never touches the running item), clear pending (deletes exactly the items pending at the recheck, so work another client queues afterwards is kept), cancel the running item by id (the atomic `POST /api/jobs/{id}/cancel`; on a ComfyUI without it, `/interrupt {prompt_id}`, labelled as non-atomic), and a separately labelled **global** interrupt that is refused if the running item changed. Another ComfyUI client can still start work just before a global interrupt; upstream has no conditional form. Cancel and interrupt are requests: ComfyUI stops at its next checkpoint (between nodes, or between sampler steps), so a long node finishes first. On the Mac a cancelled job ended about 22 s later, at the next node.
- Every change holds the same slot as engine switches and rechecks the ComfyUI process (PID + start time) and the queue first: a view from before a ComfyUI restart or an engine switch is refused, and an unreadable queue is `unknown`, never empty. An engine switch away from ComfyUI with an unknown queue now needs the same confirmation as queued work. Each change adds a `comfyui-queue` row to the switch history (ids and counts only).
- **Outputs** tab: read-only, paged gallery of `comfyui/output/` (PNG, JPEG, WebP, GIF, MP4, WebM, MOV), newest first, with the ComfyUI job id when its history still has it. Scans are time-boxed and bounded (`COMFYUI_OUTPUT_SCAN_MAX` entries, `COMFYUI_OUTPUT_SCAN_DEPTH` levels, cached `COMFYUI_OUTPUT_CACHE_S`). Files are addressed by opaque ids; symlinks, other types and dot-files are never listed or served; a file replaced after the scan returns 410. Content is streamed in 256 KiB chunks with byte ranges (video seeking), `nosniff` and a sandbox CSP. No deletion.
- API: `GET /api/comfyui/queue`, `POST /api/comfyui/queue/delete` `{ids, session}`, `POST /api/comfyui/queue/clear` `{session}`, `POST /api/comfyui/queue/cancel` `{id, session}`, `POST /api/comfyui/interrupt` `{expected_running, session}`, `GET /api/comfyui/outputs` (`offset`, `limit` ≤ 200, `refresh`), `GET /api/comfyui/outputs/{id}/content` (`download=1` for an attachment). `session` is the value from the latest queue read.

## Decision sidecar (M6)

A Jev-style decision model next to the chat model: OpenJev serving DiffusionGemma 26B-A4B (4-bit, MLX) answers typed questions about a state (`noul` yes/no, `choice`, `score`) with probabilities, from one read-only denoising pass. Off unless `DECISION_ENABLED=true`. Plan and measurements: pre-split design notes (private).

- **Host**: `/Volumes/Models/decision/bin/openjev {status,start,stop}` (versioned in `host/decision/`). Loopback `:8081` only, with `OPENJEV_API_KEY` from `decision/etc/openjev.env` (mode 600; set the same value as `DECISION_API_KEY` for the admin). It refuses to start next to ComfyUI, and ComfyUI refuses to start next to it.
- **Models page card**: start/stop as jobs after a memory check (RAM − `DECISION_HEADROOM_GB`, or the GPU wired limit when set; the MLX model's weights + full-attention KV at its `max_kv_size` or full context; `DECISION_FOOTPRINT_GB` for the sidecar; memory pressure must be normal) and only next to `DECISION_ALLOWED_MLX`. Switching to ComfyUI stops the sidecar first; switching MLX to a model not allowed needs confirmation. A sidecar that dies raises `decision_down` and is never restarted automatically. Quality runs refuse while it runs.
- **Decision page**: a question builder with presets, probability bars, "copy as curl" (key left out), history (daily p50/p95, availability, last 100 reads), and LAN clients.
- **LAN gateway** (`DECISION_GATEWAY_PORT`, default `:8082`, its own app in the same process: no session, no admin routes): `POST /v1/systemone` and `GET /v1/models` with OpenJev's bodies unchanged, `GET /healthz`. No key is needed unless `DECISION_GATEWAY_REQUIRE_KEY=true`; a client key (`dsk_…`, shown once, stored as SHA-256, revocable at once) is optional and attributes usage to that client; in-flight limits per client and overall (429 + `Retry-After`); 503 while the sidecar is stopped (never started by the gateway).
- **Usage**: one row per read from the page or the gateway (counts, question types, tokens, timings, status; never the state or question text), by day and by client on the Decision page and as a "Decision model" section on the Usage page.
- API: `GET /api/decision`, `POST /api/decision/start|stop|read`, `GET /api/decision/history?days=`, `GET|POST /api/decision/clients`, `POST /api/decision/clients/{id}/rename|revoke`, `GET /api/decision/usage?days=&group=day|client`.

### Use the decision API from another machine

1. **Key (optional).** By default the gateway needs no key on the LAN. To see usage per machine, create a client on the admin's Decision page (`http://mac-mini.local:8090/decision`, **LAN clients**), copy its `dsk_…` key at once (shown only that time) and send it as `Authorization: Bearer <key>`. Requests without a valid key are counted as "(no key)". Set `DECISION_GATEWAY_REQUIRE_KEY=true` to refuse them (401).
2. **Make sure the sidecar runs.** Start it on the Models page (Decision sidecar card). While it is stopped the gateway answers 503; it never starts the sidecar itself.
3. **Call the gateway** at `http://mac-mini.local:8082`.

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /v1/systemone` | optional key | Ask typed questions about a state (below) |
| `GET /v1/models` | optional key | OpenJev's model list |
| `GET /healthz` | none | `{"status": "ok"}` when the gateway is up (says nothing about the sidecar) |

**Request** (OpenJev / Jev format, ≤ 64 KiB):

```json
{
  "state": "The nightly backup failed: disk quota exceeded on /srv/backup",
  "model": "openjev-latest",
  "questions": {
    "is_error": {"type": "noul", "instructions": "Does this report a failure?"},
    "area": {"type": "choice", "instructions": "Which area?",
             "criteria": {"storage": "Disks, quotas, backups", "network": "Connectivity", "auth": "Logins, keys"}},
    "urgency": {"type": "score", "criteria": ["low", "normal", "high", "critical"]}
  }
}
```

- `state`: text or any JSON (≤ 32 KiB). `model`: `openjev-latest` (default), `openjev-0.1`, `jev-latest` or `jev-preview`.
- `questions`: 1–32, keyed by an id (`A-Z a-z 0-9 _ . -`, ≤ 64 chars).
  - `noul`: yes/no; optional `criteria: {"true": …, "false": …}`.
  - `choice`: `criteria` maps 2–26 option names to descriptions.
  - `score`: `criteria` lists 2–10 levels, lowest first.
- Optional `steps` (1–8 denoising steps) and `samples` (1–8 independent reads, averaged; without it the sidecar re-reads uncertain answers on its own). `images`, `think` and `sequential` are not accepted through the gateway.

**Response** (OpenJev's body, unchanged; `Server-Timing` header included):

```json
{
  "model": "openjev-0.1",
  "answers": {
    "is_error": {"type": "noul", "noul": 0.99999},
    "area": {"type": "choice", "choice": "storage",
             "probabilities": {"storage": 0.99997, "network": 0.00002, "auth": 0.000002}, "confidence": 0.9997},
    "urgency": {"type": "score", "score": 2.16, "legend": {"0": "low", "1": "normal", "2": "high", "3": "critical"},
                "probabilities": {"0": 0.01, "1": 0.12, "2": 0.57, "3": 0.30}, "confidence": 0.30}
  },
  "usage": {"input_tokens": 135, "output_tokens": 0}
}
```

`noul` is P(yes). `score` is the expected level Σ i·pᵢ. `confidence` is `1 − H(p)/ln K` (1 = certain, 0 = uniform); it is the model's certainty, not accuracy.

**Errors** use OpenJev's shape, `{"detail": {"error_type": "...", "message": "..."}}`:

| Status | `error_type` | Meaning |
|---|---|---|
| 400 | `api_usage_error` | Bad body or a question the schema refuses (upstream 400s may carry `detail` as a plain string) |
| 401 | `authentication_error` | Missing, wrong or revoked key (only with `DECISION_GATEWAY_REQUIRE_KEY=true`) |
| 413 | `api_usage_error` | Body over 64 KiB |
| 429 | `rate_limit_error` | Too many requests in flight (4 overall, 2 per key); retry after `Retry-After` seconds |
| 503 | `unavailable` | Sidecar stopped or not answering |
| 504 | `timeout` | No answer within `DECISION_REQUEST_TIMEOUT_S` (10 s) |
| 502 | `bad_gateway` | Malformed reply from the sidecar |

**curl:**

```sh
curl -s http://mac-mini.local:8082/v1/systemone \
  -H 'Content-Type: application/json' \
  -d '{"state": "My card was charged twice", "model": "openjev-latest",
       "questions": {"is_billing": {"type": "noul", "instructions": "Is this a billing issue?"}}}'
```

**Python** (`httpx` or `requests`):

```python
import os, time
import httpx

GATEWAY = "http://mac-mini.local:8082"
# optional: a dsk_ client key attributes usage to that client
KEY = os.environ.get("DECISION_KEY")
HEADERS = {"Authorization": f"Bearer {KEY}"} if KEY else {}


def decide(state, questions, retries=3):
    body = {"state": state, "model": "openjev-latest", "questions": questions}
    for attempt in range(retries):
        r = httpx.post(f"{GATEWAY}/v1/systemone", json=body, headers=HEADERS, timeout=30)
        if r.status_code == 429:  # one slot: back off and retry
            time.sleep(float(r.headers.get("Retry-After", "1")))
            continue
        r.raise_for_status()
        return r.json()["answers"]
    raise RuntimeError("decision gateway busy")


answers = decide(
    {"request": "Draw a watercolour fox in the snow"},
    {"route": {"type": "choice", "instructions": "Which backend should handle this?",
               "criteria": {"code": "Programming", "image": "Generate an image",
                            "math": "Calculations", "chat": "Conversation"}}},
)
route = answers["route"]
print(route["choice"], route["confidence"])  # image 0.99…
if route["confidence"] < 0.5:
    ...  # ambiguous: fall back to a default or ask the chat model
```

**Jev SDKs:** the bodies match OpenJev, which follows TypeSafe's Jev API, so clients built for it should only need the base URL `http://mac-mini.local:8082` (a `dsk_…` key is optional). This has not been tried with the SDKs themselves.

**Notes:**
- Reads run one at a time on the Mac (~0.2–0.6 s each). While reads run, the chat model (Qwen) generates more slowly (−11% at 0.5 reads/s, −64% when saturated; see the plan's Phase 0 record).
- Each request is recorded per key (or "(no key)") and per day (counts, question types, tokens, timings and status; never the state or the questions). See the Decision page and the "Decision model" section of the Usage page.
- The gateway is plain HTTP on the LAN; the key travels in clear text. Use it only on the home network.

## Sidecar slot and TTS (M7)

One small model at a time runs next to the chat model: the **sidecar slot**. Today it holds OpenJev (the M6 decision sidecar) or **Qwen3-TTS** (`Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` on mlx-audio 0.5.7). TTS is off unless `TTS_ENABLED=true`. Plan, Phase 0 measurements and decisions: pre-split design notes (private).

- **Rules**: at most one sidecar runs, never next to ComfyUI, and only next to an MLX model on that sidecar's allow-list (`DECISION_ALLOWED_MLX`, `TTS_ALLOWED_MLX`). Starting one while the other runs is a 409 naming it; confirming turns it into one job that stops the old one, checks memory for the new one alone and starts it. If the new one fails, the old one stays stopped (never restarted), the intent is `stopped`, and no `*_down` alert fires. If two run at once (a start by hand on the Mac), every start, swap and engine switch is refused until one is stopped by hand; diagnostics show `sidecar_conflict`.
- **Host**: `/Volumes/Models/tts/bin/qwen-tts {status,start,stop}` (versioned in `host/tts/`), loopback `:8083` only, no key (mlx-audio has none). OpenJev, ComfyUI and qwen-tts each refuse to start next to the others.
- **Memory**: the TTS sidecar counts as `TTS_FOOTPRINT_GB` (8: streamed requests peak at ~6.8 GB, flat in length). Measured next to `qwen3.6-35b-a3b`: pressure normal, but **the chat model decodes ~70% slower while speech is being generated** (94 → 28 tok/s); speech runs at ~0.25× real time.
- **Models page**: the **Sidecar** card (select Decision or TTS, start/stop, budget, today's traffic) replaces the Decision card.
- **TTS page** (`/tts`): text (up to 10,000 characters) → voice, format (WAV/MP3), optional style instruction → audio player and download, "copy as curl", and history (daily p50/p95, availability). **Usage page**: a "Text to speech" section by day and by client. Text and audio are never stored, only counts and timings.
- **Long text** is split at paragraphs, sentences, then clauses into chunks of `TTS_CHUNK_CHARS` = 800 *weighted* characters (a CJK character counts 4: Chinese is read ~5× slower than English), about 55 s of speech each, synthesized in order with the same voice and joined. Each chunk is capped at ~2× the tokens it needs; a chunk that reaches its cap without ending is refused (502), so a runaway generation never returns garbage or eats memory.
- API (login + CSRF): `GET /api/sidecars`, `POST /api/sidecars/{openjev|qwen-tts}/start` (`{"confirm": true}` to swap), `POST /api/sidecars/{name}/stop`, `GET /api/tts`, `POST /api/tts/speak`, `GET /api/tts/history?days=`, `GET /api/tts/usage?days=&group=day|client`. The M6 `/api/decision/*` routes still work.

### Use the TTS API from another machine

The same LAN gateway as the decision model (`http://mac-mini.local:8082`, keys optional unless `DECISION_GATEWAY_REQUIRE_KEY=true`; a `dsk_…` key from the Decision page attributes usage). Start the TTS sidecar on the Models page first: the gateway never starts or swaps sidecars.

| Endpoint | Purpose |
|---|---|
| `POST /v1/audio/speech` | OpenAI-style speech (below) |
| `GET /v1/models` | `qwen3-tts` while TTS runs; OpenJev's list while OpenJev runs; 503 when none runs |

**Request** (≤ 64 KiB): `{"model": "qwen3-tts", "input": "…", "voice": "Vivian", "instructions": "calm, warm", "response_format": "wav"}`

- `input`: 1–10,000 characters. `voice`: `Vivian` (default), `Serena`, `Uncle_Fu`, `Dylan` (Beijing), `Eric` (Sichuan), `Ryan`, `Aiden`, `Ono_Anna`, `Sohee`. `instructions`: optional style prompt (≤ 1,000 chars). `response_format`: `wav` (24 kHz mono 16-bit) or `mp3`. `speed`: only `1.0` (the model ignores speed, so other values are refused).
- **Response**: the audio bytes, with `X-Audio-Seconds` and `Server-Timing` headers.

| Status | `error_type` | Meaning |
|---|---|---|
| 400 | `api_usage_error` | Bad body, unknown voice, text too long, `speed` ≠ 1 |
| 401 | `authentication_error` | Missing or revoked key (only when keys are required) |
| 413 | `api_usage_error` | Body over 64 KiB |
| 429 | `rate_limit_error` | One speech request at a time (shared with the TTS page); retry after `Retry-After` seconds |
| 503 | `unavailable` | TTS sidecar not running; `detail.running` names the sidecar that is (`openjev`) or `null` |
| 502 | `bad_gateway` | The sidecar returned non-audio, mismatched chunks, or a chunk that never ended |
| 504 | `timeout` | Not done within 30 s + 0.04 s per weighted character |

```sh
curl -s http://mac-mini.local:8082/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model": "qwen3-tts", "input": "Hello from the Mac mini.", "voice": "Ryan"}' -o hello.wav
```

```python
import time
import httpx

def speak(text, voice="Vivian", fmt="mp3", key=None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": "qwen3-tts", "input": text, "voice": voice, "response_format": fmt}
    while True:
        r = httpx.post("http://mac-mini.local:8082/v1/audio/speech", json=body, headers=headers, timeout=900)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "5")))
            continue
        r.raise_for_status()
        return r.content

open("out.mp3", "wb").write(speak("今天天气很好。We can go for a walk.", voice="Vivian"))
```

The body follows OpenAI's speech API, so the OpenAI SDK should work with `base_url="http://mac-mini.local:8082/v1"` (any `api_key`, or a `dsk_…` key); this has not been tried, and fields the gateway does not know are refused (400). 10,000 English characters take ~3 minutes; 10,000 Chinese characters ~12.

### Roll back M7 (schema 12 → 11)

Schema 12 only adds (`tts_requests`, `sidecar_intent`, the availability `sidecar` column), so the M6 app runs on it once the version is lowered:

1. Stop the TTS sidecar by hand first (the M6 app does not know it): `/Volumes/Models/tts/bin/qwen-tts stop`.
2. Stop the admin and back up the database (Deploy section).
3. `sqlite3 ~/local-llm-admin/data/admin.sqlite 'PRAGMA user_version = 11'`
4. `ops/rollback-mac.sh` to the M6 build. It reads `decision_intent` and ignores the M7 rows.

## Embedding service (M8)

An **always-on embedding and rerank service** for LAN clients, Cortex first:
- **Qwen3-Embedding-4B**, 8-bit, quantized locally from `Qwen/Qwen3-Embedding-4B`; 2560 dimensions, last-token pooling, unit-norm.
- **Qwen3-Reranker-0.6B**.

One MLX-VLM 0.7.3 process on loopback `:8084` serves both. It is off unless `EMBED_ENABLED=true`. Spec, plan, Phase 0 record and deploy record: pre-split design notes (private).

- **Not a sidecar.** It runs next to the chat model *and* whichever sidecar holds the slot (TTS or OpenJev). Slot swaps never touch it.
- **ComfyUI:** switching the engine to ComfyUI stops it first, and its intent stays `running`. Switching back restores it in the same job (start, ready, warm up); a failed restore doesn't fail the switch. `qwen-embed` and `comfyui` each refuse to start next to the other.
- **MLX models:** switching to a model not on `EMBED_ALLOWED_MLX` (same default list as TTS) needs confirmation, stops the service, and sets its intent to `stopped`.
- **Crashes:** a crash or reboot raises `embed_down` ("Embeddings service is down"). It is never restarted automatically; press Start on the card.
- **Host:** `/Volumes/Models/embed/bin/qwen-embed {status,start,stop}` (versioned in `host/embed/`). The reranker's `chat_template.jinja` must stay renamed to `chat_template.jinja.st-crossencoder`: with it, MLX-VLM scores an empty query and document, and `qwen-embed` refuses to start.
- **Memory:** the service counts as `EMBED_FOOTPRINT_GB` (9) in every budget check: MLX + slot sidecar + embeddings vs the limit. Requests are capped at `EMBED_MAX_INPUTS` = 32 inputs, because MLX keeps its buffer cache: 8.1 GB after 32-input batches, 13 GB after a 128-input one.
- **Chat speed:** embedding shares the GPU with the chat model. Measured next to `qwen3.6-35b-a3b` (~95 tok/s alone):
  - back-to-back batches dropped chat to 11 tok/s;
  - so the admin runs **one upstream call at a time, then waits `EMBED_MIN_INTERVAL_MS`** (1500), shared by the gateway and the admin page;
  - with that gap, a client sending batches as fast as it can leaves chat at ~58 tok/s, and embedding throughput is ~10 inputs/s.

  Bulk re-embeds are best run off-hours.
- **Models page:** the **Embeddings** card shows the state (running, starting, paused while ComfyUI runs, down, stopped), intent, models, memory, weights, allow-list, today's requests and the budget, with Start and Stop.
- **Embeddings page** (`/embed`): a similarity matrix for 2–8 texts (optionally treating the first as a query), rerank, and history. **Usage page:** an "Embeddings" section by day and by client. Text and vectors are never stored, only counts and timings.
- API (login + CSRF): `GET /api/services`, `POST /api/services/qwen-embed/start|stop`, `GET /api/embed`, `POST /api/embed/similarity`, `POST /api/embed/rerank`, `GET /api/embed/history?days=`, `GET /api/embed/usage?days=&group=day|client`.
- Quality runs refuse while it runs.

### Use embeddings and rerank from another machine

**Through LiteLLM (preferred):** `http://litellm.example.com:4000` with a LiteLLM key.
- `qwen3-embed` is `openai/` with `allowed_openai_params: ["dimensions"]`, so `dimensions` passes through.
- `qwen3-rerank` is `jina_ai/`; `/rerank` and `/v1/rerank` both work.

**Or the gateway directly:** `http://mac-mini.local:8082`, with the same optional `dsk_` keys as above.

| Endpoint | Purpose |
|---|---|
| `POST /v1/embeddings` | OpenAI embeddings: `{"model": "qwen3-embed", "input": "…" or ["…", …], "dimensions": 1024, "encoding_format": "float"\|"base64"}` |
| `POST /v1/rerank` | Cohere/Jina rerank: `{"model": "qwen3-rerank", "query": "…", "documents": ["…"], "top_n": 5, "return_documents": false, "instruction": "…"}` → `{"results": [{"index", "relevance_score"}], …}`, highest score first |
| `GET /v1/models` | Also lists `qwen3-embed` and `qwen3-rerank` while the service runs |

**Embeddings fields:**
- `input`: 1–32 texts, each 1–32,000 characters.
- `dimensions`: 32–2560. The admin truncates and re-normalises (MRL), so smaller vectors stay unit-norm.

**Rerank fields:** up to 200 documents.

**Query instruction:** add it on the client side. The service applies no prompt format to embeddings:
- **queries** are embedded as `Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: <text>`;
- **documents** are embedded as they are.

**Rerank instruction:** if `instruction` is omitted, the admin sends the model card's ("Given a web search query, retrieve relevant passages that answer the query"). MLX-VLM's own default ranks clearly worse.

| Status | Meaning |
|---|---|
| 400 | Bad body or limits (`api_usage_error`) |
| 413 | Body over 4 MiB |
| 429 | Busy: in-flight limit `EMBED_MAX_IN_FLIGHT` = 2, shared with the admin page; retry after `Retry-After: 1` |
| 502 | The service returned something malformed (wrong count, width, NaN, bad index). Never passed on |
| 503 | Not available; `detail.reason` is `stopped` (start it on the card), `comfyui_active` (wait), or `down` (crashed: `embed_down` is open) |
| 504 | Not answered within 30 s + 0.2 s per input |

```sh
curl -s http://mac-mini.local:8082/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model": "qwen3-embed", "input": ["hello world"], "dimensions": 1024}'
curl -s http://mac-mini.local:8082/v1/rerank -H 'Content-Type: application/json' \
  -d '{"model": "qwen3-rerank", "query": "capital of france", "documents": ["Bread needs yeast.", "Paris is the capital of France."]}'
```

**Correctness check:** `python -m local_llm_admin.embed_check --base http://mac-mini.local:8082 --reference tests/fixtures/embed/reference.json` compares live vectors and rankings with CPU references from the model cards' own code (`ops/embed-reference.py`). It must print `PASS`.

### Roll back M8 (schema 13 → 12)

1. Stop the admin and back up the database. The pre-M8 backup is `~/local-llm-admin/backups/admin-20261010-203421-pre-m8.sqlite`.
2. Run `/Volumes/Models/embed/bin/qwen-embed stop`.
3. `sqlite3 ~/local-llm-admin/data/admin.sqlite 'PRAGMA user_version = 12'`
4. Run `ops/rollback-mac.sh`, and restore `~/.config/local-llm-admin/env.bak-pre-m8`. The M7 app ignores `embed_requests` and `embed_intent`.
5. Remove the `qwen3-embed` and `qwen3-rerank` routes from `~/litellm/config.yaml` (backup `config.yaml.bak-20261010-204828`) and restart LiteLLM.
