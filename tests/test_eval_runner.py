"""M10: eval runs through the quality runner."""

import asyncio
import contextlib
import hashlib
import json
import subprocess
import time

import httpx
import pytest

from local_llm_admin import auth, quality_runner
from local_llm_admin import jobs as jobs_mod
from local_llm_admin.app import create_app
from local_llm_admin.chat import ACTIVE_MODEL_ID
from local_llm_admin.eval_suites import parse_suite
from local_llm_admin.quality_runner import (
    QualityError,
    RunPlan,
    eval_payload,
    eval_request_timeout,
)

from .fake_mlx_server import FakeMlxServer

ANSWERS = {
    "Reply with exactly the word READY": "READY",
    "Write the word banana": "<think>b-a-n</think>ananab",
}
TINY = {
    "id": "tiny",
    "version": 1,
    "title": "Tiny",
    "cases": [
        {"id": "ready", "category": "instruction", "prompt": "Reply with exactly the word READY",
         "max_tokens": 16, "grader": {"type": "exact", "answer": "READY"}},
        {"id": "backwards", "category": "instruction", "prompt": "Write the word banana backwards",
         "grader": {"type": "exact", "answer": "ananab"}},
        {"id": "other", "category": "reasoning", "prompt": "Name the capital of France",
         "grader": {"type": "exact", "answer": "Paris"}},
    ],
}  # fmt: skip
TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}


def _reply(payload):
    user = payload["messages"][-1]["content"]
    text = next((v for k, v in ANSWERS.items() if user.startswith(k)), "no idea")
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}  # fmt: skip


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(jobs_mod, "WAIT_POLL_S", 0.01)


@pytest.fixture
def server():
    return FakeMlxServer()


@pytest.fixture
def settings(fake_comfy, fake_mlx):
    s = fake_comfy.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "test-secret"
    s.quality_ready_timeout_s = 1
    s.quality_rss_interval_s = 0.01
    (fake_mlx.state / "track_env").write_text("")
    gemma = fake_mlx.root / "models/Gemma"
    gemma.mkdir()
    (gemma / "config.json").write_text(json.dumps({"model_type": "gemma4"}))
    return s


@contextlib.asynccontextmanager
async def _client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.9", 1234)),
        base_url="http://admin.test",
    ) as c:
        assert (await c.post("/login", data={"password": "pw"})).status_code == 303
        c.headers["X-CSRF-Token"] = (await c.get("/api/session")).json()["csrf"]
        c.app = app
        yield c


@pytest.fixture
async def client(settings, fake_comfy, server):
    app = create_app(
        settings,
        start_background=False,
        comfy_transport=fake_comfy.transport,
        quality_transport=server.transport,
    )
    async with app.router.lifespan_context(app):
        await app.state.startup_task
        async with _client(app) as c:
            yield c


@pytest.fixture
def mlx_running(settings):
    subprocess.run(
        [str(settings.mlx_model_path), "start", "qwen"], capture_output=True, check=False
    )


@pytest.fixture
def tiny_suite(client):
    client.app.state.evals.add_custom(parse_suite(TINY))


@pytest.fixture
def switch_during_run(server, fake_mlx):
    def operator_switches(payload):
        (fake_mlx.root / "run/active.env").write_text("MLX_ACTIVE_NAME=gemma\n")

    server.on_chat = operator_switches


async def _run(client, **body):
    r = await client.post("/api/quality/runs", json={"kind": "eval", "scope": "current", **body})
    assert r.status_code == 202, r.text
    await client.app.state.quality.wait()
    run = (await client.get(f"/api/quality/runs/{r.json()['id']}")).json()
    return run, client.app.state.evals.results(run["id"])


async def test_eval_run_scores_and_promotes(client, server, mlx_running, tiny_suite):
    server.reply = _reply
    run, results = await _run(client, suite="tiny")
    assert run["status"] == "succeeded" and run["restoration"]["outcome"] == "not_needed"
    assert {x["case_id"]: x["passed"] for x in results} == {"ready": 1, "backwards": 1, "other": 0}
    assert all(x["grade_state"] == "graded" and x["prompt_tokens"] == 10 for x in results)
    assert results[1]["response"] == "<think>b-a-n</think>ananab"
    card = client.app.state.evals.scorecard()
    assert card[0]["suites"]["tiny"]["mean"] == round(2 / 3, 4)
    assert card[0]["model"] == "qwen" and card[0]["fingerprint"] is not None
    (case,) = run["cases"]
    assert case["case_key"] == "eval:tiny" and case["kind"] == "eval"
    assert case["status"] == "complete" and case["summary"]["n"] == 3
    assert run["settings"]["suite_id"] == "tiny" and run["settings"]["thinking"] is False


