# Strata engine with Qwen3.8-Flash-Next (Q2_0 first, IQ3_XXS gated)

Date: 2026-10-15
Status: Done (2026-10-16). Phases 0–6 complete. Q2_0 was measured in a sandbox
(`docs/DESIGN.md` §3b) and is the default pack at 262144. IQ3_XXS was tried at 131072 and
failed the decision gate on speed (decode 26–32 tok/s, 4K prompt 427 tok/s); it is kept as a
second live pack by the operator's choice (still faster than the 27B dense MLX models).
Deployed 2026-10-16: Strata serves on MLX's port 8080, so the existing LiteLLM route reaches
whichever engine is running (decision 4), and the Engine dropdown switches between the packs
(decision 7). The sandbox was deleted after Phase 0.

## 1. Goal

Run Qwen3.8-Flash-Next (125B MoE, 262K context) on the Mac mini through the
[Strata](https://github.com/Niko1221/Strata) engine, and manage it from the admin as a
third GPU engine next to MLX and ComfyUI: start, stop, switch, status, metrics, logs, chat
test, compatibility checks and LiteLLM usage.

The user asked for the IQ3 quant and wanted to know whether Q2 is needed instead.

## 2. Feasibility (research, 2026-10-15)

Sources: Strata `README.md`, `docs/MAC.md`, `docs/DETAILS.md`; Unsloth's Qwen3.8-Flash-Next
page. All figures are the projects' own claims and have not been checked on our hardware.

### 2.1 The Mac against Strata's Mac guidance

| Fact | Source | Effect on us |
|---|---|---|
| Mac engine (Metal) is **experimental**. It is built from source (`./setup.sh`, `build-metal/strata`), needs macOS 15+ and the Command Line Tools. No prebuilt Mac binary | MAC.md, README | We build on the Mac; the build is part of the host setup |
| Measured Macs: M5 Max 128 GB (resident) and M4 Mac mini 32 GB (streamed). **M5 Pro 64 GB is not measured** | MAC.md | Our numbers are new; Phase 0 must measure them |
| Pack sizes on Mac: Q2_0 37.6 GB (needs ~46 GB), IQ3_XXS 47.0 GB (needs ~55 GB), IQ3_S 54.8 GB (~63 GB) | MAC.md | — |
| Recommendation by memory: **64 GB → Q2_0**; IQ3_XXS recommended from **74 GB** | MAC.md | Our 64 GB Mac is below the IQ3_XXS line |
| Resident packs are capped at 70% of RAM, the engine's anonymous memory at 60% (64 GB+). The engine streams experts from disk when they exceed 60% of Metal's share | MAC.md | 70% of 64 GB ≈ 44.8 GB: Q2_0 (37.6 GB) stays resident; IQ3_XXS (47.0 GB) does not and will be **streamed** |
| Streaming was measured only at 32 GB, and "the model file must be on the internal SSD. External disks are untested and likely slower." Streaming also needs a 34–40 GB experts' copy | MAC.md | Our weights live on the external `/Volumes/Models` drive. IQ3_XXS needs ~47 + ~40 GB on the **internal** disk |
| The caps exist because of two documented kernel panics on the M5 Max | MAC.md | Do not override them with `STRATA_METAL_STREAM=0` to force IQ3_XXS resident |
| Context: setup recommends **32K below 96 GB**; measured up to 32K | MAC.md | Start at 32K; our MLX models run 262K today |
| One request at a time on Mac (`--parallel` not ported) | MAC.md | Same as our MLX single decode slot |
| MTP speculative decoding works on Mac (`--spec 2`, token-identical); vision works but was checked on one image | MAC.md | Compatibility cases `vision` may be `unsupported`/flaky at first |

### 2.2 Speed we can expect

M5 Max 128 GB, resident, short prompt, MTP on: Q2_0 ~120 tok/s, IQ3_XXS ~109 tok/s decode;
prompt ~1,250–1,450 tok/s. M4 Mac mini 32 GB, streamed, no MTP: Q2_0 ~14 tok/s, IQ3_XXS
~9–10 tok/s.

Our M5 Pro has roughly half the M5 Max's memory bandwidth, so Q2_0 resident should land
well under the Max's numbers, perhaps 50–70 tok/s (estimate, not measured). IQ3_XXS
streamed on 64 GB sits somewhere between the two measured cases: most experts fit in the
page cache, but every miss is a disk read. Phase 0 measures both.

### 2.3 Quality difference

Unsloth's KLD table for the same model (their UD quants, not Strata's packs, so only a
rough guide): UD-Q2_K_XL top-1 82.7%, mean KLD 0.225; UD-IQ3_XXS top-1 85.4%, mean KLD
0.165. IQ3 is better, but the gap is a few points, not a different class.

