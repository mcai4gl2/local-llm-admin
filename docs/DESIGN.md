# Local LLM Admin — design

Date: 2026-09-28 (implementation 2026-09-29)
Status: Phase 0 done (2026-09-29; see §3a). Phases 1–4 implemented and tested in the devcontainer (216 tests, 98% coverage). Deployment (Phase 5) is next and needs approval.
Location: repository root

## 1. Goal

Build a web admin page for the Mac mini LLM server (`mac-mini.local`) so it can be managed without SSH. It shows:

1. The selected model, whether it is actually loaded, and recent inference speed.
2. CPU, memory, disk, and (if available) GPU use of the machine.
3. Model switching (plus start and stop) from the browser.
4. A small chat window for testing the running model.
5. A log viewer for the server, download, and admin logs.
6. Token usage of the local model **by date**, read from the LiteLLM gateway's own API on the LiteLLM host (`litellm.example.com:4000`). This covers the same data as `~/litellm/scripts/session-report.sh`, without running that script.

Non-goals: replacing `mlx-model`; downloading models from the browser (v1); a public or internet-facing UI; multi-user roles.

## 2. Constraints

- **Independent of the MLX setup.** The admin never imports `mlx`/`mlx_vlm`, never uses `/Volumes/Models/venvs/mlx` or `/Volumes/Models/python`, and never writes under `/Volumes/Models`. It works through four interfaces only:
  - the `mlx-model` CLI
  - the HTTP API on `127.0.0.1:8080`
  - read-only file reads under `/Volumes/Models/{etc,run,logs,models}`
  - OS metrics
- **Own venv, own folder, own deploy.** It lives on the Mac's internal disk, so it keeps running (and can report the problem) when the USB drive is unmounted.
- **One MLX process at a time.** Every model-lifecycle action goes through `mlx-model`, which owns the lock (`run/mlx.lock`) and the port checks. The admin never starts `mlx_vlm.*` itself.
- **Single decode slot** (`--max-num-seqs 1`). Test chats compete with LiteLLM clients, and the UI says so.
- **Usage comes from the LiteLLM HTTP API only.** Nothing is installed on the LiteLLM host, and nothing touches Postgres or `~/litellm`. The master key never leaves that host; the admin uses its own read-only key (§5.6).
- Project conventions: type hints everywhere, pytest with >80% coverage, work in the devcontainer, devlog after each session.

## 3. Facts established (read-only survey, 2026-09-28)

| Fact | Evidence |
|---|---|
| Control CLI `/Volumes/Models/bin/mlx-model {list,status,start [name],stop,switch <name>,add}`. `switch` blocks until `/health` is healthy or 180 s passes. `list` marks the *selected* name with `*`. | script source |
| Registry `/Volumes/Models/etc/models.conf`, `name<TAB>dir` | file |
| State `run/active.env` (`MLX_ACTIVE_NAME`, `MLX_ACTIVE_PATH`, …), `run/mlx-server.pid`, symlink `models/active` | files |
| `GET :8080/health` returns `status`, `loaded_model`, `loaded_context_size`, `loaded_tool_parser`, … | curl |
| `mlx-server.log` has structured lines: `Prefill completed … rate=392.5 tok/s`, `Decode completed … generated_tokens=… rate=17.8 tok/s`, `===== <ts> start <name> =====` | log |
| `download-models.log` is curl progress output with `\r` in it | log |
| Chat UI `chat-qwen.sh` binds `:7860` and **stops the API** | script |
| Apple M5 Pro, 64 GB RAM, macOS 27; `/opt/homebrew/bin/uv` exists; no LaunchAgents for MLX | ssh |
| `session-report.sh`: text `psql` output grouped by user, session, model, and status (`LIMIT 100`), **not by date**. It works only on the LiteLLM host (`docker exec`). | script |
| LiteLLM v1.99.1 API has `GET /user/daily/activity[/aggregated]` (`start_date`, `end_date`, `model`, `api_key`, `user_id`, `timezone`, pagination) and `GET /spend/logs/v2` (`session_id`, `user_id`, `status_filter`, `start_date`, `end_date`, pagination) | `/openapi.json` |
| `/user/daily/activity/aggregated` for 2026-09-20..28 with `model=openai//Volumes/Models/models/active&timezone=-480` returned 11 requests, 13,608 prompt and 251 completion tokens, broken down by `model_groups` (`local-qwen`), `api_keys` (with `key_alias`), `providers`, and `endpoints`. This **exactly matches** `LiteLLM_SpendLogs`. | curl (master key, GET only) |
| The `model` filter matches the **upstream model string**, not the route name: `model=local-qwen` returns nothing. Two early requests used `openai//Volumes/Models/models/Qwen3.8-27B-4bit`. | curl |
| Daily rollups (`LiteLLM_DailyUserSpend` etc.) and spend logs are both purged after 90 days (`cleanup-spend.sh` matches `*Spend*` tables). Ops timezone is `Asia/Hong_Kong`. | scripts |

Consequences:
- Select local usage by the **local upstream model strings** (`openai//Volumes/Models/models/…`), not by route name, so renamed routes still count. The set of strings is configurable, and defaults to anything starting `openai//Volumes/Models/models/`.
- LiteLLM always sees the model id `/Volumes/Models/models/active`, so it cannot tell which weights answered. The admin records its own **switch history** so usage can be attributed to the real model.
- No the LiteLLM host component is needed. The admin reads both the date view and the per-session view from `/spend/logs/v2` (changed in Phase 0, see §3a).

