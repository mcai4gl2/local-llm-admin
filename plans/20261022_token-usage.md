# Local-model token usage (input / cached / output) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-10-22
Status: Proposed. Not started.

**Goal:** Show how many tokens the local models consumed (input, of which cached, and
output) per day, per model and in total, measured by the engines themselves. The totals
must stay correct after the per-request history is pruned.

**Architecture:** The M4 performance ingestion already parses `mlx-server.log` into
`inference_requests`, with `prompt_tokens`, `cached_tokens` and `generated_tokens` on each
row. Nothing adds those numbers up. This plan adds a token rollup to `PerformanceStore`,
a daily table that keeps totals beyond retention, an ingestion source for Strata requests,
and a "Token usage" section on the Performance page.

**Tech Stack:** Python 3.11, FastAPI, SQLite (admin DB, migration v15), the M4 `Ingestor`
and `Correlator`, the existing chart helpers in `static/app.js`. No new dependencies.

## Where things stand

| Source | Per-request timing | Per-request tokens | Totals shown |
|---|---|---|---|
| MLX (`mlx-server.log` → `inference_requests`) | yes (TTFT, prefill, decode, elapsed) | yes: `prompt_tokens`, `cached_tokens`, `generated_tokens` | Only in the Performance requests table, per row. `summary()` has no token sums. The Models page sums prompt and generated tokens per model but leaves out cached tokens. |
| Strata | no durable rows (`/metrics` has a per-request `requests` list, but the admin ignores it) | no | Live `/metrics` totals since the process started (`parse_strata_metrics`); lost on restart |
| LiteLLM usage page | n/a | prompt and completion tokens per route, no cached tokens | per day and app, but only for traffic through the gateway, and one route covers every local model |
| Decision, embeddings sidecars | own stores | `input_tokens` / `tokens` | their own usage pages (out of scope) |

The gaps:

1. No token totals per day or model from the engine's own counts, and cached tokens are
   never totalled.
2. `inference_requests` rows are pruned after `observability_retention_days` (default 90),
   so any sum over that table loses older usage.
3. Strata requests leave no tokens behind once the process restarts.
4. Traffic the admin makes itself (quality runs, evals, test chat) is mixed into the
   counts with client traffic.

## Global Constraints

- Public repository: no real hosts, users, paths or keys in code, tests, fixtures or this
  plan. Use the placeholders in `AGENTS.md`.
- All commands run in the dev container (`docker exec -u vscode -w /workspaces/local-llm-admin $C …`).
- Gates: `uv run pytest --cov --cov-fail-under=80`, `uv run ruff check src tests scripts`,
  `uv run mypy src scripts` (strict). The rollup and fold paths need 100% line coverage.
- Write the test first for every behaviour. Stage only and never commit unless told to.
  No worktrees. Add a devlog entry after each task.
- Missing counts are never estimated. A request with no token count is counted as
  "unmeasured", like the existing "partial" rows.
- Migrations are append-only and re-runnable. This plan adds exactly one: v15.

## Definitions

- **Input tokens**: the engine's prompt token count for the request (`prompt_tokens`).
- **Cached tokens**: the part of the input the engine reused from its prompt cache
  (`cached_tokens`). Shown as a share of input, and as uncached input = input − cached.
- **Output tokens**: generated tokens (`generated_tokens`). Reasoning tokens are included,
  because the engine logs them that way.

Task 0 has to confirm, from real log lines, that `prompt_tokens` includes the cached part
and does not exclude it. If it turns out to exclude it, input = prompt + cached, and the
definitions above change.

## Tasks

### Task 0: Verify the counters on the Mac (read-only)

- [ ] On the Mac, send two identical long prompts through the MLX server, one after the
      other. Record the `Prefill completed` and `Request completed` lines. Check whether
      `prompt_tokens` stays the same while `cached_tokens` rises (cached is part of input)
      or whether `prompt_tokens` drops (cached is excluded).
- [ ] Check that `Request completed prompt_tokens` and `Prefill completed prompt_tokens`
      agree for the same request.
