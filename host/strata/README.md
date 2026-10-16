# Strata engine (Qwen3.8-Flash-Next on Metal)

A third GPU engine next to MLX and ComfyUI: Qwen3.8-Flash-Next (125B MoE) through
[Strata](https://github.com/Niko1221/Strata)'s Metal build. Plan: `plans/20261015_strata-qwen38-flash-next.md`.

| Path | What |
|---|---|
| `bin/strata` | `status`, `start`, `stop` (versioned, deployed by `ops/deploy-host.sh`) |
| `app/` | Strata checkout at the commit in `host/environment/strata.txt`, with its own `.python/`, `.venv/`, `build-metal/` and the `strata-<pack>.json` run configs its `./setup.sh` writes |
| `data/` | `Strata-data` for Q2_0: GGUF packs, MTP layer (`--data-dir`, `--models-dir`) |
| `/Users/you/strata/data` | `Strata-data` for IQ3_XXS, on the internal SSD: the pack streams its experts from disk, which Strata supports only from the internal drive (~111 GB with the experts' copy) |
| `etc/strata.env` | mode 600: `STRATA_MODEL` (`Q2_0` default), `STRATA_CONTEXT` (shown only), optional `STRATA_API_KEY` and `STRATA_ALLOWED_HOSTS` |
| `home/` | `HOME`, caches and `TMPDIR` the engine runs with (never the real home) |
| `logs/strata.log`, `run/strata.pid`, `run/strata.pack` | log (rotated at 50 MiB by `start`), pid, the pack the last start used |

Rules:

- Serves on `0.0.0.0:8080`, the MLX server's address: only one engine runs at a time, so
  LiteLLM's one route reaches whichever runs, and the model name it sends does not matter
  (Strata answers under any name). Host and port are fixed by `bin/strata`.
- Without `STRATA_API_KEY` the API is open on the LAN, like the MLX server; a LAN client can
  also change Strata's model settings or unload the model (no MCP tools are set up). With a
  key, every client (LiteLLM too) must send it. Strata answers Host names that are an IP, its
  own name or `.local` name; `STRATA_ALLOWED_HOSTS` adds others.
- `bin/mlx-model` and `comfyui/bin/comfyui` recognise Strata by its command line
  (`serve/server.py --engine strata`), from `run/strata.pid` or the `:8080` listener:
  `mlx-model stop` never kills it.
- An engine, not a sidecar: it never runs next to MLX, ComfyUI, the sidecars or the embedding
  service (`bin/strata`, `bin/mlx-model` and `comfyui/bin/comfyui` refuse). The admin's
  Engine control stops the others first and restores the services when you switch away.
- Strata's own memory caps stay on. Never force a pack resident above them
  (`STRATA_METAL_STREAM=0`): the caps exist because of kernel panics on larger Macs.
- A pack or context change is a Strata `./setup.sh --model <pack> --context <n> --no-start`
  run (from `app/`, with `HOME` pointed at `home/`), then `STRATA_MODEL` in `etc/strata.env`.
  Both packs are installed: Q2_0 at 262144 (resident, MTP drafts, ~60 tok/s) and IQ3_XXS at
  131072 (streamed, no drafts, ~30 tok/s). The admin's Engine control lists one entry per
  installed pack (`strata:<pack>`); by hand, `bin/strata start IQ3_XXS`. The last pack
  started is kept in `run/strata.pack`, so a plain `start` (the watchdog's) runs it again;
  `STRATA_MODEL` is the default before any pack was picked. Status reads the context from
  the pack's run config.

```sh
/Volumes/Models/strata/bin/strata start      # loading takes a few minutes
curl -s http://127.0.0.1:8080/health   # "service": "strata"
/Volumes/Models/strata/bin/strata stop
```