## 3a. Phase 0 results (2026-09-29)

| Check | Result |
|---|---|
| GPU via `ioreg -r -d 1 -c IOAccelerator` without sudo | Works (`"Device Utilization %"=81` under load). Memory pressure `sysctl` works too. |
| Port 8090 on the Mac | Free. |
| `mlx-model list` | `* qwen3.8-27b      /Volumes/…/Qwen3.8-27B-4bit` (+ `qwen3.8-27b-8bit`, `gemma-4-26b-a4b`). Matches the parser. |
| `mlx-model status` | `process: running pid N` or `process: stopped`, then the `active.env` lines, then `health: {…}`. The parser now reads only the `process:` line. |
| `/health` | `status: "healthy"`, `loaded_context_size: 262144`, `loaded_tool_parser: "qwen3_coder"`, `continuous_batching_enabled: true`. |
| `mlx-server.log` | `<ts>,ms - LEVEL - Request started: … in_flight=N`, `Prefill completed: … rate=X tok/s`, `Decode started: … time_to_first_token=Xs`, `Decode completed: … generated_tokens=N … rate=X tok/s`, `Request completed|failed: … in_flight=N`, `Generation cancelled`. `in_flight=N` is now the in-flight source; TTFT is shown. Mac clock is HKT (+0800), so switch-history timestamps are already in the ops timezone. |
| `run/mlx.lock` | A **directory** taken with `mkdir`. `mlx-model` has **no cleanup trap**: `start_server` uses `exit 1` on failure, which skips `rmdir`, so a failed start leaves a stale lock and every later `mlx-model` command fails. The admin shows the lock's age and says to remove it by hand (it never writes under `/Volumes/Models`). Fixing `mlx-model` itself is a separate change. |
| `download-models.log` | `===== <ts> start =====`, `GET <repo>/<file>`, interleaved curl `\r` meters (downloads run in parallel), `===== <ts> done fail=N =====`. The summary reports per run, not per file. |
| Viewer identity | Created: user `app-local-llm-admin` (`proxy_admin_viewer`), key `local-llm-admin-viewer` with `models: ["no-default-models"]`, stored at `~/litellm/keys/local-llm-admin-viewer.json` (0600). |
| Viewer permissions | Reads `/user/daily/activity/aggregated` and `/spend/logs/v2` for all users. 403 on `/v1/chat/completions` (any model), `/key/generate`, `/key/delete`, `/user/new`, `/model/new`. `/v1/models` is empty. |
| `models: []` | Would have meant **all models** in LiteLLM (`_check_model_access_helper` allows an empty list), so the key uses `["no-default-models"]` instead. |
| `model` query filter | Checked against the key's own model access, so the viewer key gets **403** with `model=`. Multiple values or a prefix are moot. |
| `timezone` parameter | Has **no effect** on bucketing: `-480`, `480`, and `0` give identical results, and the daily rollups are **UTC days** (they match UTC buckets of the spend logs exactly). |
| Spend logs vs rollups | Local rows reconcile exactly with the rollups: 14 requests, 15,845 prompt, 283 completion tokens (2026-09-28 UTC). `page_size=1000` is accepted; the whole 90-day window was 618 rows. `model_group` varies (`local-qwen`, `qwen3.8-27b`), so matching uses the model string. |
| `AbandonProcessGroup` on macOS 27 | Verified 2026-09-29: `launchctl kickstart -k` of the admin left both an admin-started MLX server (pid 19550) and ComfyUI (pid 19405) running. |
| **Local Network privacy (found 2026-09-29)** | Under launchd, the admin's Python gets `[Errno 65] No route to host` for other LAN hosts (litellm.example.com:4000), while loopback and the Mac's own IP work; over SSH the same binary connects. So the usage page cannot reach LiteLLM until the admin's Python is allowed in System Settings → Privacy & Security → Local Network. |

## 3b. Strata Phase 0 results (2026-10-16)

Strata 0.1.44.1-metal, pack Q2_0, context 32768, MTP on, `--mmap-experts`, in a sandbox on the
models volume with its own `HOME`; MLX, ComfyUI, the sidecars and the embedding service
stopped. Bench: `ops/strata-phase0/bench.py`, streamed chat completions.

