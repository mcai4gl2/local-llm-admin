# Image studio and remaining Qwen-Image follow-up

Date: 2026-11-07
Status: Done. Studio deployed; MFLUX 4-bit qualified; engine restored to MLX.
Component: admin / ComfyUI

## Goal

A logged-in page where a template, prompt, size and optional reference images are enough
to generate. No ComfyUI graph editing. Finish the deferred follow-up: prompt-enhancer
encoders, multi-reference edit wiring, and the MFLUX path. The blog's GGUF repo
redirects to the Turbo quant already installed; the studio exposes that checkpoint, which
ships without a safety checker.

## Scope

- Pure workflow builder for turbo text-to-image and turbo edit (1–10 references), int8 and
  GGUF, optional enhancer nodes.
- `/images` page and `/api/images/*`. Generation requires ComfyUI; the page can request the
  existing engine switch.
- Download the two int8 prompt-enhancer encoders. Qualify a 3-reference edit on the Mac.
- Install MFLUX and qualify one Turbo generation at 4-bit. 6-bit and 8-bit stay available
  as the same command with `-q`. Do not add MFLUX as a second exclusive engine in this change.

## Outcome

- [x] Studio page and API, deployed. Turbo text-to-image, edit (1–10 slots), and Anima Turbo.
- [x] Enhancer encoders downloaded (`qwen-pe done fail=0`, about 8.8 GB each). Not used in a live generation yet.
- [x] Three-reference edit completed in 315 seconds.
- [x] Anima Turbo weights installed. Shrine-maiden portrait at 832×1216 completed in 15.3 seconds.
- [x] MFLUX 0.24.0 installed at `/Volumes/Models/venvs/mflux`. Recorded in `host/environment/mflux.txt`.
- [x] MFLUX 4-bit Turbo image. `/Volumes/Models/mflux/output/turbo-q4.png`: 8 steps at
      about 3.0 s/it (~25 s), peak MLX memory 6.28 GB at 1024×1024, 4-bit quantization.
      The output matched the prompt. Weights now cached on the volume, so 6-bit and 8-bit
      are quick follow-up runs.

Optional follow-up stays in [backlog](../backlog/platform/20261107_comfyui-image-followup.md): a 10-reference timing pass, and 6/8-bit timings after the 4-bit image exists.

## Non-goals

No third-party refusal-layer LoRAs. No content-filter bypass tooling. No commit unless asked.
