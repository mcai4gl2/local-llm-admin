"""M6 Phase 2: the decision sidecar adapter (decision.py)."""

import json
import math
import time
from types import SimpleNamespace

import pytest

from local_llm_admin.alerts import AlertStore
from local_llm_admin.decision import (
    DecisionClient,
    DecisionError,
    normalize_answers,
    parse_server_timing,
    upstream_error,
    validate_request,
)
from local_llm_admin.jobs import JobStore
from local_llm_admin.sidecars import (
    SidecarMonitor,
    SidecarRegistry,
    context_tokens,
    kv_bytes_per_token,
    mlx_estimate_gb,
    write_intent,
)

from .conftest import FIX

QWEN36 = {
    "model_type": "qwen3_5_moe",
    "text_config": {
        "num_hidden_layers": 40,
        "num_attention_heads": 16,
        "num_key_value_heads": 2,
        "head_dim": 256,
        "full_attention_interval": 4,
        "layer_types": ["linear_attention"] * 3 * 10 + ["full_attention"] * 10,
        "max_position_embeddings": 262144,
    },
}


def fx(name):
    return json.loads((FIX / "decision" / f"{name}.json").read_text())


def body(**over):
    b = {
        "state": {"ticket": "charged twice"},
        "model": "openjev-latest",
        "questions": {
            "is_billing": {"type": "noul", "instructions": "Billing?"},
            "department": {"type": "choice", "criteria": {"Billing team": "money", "tech": "bugs"}},
            "urgency": {"type": "score", "criteria": ["low", "high", {"label": "critical"}]},
        },
    }
    b.update(over)
    return b


# --- request validation ---------------------------------------------------------------


def test_recorded_fixture_requests_validate():
    for name in ("noul", "choice", "score"):
        assert validate_request(fx(name)["request"])["questions"]


def test_valid_request_keeps_upstream_shape():
    out = validate_request(body(steps=2, samples=3))
    assert out["steps"] == 2 and out["samples"] == 3
    assert out["questions"]["department"]["criteria"] == {"Billing team": "money", "tech": "bugs"}
    assert "images" not in out


def test_model_defaults_to_latest():
    b = body()
    del b["model"]
    assert validate_request(b)["model"] == "openjev-latest"


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"model": "gpt"}, "model"),
        ({"questions": {}}, "questions"),
        ({"questions": {f"q{i}": {"type": "noul"} for i in range(33)}}, "questions"),
        ({"questions": {"bad id!": {"type": "noul"}}}, "questions"),
        ({"questions": {"q": {"type": "maybe"}}}, "questions"),
        ({"questions": {"q": {"type": "choice", "criteria": {"only": "one"}}}}, "questions"),
        (
            {"questions": {"q": {"type": "choice", "criteria": {f"c{i}": "" for i in range(27)}}}},
            "questions",
        ),
        ({"questions": {"q": {"type": "score", "criteria": ["one"]}}}, "questions"),
        ({"questions": {"q": {"type": "noul", "extra": 1}}}, "questions"),
        ({"steps": 9}, "steps"),
        ({"samples": 9}, "samples"),
        ({"images": ["data:image/png;base64,AA=="]}, "images"),
        ({"think": 100}, "think"),
        ({"sequential": True}, "sequential"),
    ],
)
def test_invalid_requests_name_the_field(change, where):
    with pytest.raises(DecisionError) as e:
        validate_request(body(**change))
    assert e.value.code == 400 and where in e.value.message


def test_oversized_state_and_body_refused():
    with pytest.raises(DecisionError, match="state is larger"):
        validate_request(body(state="x" * 33_000))
    big = {f"c{i}": "d" * 3000 for i in range(26)}
    with pytest.raises(DecisionError, match="request is larger"):
        validate_request(body(questions={"q": {"type": "choice", "criteria": big}}))


def test_non_json_body_refused():
    with pytest.raises(DecisionError, match="not JSON"):
        validate_request({"state": {1, 2}})


# --- responses --------------------------------------------------------------------------


def test_normalize_recorded_answers():
    (noul,) = normalize_answers(fx("noul")["response"])
    assert noul["type"] == "noul" and noul["top"] == "yes" and not noul["invalid"]
    assert math.isclose(noul["probs"]["yes"] + noul["probs"]["no"], 1.0)
    (choice,) = normalize_answers(fx("choice")["response"])
    assert choice["top"] == "billing" and 0.8 < choice["confidence"] < 0.9
    (score,) = normalize_answers(fx("score")["response"])
    assert score["top"] == "2" and 2.0 < score["expected"] < 2.3
    assert score["legend"]["3"] == "critical"


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "choice", "choice": "a", "probabilities": {"a": 0.7, "b": 0.7}},
        {"type": "choice", "choice": "a", "probabilities": {"a": -0.1, "b": 1.1}},
        {"type": "choice", "choice": "a", "probabilities": {"a": float("nan"), "b": 1.0}},
        {"type": "choice", "choice": "a", "probabilities": {}},
        {"type": "score", "probabilities": "x"},
        {"type": "noul", "noul": "high"},
        "garbage",
    ],
)
def test_bad_distributions_are_invalid_not_shown(answer):
    (row,) = normalize_answers({"answers": {"q": answer}})
    assert row["invalid"] and row["probs"] is None and row["top"] is None