async def test_payload_turns_thinking_off_both_ways(client, server, mlx_running, tiny_suite):
    server.reply = _reply
    await _run(client, suite="tiny")
    p = server.requests[-1]
    assert p["temperature"] == 0 and p["enable_thinking"] is False
    assert p["chat_template_kwargs"] == {"enable_thinking": False} and p["stream"] is False
    assert p["model"] == ACTIVE_MODEL_ID and p["seed"] == 0
    assert server.requests[0]["max_tokens"] == 16 and "tools" not in p


async def test_thinking_run_uses_the_thinking_budget(client, server, mlx_running, tiny_suite):
    server.reply = _reply
    client.app.state.quality.settings.eval_thinking_max_tokens = 4096
    run, results = await _run(client, suite="tiny", thinking=True)
    assert all(p["enable_thinking"] is True and p["max_tokens"] == 4096 for p in server.requests)
    off, _ = await _run(client, suite="tiny")
    on = client.app.state.evals.results(run["id"])[0]["settings_hash"]
    assert client.app.state.evals.results(off["id"])[0]["settings_hash"] != on


async def test_stopped_while_thinking_scores_zero(client, server, mlx_running, tiny_suite):
    server.reply = lambda p: {
        "choices": [{"message": {"content": "<think>long"}, "finish_reason": "length"}]
    }
    _, results = await _run(client, suite="tiny")
    assert all(x["score"] == 0 and "stopped while thinking" in x["grade_detail"] for x in results)


async def test_reasoning_content_is_counted_not_graded(client, server, mlx_running, tiny_suite):
    server.reply = lambda p: {
        "choices": [{"message": {"content": "", "reasoning_content": "hmm" * 10},
                     "finish_reason": "length"}]
    }  # fmt: skip
    _, results = await _run(client, suite="tiny")
    assert all(x["reasoning_chars"] == 30 and x["score"] == 0 for x in results)


async def test_http_error_is_failed_and_excluded(client, server, mlx_running, tiny_suite):
    server.reply = lambda p: httpx.Response(500, text="boom")
    run, results = await _run(client, suite="tiny")
    assert {x["grade_state"] for x in results} == {"failed"}
    assert all(x["score"] is None and "500" in x["error"] for x in results)
    assert run["cases"][0]["summary"]["n"] == 0 and run["cases"][0]["summary"]["excluded"] == 3
    assert client.app.state.evals.scorecard() == []


async def test_transport_error_and_bad_body_are_failed(client, server, mlx_running, tiny_suite):
    def flaky(payload):
        if "READY" in payload["messages"][-1]["content"]:
            raise httpx.ReadError("reset")
        if "banana" in payload["messages"][-1]["content"]:
            return httpx.Response(200, text="not json")
        return {"choices": [{"message": "Paris"}]}

    server.reply = flaky
    _, results = await _run(client, suite="tiny")
    assert [x["grade_state"] for x in results] == ["failed"] * 3
    assert "reset" in results[0]["error"]
    assert all("invalid response" in x["error"] for x in results[1:])


async def test_slow_case_times_out_as_failed(client, server, mlx_running, tiny_suite, monkeypatch):
    monkeypatch.setattr(quality_runner, "eval_request_timeout", lambda base, _tokens: base)
    client.app.state.quality.settings.quality_request_timeout_s = 0.05

    async def slow(request):
        if request.url.path == "/v1/chat/completions":
            await asyncio.sleep(1)
        return server.handler(request)

    client.app.state.quality.transport = httpx.MockTransport(slow)
    _, results = await _run(client, suite="tiny")
    assert {x["grade_state"] for x in results} == {"failed"}
    assert "no response within" in results[0]["error"]