### 2.4 Verdict

- **Q2_0 is the safe target** for a 64 GB Mac: Strata's own recommendation, resident,
  fits under the 70% cap with ~7 GB margin, and can be loaded from the external drive.
- **IQ3_XXS is possible but not recommended as the default.** It runs only in streaming
  mode on 64 GB, a mode measured only on a 32 GB Mac, and needs ~87 GB free on the
  internal disk. Treat it as a Phase 0 experiment: adopt it only if it beats a threshold
  we set (§4, Phase 0).
- **Either way, nothing else can run beside it.** With Q2_0 using ~46 GB, the decision,
  TTS and embedding sidecars must stop while Strata runs (same rule as ComfyUI).

## 3. Design decisions

1. **Strata is an engine, not a sidecar.** It owns the GPU and most of memory, so it joins
   `EngineKind` as `strata`, exclusive with MLX and ComfyUI. `plan_transition` stops
   whichever engine runs and all sidecars and always-on services before starting it, and
   restores the services when leaving it, exactly as for ComfyUI.
2. **The admin drives it only through a host wrapper and its HTTP API**, as with
   `comfyui/bin/comfyui`. New host script `strata/bin/strata {start,stop,status}` (zsh),
   versioned under `host/strata/` and listed in `ops/host-files.list`. The admin never
   imports Strata, never runs its venv, never writes under its folders.
3. **Install location.** The Phase 0 experiment runs in its own isolated sandbox (§4,
   Phase 0) and is deleted afterwards. The production install (Phase 1 on) puts the Strata
   checkout, build and `Strata-data` under `/Volumes/Models/strata/` (Q2_0 resident is read
   once at start). If IQ3_XXS is adopted,
   its pack and experts' copy must move to the internal disk; the wrapper takes the data
   directory from its env file so this is a configuration change, not a code change.
4. **Same port as MLX** (revised 2026-10-16; first planned as its own loopback port 8086).
   The operator's LiteLLM has one route to the MLX server's `:8080`, and switching engines
   should change only what answers there. The wrapper starts Strata with
   `--host 0.0.0.0 --port 8080`; it answers under any model name, so the route needs no
   change. Status stays unambiguous without the port: host scripts recognise Strata's server
   by its command line, the admin by its pid file and its `/health` (`service: "strata"`).
5. **Pack selection is configuration.** The wrapper reads `STRATA_MODEL` (`Q2_0` or
   `IQ3_XXS`) and `STRATA_CONTEXT` (default 262144) from its env file and runs the matching
   `run-<model>.sh` settings. One pack runs at a time. Revised 2026-10-16 (both packs
   installed): the Engine control has a `strata:<pack>` entry per installed pack and
   switches between them; `bin/strata start <pack>` remembers the last pack.
6. **LiteLLM route.** The existing local route (revised 2026-10-16): it sends the MLX
   upstream string, which usage already counts as local; the switch history records
   `strata`, so usage can say which engine answered. Strata's own ids stay in
   `LITELLM_LOCAL_MODELS` for clients that name them.
7. **Auth like MLX** (revised 2026-10-16; first planned as loopback plus a key). On the LAN
   without a key, as the MLX server is, so LiteLLM's route needs no key; Strata's Host check
   still refuses names it does not know. Strata adds a little MLX does not: a LAN client can
   change its model settings or unload the model (no MCP tools are set up). `STRATA_API_KEY`
   in its env file turns a key on; then LiteLLM must send it.

## 4. Phases

