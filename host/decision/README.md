# Decision sidecar (OpenJev on DiffusionGemma, MLX)

Typed questions about a state (yes/no `noul`, `choice`, `score`), answered with
probabilities from one read-only denoising pass of DiffusionGemma 26B-A4B. No text
is generated. Plan and measurements: pre-split design notes
(private).

| Path | What |
|---|---|
| `bin/openjev` | `status`, `start`, `stop` (versioned in the repo, deployed by `ops/deploy-host.sh`) |
| `app/` | OpenJev checkout, pinned commit in `host/environment/decision-app.txt` |
| `venv/` | its own venv (mlx-vlm 0.6.15); never the MLX venv |
| `models/diffusiongemma-26B-A4B-it-4bit/` | weights (16.5 GB) |
| `etc/openjev.env` | mode 600: `OPENJEV_API_KEY`, `OPENJEV_MLX_CACHE_LIMIT_GB` (others ignored) |
| `logs/openjev.log`, `run/openjev.pid` | log and pid |

Rules:

- Listens on `127.0.0.1:8081` only. Host, port, backend and model path are fixed by
  `bin/openjev`, whatever the env file says. LAN clients use the admin's gateway (`:8082`).
- Runs next to the MLX server, never next to ComfyUI (both scripts refuse).
- Starting it by hand skips the admin's memory check. Both models together peak at
  ~46 GB at a 30k Qwen prompt; Qwen decodes slower while the sidecar is busy.

```sh
/Volumes/Models/decision/bin/openjev start
KEY=$(sed -n 's/^OPENJEV_API_KEY=//p' /Volumes/Models/decision/etc/openjev.env)
curl -s http://127.0.0.1:8081/v1/systemone -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"state": "My card was charged twice", "model": "openjev-latest",
       "questions": {"is_billing": {"type": "noul", "instructions": "Is this a billing issue?"}}}'
```

## LAN access: the admin gateway (:8082)

Other machines never reach `:8081`. The admin (when `DECISION_ENABLED=true`) also listens on
`0.0.0.0:8082` with the same request and response bodies as OpenJev, a key per client
(create, rename and revoke them on the admin's Decision page), in-flight limits, and usage
per client and per day (counts, types and timings only; never the state or the questions).

```sh
curl -s http://mac-mini.local:8082/v1/systemone \
  -H "Authorization: Bearer dsk_xxxxxxxx_..." -H 'Content-Type: application/json' \
  -d '{"state": "My card was charged twice", "model": "openjev-latest",
       "questions": {"is_billing": {"type": "noul", "instructions": "Is this a billing issue?"}}}'
```

`GET /v1/models` (with a key) and `GET /healthz` (no key) are the only other routes. A stopped
sidecar returns 503; the gateway never starts it.
