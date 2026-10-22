"""M10: /api/evals/* routes."""

import json
import subprocess

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin import jobs as jobs_mod
from local_llm_admin.app import create_app
from local_llm_admin.eval_suites import parse_suite

from .fake_mlx_server import FakeMlxServer
from .test_eval_runner import TINY, _reply

JUDGED = {
    "id": "judged",
    "version": 1,
    "title": "Judged",
    "cases": [
        {"id": "j", "category": "open", "prompt": "Reply with exactly the word READY",
         "grader": {"type": "judge", "rubric": "ok"}},
        {"id": "ready", "category": "instruction", "prompt": "Reply with exactly the word READY",
         "grader": {"type": "exact", "answer": "READY"}},
    ],
}  # fmt: skip
JSONL = '{"id": "a", "category": "x", "prompt": "p", "grader": {"type": "exact", "answer": "y"}}\n'


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


@pytest.fixture
async def app(settings, fake_comfy, server):
    app = create_app(
        settings,
        start_background=False,
        comfy_transport=fake_comfy.transport,
        quality_transport=server.transport,
    )
    async with app.router.lifespan_context(app):
        await app.state.startup_task
        yield app


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.9", 1234)),
        base_url="http://admin.test",
    ) as c:
        assert (await c.post("/login", data={"password": "pw"})).status_code == 303
        c.headers["X-CSRF-Token"] = (await c.get("/api/session")).json()["csrf"]
        c.app = app
        yield c


@pytest.fixture
def mlx_running(settings):
    subprocess.run(
        [str(settings.mlx_model_path), "start", "qwen"], capture_output=True, check=False
    )


@pytest.fixture
def suites(app):
    app.state.evals.add_custom(parse_suite(TINY))
    app.state.evals.add_custom(parse_suite(JUDGED))


async def _run(client, **body):
    r = await client.post("/api/quality/runs", json={"kind": "eval", "scope": "current", **body})
    assert r.status_code == 202, r.text
    await client.app.state.quality.wait()
    return r.json()["id"]


@pytest.fixture
async def finished_tiny_run(client, server, mlx_running, suites):
    server.reply = _reply
    return await _run(client, suite="tiny")


def _sql(app, sql, *args):
    with app.state.evals._conn() as c:
        return c.execute(sql, args).fetchall()


async def test_requires_login_and_csrf(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
    ) as anon:
        assert (await anon.get("/api/evals/suites")).status_code == 401
        assert (await anon.get("/api/evals/scorecard")).status_code == 401
        await anon.post("/login", data={"password": "pw"})
        assert (await anon.delete("/api/evals/suites/x")).status_code == 403
        assert (await anon.post("/api/evals/estimate", json={})).status_code == 403
        r = await anon.post("/api/evals/suites", data={"id": "m", "title": "M"},
                            files={"file": ("s.jsonl", b"")})  # fmt: skip
        assert r.status_code == 403


async def test_suite_listing_and_detail(client, suites):
    listing = (await client.get("/api/evals/suites")).json()["suites"]
    assert {"core-v1", "tiny"} <= {s["id"] for s in listing}
    one = (await client.get("/api/evals/suites/tiny")).json()
    assert one["id"] == "tiny" and len(one["cases"]) == 3
    assert (await client.get("/api/evals/suites/tiny", params={"version": 1})).status_code == 200
    assert (await client.get("/api/evals/suites/tiny", params={"version": 9})).status_code == 404
    assert (await client.get("/api/evals/suites/nope")).status_code == 404
    assert (await client.get("/api/evals/suites/nope/export")).status_code == 404


async def test_import_export_roundtrip(client):
    r = await client.post("/api/evals/suites", data={"id": "mine", "title": "Mine"},
                          files={"file": ("s.jsonl", JSONL.encode())})  # fmt: skip
    assert r.status_code == 201
    out = await client.get("/api/evals/suites/mine/export")
    assert out.headers["content-disposition"].startswith("attachment")
    back = [json.loads(line) for line in out.text.splitlines() if line.strip()]
    assert [c["id"] for c in back] == ["a"]
    assert back[0]["grader"] == {"type": "exact", "answer": "y", "normalize": True}


