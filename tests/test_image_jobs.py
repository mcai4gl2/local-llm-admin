"""Template workflows for the image studio. No ComfyUI process."""

from __future__ import annotations

import pytest

from local_llm_admin.image_jobs import ImageJobError, build_workflow, templates


def _job(**overrides: object) -> dict:
    base = {
        "template": "turbo-t2i",
        "prompt": "a pale blue hanfu in a misty garden",
        "negative": "",
        "width": 1024,
        "height": 1024,
        "seed": 7,
        "model": "int8",
        "enhancer": False,
        "images": [],
    }
    base.update(overrides)
    return base


def test_history_images_keeps_basenames() -> None:
    from local_llm_admin.image_jobs import history_images

    found = history_images({"outputs": {"13": {"images": [
        {"filename": "studio_00001_.png", "subfolder": "", "type": "output"},
        {"filename": "../secret.png"},
    ]}}})
    assert found == [{"filename": "studio_00001_.png", "subfolder": "", "type": "output"}]


def test_templates_are_public_and_named() -> None:
    ids = [item["id"] for item in templates()]
    assert ids == ["turbo-t2i", "turbo-edit", "anima-turbo"]
    blob = str(templates())
    assert "192.168" not in blob
    assert "safety checker" not in blob.lower()


def test_t2i_uses_turbo_schedule_and_int8_loader() -> None:
    graph = build_workflow(_job())
    assert graph["1"]["class_type"] == "UNETLoader"
    assert graph["1"]["inputs"]["unet_name"] == "qwen_image_2.1_turbo_int8_convrot.safetensors"
    assert graph["6"]["inputs"]["cfg"] == 1.0
    assert graph["8"]["inputs"]["sampler_name"] == "euler"
    sigmas = [part.strip() for part in graph["9"]["inputs"]["sigmas"].split(",")]
    assert len(sigmas) == 9
    assert sigmas[0] == "1.0" and sigmas[-1] == "0"
    assert graph["10"]["inputs"] == {"width": 1024, "height": 1024, "batch_size": 1}
    assert graph["4"]["inputs"]["prompt"].startswith("a pale blue")
    assert "14" not in graph


def test_gguf_uses_gguf_loader() -> None:
    graph = build_workflow(_job(model="gguf"))
    assert graph["1"]["class_type"] == "UnetLoaderGGUF"
    assert graph["1"]["inputs"]["unet_name"].endswith(".gguf")


def test_edit_wires_each_reference_and_matched_latent() -> None:
    graph = build_workflow(_job(
        template="turbo-edit",
        images=["a.png", "b.png", "c.png"],
        width=1920,
        height=1088,
    ))
    assert "10" not in graph
    assert graph["11"]["inputs"]["latent_image"] == ["4", 2]
    assert graph["4"]["inputs"]["vae"] == ["3", 0]
    assert graph["4"]["inputs"]["images.image_1"] == ["20", 0]
    assert graph["4"]["inputs"]["images.image_3"] == ["22", 0]
    assert graph["20"]["inputs"]["image"] == "a.png"
    assert graph["22"]["inputs"]["image"] == "c.png"


def test_enhancer_inserts_prompt_encoder() -> None:
    graph = build_workflow(_job(enhancer=True))
    assert graph["14"]["class_type"] == "CLIPLoader"
    assert "pe_t2i" in graph["14"]["inputs"]["clip_name"]
    assert graph["15"]["class_type"] == "TextGenerate"
    assert graph["4"]["inputs"]["prompt"] == ["15", 0]
    edit = build_workflow(_job(template="turbo-edit", enhancer=True, images=["a.png"]))
    assert "pe_i2i" in edit["14"]["inputs"]["clip_name"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"prompt": "  "},
        {"width": 1000},
        {"height": 64},
        {"model": "bf16"},
        {"template": "missing"},
        {"template": "turbo-edit", "images": []},
        {"template": "turbo-edit", "images": [f"{i}.png" for i in range(11)]},
        {"template": "turbo-t2i", "images": ["x.png"]},
        {"images": ["../secret.png"]},
        {"seed": -1},
    ],
)
def test_rejects_bad_jobs(overrides: dict) -> None:
    with pytest.raises(ImageJobError):
        build_workflow(_job(**overrides))


def test_anima_uses_tag_prefix_and_turbo_sampler() -> None:
    graph = build_workflow(_job(
        template="anima-turbo", prompt="1girl, adult, shrine maiden, red hakama", width=832, height=1216,
    ))
    assert graph["1"]["inputs"]["unet_name"] == "anima-turbo-v1.1.safetensors"
    assert graph["4"]["inputs"]["text"].startswith("masterpiece, best quality, score_7, safe, ")
    assert "1girl, adult" in graph["4"]["inputs"]["text"]
    assert graph["7"]["inputs"]["steps"] == 10
    assert graph["7"]["inputs"]["cfg"] == 1.0
    assert graph["6"]["inputs"] == {"width": 832, "height": 1216, "batch_size": 1}


def test_anima_rejects_oversized_frame() -> None:
    with pytest.raises(ImageJobError):
        build_workflow(_job(template="anima-turbo", width=1920, height=1088))
