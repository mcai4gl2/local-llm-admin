"""M6 Phase 5: the LAN gateway (:8082) in front of the loopback sidecar."""

import asyncio
import json
import logging

import httpx
import pytest

from local_llm_admin.decision import DecisionClient
from local_llm_admin.decision_keys import ClientKeys
from local_llm_admin.decision_store import DecisionStore
from local_llm_admin.gateway import create_gateway_app

from .conftest import FIX

BODY = {
    "state": "SECRET-STATE-TEXT",
    "model": "jev-latest",
    "questions": {"department": {"type": "choice", "criteria": {"billing": "$", "tech": "bugs"}}},
}


@pytest.fixture
async def gw(fake_decision):
    s = fake_decision.settings
    db = s.admin_data_dir / "admin.sqlite"
    keys, history = ClientKeys(db), DecisionStore(db)
    client = DecisionClient(s, transport=fake_decision.transport)
    app = create_gateway_app(s, client, keys, history)
    alice = keys.create("alice")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.50", 5555)),
        base_url="http://gw",
    ) as c:
        c.headers["Authorization"] = f"Bearer {alice['key']}"
        yield c, app, keys, history, alice


def _rows(history):
    return history.recent(100)


async def test_healthz_needs_no_key_and_says_nothing(gw):
    c, *_ = gw
    del c.headers["Authorization"]
    r = await c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_read_is_passed_through_and_recorded(gw, fake_decision):
    c, app, keys, history, alice = gw
    fake_decision.start()
    r = await c.post("/v1/systemone", json=BODY)
    fx = json.loads((FIX / "decision/choice.json").read_text())
    assert r.status_code == 200 and r.json() == fx["response"]  # OpenJev's body unchanged
    assert "server-timing" in r.headers
    assert fake_decision.reads[0]["model"] == "jev-latest"
    (row,) = _rows(history)
    assert row["source"] == "gateway" and row["client_id"] == str(alice["id"])
    assert row["status"] == "ok" and row["input_tokens"] == 166 and row["n_questions"] == 1
    raw = (fake_decision.settings.admin_data_dir / "admin.sqlite").read_bytes()
    assert b"SECRET-STATE-TEXT" not in raw and alice["key"].encode() not in raw


@pytest.mark.parametrize(
    "auth", [None, "Bearer nope", "Bearer dsk_aaaaaaaa_" + "A" * 43, "Basic x"]
)
async def test_bad_keys_are_401_and_recorded_without_the_key(gw, fake_decision, auth):
    c, app, keys, history, alice = gw
    fake_decision.settings.decision_gateway_require_key = True
    fake_decision.start()
    if auth is None:
        del c.headers["Authorization"]
    else:
        c.headers["Authorization"] = auth
    r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 401
    assert r.json()["detail"]["error_type"] == "authentication_error"
    (row,) = _rows(history)
    assert row["client_id"] is None and row["error_code"] == 401
    assert fake_decision.reads == []


@pytest.mark.parametrize("auth", [None, "Bearer nope", "Basic x"])
async def test_keys_are_optional_by_default(gw, fake_decision, auth):
    c, app, keys, history, alice = gw
    fake_decision.start()
    if auth is None:
        del c.headers["Authorization"]
    else:
        c.headers["Authorization"] = auth
    r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 200 and len(fake_decision.reads) == 1
    (row,) = _rows(history)
    assert row["client_id"] is None and row["status"] == "ok"
    assert app.state.per_client == {}


async def test_keyless_requests_share_a_per_address_cap(gw, fake_decision, monkeypatch):
    c, app, keys, history, alice = gw
    fake_decision.start()
    fake_decision.settings.decision_gateway_client_inflight = 1
    del c.headers["Authorization"]
    gate = asyncio.Event()
    real = DecisionClient.forward

    async def slow(self, payload):
        await gate.wait()
        return await real(self, payload)

    monkeypatch.setattr(DecisionClient, "forward", slow)
    first = asyncio.create_task(c.post("/v1/systemone", json=BODY))
    await asyncio.sleep(0.05)
    assert app.state.per_client == {"addr:192.0.2.50": 1}
    assert (await c.post("/v1/systemone", json=BODY)).status_code == 429
    gate.set()
    assert (await first).status_code == 200 and app.state.per_client == {}


