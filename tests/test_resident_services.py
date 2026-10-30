"""Resident catalog validation and legacy embedding contracts."""

import json
import os
import time
from dataclasses import replace

import psutil
import pytest

from local_llm_admin.config import Settings
from local_llm_admin.sidecars import (
    SidecarRegistry,
    embed_spec,
)


def test_embedding_registration_preserves_public_contract():
    settings = Settings(embed_enabled=True)
    spec = embed_spec(settings)
    assert spec.name == "qwen-embed" and spec.intent_key == "embed_intent"
    assert spec.kind == "service" and spec.cli_path == settings.embed_path
    assert [(m.id, m.capability) for m in spec.models] == [
        (settings.embed_model_id, "embeddings"),
        (settings.rerank_model_id, "rerank"),
    ]
    assert spec.ui_path == "/embed"
    assert spec.log_sources == ("embed",)


def test_catalog_contains_disabled_ocr_without_changing_embedding_defaults():
    registry = SidecarRegistry(Settings())
    assert registry.services() == []
    assert [s.name for s in registry.services(enabled_only=False)] == ["qwen-embed", "paddleocr-vl"]


def test_third_service_registers_without_shared_special_cases(monkeypatch):
    from local_llm_admin import sidecars
    from local_llm_admin.sidecars import ServiceModel

    def third(settings):
        return replace(
            embed_spec(settings), name="third", port=8099, intent_key="third_intent",
            models=(ServiceModel("third-model", "classification"),),
        )

    monkeypatch.setattr(sidecars, "SERVICE_BUILDERS", [sidecars.embed_spec, third])
    registry = SidecarRegistry(Settings(embed_enabled=True))
    assert [s.name for s in registry.services()] == ["qwen-embed", "third"]
    assert registry.get_service("third").models[0].capability == "classification"


@pytest.mark.parametrize("collision", ["name", "port", "intent_key", "models"])
def test_catalog_rejects_ambiguous_ownership(monkeypatch, collision):
    from local_llm_admin import sidecars
    from local_llm_admin.sidecars import ServiceModel

    def third(settings):
        original = embed_spec(settings)
        fields = dict(name="third", port=8099, intent_key="third_intent",
                      models=(ServiceModel("third-model", "classification"),))
        fields[collision] = getattr(original, collision)
        return replace(original, **fields)

    monkeypatch.setattr(sidecars, "SERVICE_BUILDERS", [sidecars.embed_spec, third])
    with pytest.raises(ValueError, match="duplicate|conflict"):
        SidecarRegistry(Settings(embed_enabled=True))


def test_disabled_running_services_still_own_resources(monkeypatch):
    registry = SidecarRegistry(Settings(embed_enabled=False))
    from local_llm_admin.sidecars import SidecarProcess, SidecarStatus
    monkeypatch.setattr(SidecarProcess, "status", lambda self: SidecarStatus(
        self.spec.name == "qwen-embed", 123 if self.spec.name == "qwen-embed" else None
    ))
    assert [s.name for s in registry.services_running()] == ["qwen-embed"]
    assert not registry.down_sync("qwen-embed")


@pytest.mark.parametrize("problem", ["missing", "cycle", "duplicate", "identity", "port"])
def test_catalog_rejects_invalid_component_graph(monkeypatch, problem, tmp_path):
    from local_llm_admin import sidecars
    from local_llm_admin.sidecars import ServiceComponent
    child = ServiceComponent("vlm", 8098, tmp_path / "child.pid", "vlm")
    if problem == "missing":
        components = (replace(child, requires=("absent",)),)
    elif problem == "cycle":
        components = (replace(child, requires=("vlm",)),)
    elif problem == "duplicate":
        components = (child, child)
    elif problem == "identity":
        components = (replace(child, cmdline_marker=""),)
    else:
        components = (replace(child, port=8084),)
    monkeypatch.setattr(sidecars, "SERVICE_BUILDERS", [
        lambda settings: replace(embed_spec(settings), components=components)
    ])
    with pytest.raises(ValueError):
        SidecarRegistry(Settings(embed_enabled=True))