async def test_import_errors(client):
    r = await client.post("/api/evals/suites", data={"id": "bad", "title": "B"},
                          files={"file": ("s.jsonl", b"{oops")})  # fmt: skip
    assert r.status_code == 400 and "line 1" in r.json()["detail"]
    r = await client.post("/api/evals/suites", data={"id": "core-v1", "title": "B"},
                          files={"file": ("s.jsonl", JSONL.encode())})  # fmt: skip
    assert r.status_code == 409
    r = await client.post("/api/evals/suites", data={"id": "bad id!", "title": "B"},
                          files={"file": ("s.jsonl", JSONL.encode())})  # fmt: skip
    assert r.status_code == 400
    r = await client.post("/api/evals/suites", data={"id": "bin", "title": "B"},
                          files={"file": ("s.jsonl", b"\xff\xfe")})  # fmt: skip
    assert r.status_code == 400


async def test_oversize_import_names_the_limit(client, settings):
    settings.eval_import_max_bytes = 1024
    r = await client.post("/api/evals/suites", data={"id": "big", "title": "B"},
                          files={"file": ("s.jsonl", b"x" * 2000)})  # fmt: skip
    assert r.status_code == 400 and "larger than 1024 bytes" in r.json()["detail"]


async def test_delete_custom_only(client):
    assert (await client.delete("/api/evals/suites/core-v1")).status_code == 409
    assert (await client.delete("/api/evals/suites/nope")).status_code == 404
    await client.post("/api/evals/suites", data={"id": "mine", "title": "Mine"},
                      files={"file": ("s.jsonl", JSONL.encode())})  # fmt: skip
    assert (await client.delete("/api/evals/suites/mine")).status_code == 204
    assert (await client.get("/api/evals/suites/mine")).status_code == 404


async def test_estimate_without_benchmark_uses_defaults(client, mlx_running):
    r = await client.post("/api/evals/estimate",
                          json={"suite": "core-v1", "scope": "current", "thinking": False})  # fmt: skip
    (m,) = r.json()["models"]
    assert m["name"] == "qwen" and m["basis"] == "default" and m["seconds"] > 0


async def test_estimate_arithmetic_and_switches(client, mlx_running, suites):
    body = {"suite": "judged", "scope": "all", "thinking": False}
    models = (await client.post("/api/evals/estimate", json=body)).json()["models"]
    assert [m["name"] for m in models] == ["qwen", "gemma"]
    case_s = len("Reply with exactly the word READY") / 4 / 300
    judge_s = case_s + (512 / 2) / 20  # judge: half of max_tokens
    exact_s = case_s + 64 / 20  # exact: capped at 64
    assert models[0]["seconds"] == pytest.approx(judge_s + exact_s, abs=0.06)
    assert models[1]["seconds"] == pytest.approx(judge_s + exact_s + 60, abs=0.06)
    thinking = {**body, "scope": "current", "thinking": True}
    t = (await client.post("/api/evals/estimate", json=thinking)).json()["models"][0]
    assert t["seconds"] > models[0]["seconds"]


async def test_estimate_errors(client, mlx_running):
    assert (
        await client.post("/api/evals/estimate", json={"suite": "nope", "scope": "current"})
    ).status_code == 404
    r = await client.post("/api/evals/estimate", json={"suite": "core-v1", "scope": "huh"})
    assert r.status_code == 400
    r = await client.post("/api/evals/estimate",
                          json={"suite": "core-v1", "scope": "model", "model": "missing"})  # fmt: skip
    assert r.status_code == 404


async def test_estimate_uses_benchmark_medians(client, finished_tiny_run, app):
    (fp,) = {r["fingerprint"] for r in app.state.evals.results(finished_tiny_run)}
    medians = {
        "backend_decode_tok_s": 40.0,
        "backend_prefill_tok_s": 600.0,
        "peak_rss_bytes": 2 * 1024**3,
    }
    with app.state.evals._conn() as c:
        c.execute(
            "INSERT INTO quality_baselines (fingerprint, case_key, settings_hash, cache_state, model,"
            " run_id, case_id, medians, ts) VALUES (?, 'p2k', 'h', 'verified_cold', 'qwen', 'r', 1, ?, 1)",
            (fp, json.dumps(medians)),
        )
    body = {"suite": "tiny", "scope": "current", "thinking": False}
    (m,) = (await client.post("/api/evals/estimate", json=body)).json()["models"]
    assert m["basis"] == "benchmark" and m["seconds"] < 5
    card = (await client.get("/api/evals/scorecard")).json()["models"][0]
    assert card["speed"] == {"decode_tok_s": 40.0, "prefill_tok_s": 600.0, "peak_rss_gb": 2.0}


