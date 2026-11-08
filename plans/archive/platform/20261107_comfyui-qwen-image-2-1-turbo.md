# ComfyUI + Qwen-Image-2.1-Turbo on the Mac mini

Date: 2026-11-07
Status: Done. Qualified on the Mac mini; engine restored to MLX.
Component: ComfyUI engine (host)

## Problem and goal

Article request: deploy Qwen-Image-2.1 with ComfyUI on the Mac mini
(<https://www.freedidi.com/25507.html>). Research shows the article's extra quant
repo is a mirror of the **official** `Qwen/Qwen-Image-2.1-Turbo` release, which ships
without a built-in safety checker; no third-party weight modification is involved
(`abenzerps/Qwen-Image-2.1-Turbo-Quantized`, `qwen-research` license, GGUF/int8/fp8/MLX quants
plus the companion text encoder and VAE).

Goal: serve Qwen-Image-2.1-Turbo (7B visual generation, unified text-to-image and image
editing, 8-step turbo sampling, native transparency, up to 10 reference images) from the
existing ComfyUI install on the Mac mini (M5 Pro, 64 GB), managed through the admin's
exclusive engine control.

## Research findings

- **Weights** (`abenzerps/Qwen-Image-2.1-Turbo-Quantized`):
  - `qwen-image-2.1-turbo-int8_convrot.safetensors` — 7.26 GB, native ComfyUI loader.
  - `qwen-image-2.1-turbo-Q4_K_M.gguf` — 4.05 GB, needs the GGUF loader node.
  - `text_encoders/qwen3vl_8b_int8_convrot.safetensors` — 9.35 GB (bf16 is 17.5 GB; skip).
  - `vae/qwen_image_2.1_vae_bf16.safetensors` — 0.68 GB.
  - MLX 4/6/8-bit quants also exist (MFLUX path, deferred).
- **Workflows** (Comfy-Org/workflow_templates, verified live):
  `image_qwen_image_2_1_turbo_t2i.json` (turbo: ManualSigmas 8-step schedule, Euler,
  CFG 1 — negative prompts have no effect; the plain `image_qwen_image_2_1_t2i.json` from
  the article is the 25–50 step non-turbo variant) and `image_qwen_image_2_1_image_edit.json`.
- **GGUF loader**: `leejet/ComfyUI-GGUF` custom node (verified live) + `gguf` pip package in
  the ComfyUI venv.
- **Existing install** (`host/README.md`, `host/environment/`): ComfyUI 0.37.0 at
  `/Volumes/Models/comfyui/app` (checkout recorded 2026-09-30, no custom nodes), venv
  Python 3.12, weights in `/Volumes/Models/comfyui/models`, port 8188, exclusive engine —
  starting ComfyUI stops MLX and the sidecars via the admin.
- **Memory budget**: int8 safetensors + int8 text encoder + VAE ≈ 18 GB peak — comfortable
  on 64 GB even alongside nothing else; GGUF path ≈ 14 GB. ComfyUI runs exclusively anyway.

## Path decision

- **Phase A — native safetensors (primary):** int8_convrot diffusion file, stock
  `UNETLoader`, no custom nodes. Most likely fastest on MPS and simplest to qualify.
- **Phase B — GGUF (article parity):** Q4_K_M + `leejet/ComfyUI-GGUF`. Smaller footprint;
  kept as the low-memory option and to match the article's setup.
- **Deferred to backlog:** MLX/MFLUX variant, multi-reference image-edit qualification,
  prompt-enhancer encoder (`qwen3.5_9b_...pe_t2i`), admin UI integration for image jobs.

## Implementation sequence

- [x] 1. Read-only preflight: 3.4 TiB free, checkout still `a716932` (0.37.0, clean),
      `comfy/ldm/qwen_image21` and `QwenImage21` already present. Turbo templates were
      missing from installed `comfyui-workflow-templates-json` 0.1.96.
- [x] 2. Checkout left at 0.37.0 (core support was already there). Templates packages
      updated in the ComfyUI venv (`comfyui-workflow-templates` 0.11.103,
      `comfyui-workflow-templates-json` 0.1.129); turbo t2i and turbo edit templates present.
      Environment files re-recorded.
- [x] 3. Weights downloaded with resumable curl into the ComfyUI model folders and
      verified against the repo `SHA256SUMS`. Files are named to match the stock templates
      (`qwen_image_2.1_turbo_int8_convrot.safetensors`, `qwen_image_2.1_turbo_Q4_K_M.gguf`,
      `qwen3vl_8b_int8_convrot.safetensors`, `qwen_image_2.1_vae_bf16.safetensors`).
- [x] 4. `leejet/ComfyUI-GGUF` at `373048b` installed with `gguf==0.19.0`. Because the
      launcher uses `--base-directory`, the node lives in
      `/Volumes/Models/comfyui/custom_nodes`, not `app/custom_nodes`.
- [x] 5. Admin engine switch to ComfyUI (job succeeded; exclusive stop of MLX and the
      running resident services). API smoke used the template chain: `UNETLoader`,
      `CLIPLoader` type `qwen_image`, `TextEncodeQwenImage21`, `QwenImage21Cache`,
      `CFGGuider` CFG 1, `ManualSigmas` 8-step schedule, Euler, `SamplerCustomAdvanced`,
      fixed seed. 1024×1024 coherent; 2048×2048 completed. Memory pressure stayed normal.
- [x] 6. Same chain with `UnetLoaderGGUF` and the Q4_K_M file. Both paths ran.
- [x] 7. Single-reference edit via `TextEncodeQwenImage21` (`images.image_1` plus VAE)
      and the encoder's matched latent. Reference was honored; CFG 2 kept the subject
      cleaner than the template's CFG 8 default on this prompt.
- [x] 8. Admin switch back to the previously selected MLX model succeeded. Resident
      services that were running before the switch came back; ComfyUI was stopped.
- [x] 9. Host records and this plan updated; devlog, leak scan, and staging follow.

## Outcome

ComfyUI 0.37.0 did not need a checkout upgrade. Prompt-enhancer weights were not
installed; the enhancer switch stayed off.

| Run | Prompt executed | Notes |
|---|---|---|
| int8 1024 cold | 62.8 s | ~5.4 s/it after the first step; includes weight load |
| int8 1024 warm | 46.1 s | 5.25 s/it |
| int8 2048 | 218.8 s | 25.9 s/it |
| GGUF Q4_K_M 1024 | 52.9–56.5 s | ~6.2–6.4 s/it; slower than int8, smaller file |
| edit, one reference | 113–154 s | CFG 2 preserved the subject; CFG 8 washed it out |

`memory_pressure` after the runs reported about 95% of system memory free. Peak weight
set is int8 diffusion + int8 text encoder + VAE (about 18 GB on disk).

## Acceptance and validation

- Turbo t2i at 1024×1024 completes end-to-end through the ComfyUI API with the documented
  8-step sigma schedule and produces a coherent saved image; memory pressure stays normal.
- Both safetensors and GGUF paths run; timings and quality notes recorded in the devlog.
- Engine exclusivity is preserved: the admin shows ComfyUI active while it runs, and the
  switch back to MLX restores prior services.
- All tracked files stay public-safe; host changes are reflected in `ops/host-diff.sh`.

## Non-goals

No admin UI changes for image generation, no MFLUX deployment, no benchmark matrix, no
content-filter tooling. The Turbo checkpoint ships without a safety checker; local use
remains subject to applicable law and the `qwen-research` license. Live Mac changes
(`deploy-mac.sh`-class actions and engine switches) require explicit approval first.

## References

- Article: <https://www.freedidi.com/25507.html>
- Weights: <https://huggingface.co/abenzerps/Qwen-Image-2.1-Turbo-Quantized>
- Base model: <https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo>
- Workflows: <https://github.com/Comfy-Org/workflow_templates> (`image_qwen_image_2_1_*`)
- GGUF node: <https://github.com/leejet/ComfyUI-GGUF>
- Host layout: [host/README.md](../../host/README.md), engine control: `src/local_llm_admin/engines.py`
