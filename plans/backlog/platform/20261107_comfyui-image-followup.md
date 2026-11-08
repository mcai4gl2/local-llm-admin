# ComfyUI image follow-up

Status: Optional. The MFLUX 4-bit image is done; only further timings remain.
Component: ComfyUI engine (host)

The [archived qualification](../../archive/platform/20261107_comfyui-qwen-image-2-1-turbo.md)
qualified int8 safetensors and Q4_K_M GGUF text-to-image, plus a single-reference edit,
on the existing ComfyUI 0.37.0 install. Local use stays subject to applicable law and the
`qwen-research` license. The Turbo checkpoint ships without a safety checker.

## Deferred

- MFLUX 6-bit and 8-bit timings. The 4-bit Turbo image exists at
  `/Volumes/Models/mflux/output/turbo-q4.png` (~25 s at 1024×1024, peak 6.28 GB). 6 and 8
  are the same command with `--quantize`. Next decision: only if a comparison is wanted.
- A 10-reference timing pass. The studio and a 3-reference edit already use the
  same `images.image_N` inputs. Next decision: only if a fixed 10-image set is needed.