def test_normalize_tolerates_missing_answers():
    assert normalize_answers({}) == [] and normalize_answers({"answers": []}) == []


def test_server_timing():
    assert parse_server_timing("model;dur=0.0, server;dur=400.9, total;dur=400.9") == {
        "model": 0.0,
        "server": 400.9,
        "total": 400.9,
    }
    assert parse_server_timing(None) == {} and parse_server_timing("junk") == {}


@pytest.mark.parametrize(
    ("status", "detail", "code", "text"),
    [
        (400, {"detail": "Choice question must have at least one choice: x"}, 400, "at least one"),
        (
            400,
            {"detail": {"error_type": "api_usage_error", "message": "Invalid request."}},
            400,
            "Invalid",
        ),
        (401, {"detail": {"message": "bad key"}}, 502, "admin's key"),
        (403, None, 502, "admin's key"),
        (429, {}, 429, "busy"),
        (529, {"detail": {"error_type": "overloaded"}}, 429, "busy"),
        (500, "x", 502, "HTTP 500"),
        (418, None, 400, "HTTP 418"),
    ],
)
def test_upstream_error_mapping(status, detail, code, text):
    err = upstream_error(status, detail)
    assert err.code == code and text in err.message


# --- memory budget ---------------------------------------------------------------------


def test_kv_bytes_counts_full_attention_layers_only():
    assert kv_bytes_per_token(QWEN36) == 20_480  # Phase 0 R2
    interval_only = {"text_config": {**QWEN36["text_config"], "layer_types": None}}
    assert kv_bytes_per_token(interval_only) == 20_480
    dense = {
        "num_hidden_layers": 64,
        "num_attention_heads": 40,
        "num_key_value_heads": 8,
        "hidden_size": 5120,
    }
    assert kv_bytes_per_token(dense) == 64 * 8 * 128 * 4
    assert kv_bytes_per_token({"num_hidden_layers": 2}) is None


def test_context_tokens_prefers_profile_cap():
    assert context_tokens(QWEN36, {"launch": {"max_kv_size": 131072}}) == 131072
    assert context_tokens(QWEN36, {"launch": {}}) == 262144
    assert context_tokens(QWEN36, None) == 262144
    assert context_tokens({"max_position_embeddings": 4096}, None) == 4096
    assert context_tokens({}, None) is None


def test_mlx_estimate():
    gb = mlx_estimate_gb(19 * 1024**3, QWEN36, 262144)
    assert gb == pytest.approx(19 + 5.0)
    assert mlx_estimate_gb(1, {}, 10) is None and mlx_estimate_gb(1, QWEN36, None) is None


# --- client --------------------------------------------------------------------------


@pytest.fixture
def client(fake_decision):
    return DecisionClient(fake_decision.settings, transport=fake_decision.transport)


def test_argv_allow_list(client, fake_decision):
    assert client.argv("start") == [str(fake_decision.settings.decision_path), "start"]
    with pytest.raises(ValueError):
        client.argv("rm")


async def test_status_stopped_then_running(client, fake_decision):
    st = await client.status()
    assert st["running"] is False and st["ready"] is False and st["pid"] is None
    fake_decision.start()
    st = await client.status()
    assert st["running"] and st["ready"] and st["pid"] == client.pid_from_file()
    assert st["rss_bytes"] and st["rss_bytes"] > 0


def test_reused_pid_is_not_the_sidecar(client, fake_decision):
    import os

    (fake_decision.root / "run/openjev.pid").write_text(str(os.getpid()))
    assert client.running().running is False
    assert client.down_sync() is True
    (fake_decision.root / "run/openjev.pid").write_text("garbage")
    assert client.running().running is False


def test_rss_of_missing_process(client):
    assert client.rss_bytes(None) is None and client.rss_bytes(999_999_999) is None


def test_cli_status_reports_weights(client, fake_decision):
    st = client.cli_status()
    assert st["ok"] and st["weights"] == "complete"
    (fake_decision.state / "decision_weights").write_text("incomplete")
    assert client.cli_status()["weights"] == "incomplete"


def test_cli_status_missing_binary(fake_decision, tmp_path):
    fake_decision.settings.decision_bin = tmp_path / "nope"
    st = DecisionClient(fake_decision.settings).cli_status()
    assert st["ok"] is False and st["weights"] is None


async def test_read_forwards_with_key_and_normalises(client, fake_decision):
    fake_decision.start()
    out = await client.read(body())
    (sent,) = fake_decision.reads
    assert sent["model"] == "openjev-latest" and set(sent["questions"]) == {
        "is_billing",
        "department",
        "urgency",
    }
    assert out["answers"][0]["top"] == "billing"
    assert out["usage"] == {"input_tokens": 166, "output_tokens": 0}
    assert out["server_timing"]["total"] == 400.9 and out["latency_ms"] >= 0