async def test_tool_and_judge_cases(client, server, mlx_running):
    suite = {
        "id": "mixed", "version": 1, "title": "Mixed",
        "cases": [
            {"id": "tool", "category": "tools", "prompt": "weather in Paris", "tools": [TOOL],
             "grader": {"type": "tool_call", "name": "get_weather", "arguments": {"city": "Paris"}}},
            {"id": "notool", "category": "tools", "prompt": "think forever", "tools": [TOOL],
             "grader": {"type": "tool_call", "name": None}},
            {"id": "essay", "category": "writing", "prompt": "Write a haiku",
             "grader": {"type": "judge", "rubric": "is it a haiku"}},
            {"id": "silent", "category": "writing", "prompt": "Say nothing",
             "grader": {"type": "judge", "rubric": "anything"}},
        ],
    }  # fmt: skip
    client.app.state.evals.add_custom(parse_suite(suite))
    call = {
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
    }

    def reply(payload):
        user = payload["messages"][-1]["content"]
        msg = {
            "weather in Paris": {"content": None, "tool_calls": [call]},
            "think forever": {"content": "<think>still going"},
            "Write a haiku": {"content": "old pond / frog / splash"},
        }.get(user, {"content": ""})
        return {"choices": [{"message": msg, "finish_reason": "stop"}]}

    server.reply = reply
    run, results = await _run(client, suite="mixed")
    by = {x["case_id"]: x for x in results}
    assert by["tool"]["passed"] == 1 and json.loads(by["tool"]["tool_calls"])[0] == call
    assert server.requests[0]["tools"] == [TOOL]
    assert by["notool"]["score"] == 0 and "stopped while thinking" in by["notool"]["grade_detail"]
    # no judge is configured: the judge phase leaves the row ungraded, never scored
    assert by["essay"]["grade_state"] == "ungraded" and by["essay"]["score"] is None
    assert by["silent"]["grade_state"] == "graded" and by["silent"]["score"] == 0
    assert run["cases"][0]["summary"]["n"] == 3


async def test_eval_needs_a_suite_and_others_refuse_one(client, mlx_running):
    assert (
        await client.post("/api/quality/runs", json={"kind": "eval", "scope": "current"})
    ).status_code == 400
    assert (
        await client.post(
            "/api/quality/runs", json={"kind": "benchmark", "scope": "current", "suite": "tiny"}
        )
    ).status_code == 400
    assert (
        await client.post(
            "/api/quality/runs", json={"kind": "benchmark", "scope": "current", "thinking": True}
        )
    ).status_code == 400
    assert (
        await client.post(
            "/api/quality/runs", json={"kind": "eval", "scope": "current", "suite": "nope"}
        )
    ).status_code == 404
    assert client.app.state.control.runner.running() is None


async def test_builtin_suites_are_synced_at_startup(client):
    assert client.app.state.evals.suite("core-v1") is not None


async def test_runner_without_an_eval_store_refuses_evals(client, mlx_running):
    qr = client.app.state.quality
    qr.evals = None
    with pytest.raises(QualityError) as err:
        await qr.start("eval", "current", suite="core-v1")
    assert err.value.code == 503


async def test_eval_uses_the_eval_deadline(client, server, mlx_running, tiny_suite):
    client.app.state.quality.settings.eval_run_deadline_s = 0.001
    server.reply = _reply
    run, results = await _run(client, suite="tiny")
    assert run["status"] == "failed" and run["error"] == "run deadline reached"
    assert results == []


async def test_intervention_keeps_results_but_does_not_promote(
    client, server, mlx_running, tiny_suite, switch_during_run
):
    server.reply = _reply
    run, results = await _run(client, suite="tiny")
    assert run["status"] == "intervention_required"
    assert [x["case_id"] for x in results] == ["ready"]  # the first answer is kept
    assert client.app.state.evals.scorecard() == []


async def test_cancel_closes_the_eval_case(client, server, mlx_running, tiny_suite):
    qr = client.app.state.quality

    async def slow(request):
        if request.url.path == "/v1/chat/completions":
            await asyncio.sleep(5)
        return server.handler(request)

    qr.transport = httpx.MockTransport(slow)
    r = await client.post(
        "/api/quality/runs", json={"kind": "eval", "scope": "current", "suite": "tiny"}
    )
    for _ in range(100):
        if qr._plan is not None and qr.store.get_run(r.json()["id"], True)["cases"]:
            break
        await asyncio.sleep(0.01)
    await client.post(f"/api/quality/runs/{r.json()['id']}/cancel")
    await qr.wait()
    run = (await client.get(f"/api/quality/runs/{r.json()['id']}")).json()
    assert run["status"] == "cancelled" and run["cases"][0]["status"] == "cancelled"
    assert client.app.state.evals.scorecard() == []


async def test_model_load_failure_is_a_failed_eval_case(client, mlx_running, tiny_suite):
    qr = client.app.state.quality
    orig = qr._run_cli

    async def refuse_gemma(act, name, label=None):
        return "failed" if name == "gemma" else await orig(act, name, label)

    qr._run_cli = refuse_gemma
    r = await client.post(
        "/api/quality/runs",
        json={"kind": "eval", "scope": "model", "model": "gemma", "suite": "tiny"},
    )
    await qr.wait()
    run = (await client.get(f"/api/quality/runs/{r.json()['id']}")).json()
    assert run["status"] == "failed" and run["cases"][0]["case_key"] == "load"
    assert run["cases"][0]["status"] == "failed"


