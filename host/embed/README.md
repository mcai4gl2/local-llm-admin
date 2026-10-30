# Embedding service (Qwen3-Embedding + Qwen3-Reranker on MLX-VLM)

Always-on embeddings and rerank for LAN clients (Cortex first) and the admin `/embed` page.
Plan: pre-split design notes (private).

| Path | What |
|---|---|
| `bin/qwen-embed` | `status`, `start`, `stop` (versioned, deployed by `ops/deploy-host.sh`) |
| `venv/` | its own venv (mlx-vlm pinned in `host/environment/embed-app.txt`) |
| `models/Qwen3-Embedding-4B-8bit/`, `models/Qwen3-Reranker-0.6B/` | weights (~4.5 GB + ~1.2 GB) |
| `etc/qwen-embed.env` | mode 600, optional: `EMBED_API_KEY`, `EMBED_LOG_LEVEL` |
| `logs/qwen-embed.log`, `run/qwen-embed.pid`, `cache/hf/` | log, pid, offline HF cache |

Rules:

- Listens on `127.0.0.1:8084` only. Host, port and models are fixed by `bin/qwen-embed`.
- Never next to ComfyUI (both scripts refuse). It does run next to the MLX server and
  whichever sidecar holds the slot (TTS or OpenJev).
- The admin's Embeddings card owns start and stop, and its memory check counts this
  service. Starting it by hand skips that check.
- LAN access is the admin gateway `POST :8082/v1/embeddings` and `/v1/rerank`, not this port.

```sh
/Volumes/Models/embed/bin/qwen-embed start
curl -s http://127.0.0.1:8084/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model":"/Volumes/Models/embed/models/Qwen3-Embedding-4B-8bit","input":["hello"]}' | head -c 300
```
## Resident catalog integration

Embeddings retains its service/model IDs, private settings and independent intent. The
shared resident guard now accounts for every configured resident and the target MLX model.
Before OCR is provisioned, legacy embedding detection remains available. Once its host
payload is installed, a missing or mismatched private resident catalog refuses new starts.
Generate it from the same admin settings as the [OCR runtime](../ocr/README.md). Existing
embedding/rerank request history is read without migration or copying; logs are available
as the `embed` source. Native embedding dependencies are not upgraded for OCR.