| Check | Result |
|---|---|
| `./setup.sh --model Q2_0` | Done in 2 h 32 min, bound by the download (62 GB of GGUF shards, 5.7 GB MTP data); the Metal build took minutes. Writes `app/strata-q2_0.json` and serves the model id `qwen3.8-flash-next-q2_0`. |
| Start to `/health` `loaded: true` | 36.8 s (warm page cache). |
| Process tree | `serve/server.py` runs `build-metal/strata --serve` as a direct child; ~40 GB resident (mostly the mapped experts). |
| Short prompt (65 tokens, first request) | TTFT 2.1 s, decode 41.8 tok/s. |
| 2K-token answer | TTFT 0.65 s, decode 56.6 tok/s, 760 of 1289 drafts accepted. |
| 4K prompt (3,685 tokens) | TTFT 13.8 s, prompt 267 tok/s, decode 65.5 tok/s. |
| 29K prompt (29,029 tokens) | TTFT 34.9 s, prompt 831 tok/s, decode 65.1 tok/s. |
| Memory | Pressure level 1 (normal) throughout, swap unchanged (837 MB before and after). |
| SIGTERM | Port closed and both processes gone in 0.6 s; memory back to its idle level. |
| `/health` | No auth; `{status, max_context, model, images, api_key, image_generation, loaded, service: "strata"}`. |
| `/metrics` | JSON with `engine`, `live`, `requests`, `totals`, `hardware`, `history`; `?format=prometheus` gives vLLM-named series. Needs the API key. |
| Per-user state | `~/.config`, `~/.cache`, `~/.local`, LaunchAgents/Daemons, shell rc files, crontab, brew and `/usr/local` unchanged. One CMake try-compile probe crashed during the build and left a crash report. |
| Chat template | `reasoning_effort` accepts `low`, `medium`, `xhigh` (`high` maps to `xhigh`); a reply that hits `max_tokens` while thinking has no answer. |
| Thinking off | Only `chat_template_kwargs: {"enable_thinking": false}` or `reasoning_effort: "none"`; a top-level `enable_thinking` (MLX's spelling) is ignored and the model thinks at `xhigh`. The compatibility cases send both. |
| Context 262144 (the trained length) | `setup.sh --context 262144`; KV stays fp16 on a Mac (`--kv` is ignored), ~9 GB, leaving ~1 GB of GPU memory free. Ready in 32.7 s. |
| 197.5K-token prompt at 262144 | TTFT ~240 s (prompt 760–830 tok/s), decode ~58 tok/s; a code hidden at 10%, 50% and 90% depth found every time (also at 8K). Pressure normal, swap unchanged. |

IQ3_XXS, context 131072, same Strata build; models on the internal SSD (the pack does not fit
in memory, so experts stream from disk):

| Check | Result |
|---|---|
| `./setup.sh --model IQ3_XXS --context 131072` | Done in 2 h 6 min (76 GB of GGUF shards, plus a 43 GB streaming copy of the experts: 111 GB on disk). No MTP draft layer: setup leaves it out when experts stream ("one token per step is faster there"), and writes `--suffix-draft 0`. Model id `qwen3.8-flash-next-iq3_xxs`. |
| Start to `/health` `loaded: true` | 4.1 s, 2.6 s, 2.1 s over three cold starts, no errors. The 28 GB expert arena fills and wires (mlock) after that, so the first request waits ~12 s. |
| Memory | 28 GB of the ~40 GB of experts held in the wired arena; the rest read from the SSD on a miss. ~29 GB resident. Pressure normal, swap unchanged. |
| 2K-token answer | Decode 32.2 tok/s (no drafts). |
| 4K prompt | Prompt 427 tok/s, decode 26.2 tok/s. |
| 29K prompt | Prompt 533 tok/s, decode 29.0 tok/s. |
| 98.8K-token prompt, thinking on | TTFT 177–187 s (prompt 529–557 tok/s), decode 27–29 tok/s; the code found at 10%, 50% and 90% depth. |

Decision gate (`plans/20261015_strata-qwen38-flash-next.md`): IQ3_XXS fails it. Memory and
cold starts pass, but decode is 26–32 tok/s, below 60% of Q2_0's (about 35 tok/s), and a 4K
prompt reads at 427 tok/s, below 500. Q2_0 ships, at a 262144 context. Its decode is 57–66 tok/s, inside the 50–70 tok/s estimate. Engine-side request
statistics go to `app/strata-<pack>.log`, apart from the server log the wrapper keeps; the
logs page shows both (§5.5).

## 4. Architecture

```
 Browser (LAN)
    │  HTTP :8090, session cookie
    ▼
┌──────────────────────────── Mac mini mac-mini.local ──────────────────────────┐
│ local-llm-admin  (/Users/you/local-llm-admin, own uv venv, LaunchAgent)       │
│  FastAPI + Jinja2 + htmx                                                      │
│   ├─ status     → mlx-model status, :8080/health, run/active.env              │
│   ├─ metrics    → psutil + sysctl/ioreg sampler (5 s ring buffer)             │
│   ├─ models     → mlx-model list/switch/start/stop  (subprocess, job queue)   │
│   ├─ chat       → proxy stream to 127.0.0.1:8080/v1/chat/completions          │
│   ├─ logs       → tail/follow allow-listed files                              │
│   └─ usage      → LiteLLM API (read-only virtual key, server side, cached)    │
│  SQLite: switch history, job records                                          │
└───────────────────────────────────────────────────────────────────────────────┘
                     │ HTTP :4000 (LAN, Bearer <admin-viewer key>)
                     ▼
┌──────────────────────────── LiteLLM host 192.0.2.10 ──────────────────────────┐
│ LiteLLM gateway (existing, unchanged)                                         │
│   ├─ GET /user/daily/activity[/aggregated]  → tokens by date                  │
│   └─ GET /spend/logs/v2                      → per-request / per-session rows │
└───────────────────────────────────────────────────────────────────────────────┘
```

One Python package, `local_llm_admin`, deployed to one host. Dependencies: FastAPI, uvicorn, jinja2, httpx, psutil, pydantic-settings.

### Why this shape

- **Server-rendered pages with htmx, no Node build.** Five simple pages don't need a SPA. Deploying stays "sync source + `uv sync`", with no frontend toolchain on the Mac. Charts use one vendored `uPlot` file, and streaming uses `fetch` + `ReadableStream`.
- **Shell out to `mlx-model` instead of reimplementing it.** It already handles locking, the single-process rule, the symlink, and the health wait. Duplicating that would create a second, drifting source of truth.
- **LiteLLM's own API instead of a script or direct SQL.** The daily rollups are already computed by the gateway, support timezones, and matched the raw spend logs exactly. This avoids a second service on the LiteLLM host, `docker exec`, and a hand-written query that could drift from LiteLLM's schema. The cost is a dedicated read-only key, which is created once (§5.6).

## 5. Components

### 5.1 Status (item 1)

`GET /api/status` merges:
- `run/active.env` → selected name and path.
- `mlx-model status` → process running or stopped, plus pid.
- `GET 127.0.0.1:8080/health` (2 s timeout) → loaded, context size, tool parser.
- Listener check on `:7860` → "Chat UI mode (API down)".
- Recent inference from the last N `mlx-server.log` lines: last decode tok/s, last prefill tok/s, time of the last request, and whether a request is in flight (`Request started` without a matching `completed`/`failed`/`cancelled`).
- Weights size (`du` result cached per model dir) and `config.json` summary: architecture, quantization bits, and `max_position_embeddings` if present.

It resolves to one state for the header badge: `serving | loading | switching | stopped | chat-ui | drive-missing | error`.

### 5.2 Metrics (item 2)

A background sampler runs every 5 s and keeps an in-memory ring buffer (1 h = 720 points). There is no DB for metrics in v1.

| Metric | Source |
|---|---|
| CPU total and per core, load average | `psutil.cpu_percent(percpu=True)`, `os.getloadavg()` |
| Memory used, available, wired/compressed, swap | `psutil.virtual_memory()`, `swap_memory()` |
| macOS memory pressure (normal/warn/critical) | `sysctl kern.memorystatus_vm_pressure_level` |
| Disk usage for `/` and `/Volumes/Models`; mounted flag | `psutil.disk_usage`, `os.path.ismount` |
| Disk I/O and network throughput | `psutil.disk_io_counters`, `net_io_counters` (deltas) |
| MLX server process RSS and CPU | `psutil.Process(pid)` for the pid in `run/mlx-server.pid` |
| GPU utilisation | `ioreg -r -d 1 -c IOAccelerator` → `PerformanceStatistics["Device Utilization %"]`. **Verify in Phase 0**; hide the tile if it is unavailable. No `sudo powermetrics`. |

API: `GET /api/metrics/current` and `GET /api/metrics/history?window=15m|1h`. The dashboard polls every 5 s.

### 5.3 Model switching (item 3)

- `GET /api/models` parses `mlx-model list`: name, dir, selected flag, dir exists, `config.json` present, size.
- `POST /api/models/switch {name}`, `POST /api/models/start`, `POST /api/models/stop`:
  - `name` must match `^[A-Za-z0-9._-]+$` **and** appear in the current `list` output. It is passed as an argv element, never through a shell.
  - Refused with 409 if a job is already running in the admin, or if `run/mlx.lock` exists.
  - Refused with 409 if the drive is not mounted.
  - Runs as a background job, `subprocess` with `start_new_session=True` and a 300 s timeout. Output is captured into the job record. The UI polls `GET /api/jobs/{id}` and shows the output live.
  - The server that `mlx-model` starts must outlive the admin. `mlx-model` already `nohup`s it into the background. The LaunchAgent sets `AbandonProcessGroup=true` so restarting the admin never kills the MLX server, and the job runs in a new session.
  - The UI asks for confirmation and warns: "Switching stops the API for ~30–90 s. In-flight LiteLLM requests to `local-qwen` will fail."
- **Switch history:** every job writes `(ts, action, from_name, to_name, result, duration_s, output_tail)` to SQLite. Phase 1 backfills the history from the `===== … start <name> =====` markers in `mlx-server.log`. The usage page uses it to label which weights were active on each day.
- **Register a downloaded model (added 2026-09-29, was "later"):** `POST /api/models/register {dir_name, name}` runs `mlx-model add <name> <dir>` as a job (the CLI owns `models.conf`). It is refused when the directory isn't one of those discovered (404), is already registered, isn't complete, or has no `config.json`, or when the name is already in use (409). The name check matters because `mlx-model add` would silently repoint an existing name. The suggested name is the lowercased directory name.

### 5.4 Chat test window (item 4)

- `POST /api/chat` (SSE) proxies to `127.0.0.1:8080/v1/chat/completions` with `stream: true` and the model id `/Volumes/Models/models/active`.
- Controls: system prompt, temperature, `max_tokens` (default 512, capped at 4096), and a "thinking" toggle if the model supports it (Qwen `reasoning_content` is shown collapsed).
- The conversation lives only in the browser (no persistence). There is a clear button.
- After each reply it shows prompt tokens, completion tokens, TTFT, and tok/s, taken from the response `usage`/`timings` or measured client-side.
- **Stop** aborts the stream. The server logs `Generation cancelled`, which frees the slot.
- A banner notes that chat uses the single decode slot, bypasses LiteLLM, and so does not appear in the usage numbers.
- Disabled unless the status is `serving`.

### 5.5 Logs (item 5)

- Allow-listed sources only. The id maps to a fixed path, and client-supplied paths are never accepted:
  `mlx-server` → `/Volumes/Models/logs/mlx-server.log`, `chat-ui` → `…/chat-qwen.log`, `serve` → `…/serve-qwen.log`, `admin` → the admin's own log. Download logs are discovered when requested: every `logs/download-<name>.log` whose stem matches `^download-[A-Za-z0-9_-]+$` becomes source `download-<name>` (for example `download-models`, `download-variant`). The id pattern has no dots or slashes and must name an existing file in the fixed logs directory.
- With `STRATA_ENABLED=true`: `strata` → `/Volumes/Models/strata/logs/strata.log` (the wrapper's server log: starts and per-request progress), and `strata-engine-<pack>` → `…/strata/app/strata-<pack>.log` (Strata's engine log: memory footprint, prompt and decode statistics) for every pack in `STRATA_PACKS` whose `app/strata-<pack>.json` run config exists. Strata appends to its engine log across starts; `bin/strata start` rotates both at 50 MiB, keeping 8.
- `GET /api/logs/{id}?lines=500&grep=…&level=INFO|WARNING|ERROR` reads from the end of the file in blocks, so a large log is never read whole.
- `GET /api/logs/{id}/follow` (SSE) tails by byte offset, polling every 1 s. It detects truncation or rotation through the inode and size.
- Curl progress is normalised: split on `\r` and keep the last segment. The download log view also gets a progress summary per file (bytes on disk against the size in `model.safetensors.index.json` or the HTTP `Content-Length` logged).
- The UI highlights WARNING/ERROR and has pause/resume, wrap toggle, and download of the raw tail.
- `mlx-server.log` has no rotation today. The admin shows its size and keeps its own log with a rotating handler, but never rotates MLX logs (it doesn't write under `/Volumes/Models`).

### 5.6 Usage by date (item 6)

The data comes from the existing LiteLLM gateway API at `http://litellm.example.com:4000`. The admin calls it server-side; the browser never sees the key.

| Admin endpoint | LiteLLM call | Purpose |
|---|---|---|
| `GET /api/usage/daily?from&to` | `GET /spend/logs/v2?start_date&end_date&page&page_size=1000` (UTC bounds of the local days, no `model` filter), rows matched to local model strings and bucketed by ops-timezone day in the admin | Per-day totals (`prompt_tokens`, `completion_tokens`, `total_tokens`, `api_requests`, `successful_requests`, `failed_requests`) with breakdowns by app (key alias), `model_groups`, and `models` |
| `GET /api/usage/sessions?from&to&page` | The same rows (shared cache), grouped by `session_id` and app | The "sessions" view that `session-report.sh` provides today: app, session, requests, tokens, first and last timestamps, status |
| `GET /api/usage/health` | `GET /health/liveliness` plus a one-row `/spend/logs/v2` call | Shows whether the gateway is reachable and the key works |

*Changed in Phase 0:* the daily view was planned on `/user/daily/activity/aggregated`, but its rollups are UTC days (the `timezone` parameter does not re-bucket them) and its `model` filter is refused for a key without model access (§3a). Spend logs carry per-request timestamps, so the admin buckets them into ops-timezone days itself.

Rules:
- **Local model selection.** `LITELLM_LOCAL_MODELS` is a comma-separated list of upstream model strings; an entry ending in `/` is a prefix. The default is `openai//Volumes/Models/models/,openai/qwen3.8-flash-next-q2_0,openai/qwen3.8-flash-next-iq3_xxs`, so every weights directory behind the local route counts, whatever the route is called, and so do Strata's Q2_0 and IQ3_XXS packs (Strata serves `qwen3.8-flash-next-<pack>`; another pack's id is added by hand). Matching is done in the admin (rows may carry the model with or without the `openai/` prefix).
- **Timezone.** `LITELLM_TIMEZONE_OFFSET_MIN=-480` (JavaScript `getTimezoneOffset` convention, Asia/Hong_Kong) sets the ops day used for the UTC request bounds and for bucketing. Example: a request at 22:34 UTC on 09-28 counts on 09-29.
- **Range.** At most 90 days (the retention in `cleanup-spend.sh`), with the last 30 as the default. Dates are validated as ISO and are never passed through unchecked.
- **Caching.** The local rows are cached in memory for 60 s per range and shared by the daily and sessions views. The usage page is the only consumer, so there is no background polling.
- **Pagination.** `page_size=1000`, following `total_pages` (and full pages when `total_is_capped` is set), with a hard cap of 20 pages; hitting the cap marks the result `truncated` and the page says so.
- **Schema tolerance.** Rows are read with `dict.get` and unknown fields are ignored, because LiteLLM adds fields between versions. A contract test pins the recorded v1.99.1 rows (`tests/fixtures/spend_logs_recorded.json`) to 14 requests, 15,845 prompt and 283 completion tokens.
- **App attribution.** "App" is the virtual key's alias (`metadata.user_api_key_alias`), which matches `HOMELAB_SETUP.md`. Requests without an alias are shown as "(no alias)" (the sessions view falls back to the LiteLLM user).

**Read-only LiteLLM identity (created once, with approval):**
- Create a user `app-local-llm-admin` with role `proxy_admin_viewer`, and a key with alias `local-llm-admin-viewer`, following the pattern of `create-app-identities.sh`.
- Keep the key file under `~/litellm/keys/` (0600), and put a copy in the admin's env file on the Mac as `LITELLM_ADMIN_KEY`.
- Give the key **no model access** (`models: ["no-default-models"]`; an empty list would mean *all* models in LiteLLM), so a leaked key can read usage but cannot spend GPU or API quota.
- **Phase 0 checks** (done 2026-09-29, all passed; see §3a):
  - `proxy_admin_viewer` can read `/user/daily/activity/aggregated` and `/spend/logs/v2` for **all** users.
  - An empty model list doesn't block those management routes.
  - The key gets 401/403 on `/v1/chat/completions` and on any write route, for example `POST /key/generate`.
  - If `proxy_admin_viewer` is too narrow, stop and decide; do not fall back to the master key.
- Add the identity to the table in `HOMELAB_SETUP.md` §6.

**Admin usage page:**
- Date range picker: 7, 30, or 90 days, or custom.
- A stacked bar chart of prompt and completion tokens per day.
- A table by date and app, with error counts and a "model active that day" column taken from the switch history.
- A "Sessions" tab with the `/spend/logs/v2`-derived table.
- If LiteLLM is unreachable or the key is rejected, only this page shows an error; everything else keeps working.

### 5.7 Task-quality evals (M10)

Plan: `plans/20261017_task-quality-evals.md`. Summary of what was built.

- **Run kind.** `kind="eval"` in `quality_runner.py`, with `suite` and `thinking`; same held job slot, model switching, cancellation and restoration as other quality runs. Its deadline is `EVAL_RUN_DEADLINE_S` (12 h). The request timeout is `max(QUALITY_REQUEST_TIMEOUT_S, max_tokens / 5)` seconds (a 5 tok/s floor), clamped by the run deadline, because an 8192-token thinking answer outlasts 300 s.
- **Suites** (`eval_suites.py`, `evals/*.json`): versioned, content-hashed; `core-v1`, `writing-v1`, generated `needle-v1`. Custom JSONL import is bounded (size, case count, regex length, duplicate ids) and walks each regex's parse tree (`re._parser`): a repeat may not contain an alternation, a repeat inside a repeat needs the outer one bounded at 100 or less, and that bounded body may hold only single characters and their repeats, each sharing no character with what can follow it (so it splits one way). Polynomial patterns outside repeated groups are not caught; the runner grades in a worker thread. Generated suites cannot be exported (400).
- **Graders** (`eval_graders.py`): deterministic graders plus `judge`. Only the final answer is graded: `<think>` blocks are removed, and with a closing tag that has no opening one (the template opened it in the prompt) only the text after the last `</think>` counts. A response cut off while thinking scores 0, as does a thinking-on reply that hit the token limit with no `</think>` and no separate reasoning. The run's `thinking` is stored per result so regrade applies the same rule. Generation settings: temperature 0, seed 0, thinking set as both the top-level `enable_thinking` and `chat_template_kwargs.enable_thinking` (no `reasoning_effort` is sent).
- **Judge** (`eval_judge.py`): runs after all candidates, once per run. `external` or `mlx:<name>` (one switch). Verdicts are JSON on a 1 to 5 scale; prose is retried once, then `judge_error`. Self-judged results are flagged. Strata supports only an external judge.
- **Storage** (`eval_store.py`, migration v14): `eval_suites`, `eval_results` (responses capped at 64 KiB, grade state per row) and `eval_scores` (latest per fingerprint, suite, suite hash and settings hash). Both carry `thinking`; the scorecard keys cells `<suite>` and `<suite>+thinking`. Responses are kept so `regrade` needs no model.
- **Promotion** happens only at the end of a run that finished without cancellation, deadline or engine intervention (after the judge phase): each model is finalized without promoting, then again with promotion. A model with no graded rows writes no score, and a model with any `failed` row is not promoted (its summary is still recorded).
- **Statistics** (`eval_stats.py`): bootstrap interval over cases (2000 resamples, seed 0); paired bootstrap for compare, verdict only with at least `MIN_PAIRED_N = 10` shared cases and an interval that excludes 0.
- **Identity.** MLX scores are keyed by the weights fingerprint from `model_info`. Strata scores are keyed `strata-<sha256(served model id)[:16]>` with no speed (no benchmark baselines). Compare refuses when suite hash or settings hash differ (so Strata never compares with MLX: the runtime fingerprint is in the settings hash), or, for a suite with judge cases, when the judges on the graded judge rows differ; self-judged sides are flagged.
- **Estimate.** `POST /api/evals/estimate` uses benchmark medians (else defaults), adds a switch cost per other model and reports the judge in use. For needle suites it expands the cases with the current engine's context (`/health`: `loaded_context_size`, or `max_context` for Strata) for every target model, so it is approximate for models with another context.
- **UI.** Quality tab: suite picker, thinking toggle, estimate dialog, results with case details, scorecard and compare. Models tab: a Quality column (thinking-off `core-v1`, `writing-v1`) on the inventory table, taken fresh from the scorecard on each `/api/models/inventory` call by weights fingerprint (computed inside the cached, probe-guarded scan, so a cache hit does no drive I/O). Strata scores (`strata-<hash>`) appear only on the Quality tab scorecard, not in this column.
- **Measured numbers** (durations, scores, estimate error, judge agreement) are filled in after the first runs on the Mac. Not yet recorded.

## 6. Security

- LAN only: bind `0.0.0.0:8090` on the Mac. Never port-forward. Tailscale is fine later.
- **Admin login is required.** The page can stop the GPU server and offers a chat, so it must not be as open as `:8080`. There is a single admin password, stored as an argon2 hash in `~/.config/local-llm-admin/env` (0600), and a signed, HttpOnly, SameSite=Strict session cookie that lasts 7 days. There is a CSRF token on every POST, and login is rate-limited.
- Subprocesses use fixed argv only; there is no `shell=True`. Log sources and model names are allow-listed.
- The Mac holds only `LITELLM_ADMIN_KEY`: read-only, no model access, and revocable in the LiteLLM UI. It never holds the master key, Postgres credentials, or provider keys. The key is sent only to `litellm.example.com:4000` and never to the browser, logs, or error messages.
- The admin runs as the Mac's login user.

## 7. Deployment

| Item | Mac mini |
|---|---|
| Folder | `/Users/you/local-llm-admin/` (`app/`, `.venv/`, `python/`, `data/admin.sqlite`, `logs/`) |
| Python | uv-managed 3.12 with `UV_PYTHON_INSTALL_DIR=/Users/you/local-llm-admin/python`, separate from `/Volumes/Models/python` |
| Service | `~/Library/LaunchAgents/com.homelab.local-llm-admin.plist`: `RunAtLoad`, `KeepAlive`, `AbandonProcessGroup=true`, `PATH` with `/opt/homebrew/bin`, stdout to `logs/` |
| Port | 8090 |
| Config | `~/.config/local-llm-admin/env` (0600): password hash, session secret, `LITELLM_BASE_URL=http://litellm.example.com:4000`, `LITELLM_ADMIN_KEY`, `LITELLM_LOCAL_MODELS`, MLX root `/Volumes/Models`, API `http://127.0.0.1:8080` |

- Nothing is installed on the LiteLLM host. The only change there is creating the read-only LiteLLM identity (§5.6).
- `ops/deploy-mac.sh`:
  - rsyncs `src/`, `pyproject.toml`, and `uv.lock` to `app/`
  - runs `uv sync --frozen --no-dev` into `.venv`
  - reloads the LaunchAgent
  - runs a smoke check (`/healthz`)
- `rollback-mac.sh` restores the previous version, kept as `app.prev/`.
- Deployment is the first time anything is written on the Mac. It is a separate, explicitly approved step.
- Optional: once the admin is running, add it to `HOMELAB_SETUP.md` and `LOCAL_LLM_SETUP.md`. Also optional: a `mlx-model start` on boot, as a checkbox action in the admin ("auto-start last model when admin starts and drive is mounted", off by default).

## 8. Project layout

```
local-llm-admin/
├── docs/DESIGN.md
├── pyproject.toml
├── uv.lock
├── src/local_llm_admin/
│   ├── config.py             # pydantic-settings
│   ├── app.py                # FastAPI factory, auth, routes
│   ├── mlx.py                # mlx-model wrapper, active.env, health, log-derived stats
│   ├── jobs.py               # single-flight job runner + SQLite history
│   ├── metrics.py            # sampler + ring buffer
│   ├── logs.py               # tail/follow/normalise
│   ├── chat.py               # streaming proxy
│   ├── litellm_usage.py      # LiteLLM API client, pagination, cache, pydantic models
│   ├── templates/            # dashboard, models, chat, logs, usage, login
│   └── static/               # htmx, uPlot (vendored), app.css, chat.js
├── ops/                      # deploy/rollback scripts, LaunchAgent plist template
└── tests/                    # fake mlx-model script, fixture logs, recorded LiteLLM responses
```

## 9. Testing

- **Unit tests:** `active.env`/`list`/`status` parsing; log-line parsing (decode/prefill rates, in-flight detection, start markers); `\r` normalisation; reverse tail and rotation handling; name and date validation; job single-flight and timeout; ring buffer; LiteLLM response parsing, merging of several model strings, pagination, session grouping, and cache expiry.
- **Integration tests (devcontainer):**
  - A fake `mlx-model` shell script with scripted delays and exit codes.
  - A fake OpenAI streaming server standing in for `:8080`.
  - A fake LiteLLM server (`respx`) replaying recorded v1.99.1 responses, including 401, timeout, and `has_more` paging.
  - These run the app end to end with httpx `ASGITransport`.
- **Contract test:** the recorded 2026-09-28 `/user/daily/activity/aggregated` fixture must parse to 11 requests, 13,608 prompt tokens, and 251 completion tokens.
- **Security tests:** unauthenticated requests are rejected, CSRF is required, a path outside the allow-list in the logs API is refused, names with shell metacharacters are refused, and the LiteLLM key never appears in responses or logs.
- **Coverage:** >80% overall, and 100% on `mlx.py` command building, `jobs.py`, and the date/model-filter handling in `litellm_usage.py` (the critical paths).
- **Smoke tests after deploy:**
  - Pages load and status is `serving`.
  - The usage page shows the known 2026-09-28 totals.
  - A switch to the same model (`qwen3.8-27b`) completes and `/health` comes back.
  - A chat returns tokens.
  - Log follow shows the chat's `Decode completed` line.

## 10. Phases

0. ✅ 2026-09-29 **Verify assumptions.** Results in §3a. `AbandonProcessGroup` is left to the Phase 5 smoke test.
   - Read-only:
     - GPU utilisation via `ioreg` works without sudo.
     - Port 8090 is free.
     - The macOS 27 LaunchAgent behaviour with `AbandonProcessGroup`.
     - The `model` filter semantics (several values or a prefix?) and the `timezone` sign convention.
     - Record LiteLLM fixtures.
   - Approved write: create the `local-llm-admin-viewer` viewer key and run the permission checks in §5.6.
1. ✅ 2026-09-29 **Skeleton + status + metrics + logs.** All read-only. The package, config, auth, the dashboard, and the logs page. Tests.
2. ✅ 2026-09-29 **Usage page** against the LiteLLM API (reworked onto `/spend/logs/v2` after Phase 0; checked live with the viewer key).
3. ✅ 2026-09-29 **Model control:** switch/start/stop, jobs, switch history with backfill.
4. ✅ 2026-09-29 **Chat window.**
5. ⏳ **Deploy to the Mac** *(scripts written in `ops/`, not run)* (LaunchAgent), smoke tests, update `LOCAL_LLM_SETUP.md` and `HOMELAB_SETUP.md`, update Cortex.
7. ✅ 2026-09-29 **ComfyUI support:** global engine control (MLX / ComfyUI / Off), ComfyUI card, weights and log, `mlx-model` :8188 guard. Spec and plan: pre-split design notes (private). Verified live on the Mac 2026-09-29.
6. **Later:** auto-start the last model, optional log rotation proposal for `mlx-server.log`. (Registering downloaded models was pulled forward on 2026-09-29, see §5.3.)

## 11. Decisions

| # | Decision | Status |
|---|---|---|
| D1 | Admin login | Proposed: single password + session cookie (recommended because of the stop/switch actions) |
| D2 | Frontend stack | Implemented 2026-09-29: FastAPI + Jinja2 + vanilla JS with inline-SVG charts (no htmx or uPlot), no Node build (§12) |
| D3 | Port | Proposed: admin on 8090 (Mac) |
| D4 | Install folder | Proposed: `/Users/you/local-llm-admin` |
| D5 | Usage data source | **Decided 2026-09-28, revised 2026-09-29:** LiteLLM API with a dedicated `proxy_admin_viewer` key (no model access). Both views come from `/spend/logs/v2`, bucketed in the ops timezone by the admin (§3a explains why not the UTC daily rollups). The earlier the LiteLLM host agent running SQL and `session-report.sh` is dropped. |
| D6 | Chat routing | Proposed: direct to `:8080` (a pure model test; not counted in LiteLLM usage). The alternative is through LiteLLM with a separate chat-capable key, so test chats are counted. |
| D7 | Strata engine (Qwen3.8-Flash-Next) | **Implemented 2026-10-15** (`plans/20261015_strata-qwen38-flash-next.md`): a third exclusive engine (`EngineKind` `strata`), driven only through `strata/bin/strata` and its HTTP API on the MLX server's `0.0.0.0:8080` (2026-10-16: LiteLLM's one route reaches whichever engine runs; host scripts tell Strata apart by its command line, the admin by its pid file and `/health` `service: "strata"`). One Engine entry per installed pack (`strata:<pack>`, from the `app/strata-<pack>.json` run configs; the running pack from `/health`'s model id): picking another pack while Strata runs restarts it, and rollback brings the old pack back. `bin/strata start <pack>` keeps the choice in `run/strata.pack`, so a plain start (the watchdog's) runs it again; the intent stays `strata`. Switching to it stops MLX or ComfyUI, the sidecar and every always-on service; leaving it restores the services meant to run. Off unless `STRATA_ENABLED=true`. Test chat and the compatibility cases follow it (benchmarks and model sweeps stay MLX-only). |

