"""M7: the sidecar slot's spec, process view and registry."""

import subprocess

import pytest

from local_llm_admin.sidecars import (
    INTENT_KEY,
    SidecarProcess,
    SidecarRegistry,
    SidecarSpec,
    openjev_spec,
    read_intent,
    write_intent,
)
from local_llm_admin.storage import Database


def test_openjev_spec_follows_settings(fake_decision):
    s = fake_decision.settings
    spec = openjev_spec(s)
    assert (spec.name, spec.label, spec.title) == ("openjev", "decision", "Decision sidecar")
    assert spec.port == s.decision_port and spec.api_base == s.decision_api_base
    assert spec.pid_file == s.decision_root / "run" / "openjev.pid"
    assert spec.allowed_mlx == frozenset({"qwen"}) and spec.check_id == "decision"
    assert spec.down_alert_key == "decision_down" and spec.footprint_gb == s.decision_footprint_gb


def test_spec_allows_only_listed_models():
    spec = SidecarSpec(name="x", label="X", title="X sidecar", port=1, allowed_mlx=frozenset({"a"}))
    assert spec.allows("a") and not spec.allows("b") and not spec.allows(None)


def test_registry_reads_settings_on_every_call(fake_decision):
    s = fake_decision.settings
    reg = SidecarRegistry(s)
    assert [x.name for x in reg.specs()] == ["openjev"]
    s.decision_allowed_mlx = "other"
    assert reg.get("openjev").allowed_mlx == frozenset({"other"})
    s.decision_enabled = False
    assert reg.specs() == [] and [x.name for x in reg.specs(enabled_only=False)] == [
        "openjev",
        "qwen-tts",
    ]
    with pytest.raises(KeyError):
        reg.get("openjev")
    with pytest.raises(KeyError):
        reg.get("nope")
    assert reg.down_sync("openjev") is True  # a disabled sidecar is never "up"


def test_process_status_start_stop(fake_decision):
    reg = SidecarRegistry(fake_decision.settings, {"openjev": fake_decision.transport})
    assert reg.running() == [] and reg.down_sync("openjev")
    fake_decision.start()
    proc = reg.process("openjev")
    st = proc.status()
    assert st.running and st.pid == int(
        fake_decision.settings.decision_root.joinpath("run/openjev.pid").read_text()
    )
    assert [x.name for x in reg.running()] == ["openjev"]
    assert reg.ready_sync("openjev") and proc.rss_bytes(st.pid) > 0
    subprocess.run(reg.argv("openjev", "stop"), check=True, capture_output=True)
    assert reg.down_sync("openjev")


def test_reused_pid_is_not_the_sidecar(fake_decision):
    s = fake_decision.settings
    (s.decision_root / "run/openjev.pid").write_text("1")  # init, not openjev
    assert SidecarRegistry(s).process("openjev").status().pid is None


def test_argv_rejects_unknown_actions(fake_decision):
    with pytest.raises(ValueError):
        SidecarRegistry(fake_decision.settings).argv("openjev", "rm")


def test_cli_status_parses_weights(fake_decision):
    proc = SidecarProcess(lambda: openjev_spec(fake_decision.settings), None, 5.0)
    out = proc.cli_status()
    assert out["ok"] and out["weights"] == "complete"
    fake_decision.settings.decision_bin = fake_decision.root / "missing"
    out = proc.cli_status()
    assert not out["ok"] and out["weights"] is None


def test_warmup_registration(fake_decision):
    reg = SidecarRegistry(fake_decision.settings)
    assert reg.warmup("openjev") is None
    reg.set_warmup("openjev", lambda: True)
    assert reg.warmup("openjev")() is True


def test_tts_spec_follows_settings(fake_tts):
    from local_llm_admin.sidecars import tts_spec

    s = fake_tts.settings
    spec = tts_spec(s)
    assert (spec.name, spec.label, spec.title, spec.check_id) == (
        "qwen-tts",
        "TTS",
        "TTS sidecar",
        "tts",
    )
    assert spec.port == s.tts_port and spec.ready_path == s.tts_ready_path
    assert spec.pid_file == s.tts_root / "run" / "qwen-tts.pid" and spec.down_alert_key == "tts_down"
    assert spec.allowed_mlx == frozenset({"qwen"})