Each code phase follows the repo rules: test first, `pytest --cov-fail-under=80`, ruff,
mypy strict, devlog entry, public-safe text.

### Phase 0 — Isolated experiment on the Mac (manual, needs approval)

**Rule: the experiment must not change the Mac's system or the existing setup.** Everything
it creates lives in one sandbox directory, and deleting that directory undoes it.

**Why not a VM or a container.** A macOS VM gets a paravirtual GPU and a slice of memory,
and Docker on macOS has no Metal at all, so neither can run or measure Strata's Metal
engine. Creating a separate macOS user account would itself change the system. Isolation is
therefore done by confinement plus a before/after audit.

**Sandbox layout** (`SANDBOX` = `/Volumes/Models/strata-spike` for Q2_0; for IQ3_XXS a
directory on the internal disk such as `/Users/you/strata-spike`, because streaming needs
the internal SSD):

| Path | Holds |
|---|---|
| `$SANDBOX/app` | Strata checkout at a pinned commit; its own `.python/`, `.venv/`, `build-metal/` |
| `$SANDBOX/data` | `Strata-data`: models, packs, MTP layer (`--data-dir`, `--models-dir`) |
| `$SANDBOX/home` | Fake `HOME` with `.config`, `.cache`, `Library/Caches`, `tmp` |
| `$SANDBOX/logs`, `$SANDBOX/results` | Engine log, benchmark output, recorded fixtures |
| `$SANDBOX/audit` | Before/after snapshots |

**Confinement.** Every Strata command runs through one launcher, `$SANDBOX/env.sh`, that
starts from a clean environment (`env -i`) and sets only:

