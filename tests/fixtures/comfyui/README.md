# ComfyUI API fixtures

Shaped from ComfyUI 0.37.0 source (commit a7169322485d, `server.py` `get_queue`/`get_history`,
`comfy_execution/jobs.py`), the version recorded in `host/environment/comfyui-app.txt`.
They are not captures from the Mac: prompts are reduced to two placeholder nodes and ids are
made up. On 2026-10-01 live `/queue` and `/history` payloads from the Mac (same commit) were compared against them: same tuple length and types, `extra_data` keys `client_id`/`create_time`, history keys `prompt/outputs/status/meta`, status keys `status_str/completed/messages`.

- `queue.json`: `GET /queue` with one running and two pending items (5-element tuples:
  number, prompt_id, prompt, extra_data, outputs_to_execute; sensitive index 5 removed).
- `history.json`: `GET /history` with one finished job that saved an image and a video.