async def test_results_endpoint(client, finished_tiny_run):
    r = await client.get(f"/api/evals/runs/{finished_tiny_run}/results")
    assert [x["case_id"] for x in r.json()["results"]] == ["ready", "backwards", "other"]
    r = await client.get(f"/api/evals/runs/{finished_tiny_run}/results", params={"model": "nope"})
    assert r.json() == {"results": []}


async def test_scorecard_has_null_speed_without_baseline(client, finished_tiny_run):
    (card,) = (await client.get("/api/evals/scorecard")).json()["models"]
    assert card["model"] == "qwen" and card["speed"] is None
    assert card["suites"]["tiny"]["mean"] == round(2 / 3, 4)
    assert card["suites"]["tiny"]["run_id"] == finished_tiny_run


async def test_regrade_uses_stored_responses(client, finished_tiny_run, server, app):
    _sql(app, "UPDATE eval_results SET score = 0.5, passed = 0 WHERE case_id = 'ready'")
    before = len(server.requests)
    r = await client.post(f"/api/evals/runs/{finished_tiny_run}/regrade")
    assert r.status_code == 200 and len(server.requests) == before
    body = r.json()
    assert body["regraded"] == 3 and body["changed"] == 1
    assert body["models"][0]["mean"] == round(2 / 3, 4)
    ready = next(x for x in app.state.evals.results(finished_tiny_run) if x["case_id"] == "ready")
    assert ready["score"] == 1.0 and ready["passed"] == 1
    # the case row summary was refreshed and the run (promoted before) stays promoted
    run = (await client.get(f"/api/quality/runs/{finished_tiny_run}")).json()
    assert run["cases"][0]["summary"]["mean"] == round(2 / 3, 4)
    assert _sql(app, "SELECT run_id FROM eval_scores")[0]["run_id"] == finished_tiny_run


async def test_regrade_keeps_case_status_detail_and_finished(client, finished_tiny_run, app):
    _sql(app, "UPDATE quality_cases SET summary = '{\"n\": 99}'")
    before = _sql(app, "SELECT status, detail, finished FROM quality_cases")[0]
    assert (await client.post(f"/api/evals/runs/{finished_tiny_run}/regrade")).status_code == 200
    after = _sql(app, "SELECT status, detail, finished, summary FROM quality_cases")[0]
    assert tuple(after)[:3] == tuple(before)
    assert json.loads(after["summary"])["n"] == 3


async def test_regrade_of_an_aborted_run_keeps_its_abort(client, server, mlx_running, suites, app, fake_mlx):
    server.reply = _reply
    server.on_chat = lambda payload: (fake_mlx.root / "run/active.env").write_text("MLX_ACTIVE_NAME=gemma\n")
    run = await _run(client, suite="tiny")
    before = _sql(app, "SELECT status, detail, finished FROM quality_cases WHERE run_id = ?", run)[0]
    assert before["status"] != "complete" and before["detail"]
    _sql(app, "UPDATE quality_cases SET summary = '{\"n\": 99}' WHERE run_id = ?", run)
    assert (await client.post(f"/api/evals/runs/{run}/regrade")).status_code == 200
    after = _sql(app, "SELECT status, detail, finished, summary FROM quality_cases WHERE run_id = ?", run)[0]
    assert tuple(after)[:3] == tuple(before)
    assert json.loads(after["summary"])["n"] != 99


async def test_regrade_does_not_promote_an_unpromoted_run(client, finished_tiny_run, app):
    _sql(app, "DELETE FROM eval_scores")
    assert (await client.post(f"/api/evals/runs/{finished_tiny_run}/regrade")).status_code == 200
    assert _sql(app, "SELECT * FROM eval_scores") == []