- [ ] Strata (see Task 3 for what the captured `/metrics` fixture already shows): find the
      size of the `requests` list (`requests_kept`). Check whether it is fixed or set by a
      flag. Confirm that, with the conversation cache on, `prompt_tokens` includes
      `reused`. Confirm that `totals.since` changes on every restart. Check whether the
      engine log `app/strata-<pack>.log` has a per-request line that could fill gaps.
- [ ] Add scrubbed fixture lines to `tests/fixtures/` and write the findings in the devlog.

### Task 1: Token rollup over `inference_requests`

Files: `performance_store.py`, `tests/test_performance_store.py`.

- [ ] `PerformanceStore.tokens(frm, to, *, tz_offset_min, group: "day" | "model" | "day_model", model=None)`
      returns rows of `requests, measured, unmeasured, input_tokens, cached_tokens,
      output_tokens`, plus `totals`. It is a single `SUM`/`COUNT` SQL query with
      `GROUP BY`, bucketed in the ops timezone the same way `summary()` does it.
- [ ] A row counts as measured when `prompt_tokens` and `generated_tokens` are both
      present. A null `cached_tokens` on a measured row adds 0 and increments a
      `cached_unknown` count, so the cache-hit share can say "of N requests".
- [ ] Cancelled and errored rows count with whatever tokens the engine logged.
      `generated_tokens` from `req_cancel` already lands on the row.
- [ ] `summary()` gains `input_tokens`, `cached_tokens` and `output_tokens` in each day
      and in the totals, so the existing endpoint carries them.
- [ ] Tests: empty range, null counts, timezone day boundary, model filter, cancelled row
      with partial output.

### Task 2: Totals that survive retention (migration v15)

Files: `storage.py`, `performance_store.py`, `tests/test_storage.py`, `tests/test_performance_store.py`.

- [ ] v15: `token_usage_daily(date TEXT, source TEXT, model TEXT, requests, measured,
      input_tokens, cached_tokens, output_tokens, PRIMARY KEY(date, source, model))`.
      Use `model = ''` for unknown.
- [ ] Fold on prune: in the same transaction as the delete, `prune()` adds the rows it
      is about to delete into `token_usage_daily` (an `INSERT … ON CONFLICT DO UPDATE`
      that adds), then deletes them. A crash between the two cannot double-count,
      because it is one transaction.
- [ ] `tokens()` adds folded counts to live rows per (date, model). There is never any
      overlap: a row is either folded and deleted, or still live. A day cut in half by
      the prune cut-off gets its folded half from the table and its live half from the
      rows. Test the boundary day explicitly.
- [ ] The Models page `request_totals()` uses the same merge, adds `cached_tokens`, and
      so stops losing tokens past 90 days.
- [ ] Tests: prune then query gives the same totals as before the prune; prune twice is
      idempotent; the migration re-runs cleanly.

### Task 3: Strata requests (depends on Task 0)

What Strata already gives us. Today the admin reads only `totals` from `/metrics`, for the
dashboard tile, and stores nothing. The captured fixture
(`tests/fixtures/strata/metrics.json`, Strata 0.1.44.1) shows that `/metrics` also returns
a bounded `requests` list, newest first, with one record per finished request:

| Field | Maps to `inference_requests` |
|---|---|
| `time` (the request's **start**: in the fixture, each `time + duration_s` equals the next record's `time`) | `started` |
| `duration_s` | `elapsed_s`; `finished = time + duration_s` |
| `prompt_tokens` (includes `reused`: `prompt_read = prompt_tokens − reused`) | `prompt_tokens` |
| `reused` | `cached_tokens` |
| `output_tokens` | `generated_tokens` |
| `prompt_ms`, `decode_ms`, `decode_tok_s` | `prefill_s`, `prefill_tok_s` (= prompt_read / prompt_ms), `decode_s`, `decode_tok_s` |
| `finish` (`stop`, `length`, …) | `finish_reason` |

There is no request id. `totals` (`since`, `requests`, `prompt_tokens`, `reused`,
`output_tokens`) covers everything since the process started.

Design: a `StrataRequests` poller, not a log parser.

