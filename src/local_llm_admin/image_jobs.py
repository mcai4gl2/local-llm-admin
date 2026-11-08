"""ComfyUI prompt graphs for the image studio.

The installed checkpoint is the official Qwen-Image-2.1-Turbo quant. It ships without
a safety checker. The blog's GGUF link redirects to that same repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"
MAX_REFS = 10
MIN_EDGE = 256
MAX_EDGE = 2048

MODELS: dict[str, dict[str, str]] = {
    "int8": {
        "loader": "UNETLoader",
        "unet": "qwen_image_2.1_turbo_int8_convrot.safetensors",
        "label": "Turbo int8",
    },
    "gguf": {
        "loader": "UnetLoaderGGUF",
        "unet": "qwen_image_2.1_turbo_Q4_K_M.gguf",
        "label": "Turbo Q4_K_M GGUF",
    },
}
PE = {
    "turbo-t2i": (
        "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors",
        "stable_diffusion",
        "image_pe_t2i.txt",
    ),
    "turbo-edit": (
        "qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors",
        "qwen_image",
        "image_pe_i2i.txt",
    ),
}


class ImageJobError(ValueError):
    """The studio request cannot be turned into a workflow."""


def models() -> list[dict[str, str]]:
    return [{"id": key, "label": spec["label"]} for key, spec in MODELS.items()]


def catalog() -> dict[str, Any]:
    return {
        "templates": templates(),
        "models": models(),
        "checkpoint": (
            "Qwen-Image-2.1-Turbo int8 and Q4_K_M. The blog's GGUF link redirects "
            "to this official quant. It has no safety checker."
        ),
        "sizes": [
            {"label": "Square 1024", "width": 1024, "height": 1024},
            {"label": "Landscape 1920×1088", "width": 1920, "height": 1088},
            {"label": "Portrait 1088×1920", "width": 1088, "height": 1920},
            {"label": "Anime portrait 832×1216", "width": 832, "height": 1216},
        ],
    }


def history_images(entry: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    outputs = entry.get("outputs")
    if not isinstance(outputs, dict):
        return found
    for node in outputs.values():
        if not isinstance(node, dict):
            continue
        images = node.get("images")
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict) or not isinstance(image.get("filename"), str):
                continue
            name = Path(image["filename"]).name
            if name != image["filename"]:
                continue
            raw_sub = image.get("subfolder")
            subfolder = raw_sub if isinstance(raw_sub, str) else ""
            raw_kind = image.get("type")
            kind = raw_kind if raw_kind in ("output", "temp", "input") else "output"
            found.append({"filename": name, "subfolder": subfolder, "type": str(kind)})
    return found


def templates() -> list[dict[str, Any]]:
    return [
        {
            "id": "turbo-t2i",
            "label": "Text to image",
            "kind": "t2i",
            "refs": 0,
            "note": "8-step Turbo, Euler, CFG 1. Negative prompts are ignored at CFG 1.",
        },
        {
            "id": "turbo-edit",
            "label": "Edit with references",
            "kind": "edit",
            "refs": MAX_REFS,
            "note": "1 to 10 reference images. The output keeps the first image's size.",
        },
        {
            "id": "anima-turbo",
            "label": "Anime characters",
            "kind": "t2i",
            "refs": 0,
            "max_edge": 1536,
            "note": (
                "Anima Turbo, Danbooru-style tags, 10 steps, CFG 1. "
                "Adult illustration only. CircleStone non-commercial license."
            ),
        },
    ]


def _edge(raw: object, name: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ImageJobError(f"{name} must be an integer")
    if raw < MIN_EDGE or raw > MAX_EDGE or raw % 32 != 0:
        raise ImageJobError(f"{name} must be a multiple of 32 between {MIN_EDGE} and {MAX_EDGE}")
    return raw


def _name(raw: object) -> str:
    if not isinstance(raw, str) or not raw or raw != Path(raw).name or "/" in raw or "\\" in raw:
        raise ImageJobError("reference image name is invalid")
    return raw


ANIMA_PREFIX = "masterpiece, best quality, score_7, safe, "
ANIMA_NEGATIVE = (
    "worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts, chromatic aberration"
)


def _anima_workflow(
    prompt: str, negative: str, width: int, height: int, seed: int
) -> dict[str, dict[str, Any]]:
    if width > 1536 or height > 1536:
        raise ImageJobError("anime template size must be at most 1536 on each side")
    extra = negative.strip()
    return {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "anima-turbo-v1.1.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["2", 0], "text": ANIMA_PREFIX + prompt.strip()}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["2", 0], "text": ANIMA_NEGATIVE + (", " + extra if extra else "")}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0],
            "seed": seed, "steps": 10, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "anima"}},
    }


def build_workflow(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    template = raw.get("template")
    if template not in (*PE, "anima-turbo"):
        raise ImageJobError("unknown template")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ImageJobError("prompt is required")
    if len(prompt) > 8000:
        raise ImageJobError("prompt is too long")
    negative = raw.get("negative") or ""
    if not isinstance(negative, str) or len(negative) > 2000:
        raise ImageJobError("negative prompt is invalid")
    seed = raw.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 or seed > 2**63 - 1:
        raise ImageJobError("seed must be a non-negative integer")
    width, height = _edge(raw.get("width"), "width"), _edge(raw.get("height"), "height")
    images = raw.get("images") or []
    if not isinstance(images, list) or any(not isinstance(item, str) for item in images):
        raise ImageJobError("images must be a list of names")
    names = [_name(item) for item in images]
    if template == "turbo-t2i" and names:
        raise ImageJobError("text to image does not take reference images")
    if template == "turbo-edit" and not names:
        raise ImageJobError("edit needs at least one reference image")
    if len(names) > MAX_REFS:
        raise ImageJobError(f"at most {MAX_REFS} reference images")
    if template == "anima-turbo":
        if names:
            raise ImageJobError("anime template does not take reference images")
        return _anima_workflow(prompt, negative, width, height, seed)
    model = MODELS.get(str(raw.get("model") or ""))
    if model is None:
        raise ImageJobError("unknown model")
    enhancer = raw.get("enhancer") is True

    graph: dict[str, dict[str, Any]] = {
        "1": {"class_type": model["loader"], "inputs": {"unet_name": model["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "4": {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["2", 0], "prompt": prompt.strip(), "negative_prompt": negative, "resolution": 1024}},
        "5": {"class_type": "QwenImage21Cache", "inputs": {"model": ["1", 0], "device": "auto", "dtype": "default"}},
        "6": {"class_type": "CFGGuider", "inputs": {
            "model": ["5", 0], "positive": ["4", 0], "negative": ["4", 1], "cfg": 1.0}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "9": {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["7", 0], "guider": ["6", 0], "sampler": ["8", 0], "sigmas": ["9", 0],
            "latent_image": ["10", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": "studio"}},
    }
    if template == "turbo-t2i":
        graph["10"] = {"class_type": "EmptyLatentImage", "inputs": {
            "width": width, "height": height, "batch_size": 1}}
    else:
        graph["4"]["inputs"]["vae"] = ["3", 0]
        for index, name in enumerate(names):
            node = str(20 + index)
            graph[node] = {"class_type": "LoadImage", "inputs": {"image": name}}
            graph["4"]["inputs"][f"images.image_{index + 1}"] = [node, 0]
        graph["11"]["inputs"]["latent_image"] = ["4", 2]
    if enhancer:
        clip_name, clip_type, prompt_file = PE[template]
        graph["14"] = {"class_type": "CLIPLoader", "inputs": {
            "clip_name": clip_name, "type": clip_type, "device": "default"}}
        graph["15"] = {"class_type": "TextGenerate", "inputs": {
            "clip": ["14", 0],
            "prompt": prompt.strip(),
            "max_length": 4096,
            "sampling_mode": "on",
            "sampling_mode.temperature": 1.0,
            "sampling_mode.top_k": 20,
            "sampling_mode.top_p": 0.95,
            "sampling_mode.min_p": 0.0,
            "sampling_mode.repetition_penalty": 1.0,
            "sampling_mode.seed": seed,
            "sampling_mode.presence_penalty": 1.5,
            "thinking": True,
            "use_default_template": True,
            "mtp": "auto",
            "system_prompt": (HERE / prompt_file).read_text(),
        }}
        graph["4"]["inputs"]["prompt"] = ["15", 0]
    return graph