async def test_read_validates_before_forwarding(client, fake_decision):
    fake_decision.start()
    with pytest.raises(DecisionError):
        await client.read(body(steps=0))
    assert fake_decision.reads == []


async def test_read_without_key_is_503(client, fake_decision):
    from pydantic import SecretStr

    fake_decision.settings.decision_api_key = SecretStr("")
    with pytest.raises(DecisionError) as e:
        await client.read(body())
    assert e.value.code == 503


async def test_read_when_stopped_is_503(client):
    with pytest.raises(DecisionError) as e:
        await client.read(body())
    assert e.value.code == 503 and "not reachable" in e.value.message


async def test_read_timeout_is_504(client, fake_decision):
    fake_decision.start()
    fake_decision.flags["timeout"] = True
    with pytest.raises(DecisionError) as e:
        await client.read(body())
    assert e.value.code == 504


async def test_read_wrong_key_never_leaks(client, fake_decision):
    from pydantic import SecretStr

    fake_decision.start()
    fake_decision.settings.decision_api_key = SecretStr("w" * 64)
    with pytest.raises(DecisionError) as e:
        await client.read(body())
    assert e.value.code == 502 and "w" * 10 not in e.value.message


async def test_read_upstream_error_and_malformed(client, fake_decision):
    fake_decision.start()
    fake_decision.flags.update(status=400, body=fx("error_400_choice_empty")["response"])
    with pytest.raises(DecisionError) as e:
        await client.read(body())
    assert e.value.code == 400 and "at least one choice" in e.value.message
    fake_decision.flags.clear()
    fake_decision.flags["raw"] = b"not json"
    with pytest.raises(DecisionError) as e:
        await client.read(body())
    assert e.value.code == 502
    fake_decision.flags["raw"] = b"[1, 2]"
    with pytest.raises(DecisionError) as e:
        await client.read(body())
    assert e.value.code == 502 and "malformed" in e.value.message


# --- monitor ---------------------------------------------------------------------------


@pytest.fixture
def monitor(client, fake_decision):
    s = fake_decision.settings
    store = JobStore(s.admin_data_dir / "admin.sqlite")
    alerts = AlertStore(s.admin_data_dir / "admin.sqlite")
    reg = SidecarRegistry(s, {"openjev": fake_decision.transport})
    return SidecarMonitor(s, reg, store, alerts), store, alerts


def _snap(job_running=False):
    return SimpleNamespace(job_running=job_running, wall=time.time())


def _open(alerts):
    return alerts.open_condition_keys("decision_down")


async def test_monitor_alerts_only_when_intended_running(monitor, fake_decision):
    mon, store, alerts = monitor
    await mon(_snap(), None)
    assert _open(alerts) == []  # no intent recorded
    write_intent(store, "openjev", "running")
    await mon(_snap(job_running=True), None)
    assert _open(alerts) == []  # never during a job
    await mon(_snap(), None)
    assert _open(alerts) == ["decision_down"]
    fake_decision.start()
    await mon(_snap(), None)
    assert _open(alerts) == []
    calls = (fake_decision.state / "calls.log").read_text()
    assert calls.count("openjev start") == 1  # the monitor never starts it


async def test_monitor_resolves_when_intent_stopped(monitor):
    mon, store, alerts = monitor
    write_intent(store, "openjev", "running")
    await mon(_snap(), None)
    write_intent(store, "openjev", "stopped")
    await mon(_snap(), None)
    assert _open(alerts) == []


async def test_monitor_disabled_does_nothing(monitor, fake_decision):
    mon, store, alerts = monitor
    fake_decision.settings.decision_enabled = False
    write_intent(store, "openjev", "running")
    await mon(_snap(), None)
    assert _open(alerts) == []


# --- system probes and edge branches ------------------------------------------------------


def test_system_ram_is_positive():
    from local_llm_admin.sidecars import system_ram_gb

    assert system_ram_gb() > 0


@pytest.mark.parametrize(
    ("stdout", "exc", "want"),
    [("49152\n", None, 48.0), ("0\n", None, None), ("", None, None), (None, OSError, None)],
)
def test_wired_limit(monkeypatch, stdout, exc, want):
    import subprocess

    from local_llm_admin import sidecars as mod

    def fake_run(*a, **k):
        if exc:
            raise exc("no sysctl")
        return subprocess.CompletedProcess(a, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.wired_limit_gb() == want


def test_pid_that_vanishes_is_not_running(client, fake_decision, monkeypatch):
    import psutil

    (fake_decision.root / "run/openjev.pid").write_text("12345")

    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr("local_llm_admin.sidecars.psutil.Process", gone)
    assert client.running().running is False


def test_read_json(tmp_path):
    from local_llm_admin.sidecars import read_json

    (tmp_path / "a.json").write_text("[1]")
    assert read_json(tmp_path / "a.json") is None and read_json(tmp_path / "missing") is None