- `HOME=$SANDBOX/home`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME` under it,
  `TMPDIR=$SANDBOX/home/tmp`;
- `HF_HOME=$SANDBOX/home/.cache/huggingface`, `PIP_CACHE_DIR`, `UV_CACHE_DIR`,
  `PYTHONNOUSERSITE=1`, `PIP_REQUIRE_VIRTUALENV=1`;
- `PATH=/usr/bin:/bin:/usr/sbin:/sbin` (no Homebrew, no MLX venv, no uv-managed Python);
- `STRATA_API_KEY` set to a throwaway value.

And these rules:

- **No `sudo`**, no `xcode-select --install`, no Homebrew. If the Command Line Tools are
  missing (`xcode-select -p` fails), stop and ask: installing them is a system change.
- **No LaunchAgent, no login item, no MCP or agent registration**, no shell rc edits.
  Strata runs in the foreground of an SSH session (`tmux`/`nohup` inside the sandbox) and
  stops when the experiment ends.
- **Loopback only**: `--host 127.0.0.1 --port 8086`, so no firewall or Local Network prompt.
- `--no-start --no-browser` on setup; `prevent_sleep: false` in `strata-<model>.json`
  (no `caffeinate`/`pmset`). No `sysctl` changes; Strata's own memory caps stay on.
- Nothing is written under `/Volumes/Models/{bin,etc,run,models,venvs,python,logs}`, and
  the admin, MLX and LiteLLM configuration are untouched.

**Audit before running.** Read the pinned `setup.sh` and `setup.py` and grep the checkout
for writes outside the sandbox (`expanduser`, `~/`, `Library`, `/usr/local`, `sudo`,
`launchctl`, `caffeinate`, `pmset`, `sysctl`, `defaults write`, `xcode-select`, `brew`).
Anything found is either redirected by `env.sh`, disabled by a flag, or a stop-and-ask.

**Before/after snapshot** (`$SANDBOX/audit/{before,after}`, then `diff`): listings with
mtimes of the real `~/.config`, `~/.cache`, `~/Library/{LaunchAgents,Caches,Application
Support,Preferences}`, `/Library/{LaunchAgents,LaunchDaemons}`, `/usr/local`,
`/opt/homebrew` (`brew list --versions`); `crontab -l`; `pmset -g`; `sysctl
iogpu kern.memorystatus_vm_pressure_level`; `launchctl list`; `ls /Volumes/Models` and the
`ops/host-diff.sh` result. The only accepted difference is macOS's own Metal shader cache
under the per-user `DARWIN_USER_CACHE_DIR` (managed by the OS, not redirectable); it is
recorded, not treated as a failure.

**Steps**

1. Check free space: internal disk and `/Volumes/Models` (Q2_0: ~38 GB pack + ~7 GB MTP and
   pack prep + build; IQ3_XXS: ~47 GB + ~40 GB experts' copy on the internal disk).
2. Take the "before" snapshot. Run the audit of the pinned checkout.
3. Stop MLX, ComfyUI and all sidecars from the admin (Engine → off).
4. Through `env.sh`: `./setup.sh --model Q2_0 --context 32768 --port 8086 --host 127.0.0.1
   --no-browser --no-start --data-dir $SANDBOX/data --models-dir $SANDBOX/data/models`.
   Record the commit and build time.
5. Start it, then record as fixtures (scrubbed): `GET /health`, `/v1/models`, `/props`,
   `/status`, `/slots`, `/metrics` (JSON and `?format=prometheus`), and 200 lines of the
   engine log around a request (start, prefill, decode, done).
6. Measure with `ops/decision-phase0/bench.py`-style runs: decode tok/s (short prompt and
   a 2K-token story), prompt tok/s at 4K and 32K, TTFT, peak memory, memory pressure, swap,
   start time to `/health` loaded, and stop time to port closed.
7. Repeat 4–6 for IQ3_XXS in its internal-disk sandbox.
8. Check stop behaviour: does `SIGTERM` exit cleanly and release memory? Does the server
   detach from the shell so it can outlive the admin (`nohup`/`start_new_session`)?
9. Stop Strata, confirm `:8086` is closed, take the "after" snapshot and diff it.
10. Copy `results/` (scrubbed) into the repo's fixtures, then delete the sandbox(es). Keep
    the downloaded GGUF only if the decision gate passes and the operator says so (it can be
    moved into the production location in Phase 1 instead of downloaded again).
11. Restore the previous engine and services from the admin; `ops/host-diff.sh` must match.

**Decision gate.** Adopt IQ3_XXS only if all hold: no swap and memory pressure never
`critical`; decode ≥ 60% of Q2_0's decode on the same prompts; prompt ≥ 500 tok/s at 4K;
three cold starts without error. Otherwise ship Q2_0. Results go in the devlog and a short
§3a-style table in `docs/DESIGN.md`.

### Phase 1 — Host wrapper `strata/bin/strata`

- `start`: refuses if `:8080`, `:8188` or `:<STRATA_PORT>` listen, or `run/mlx.lock` or
  `run/maintenance.lock` exist (same guards as `comfyui`); starts the engine in its own
  session with output appended to `/Volumes/Models/logs/strata.log`; writes
  `strata/run/strata.pid`.
- `stop`: `SIGTERM`, wait up to 60 s, then `SIGKILL`; removes the pid file.
- `status`: prints `strata: running pid N` or `strata: stopped`, then `model: <pack>` and
  `context: <n>` (stable lines the admin parses, like `comfyui: …`).
- Env file `strata/etc/strata.env` (not versioned; example `host/strata/etc/strata.env.example`).
- Tests: `tests/test_host_strata.py` runs it under real zsh with the host harness and a
  fake engine (like `test_host_openjev.py`). `mlx-model start` and `comfyui start` gain a
  guard that refuses while Strata listens (update `host/bin/mlx-model`,
  `host/comfyui/bin/comfyui` and their tests).
- Add the wrapper, README and env example to `ops/host-files.list`.

### Phase 2 — Engine control in the admin

- `config.py`: `strata_root`, `strata_bin`, `strata_api_base`, `strata_port`,
  `strata_ready_timeout_s` (default 300 s: a cold start loads ~38–47 GB).
- `engines.py`: `StrataClient` (`argv`, `running` via pid file then port, `ready_sync`
  via `/health` reporting loaded, `status` from `/health` + `/props` + `/metrics`);
  `EngineKind = Literal["mlx", "comfyui", "strata", "off"]`; `parse_target("strata")`.
- Generalise `resolve_active` and `badge_state` from two booleans to the set of running
  engines (`conflict` when more than one runs; new states `strata`, `strata-starting`).
- `plan_transition`: stop the current engine and every sidecar/service, then start Strata
  and wait for ready; rollback restarts the previous engine. Leaving Strata restores the
  services meant to run. Add `strata_argv`, `strata_down`, `strata_ready` to `EngineOps`.
- Tests first in `tests/test_engine_plan.py` (every from/to pair among off, MLX, ComfyUI,
  Strata; forced and refused cases), `tests/test_engines.py` (status parsing against the
  Phase 0 fixtures), `tests/test_engine_api.py`.

### Phase 3 — Status, metrics, logs, performance

- Dashboard card for Strata: pack, context, loaded, requests running, KV use, TTFT,
  expert-tier hit rate (`/metrics` JSON, read with `dict.get` for schema tolerance).
- Metrics sampler: the "engine process" tile follows the Strata pid too.
- Logs: allow-list `strata` → `/Volumes/Models/logs/strata.log`; level highlighting for
  `WARNING`, `LOW: requests may stall`, `non-finite logits`.
- Performance page: per-request decode and prompt tok/s from `/metrics` totals (or the log
  lines, whichever Phase 0 shows is stable), stored like the MLX samples.

### Phase 4 — Chat and compatibility

- The chat proxy and `compatibility.py` currently target `:8080` with the MLX active model
  id. Make the target follow the active engine: Strata's base URL and the model id from its
  `/v1/models`.
- Run the M2 cases against Strata. Expect `vision` only if setup used `--vision yes`;
  `reasoning_effort_*` maps to Strata's thinking levels (off, low, medium, high).
- Tests with `tests/fake_openai.py` serving Strata-shaped responses.

### Phase 5 — Usage

- Add the Strata route's upstream model string to the `LITELLM_LOCAL_MODELS` default and
  the docs; switch history records `strata:<pack>` so the usage page labels the days.
- Contract test with a scrubbed spend-log row for the new route.

### Phase 6 — Deploy (needs approval)

1. `ops/deploy-host.sh` dry run, then `--apply` the wrapper and the updated guards.
2. `ops/deploy-mac.sh` for the admin.
3. Engine → Strata from the admin; run the compatibility checks; send one request through
   LiteLLM; check the usage page.
4. Rollback: Engine → previous MLX model; `ops/rollback-mac.sh` if the admin itself fails.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Mac engine is experimental; kernel panics were reported on the M5 Max before the caps | Keep Strata's caps; never force resident above them; Phase 0 watches memory pressure; one engine at a time |
| The experiment changes the Mac outside its sandbox (`setup.py` is not fully audited yet) | Phase 0 confinement (`env -i` launcher, fake `HOME`, no sudo/Homebrew/LaunchAgent), source audit before running, before/after snapshot diff; anything unexpected stops the experiment |
| Upstream moves fast (docs changed within days) | Pin a commit; record it in `host/environment/strata.txt`; upgrade as a separate change |
| IQ3_XXS streaming from the external drive is slow or unstable | Keep its data on the internal disk; gate adoption on Phase 0 numbers; Q2_0 is the fallback |
| Cold start of 40+ GB ties up the Mac for minutes | Long ready timeout; switch dialog warns; LiteLLM requests fail during the switch (same as MLX) |
| `/metrics` or log formats change between Strata versions | Read with `dict.get`; fixtures from the pinned commit; missing fields hide tiles instead of failing |
| Sidecars cannot run next to Strata | Same stop/restore rule as ComfyUI; the decision gateway and embedding users see "paused" |
| 32K context instead of today's 262K | Resolved 2026-10-16: 262144 measured (needles found at 197.5K tokens, ~1 GB GPU memory left); `docs/DESIGN.md` §3b |

## 6. Open questions

1. ~~Is a 32K context enough for the clients that will use this route?~~ Runs at 262144 (§3b).
2. ~~Should Strata replace the 27B MLX model as the default route, or sit beside it?~~
   Neither: it shares MLX's URL, and the Engine control picks what serves behind it.
3. Is Swift 1.5 (shorter thinking) wanted? Strata says it is not set up on Mac yet.
4. ~~How much internal disk is free?~~ Enough: IQ3_XXS data lives on the internal disk.