async def test_regrade_leaves_judge_and_failed_rows(client, server, mlx_running, suites, app):
    server.reply = _reply
    run = await _run(client, suite="judged")
    rows = app.state.evals.results(run)
    assert {x["case_id"]: x["grade_state"] for x in rows} == {"j": "ungraded", "ready": "graded"}
    _sql(
        app, "UPDATE eval_results SET grade_state = 'failed', score = NULL WHERE case_id = 'ready'"
    )
    r = await client.post(f"/api/evals/runs/{run}/regrade")
    assert r.status_code == 200 and r.json()["regraded"] == 0
    assert {x["grade_state"] for x in app.state.evals.results(run)} == {"ungraded", "failed"}


async def test_regrade_errors(client, finished_tiny_run, app):
    assert (await client.post("/api/evals/runs/nope/regrade")).status_code == 404
    _sql(app, "DELETE FROM eval_suites WHERE id = 'tiny'")
    r = await client.post(f"/api/evals/runs/{finished_tiny_run}/regrade")
    assert r.status_code == 409 and "suite" in r.json()["detail"]


async def test_regrade_refused_while_the_run_is_active(client, app, monkeypatch):
    monkeypatch.setattr(app.state.quality, "active", lambda: {"run_id": "live"})
    assert (await client.post("/api/evals/runs/live/regrade")).status_code == 409


async def test_regrade_applies_the_stored_thinking_setting(client, finished_tiny_run, app):
    _sql(app, "UPDATE eval_results SET thinking = 1, finish_reason = 'length' WHERE case_id = 'ready'")
    r = await client.post(f"/api/evals/runs/{finished_tiny_run}/regrade")
    assert r.status_code == 200 and r.json()["changed"] == 1
    ready = next(x for x in app.state.evals.results(finished_tiny_run) if x["case_id"] == "ready")
    assert ready["score"] == 0.0 and "stopped while thinking" in ready["grade_detail"]


async def _compare(client, a, b, model="qwen", suite="tiny"):
    return await client.get("/api/evals/compare", params={
        "suite": suite, "a_run": a, "a_model": model, "b_run": b, "b_model": model})  # fmt: skip


async def test_compare_reports_disagreements(client, finished_tiny_run, server):
    server.reply = lambda p: {
        "choices": [{"message": {"content": "wrong"}, "finish_reason": "stop"}]
    }
    b = await _run(client, suite="tiny")
    r = await _compare(client, finished_tiny_run, b)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n"] == 3 and body["wins"] == 2 and body["verdict"] == "no clear difference"
    assert {d["case_id"]: (d["a_score"], d["b_score"]) for d in body["disagreements"]} == {
        "ready": (1.0, 0.0), "backwards": (1.0, 0.0)}  # fmt: skip


async def test_compare_refuses_different_settings(client, finished_tiny_run, server):
    server.reply = _reply
    b = await _run(client, suite="tiny", thinking=True)
    r = await _compare(client, finished_tiny_run, b)
    assert r.status_code == 409 and "generation settings" in r.json()["detail"]
    assert r.json()["detail"].startswith("not comparable")


async def test_compare_refuses_different_suite_versions(client, finished_tiny_run, server, app):
    server.reply = _reply
    b = await _run(client, suite="tiny")
    _sql(app, "UPDATE eval_results SET suite_hash = 'other' WHERE run_id = ?", b)
    r = await _compare(client, finished_tiny_run, b)
    assert r.status_code == 409 and "suite version" in r.json()["detail"]


async def test_compare_without_shared_cases_or_results(client, finished_tiny_run, server, app):
    server.reply = _reply
    b = await _run(client, suite="tiny")
    _sql(app, "UPDATE eval_results SET case_id = 'z' || case_id WHERE run_id = ?", b)
    r = await _compare(client, finished_tiny_run, b)
    assert r.status_code == 409 and r.json()["detail"] == "not comparable: no shared cases"
    assert (await _compare(client, finished_tiny_run, "nope")).status_code == 404


async def test_quality_page_has_evals_section(client):
    html = (await client.get("/quality")).text
    for marker in (
        'id="evals"', 'id="eval-suite"', 'id="eval-scorecard"', 'id="eval-compare"',
        'id="eval-results"', "/static/evals.js",
    ):  # fmt: skip
        assert marker in html