async def test_revoked_key_fails_on_the_next_request(gw, fake_decision):
    c, app, keys, history, alice = gw
    fake_decision.settings.decision_gateway_require_key = True
    fake_decision.start()
    assert (await c.post("/v1/systemone", json=BODY)).status_code == 200
    keys.revoke(alice["id"])
    assert (await c.post("/v1/systemone", json=BODY)).status_code == 401


async def test_stopped_sidecar_is_503_and_never_started(gw, fake_decision):
    c, *_, history, _a = gw
    r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 503
    assert r.json()["detail"]["message"] == "decision sidecar is stopped"
    calls = fake_decision.state / "calls.log"
    assert not calls.exists() or "openjev start" not in calls.read_text()
    assert _rows(history)[0]["error_code"] == 503


async def test_validation_errors_use_openjev_shape(gw, fake_decision):
    c, *_ = gw
    fake_decision.start()
    r = await c.post("/v1/systemone", json={**BODY, "images": ["x"]})
    assert r.status_code == 400 and r.json()["detail"]["error_type"] == "api_usage_error"
    r = await c.post(
        "/v1/systemone", content=b"{nope", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400


async def test_body_limit(gw, fake_decision):
    c, *_ = gw
    fake_decision.start()
    big = {**BODY, "state": "x" * (70 * 1024)}
    r = await c.post("/v1/systemone", json=big)
    assert r.status_code == 413
    assert fake_decision.reads == []


async def test_upstream_errors_pass_through(gw, fake_decision):
    c, *_, history, _a = gw
    fake_decision.start()
    fx = json.loads((FIX / "decision/error_400_choice_empty.json").read_text())
    fake_decision.flags.update(status=400, body=fx["response"])
    r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 400 and r.json() == fx["response"]
    fake_decision.flags.clear()
    fake_decision.flags["timeout"] = True
    r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 504 and r.json()["detail"]["error_type"] == "timeout"
    assert [row["error_code"] for row in _rows(history)] == [504, 400]


async def test_in_flight_caps(gw, fake_decision, monkeypatch):
    c, app, keys, history, alice = gw
    fake_decision.start()
    s = fake_decision.settings
    s.decision_gateway_max_inflight = 2
    s.decision_gateway_client_inflight = 1
    gate = asyncio.Event()
    real = DecisionClient.forward

    async def slow(self, payload):
        await gate.wait()
        return await real(self, payload)

    monkeypatch.setattr(DecisionClient, "forward", slow)
    bob = keys.create("bob")
    first = asyncio.create_task(c.post("/v1/systemone", json=BODY))
    await asyncio.sleep(0.05)
    r = await c.post("/v1/systemone", json=BODY)  # alice's second: per-client cap
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    bob_task = asyncio.create_task(
        c.post("/v1/systemone", json=BODY, headers={"Authorization": f"Bearer {bob['key']}"})
    )
    await asyncio.sleep(0.05)
    carol = keys.create("carol")
    r = await c.post(
        "/v1/systemone", json=BODY, headers={"Authorization": f"Bearer {carol['key']}"}
    )
    assert r.status_code == 429  # global cap
    gate.set()
    assert (await first).status_code == 200 and (await bob_task).status_code == 200
    assert app.state.inflight == 0 and app.state.per_client == {}


async def test_models_route(gw, fake_decision):
    c, *_ = gw
    assert (await c.get("/v1/models")).status_code == 503
    fake_decision.start()
    r = await c.get("/v1/models")
    assert r.status_code == 200 and r.json() == {"models": [{"name": "openjev-latest"}]}
    del c.headers["Authorization"]
    assert (await c.get("/v1/models")).status_code == 200  # keys are optional by default
    fake_decision.settings.decision_gateway_require_key = True
    assert (await c.get("/v1/models")).status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/"),
        ("GET", "/models"),
        ("GET", "/api/status"),
        ("POST", "/api/engine"),
        ("POST", "/api/decision/start"),
        ("GET", "/api/decision/clients"),
        ("POST", "/v1/chat/completions"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", "/static/app.js"),
        ("GET", "/login"),
    ],
)
async def test_no_admin_routes_on_the_gateway(gw, method, path):
    c, *_ = gw
    assert (await c.request(method, path)).status_code == 404