- [ ] On the existing probe tick, while Strata is the active engine, call `GET /metrics`
      (one call, shared with the status probe). For each record, insert a row with
      `source='strata'`, `key = f"{since}:{time}"` (unique per process and request),
      `model='strata:<pack>'` (the pack from `/health`'s model id),
      `attribution='generation'`, `state='done'` and `complete=1`. `INSERT OR IGNORE` on
      `(source, key)` makes re-reading the same list a no-op.
- [ ] The timing columns come along for free, so Strata requests also show up in the
      Performance page's TTFT and decode charts. TTFT is roughly `prompt_ms`; this is
      noted in the UI.
- [ ] Gap accounting: keep `{since, requests, prompt_tokens, reused, output_tokens}` from
      the last poll in `kv`. If `since` is the same and the `totals` delta is larger than
      the new records add up to (more requests than `requests_kept` finished between
      two polls), write the difference as one `source='strata'`, `state='aggregate'`
      row. Its `key` is `f"{since}:gap:{requests}"`, it has
      `prompt_tokens`/`cached_tokens`/`generated_tokens` and no timings. Totals then
      always match Strata's own counters, and the per-request charts skip such rows.
- [ ] Restart: a new `since` means a new process, so all its records are new. What the
      old process did after the last poll and before it stopped is lost. The wrapper's
      stop could take a final `/metrics` snapshot before SIGTERM. That is a host-script
      change (`bin/strata`, needs approval to deploy), so it is optional; until then the
      loss is bounded by one probe interval and documented.
- [ ] Failed or cancelled requests: check in Task 0 whether they appear in `requests`.
      If they do not, they only show up in the gap row.
- [ ] Tests (using the fixture plus hand-made variants): first poll inserts 4 rows; a
      second identical poll inserts none; overflow beyond `requests_kept` produces a gap
      row with the right sums; a restart (new `since`); a pack switch; missing fields
      (read with `dict.get`, a record without `time` is skipped); `/metrics` 401 or
      timeout (no rows, no crash).

### Task 4: Separate the admin's own traffic

- [ ] Tag rows with `origin`: `quality` when `started` falls inside a quality or eval
      run's `[started, finished]` window (the run holds the lifecycle slot, so nothing
      else runs on the engine then), otherwise `client`. Test chat cannot be told apart
      in the log, so it stays `client` (documented).
- [ ] Stored as a column on `inference_requests` and a key part of `token_usage_daily`
      (both in v15). The summary filters on `origin`, defaulting to all.
- [ ] Tests: a row inside a run window, a row at the window edges, a run that is still
      open.

### Task 5: Performance page "Token usage"

Files: `templates/performance.html`, `static/performance.js`, `app.py`
(`GET /api/performance/tokens`), `tests/test_performance_api.py`.

- [ ] Tiles for the selected range: input, cached (with % of input), output, total
      requests and unmeasured.
- [ ] A daily stacked chart of uncached input, cached input and output, using the
      existing chart helpers.
- [ ] A per-model table: requests, input, cached, cache %, output, and first and last day.
      Folded days are included.
- [ ] Origin filter (all / client / quality), next to the existing model filter.
- [ ] A note under the section: the figures are the engines' own counts. They differ from
      the LiteLLM page, which counts only gateway traffic and per route.
- [ ] Models page: show cached tokens in the per-model total.
- [ ] API tests: shape, range validation (reuse the existing range parsing), model and
      origin filters.

### Task 6: Docs and close-out

- [ ] `docs/DESIGN.md` (performance section): the token rollup, the fold-on-prune and
      the Strata source.
- [ ] README: a line about the Token usage section.
- [ ] Run `scripts/check_public.py` before staging. Devlog entry.

## Review focus

1. **Double counting across the fold**: totals before and after a prune must be equal,
   including on the boundary day (Task 2).
2. **Cached semantics**: input must not be reported as prompt + cached if cached is
   already part of prompt (Task 0 decides).
3. **Strata counter resets**: a restart must neither erase usage nor add the full counter
   a second time (Task 3 fallback).

## Open questions

- Should test-chat traffic also be tagged `quality`? That would need the chat proxy to
  record its request windows. The plan leaves it as `client`.
- Should the decision and embedding sidecars' tokens be added to one "all local tokens"
  view? They have their own stores and pages, so the plan leaves them out.