async def test_evals_script_is_served_and_never_uses_innerhtml(client):
    r = await client.get("/static/evals.js")
    assert r.status_code == 200
    assert "innerHTML" not in r.text
    assert "innerHTML" not in (await client.get("/static/quality.js")).text
    assert "evals also run on Strata" in (await client.get("/static/quality.js")).text


async def test_eval_ui_texts_and_error_handling_present(client):
    q = (await client.get("/static/quality.js")).text
    assert 'kind === "eval" ? "eval" : "compatibility checks"' in q
    assert "okLabel" in q
    e = (await client.get("/static/evals.js")).text
    assert '"Delete"))' in e
    assert "Could not load results for" in e


async def test_estimate_names_the_judge(client, mlx_running, settings):
    body = {"suite": "core-v1", "scope": "current", "thinking": False}
    assert (await client.post("/api/evals/estimate", json=body)).json()["judge"] == "none"
    settings.eval_judge = "mlx:qwen"
    assert (await client.post("/api/evals/estimate", json=body)).json()["judge"] == "mlx:qwen"
    settings.eval_judge = "bogus"
    judge = (await client.post("/api/evals/estimate", json=body)).json()["judge"]
    assert judge.startswith("invalid judge setting: ")


async def test_needle_estimate_uses_the_engine_context(client, mlx_running, server):
    body = {"suite": "needle-v1", "scope": "current", "thinking": False}
    full = (await client.post("/api/evals/estimate", json=body)).json()["models"][0]["seconds"]
    server.health = {k: v for k, v in server.health.items() if k != "loaded_context_size"}
    small = (await client.post("/api/evals/estimate", json=body)).json()["models"][0]["seconds"]
    server.health = {**server.health, "loaded_context_size": 40_000}
    mid = (await client.post("/api/evals/estimate", json=body)).json()["models"][0]["seconds"]
    assert full > mid > small > 0  # 12 cases > 6 (8192, 32768) > 3 (8192 only)


async def test_needle_suite_is_not_exportable(client):
    r = await client.get("/api/evals/suites/needle-v1/export")
    assert r.status_code == 400 and "nothing to export" in r.json()["detail"]


async def _judged_pair(client, server, app, judge_a, judge_b, self_a=0):
    server.reply = _reply
    a, b = await _run(client, suite="judged"), await _run(client, suite="judged")
    for run, judge, own in ((a, judge_a, self_a), (b, judge_b, 0)):
        _sql(app, "UPDATE eval_results SET grade_state = 'graded', score = 1.0, passed = 1,"
             " judge = ?, self_judged = ? WHERE run_id = ? AND case_id = 'j'", judge, own, run)  # fmt: skip
    return a, b


async def test_compare_refuses_a_different_judge(client, server, mlx_running, suites, app):
    a, b = await _judged_pair(client, server, app, "local qwen", "external judge-m")
    r = await _compare(client, a, b, suite="judged")
    assert r.status_code == 409 and r.json()["detail"] == "not comparable: judge differs"


async def test_compare_flags_self_judged_sides(client, server, mlx_running, suites, app):
    a, b = await _judged_pair(client, server, app, "local qwen", "local qwen", self_a=1)
    r = await _compare(client, a, b, suite="judged")
    assert r.status_code == 200, r.text
    assert r.json()["self_judged"] == {"a": True, "b": False}
    plain = await _compare(client, a, a, suite="judged")
    assert plain.status_code == 200 and plain.json()["self_judged"] == {"a": True, "b": True}


async def test_compare_without_judge_cases_has_no_self_judged_flag(client, finished_tiny_run):
    r = await _compare(client, finished_tiny_run, finished_tiny_run)
    assert r.status_code == 200 and "self_judged" not in r.json()


async def test_scorecard_api_shows_both_thinking_variants(client, server, mlx_running, suites):
    server.reply = _reply
    await _run(client, suite="tiny")
    await _run(client, suite="tiny", thinking=True)
    (card,) = (await client.get("/api/evals/scorecard")).json()["models"]
    assert set(card["suites"]) == {"tiny", "tiny+thinking"}
    assert card["suites"]["tiny"]["thinking"] is False
    assert card["suites"]["tiny+thinking"]["thinking"] is True
    assert card["suites"]["tiny"]["suite_version"] == 1