def test_registry_lists_both_in_order(fake_tts):
    reg = SidecarRegistry(fake_tts.settings)
    assert [s.name for s in reg.specs()] == ["openjev", "qwen-tts"]
    fake_tts.start()
    assert [s.name for s in reg.running()] == ["qwen-tts"]


def test_tts_settings_defaults():
    from local_llm_admin.config import Settings

    s = Settings(_env_file=None)
    assert s.tts_enabled is False and s.tts_port == 8083 and s.tts_max_input_chars == 10_000
    assert s.tts_path == s.tts_root / "bin" / "qwen-tts" and len(s.tts_voice_list) == 9
    s.tts_voices = "Vivian, Ryan"
    assert s.tts_voice_list == ["Vivian", "Ryan"]


def test_intent_roundtrip(tmp_path):
    db = Database(tmp_path / "a.sqlite")
    assert read_intent(db) is None
    write_intent(db, "openjev", "running", {"id": "j9"})
    got = read_intent(db)
    assert got is not None
    assert (got["name"], got["value"], got["job_id"]) == ("openjev", "running", "j9")
    assert db.get_kv(INTENT_KEY) == got


# --- always-on services (M8) ------------------------------------------------------------

from local_llm_admin.config import Settings  # noqa: E402
from local_llm_admin.jobs import JobStore  # noqa: E402
from local_llm_admin.sidecars import (  # noqa: E402
    embed_spec,
    read_service_intent,
    write_service_intent,
)


def _embed_settings(tmp_path, **over):
    kw = {
        "admin_data_dir": tmp_path / "data",
        "admin_log_dir": tmp_path / "logs",
        "embed_enabled": True,
        "embed_root": tmp_path / "embed",
    }
    return Settings(**{**kw, **over})


def test_embed_spec_fields(tmp_path):
    spec = embed_spec(_embed_settings(tmp_path, embed_allowed_mlx="a, b"))
    assert (spec.name, spec.label, spec.kind, spec.port) == ("qwen-embed", "embeddings", "service", 8084)
    assert spec.noun == "the embeddings service"
    assert spec.intent_key == "embed_intent" and spec.down_alert_key == "embed_down"
    assert spec.allowed_mlx == frozenset({"a", "b"})
    assert spec.pid_file == tmp_path / "embed/run/qwen-embed.pid"
    assert spec.cli_path == tmp_path / "embed/bin/qwen-embed"


def test_slot_never_sees_the_service(tmp_path):
    reg = SidecarRegistry(_embed_settings(tmp_path))
    assert "qwen-embed" not in {s.name for s in reg.specs(enabled_only=False)}
    with pytest.raises(KeyError):
        reg.get("qwen-embed")
    assert [s.name for s in reg.services()] == ["qwen-embed"]
    assert reg.get_service("qwen-embed").kind == "service"
    assert reg.argv("qwen-embed", "start")[-1] == "start"  # process lookup covers services
    assert reg.down_sync("qwen-embed") is True


def test_disabled_service_is_hidden(tmp_path):
    reg = SidecarRegistry(_embed_settings(tmp_path, embed_enabled=False))
    assert reg.services() == [] and reg.services_running() == []
    with pytest.raises(KeyError):
        reg.get_service("qwen-embed")
    assert reg.down_sync("qwen-embed") is True


def test_service_intent_round_trip(tmp_path):
    s = _embed_settings(tmp_path)
    store = JobStore(s.admin_data_dir / "admin.sqlite")
    spec = embed_spec(s)
    assert read_service_intent(store, spec) in (None, "stopped")
    write_service_intent(store, spec, "running", {"id": "j1"})
    assert read_service_intent(store, spec) == "running"
    assert store.get_kv("embed_intent")["job_id"] == "j1"


def test_unavailable_reason_for_a_disabled_service(tmp_path):
    from local_llm_admin.sidecars import service_unavailable_reason

    s = _embed_settings(tmp_path, embed_enabled=False)
    reg = SidecarRegistry(s)
    store = JobStore(s.admin_data_dir / "admin.sqlite")
    assert service_unavailable_reason(reg, store, "qwen-embed", lambda: True) == "stopped"