def test_eval_payload_is_pure():
    case = parse_suite(TINY).cases[0]
    p = eval_payload(case, thinking=True, engine="Qwen3.8-Flash-Next", max_tokens_thinking=999)
    assert p["model"] == "Qwen3.8-Flash-Next" and p["max_tokens"] == 999
    assert p["messages"] == [{"role": "user", "content": "Reply with exactly the word READY"}]
    assert p["chat_template_kwargs"] == {"enable_thinking": True}
    assert (
        eval_payload(case, thinking=False, engine="mlx", max_tokens_thinking=999)["model"]
        == ACTIVE_MODEL_ID
    )


# --- Strata: evals of the current engine only ------------------------------------------
STRATA_HEALTH = {"status": "ok", "model": "Qwen3.8-Flash-Next", "max_context": 65536, "loaded_context_size": 8192,  # decoy: Strata uses max_context
                 "images": False, "loaded": True, "service": "strata"}  # fmt: skip


@pytest.fixture
async def client_strata(settings, fake_strata, fake_comfy):
    sent = []

    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json=STRATA_HEALTH)
        if request.url.path == "/v1/chat/completions":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json=_reply(sent[-1]))
        return httpx.Response(404)

    app = create_app(
        settings,
        start_background=False,
        comfy_transport=fake_comfy.transport,
        strata_transport=fake_strata.transport,
        quality_transport=httpx.MockTransport(handler),
    )
    fake_strata.start()
    async with app.router.lifespan_context(app):
        await app.state.startup_task
        app.state.control.store.set_kv("intent", {"value": "strata"})
        async with _client(app) as c:
            c.sent = sent
            app.state.evals.add_custom(parse_suite(TINY))
            yield c


async def test_strata_runs_eval_on_current_only(client_strata):
    r = await client_strata.post(
        "/api/quality/runs", json={"kind": "eval", "scope": "all", "suite": "tiny"}
    )
    assert r.status_code == 409


async def test_strata_eval_sends_its_model_id(client_strata):
    run, results = await _run(client_strata, suite="tiny")
    assert run["status"] == "succeeded" and run["restoration"]["outcome"] == "not_needed"
    assert {x["case_id"]: x["passed"] for x in results} == {"ready": 1, "backwards": 1, "other": 0}
    assert all(p["model"] == "Qwen3.8-Flash-Next" for p in client_strata.sent)
    assert results[0]["model"] == "strata"


# --- review fixes: promotion only after a clean run, deadlines, fingerprints, timeouts ----
async def test_clean_scope_all_run_promotes_every_model(client, server, mlx_running, tiny_suite):
    server.reply = _reply
    run, results = await _run(client, scope="all", suite="tiny")
    assert run["status"] == "succeeded", run
    assert {x["model"] for x in results} == {"qwen", "gemma"}
    card = client.app.state.evals.scorecard()
    assert sorted(c["model"] for c in card) == ["gemma", "qwen"]
    assert all(c["suites"]["tiny"]["mean"] == round(2 / 3, 4) for c in card)


async def test_intervention_on_second_model_promotes_nothing(
    client, server, mlx_running, tiny_suite, fake_mlx
):
    env = fake_mlx.root / "run/active.env"

    def operator_switches_back(payload):
        if "gemma" in env.read_text():
            env.write_text("MLX_ACTIVE_NAME=qwen\n")

    server.reply = _reply
    server.on_chat = operator_switches_back
    run, results = await _run(client, scope="all", suite="tiny")
    assert run["status"] == "intervention_required"
    assert {x["model"] for x in results} == {"qwen", "gemma"}  # qwen's rows are kept
    assert client.app.state.evals.scorecard() == []


async def test_deadline_during_the_last_case_does_not_promote(
    client, server, mlx_running, tiny_suite
):
    qr = client.app.state.quality

    def expire_on_last(payload):
        if payload["messages"][-1]["content"].startswith("Name the capital"):
            qr._plan.deadline = time.monotonic() - 1

    server.reply = _reply
    server.on_chat = expire_on_last
    run, results = await _run(client, suite="tiny")
    assert run["status"] == "failed" and run["error"] == "run deadline reached"
    assert len(results) == 3 and run["cases"][0]["status"] == "failed"
    assert client.app.state.evals.scorecard() == []


async def test_strata_eval_is_fingerprinted_and_promoted(client_strata):
    run, results = await _run(client_strata, suite="tiny")
    want = "strata-" + hashlib.sha256(b"Qwen3.8-Flash-Next").hexdigest()[:16]
    assert run["cases"][0]["fingerprint"] == want
    assert all(x["fingerprint"] == want for x in results)
    (card,) = client_strata.app.state.evals.scorecard()
    assert card["fingerprint"] == want and card["suites"]["tiny"]["mean"] == round(2 / 3, 4)