async def test_keys_never_logged(gw, fake_decision, caplog):
    c, app, keys, history, alice = gw
    fake_decision.start()
    with caplog.at_level(logging.DEBUG):
        await c.post("/v1/systemone", json=BODY)
        c.headers["Authorization"] = "Bearer dsk_badbadba_" + "Z" * 43
        await c.post("/v1/systemone", json=BODY)
    assert alice["key"] not in caplog.text and "Z" * 43 not in caplog.text
    assert "SECRET-STATE-TEXT" not in caplog.text


async def test_failed_usage_row_does_not_fail_the_read(gw, fake_decision, monkeypatch, caplog):
    c, app, keys, history, alice = gw
    fake_decision.start()

    def boom(**kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(history, "add_request", boom)
    with caplog.at_level(logging.ERROR):
        assert (await c.post("/v1/systemone", json=BODY)).status_code == 200
    assert "recording usage failed" in caplog.text


async def test_models_upstream_unreachable(gw, fake_decision):
    c, *_ = gw
    fake_decision.start()
    (fake_decision.state / "decision_ready").unlink()  # running, but not answering
    r = await c.get("/v1/models")
    assert r.status_code == 503 and r.json()["detail"]["error_type"] == "unavailable"


async def test_oversized_body_without_content_length(gw, fake_decision):
    c, *_ = gw
    fake_decision.start()
    big = json.dumps({**BODY, "state": "x" * (70 * 1024)}).encode()

    async def chunks():
        for i in range(0, len(big), 8192):
            yield big[i : i + 8192]

    r = await c.post(
        "/v1/systemone", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413 and fake_decision.reads == []


async def test_same_client_in_flight_twice(gw, fake_decision, monkeypatch):
    c, app, *_ = gw
    fake_decision.start()
    gate = asyncio.Event()
    real = DecisionClient.forward

    async def slow(self, payload):
        await gate.wait()
        return await real(self, payload)

    monkeypatch.setattr(DecisionClient, "forward", slow)
    t1 = asyncio.create_task(c.post("/v1/systemone", json=BODY))
    t2 = asyncio.create_task(c.post("/v1/systemone", json=BODY))
    await asyncio.sleep(0.05)
    assert app.state.inflight == 2 and list(app.state.per_client.values()) == [2]
    gate.set()
    assert (await t1).status_code == 200 and (await t2).status_code == 200
    assert app.state.per_client == {}


async def test_malformed_upstream_reply_is_502(gw, fake_decision):
    c, *_, history, _a = gw
    fake_decision.start()
    fake_decision.flags["raw"] = b"<html>"
    r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 502 and r.json()["detail"]["error_type"] == "bad_gateway"
    assert _rows(history)[0]["error_code"] == 502


async def test_reply_without_usage_or_timing(gw, fake_decision):
    c, *_, history, _a = gw
    fake_decision.start()
    fake_decision.flags.update(status=200, body={"model": "openjev-0.1", "answers": {}})
    r = await c.post("/v1/systemone", json=BODY)
    assert r.status_code == 200 and "server-timing" not in r.headers
    assert _rows(history)[0]["input_tokens"] is None
