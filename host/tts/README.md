# TTS sidecar (Qwen3-TTS on mlx-audio)

Text to speech for LAN clients and the admin TTS page. Plan:
pre-split design notes (private).

| Path | What |
|---|---|
| `bin/qwen-tts` | `status`, `start`, `stop` (versioned, deployed by `ops/deploy-host.sh`) |
| `venv/` | its own venv (mlx-audio pinned in `host/environment/tts-app.txt`) |
| `models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit/` | weights (~3.1 GB) |
| `etc/qwen-tts.env` | mode 600, optional: `TTS_API_KEY`, `TTS_LOG_LEVEL` |
| `logs/qwen-tts.log`, `run/qwen-tts.pid` | log and pid |

Rules:

- Listens on `127.0.0.1:8083` only. Host, port and model are fixed by `bin/qwen-tts`.
- Never next to ComfyUI or the decision sidecar. Starting it by hand skips the admin's
  memory check.
- LAN access is the admin gateway `POST :8082/v1/audio/speech`, not this port.

```sh
/Volumes/Models/tts/bin/qwen-tts start
curl -s http://127.0.0.1:8083/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"/Volumes/Models/tts/models/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit","input":"Hello.","voice":"Vivian","response_format":"wav"}' \
  -o /tmp/hello.wav
```