def test_eval_request_timeout_has_a_five_token_per_second_floor():
    assert eval_request_timeout(300.0, 512) == 300.0
    assert eval_request_timeout(300.0, 8192) == 8192 / 5
    assert eval_request_timeout(10.0, 100) == 20.0


async def test_eval_request_timeout_is_clamped_by_the_run_deadline(client, mlx_running, tiny_suite):
    qr = client.app.state.quality
    plan = RunPlan(run_id="x", job_id="", kind="eval", targets=[], dirs={}, original={},
                   deadline=time.monotonic() + 2)  # fmt: skip
    assert 1 < qr._eval_deadline(plan, 8192) <= 2
    plan.deadline = time.monotonic() + 10_000
    assert qr._eval_deadline(plan, 8192) == 8192 / 5


async def test_strata_without_a_model_id_is_not_promoted(client_strata, monkeypatch):
    monkeypatch.delitem(STRATA_HEALTH, "model")
    run, results = await _run(client_strata, suite="tiny")
    assert run["status"] == "succeeded" and all(x["fingerprint"] is None for x in results)
    assert all(p["model"] == ACTIVE_MODEL_ID for p in client_strata.sent)
    assert client_strata.app.state.evals.scorecard() == []


# --- judge phase (Task 8) ---------------------------------------------------------------
JUDGE_TINY = {
    "id": "judge-tiny",
    "version": 1,
    "title": "Judge tiny",
    "cases": [
        {"id": "essay", "category": "writing", "prompt": "Write a haiku",
         "grader": {"type": "judge", "rubric": "5: a haiku. 1: not one."}},
        {"id": "polite", "category": "writing", "prompt": "Decline politely",
         "grader": {"type": "judge", "rubric": "polite", "scale": 2, "reference": "No, thanks."}},
    ],
}  # fmt: skip
MIXED_JUDGE = {
    "id": "mixed-judge",
    "version": 1,
    "title": "Mixed judge",
    "cases": [
        {"id": "ready", "category": "instruction", "prompt": "Reply with exactly the word READY",
         "grader": {"type": "exact", "answer": "READY"}},
        {"id": "essay", "category": "writing", "prompt": "Write a haiku",
         "grader": {"type": "judge", "rubric": "a haiku"}},
    ],
}  # fmt: skip
VERDICT = '{"score": 4, "reason": "good"}'
VERDICT_BOTH = '{"score": 2, "reason": "fair"}'  # valid on scale 5 (0.25) and 2 (1.0)


def _verdict_reply(payload):
    return {"choices": [{"message": {"content": VERDICT_BOTH}, "finish_reason": "stop"}]}


@pytest.fixture
def judge_suite(client):
    client.app.state.evals.add_custom(parse_suite(JUDGE_TINY))
    client.app.state.evals.add_custom(parse_suite(MIXED_JUDGE))