## 12. Implementation notes (2026-09-29)

Built in `src/local_llm_admin/` as laid out in §8. Tests run in the devcontainer: `.venv/bin/pytest --cov` (242 passed after Phase 0 and model discovery, 98% overall; `jobs.py` and `litellm_usage.py` at 100%). ruff and `mypy --strict` are clean. A browser smoke run (Playwright, against a fake MLX root, a fake `:8080`, and a fake LiteLLM) covered every page, a chat, a same-model switch, log follow, light and dark themes, and phone width, with no console errors.

Decisions made during implementation (each is cheap to reverse):

- **Parsers verified in Phase 0** against recorded Mac output in `tests/fixtures/mac/` (list, status, server log, download log, `ioreg`).
- **LiteLLM fixture recorded** with the viewer key (`tests/fixtures/spend_logs_recorded.json`, sanitized: no key hashes, IPs, or real request/session ids). The small `spend_logs_p*.json` files remain as synthetic paging fixtures.
- **`/spend/logs/v2` compares `startTime` in UTC.** Local (UTC+8) dates are converted to UTC `YYYY-MM-DD HH:MM:SS` bounds. Rows are matched to local models with or without the `openai/` prefix.
- **Frontend: vanilla JS + small inline-SVG charts instead of htmx + uPlot** (D2). Every page is a JSON-polling or streaming view, so htmx would have needed a second set of HTML-fragment endpoints. There is still no Node build and no vendored files.
- **Job runner writes output to a file and waits on process exit, not pipe EOF.** `mlx-model` backgrounds the server, which may inherit stdout. On timeout the runner sends SIGTERM, then SIGKILL after 5 s. (`mlx-model` has no cleanup trap, so a killed run can leave `run/mlx.lock`; the admin reports the lock's age, see §3a.) It only signals the CLI, never its session. The single-flight slot is released even if recording fails.
- **Switch-history timestamps** from log markers are read in the ops timezone (`LITELLM_TIMEZONE_OFFSET_MIN`). The "model active that day" label counts `start`/`switch` events that succeeded or were observed.
- **Log follow SSE** ends after 30 min. The browser's EventSource reconnects, and the follow resumes from the end of the file.
- **Download summary** (§5.5) is per run (start, finish, failures, files requested) plus the latest curl progress line, because parallel downloads interleave their meters. `GET /api/downloads`; shown on the logs page for the download source.
- **Login** needs `ADMIN_PASSWORD_HASH`, generated with `local-llm-admin-hash`. With no hash set, every login fails. With no `ADMIN_SESSION_SECRET`, sessions last only until the admin restarts.