def test_ocr_requires_explicit_free_ports_and_reservation():
    with pytest.raises(ValueError, match="ports"):
        SidecarRegistry(Settings(ocr_enabled=True))
    registry = SidecarRegistry(Settings(
        ocr_enabled=True, ocr_port=8097, ocr_vlm_port=8098, ocr_footprint_gb=12.0,
    ))
    spec = registry.get_service("paddleocr-vl")
    assert spec.models[0].id == "local-ocr" and spec.kind == "service"
    assert spec.components[0].port == 8098


def test_generation_checked_service_refuses_a_reused_pid(tmp_path, monkeypatch):
    from local_llm_admin.sidecars import SidecarProcess
    process = psutil.Process(os.getpid())
    spec = replace(embed_spec(Settings()), pid_file=tmp_path / "api.pid",
                   cmdline_marker=process.cmdline()[0], generation_checked=True)
    spec.pid_file.write_text(str(process.pid))
    proc = SidecarProcess(lambda: spec, None, 1)
    assert proc.status().running and not proc.status().owned


    record = {"pid": process.pid, "pgid": os.getpgid(process.pid),
              "command": " ".join(process.cmdline()),
              "started": time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(process.create_time()))}
    spec.pid_file.with_suffix(".json").write_text(json.dumps(record))
    assert proc.status().owned
    record["started"] = "Thu Jan 01 00:00:00 1970"
    spec.pid_file.with_suffix(".json").write_text(json.dumps(record))
    assert proc.status().running and not proc.status().owned


def test_unknown_resident_owner_is_never_available(monkeypatch):
    from local_llm_admin.sidecars import SidecarProcess, SidecarStatus, service_unavailable_reason
    registry = SidecarRegistry(Settings(embed_enabled=True))
    monkeypatch.setattr(SidecarProcess, "status", lambda _: SidecarStatus(True, None, owned=False))
    assert service_unavailable_reason(registry, None, "qwen-embed", lambda: None) == "ownership_unknown"


@pytest.mark.parametrize("fields", [
    {"name": "../owner"}, {"kind": "sidecar"}, {"intent_key": ""}, {"models": ()},
])
def test_catalog_rejects_incomplete_service_identity(fields):
    from local_llm_admin.sidecars import validate_services
    with pytest.raises(ValueError):
        validate_services([replace(embed_spec(Settings()), **fields)])


def test_orphan_component_owns_resources_even_without_the_api_record(tmp_path):
    from local_llm_admin.sidecars import ServiceComponent, SidecarProcess
    process = psutil.Process(os.getpid())
    child = tmp_path / "child.pid"
    child.write_text(str(process.pid))
    spec = replace(embed_spec(Settings()), pid_file=tmp_path / "absent.pid", components=(
        ServiceComponent("pipeline", None, child, process.cmdline()[0]),
    ))
    proc = SidecarProcess(lambda: spec, None, 1)
    assert proc.status().running and not proc.status().owned and not proc.down_sync()


def test_disabled_slot_owner_cannot_disappear_from_admission(monkeypatch):
    from local_llm_admin.sidecars import SidecarProcess, SidecarStatus
    registry = SidecarRegistry(Settings(decision_enabled=False))
    monkeypatch.setattr(SidecarProcess, "status", lambda self: SidecarStatus(
        self.spec.name == "openjev", 123, owned=True))
    assert registry.ownership_conflicts() == ["openjev"]


@pytest.mark.parametrize("field,value", [
    ("embed_port", 8083), ("embed_port", 8080), ("embed_model_id", "qwen3-tts"),
])
def test_resident_registration_rejects_slot_and_engine_collisions(field, value):
    with pytest.raises(ValueError, match="duplicate|conflict"):
        SidecarRegistry(Settings(**{field: value}))