class ExternalJudge:
    def __init__(self) -> None:
        self.calls = 0
        self.reply_text: str | None = VERDICT
        self.requests: list[httpx.Request] = []
        self.on_call = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.requests.append(request)
        if self.on_call is not None:
            self.on_call(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": self.reply_text}}]})


@pytest.fixture
def external_judge(client, settings):
    from pydantic import SecretStr

    settings.eval_judge = "external"
    settings.eval_judge_base_url = "http://judge.test"
    settings.eval_judge_model = "judge-m"
    settings.eval_judge_api_key = SecretStr("sk-judge-SECRET")
    judge = ExternalJudge()
    client.app.state.quality.judge_transport = httpx.MockTransport(judge.handler)
    return judge


async def test_unparsable_twice_is_judge_error(
    client, server, mlx_running, judge_suite, external_judge
):
    external_judge.reply_text = "I think it's good."
    r = await client.post(
        "/api/quality/runs", json={"kind": "eval", "scope": "current", "suite": "judge-tiny"}
    )
    await client.app.state.quality.wait()
    rows = (await client.get(f"/api/evals/runs/{r.json()['id']}/results")).json()["results"]
    assert {x["grade_state"] for x in rows} == {"judge_error"}
    assert external_judge.calls == 2 * len(rows)  # one retry each
    assert all(x["score"] is None and x["judge"] == "external:judge-m" for x in rows)
    run = (await client.get(f"/api/quality/runs/{r.json()['id']}")).json()
    assert run["cases"][0]["summary"]["n"] == 0 and run["cases"][0]["summary"]["excluded"] == 2
    assert "judge" in run["error"]
    assert client.app.state.evals.scorecard() == []  # excluded, never scored 0


async def test_external_judge_scores_and_promotes(
    client, server, mlx_running, judge_suite, external_judge
):
    server.reply = _reply
    run, rows = await _run(client, suite="judge-tiny")
    # a judge error marks the run failed but does not block promotion
    assert run["status"] == "failed" and run["error"] == "judge: 1 of 2 cases got no verdict"
    by = {x["case_id"]: x for x in rows}
    assert by["essay"]["score"] == 0.75 and by["essay"]["passed"] == 1
    assert by["polite"]["grade_state"] == "judge_error"  # 4 is outside scale 2
    assert all(x["self_judged"] == 0 and x["judged_at"] for x in rows)
    req = external_judge.requests[0]
    assert req.headers["authorization"] == "Bearer sk-judge-SECRET"
    body = json.loads(req.content)
    assert (
        body["model"] == "judge-m"
        and "<answer>\nno idea\n</answer>" in body["messages"][1]["content"]
    )
    dumped = json.dumps(rows) + json.dumps(run)
    assert "SECRET" not in dumped
    (card,) = client.app.state.evals.scorecard()
    assert card["suites"]["judge-tiny"]["mean"] == 0.75
    assert card["suites"]["judge-tiny"]["judge"] == "external:judge-m"
    assert card["suites"]["judge-tiny"]["excluded"] == 1


async def test_judge_error_does_not_block_deterministic_promotion(
    client, server, mlx_running, judge_suite, external_judge
):
    external_judge.reply_text = None  # the judge answers nothing
    server.reply = _reply
    run, rows = await _run(client, suite="mixed-judge")
    assert {x["case_id"]: x["grade_state"] for x in rows} == {
        "ready": "graded",
        "essay": "judge_error",
    }
    (card,) = client.app.state.evals.scorecard()
    assert card["suites"]["mixed-judge"]["mean"] == 1.0
    assert (
        card["suites"]["mixed-judge"]["n"] == 1 and card["suites"]["mixed-judge"]["excluded"] == 1
    )


async def test_local_judge_switches_once_and_flags_self_judging(
    client, server, fake_mlx, mlx_running, judge_suite, settings
):
    settings.eval_judge = "mlx:qwen"
    server.reply = lambda p: {
        "choices": [
            {"message": {"content": '{"score": 4, "reason": "good"}'}, "finish_reason": "stop"}
        ]
    }
    r = await client.post(
        "/api/quality/runs", json={"kind": "eval", "scope": "current", "suite": "judge-tiny"}
    )
    await client.app.state.quality.wait()
    rows = (await client.get(f"/api/evals/runs/{r.json()['id']}/results")).json()["results"]
    rows = [x for x in rows if x["case_id"] == "essay"]  # the scale-2 case cannot take a 4
    assert all(x["self_judged"] == 1 and x["score"] == 0.75 for x in rows)


async def test_local_judge_on_another_model_switches_and_restores(
    client, server, fake_mlx, mlx_running, judge_suite, settings
):
    settings.eval_judge = "mlx:gemma"
    seen = []

    def reply(payload):
        seen.append((fake_mlx.root / "run/active.env").read_text())
        return _verdict_reply(payload)

    server.reply = reply
    before = len(fake_mlx.calls())
    run, rows = await _run(client, suite="judge-tiny")
    assert run["status"] == "succeeded", run
    assert run["restoration"]["outcome"] == "restored"
    calls = fake_mlx.calls()[before:]
    assert [c for c in calls if c.startswith("switch")] == ["switch gemma", "switch qwen"]
    assert all("gemma" in s for s in seen[2:]) and all("qwen" in s for s in seen[:2])
    essay = next(x for x in rows if x["case_id"] == "essay")
    assert essay["judge"] == "mlx:gemma" and essay["self_judged"] == 0 and essay["score"] == 0.25
    polite = next(x for x in rows if x["case_id"] == "polite")
    assert polite["score"] == 1.0 and polite["passed"] == 1 and polite["grade_detail"] == "fair"
    assert client.app.state.evals.scorecard()[0]["suites"]["judge-tiny"]["self_judged"] is False


async def test_local_judge_load_failure_is_judge_error(
    client, server, mlx_running, judge_suite, settings
):
    settings.eval_judge = "mlx:gemma"
    qr = client.app.state.quality
    orig = qr._run_cli

    async def refuse_gemma(act, name, label=None):
        return "failed" if name == "gemma" else await orig(act, name, label)

    qr._run_cli = refuse_gemma
    server.reply = _reply
    run, rows = await _run(client, suite="mixed-judge")
    assert run["status"] == "failed" and "gemma" in run["error"]
    essay = next(x for x in rows if x["case_id"] == "essay")
    assert essay["grade_state"] == "judge_error" and "gemma" in essay["grade_detail"]
    assert client.app.state.evals.scorecard()[0]["suites"]["mixed-judge"]["n"] == 1


async def test_no_judge_marks_ungraded(client, server, mlx_running, judge_suite):
    r = await client.post(
        "/api/quality/runs", json={"kind": "eval", "scope": "current", "suite": "judge-tiny"}
    )
    await client.app.state.quality.wait()
    rows = (await client.get(f"/api/evals/runs/{r.json()['id']}/results")).json()["results"]
    assert {x["grade_state"] for x in rows} == {"ungraded"}


async def test_cancel_during_judging_promotes_nothing(
    client, server, mlx_running, judge_suite, external_judge
):
    qr = client.app.state.quality
    server.reply = _reply
    external_judge.on_call = lambda req: qr._cancel.set()
    run, rows = await _run(client, suite="mixed-judge")
    assert run["status"] == "cancelled"
    assert client.app.state.evals.scorecard() == []


async def test_deadline_during_judging_promotes_nothing(
    client, server, mlx_running, judge_suite, external_judge
):
    qr = client.app.state.quality

    def expire(req):
        qr._plan.deadline = time.monotonic() - 1

    server.reply = _reply
    external_judge.on_call = expire
    run, rows = await _run(client, suite="mixed-judge")
    assert run["status"] == "failed" and run["error"] == "run deadline reached"
    assert client.app.state.evals.scorecard() == []


async def test_intervention_during_local_judging_promotes_nothing(
    client, server, fake_mlx, mlx_running, judge_suite, settings
):
    settings.eval_judge = "mlx:gemma"
    env = fake_mlx.root / "run/active.env"

    def operator_switches_back(payload):
        if "gemma" in env.read_text():
            env.write_text("MLX_ACTIVE_NAME=qwen\n")
        return _verdict_reply(payload)

    server.reply = operator_switches_back
    run, rows = await _run(client, suite="judge-tiny")
    assert run["status"] == "intervention_required"
    assert client.app.state.evals.scorecard() == []


async def test_bad_judge_settings_refuse_judge_suites(client, mlx_running, judge_suite, settings):
    settings.eval_judge = "bogus"
    r = await client.post(
        "/api/quality/runs", json={"kind": "eval", "scope": "current", "suite": "judge-tiny"}
    )
    assert r.status_code == 409 and "invalid judge setting" in r.json()["detail"]
    settings.eval_judge = "mlx:nobody"
    r = await client.post(
        "/api/quality/runs", json={"kind": "eval", "scope": "current", "suite": "judge-tiny"}
    )
    assert r.status_code == 409 and "nobody" in r.json()["detail"]
    assert client.app.state.control.runner.running() is None


async def test_bad_judge_settings_do_not_block_deterministic_suites(
    client, server, mlx_running, tiny_suite, settings
):
    settings.eval_judge = "bogus"
    server.reply = _reply
    run, _ = await _run(client, suite="tiny")
    assert run["status"] == "succeeded"


async def test_strata_refuses_a_local_judge(client_strata, settings):
    client_strata.app.state.evals.add_custom(parse_suite(JUDGE_TINY))
    settings.eval_judge = "mlx:qwen"
    r = await client_strata.post(
        "/api/quality/runs", json={"kind": "eval", "scope": "current", "suite": "judge-tiny"}
    )
    assert r.status_code == 409 and "Strata" in r.json()["detail"]
    run, _ = await _run(client_strata, suite="tiny")  # no judge cases: no switch needed
    assert run["status"] == "succeeded"


async def test_strata_with_an_external_judge(client_strata, settings):
    from pydantic import SecretStr

    client_strata.app.state.evals.add_custom(parse_suite(JUDGE_TINY))
    settings.eval_judge = "external"
    settings.eval_judge_base_url = "http://judge.test"
    settings.eval_judge_model = "judge-m"
    settings.eval_judge_api_key = SecretStr("")
    judge = ExternalJudge()
    judge.reply_text = VERDICT_BOTH
    client_strata.app.state.quality.judge_transport = httpx.MockTransport(judge.handler)
    run, rows = await _run(client_strata, suite="judge-tiny")
    assert run["status"] == "succeeded" and "authorization" not in judge.requests[0].headers
    assert next(x for x in rows if x["case_id"] == "essay")["score"] == 0.25


async def test_judge_retry_stops_at_the_run_deadline(
    client, server, mlx_running, judge_suite, external_judge
):
    qr = client.app.state.quality

    def near_deadline(payload):  # the judge phase starts with 0.3 s of run left
        if "haiku" in payload["messages"][-1]["content"]:
            qr._plan.deadline = time.monotonic() + 0.3

    def slow_prose(req):
        time.sleep(0.35)

    server.reply = _reply
    server.on_chat = near_deadline
    external_judge.reply_text = "prose"
    external_judge.on_call = slow_prose
    run, _ = await _run(client, suite="mixed-judge")
    assert run["error"] == "run deadline reached"
    assert external_judge.calls == 1  # no retry past the run deadline


# --- needle-v1: cases are generated from the loaded context -------------------------------
def _echo_code(payload):
    import re

    m = re.search(r"access code for the blue door is (K[0-9A-F]{6})\.", payload["messages"][-1]["content"])
    text = m.group(1) if m else "no idea"
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}  # fmt: skip


async def test_needle_run_uses_the_loaded_context(client, server, mlx_running):
    server.reply = _echo_code
    run, results = await _run(client, suite="needle-v1")
    assert run["status"] == "succeeded", run
    assert len(results) == 12 and all(x["passed"] == 1 for x in results)
    assert {x["category"] for x in results} == {
        "needle-8192", "needle-32768", "needle-131072", "needle-200000",
    }  # fmt: skip
    # regrade finds the generated cases again
    r = await client.post(f"/api/evals/runs/{run['id']}/regrade")
    assert r.status_code == 200 and r.json()["regraded"] == 12 and r.json()["changed"] == 0


async def test_needle_run_skips_lengths_above_a_smaller_context(client, server, mlx_running):
    server.reply = _echo_code
    server.health = {**server.health, "loaded_context_size": 40_000}
    _, results = await _run(client, suite="needle-v1")
    assert {x["category"] for x in results} == {"needle-8192", "needle-32768"}


async def test_needle_run_without_a_context_size_runs_8192_only(client, server, mlx_running):
    server.reply = _echo_code
    server.health = {k: v for k, v in server.health.items() if k != "loaded_context_size"}
    _, results = await _run(client, suite="needle-v1")
    assert len(results) == 3 and {x["category"] for x in results} == {"needle-8192"}


async def test_strata_needle_uses_max_context(client_strata):
    _, results = await _run(client_strata, suite="needle-v1")
    assert {x["category"] for x in results} == {"needle-8192", "needle-32768"}  # 131072 > 65536


async def test_thinking_template_reply_without_an_opening_tag(client, server, mlx_running, tiny_suite):
    # the template put <think> in the prompt: only the closing tag comes back
    server.reply = lambda p: {
        "choices": [{"message": {"content": "is it READY? or Paris</think>READY"},
                     "finish_reason": "stop"}]
    }  # fmt: skip
    _, results = await _run(client, suite="tiny", thinking=True)
    assert {x["case_id"]: x["passed"] for x in results} == {"ready": 1, "backwards": 0, "other": 0}
    assert {x["thinking"] for x in results} == {1}


async def test_thinking_cut_off_without_tags_scores_zero(client, server, mlx_running, tiny_suite):
    server.reply = lambda p: {
        "choices": [{"message": {"content": "READY Paris ananab"}, "finish_reason": "length"}]
    }
    _, results = await _run(client, suite="tiny", thinking=True)
    assert all(x["score"] == 0 and "stopped while thinking" in x["grade_detail"] for x in results)
    _, off = await _run(client, suite="tiny")
    assert {x["thinking"] for x in off} == {0}


async def test_a_model_with_failed_cases_is_not_promoted(client, server, mlx_running, tiny_suite):
    def one_fails(payload):
        if "banana" in payload["messages"][-1]["content"]:
            return httpx.Response(500, text="boom")
        return _reply(payload)

    server.reply = one_fails
    run, results = await _run(client, suite="tiny")
    assert [x["grade_state"] for x in results].count("failed") == 1
    summary = run["cases"][0]["summary"]
    assert summary["n"] == 2 and summary["excluded"] == 1 and summary["mean"] == 0.5
    assert client.app.state.evals.scorecard() == []


async def test_deterministic_grading_runs_off_the_event_loop(
    client, server, mlx_running, tiny_suite, monkeypatch
):
    import threading

    threads = []
    real = quality_runner.grade_response

    def spy(*args, **kwargs):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(quality_runner, "grade_response", spy)
    server.reply = _reply
    await _run(client, suite="tiny")
    assert threads == [False, False, False]
