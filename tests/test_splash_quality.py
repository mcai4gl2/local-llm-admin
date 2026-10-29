"""Qualification uses fake HTTP and synthetic scores, never the Mac or old speed verdicts."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.test_splash_phase0 import manifest_data as manifest_data

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/splash"))
try:
    SPEC = importlib.util.spec_from_file_location(
        "splash_quality", ROOT / "experiments/splash/quality.py"
    )
    assert SPEC and SPEC.loader
    quality = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = quality
    SPEC.loader.exec_module(quality)
finally:
    sys.path.pop(0)


def suite_data(n=12):
    return {
        "id": "quality-test",
        "version": 1,
        "title": "Synthetic pilot",
        "cases": [
            {
                "id": f"case-{i}",
                "category": "instruction",
                "prompt": f"Case {i}: answer READY.",
                "max_tokens": 32,
                "grader": {"type": "exact", "answer": "READY"},
            }
            for i in range(n)
        ],
    }


def manifest(data, backend="mlx", n=12):
    p = copy.deepcopy(data)
    p.update(
        cache_policy="isolated",
        backend=backend,
        rate_metric="client_output_wall_tok_s",
        draft=None
        if backend == "mlx"
        else {"checkpoint": "example/draft", "revision": "frozen", "sha256": "f" * 64},
    )
    s = suite_data(n)
    return quality.QualityManifest.model_validate(
        {
            "contract": "splash-quality-1",
            "id": backend + "-quality-1",
            "profile": p,
            "suite": s,
            "suite_sha256": quality.bench.digest(quality.parse_suite(s).model_dump()),
            "grader_sha256": quality.grader_digest(),
            "preflight_sha256": "e" * 64,
            "request_deadline_s": 60.0,
            "required_case_ids": [],
            "preflight_captured_at": time.time(),
        }
    )


def response(answer="READY", **extra):
    d = {
        "model": "local-qwen",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 4,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    }
    d.update(extra)
    return d


def fake_client(payload=None, status=200, seen=None):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "local-qwen"}]})
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, json=response() if payload is None else payload)

    return httpx.AsyncClient(
        base_url="http://127.0.0.1:18080", transport=httpx.MockTransport(handler)
    )


async def run_pair(tmp_path, data, n=12):
    runs = []
    for backend in ("mlx", "splash"):
        async with fake_client() as c:
            runs.append(
                await quality.run_quality(manifest(data, backend, n), tmp_path / backend, client=c)
            )
    return runs


async def test_real_request_translation_private_checkpoints_and_paired_grading(
    tmp_path, manifest_data
):
    runs = await run_pair(tmp_path, manifest_data)
    report = quality.compare_quality(runs)
    assert report["cases"] == 12 and report["mean_difference"] == 0
    assert report["decision"] == "inconclusive"
    assert report["difference_ci95"][0] < -0.02 < report["difference_ci95"][1]
    assert report["samples"] == {"measured": 24, "errors": 0, "truncated": 0}
    assert len(report["raw_runs"]) == 2
    for backend in ("mlx", "splash"):
        path = tmp_path / backend / f"{backend}-quality-1.json"
        assert path.stat().st_mode & 0o777 == 0o600
        assert json.loads(path.read_text())["status"] == "complete"
    before = (tmp_path / "mlx" / "mlx-quality-1.json").read_bytes()
    async with fake_client() as c:
        with pytest.raises(FileExistsError):
            await quality.run_quality(runs[0].manifest, tmp_path / "mlx", client=c)
    assert (tmp_path / "mlx" / "mlx-quality-1.json").read_bytes() == before


def test_body_is_frozen_paired_and_preserves_tools_and_backend_reasoning(manifest_data):
    a, b = manifest(manifest_data), manifest(manifest_data, "splash")
    a.suite.cases[0].tools = [
        {"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}
    ]
    b.suite.cases[0].tools = copy.deepcopy(a.suite.cases[0].tools)
    x = quality.quality_body(a, a.suite.cases[0])
    y = quality.quality_body(b, b.suite.cases[0])
    assert x["messages"] == y["messages"]
    assert x["tools"] == y["tools"] and x["tool_choice"] == "auto"
    assert x["stream"] is False and "stream_options" not in x
    assert x["enable_thinking"] is False and y["chat_template_kwargs"] == {"enable_thinking": False}
    assert x["max_tokens"] == 32 and x["seed"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        "suite_hash",
        "grader_hash",
        "duplicate",
        "judge",
        "needle",
        "required",
        "cap",
        "zero_preflight",
    ],
)
def test_manifest_rejects_unfrozen_or_unusable_suite(manifest_data, bad):
    d = manifest(manifest_data).model_dump()
    if bad == "suite_hash":
        d["suite_sha256"] = "f" * 64
    elif bad == "grader_hash":
        d["grader_sha256"] = "f" * 64
    elif bad == "duplicate":
        d["suite"]["cases"].append(copy.deepcopy(d["suite"]["cases"][0]))
    elif bad == "judge":
        d["suite"]["cases"][0]["grader"] = {"type": "judge", "rubric": "good"}
    elif bad == "needle":
        d["suite"]["needle"] = {"lengths": [8192], "depths": [0.5], "seed": "test"}
    elif bad == "required":
        d["required_case_ids"] = ["absent"]
    elif bad == "cap":
        d["suite"]["cases"][0]["max_tokens"] = 32768
    elif bad == "zero_preflight":
        d["preflight_sha256"] = "0" * 64
    if bad not in {"suite_hash", "grader_hash"}:
        d["suite_sha256"] = quality.bench.digest(d["suite"])
    with pytest.raises(ValueError):
        quality.QualityManifest.model_validate(d)


async def test_draft_cannot_make_network_requests(tmp_path, manifest_data):
    m = manifest(manifest_data)
    m.preflight_sha256 = None
    async with fake_client() as c:
        with pytest.raises(ValueError, match="preflight"):
            await quality.run_quality(m, tmp_path, client=c)
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "bad", ["http", "model", "usage", "cache", "tool", "malformed", "empty", "length"]
)
async def test_response_failures_remain_visible(tmp_path, manifest_data, bad):
    d = response()
    status = 200
    if bad == "http":
        status = 500
    elif bad == "model":
        d["model"] = "wrong"
    elif bad == "usage":
        d["usage"]["completion_tokens"] = True
    elif bad == "cache":
        d["usage"]["prompt_tokens_details"]["cached_tokens"] = 1
    elif bad == "tool":
        d["choices"][0]["message"]["tool_calls"] = [
            {"function": {"name": "echo", "arguments": "not-json"}}
        ]
    elif bad == "malformed":
        d = []
    elif bad == "empty":
        d["choices"][0]["message"]["content"] = ""
    elif bad == "length":
        d["choices"][0]["finish_reason"] = "length"
    async with fake_client(d, status) as c:
        run = await quality.run_quality(manifest(manifest_data, n=1), tmp_path, client=c)
    assert len(run.samples) == 1
    s = run.samples[0]
    assert s.measurement.error or s.measurement.finish_reason == "length"
    assert json.loads((tmp_path / "mlx-quality-1.json").read_text())["samples"]


async def test_cancellation_checkpoints_inflight_case_and_propagates(tmp_path, manifest_data):
    started = asyncio.Event()

    async def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "local-qwen"}]})
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18080", transport=httpx.MockTransport(handle)
    ) as c:
        task = asyncio.create_task(quality.run_quality(manifest(manifest_data), tmp_path, client=c))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    d = json.loads((tmp_path / "mlx-quality-1.json").read_text())
    assert d["status"] == "interrupted" and d["samples"][0]["measurement"]["error"] == "cancelled"


@pytest.mark.parametrize(
    "bad",
    [
        "missing",
        "duplicate",
        "error",
        "cache",
        "prompt",
        "weights",
        "profile",
        "runtime",
        "id",
        "grade",
        "partial",
        "truncated",
    ],
)
async def test_pair_rejects_incompatible_or_incomplete_evidence(tmp_path, manifest_data, bad):
    runs = await run_pair(tmp_path, manifest_data)
    b = runs[1]
    if bad == "missing":
        b.samples.pop()
    elif bad == "duplicate":
        b.samples.append(copy.deepcopy(b.samples[0]))
    elif bad == "error":
        b.samples[0].measurement.error = "timeout"
    elif bad == "cache":
        b.samples[0].measurement.cached_tokens = 1
    elif bad == "prompt":
        b.samples[0].measurement.prompt_tokens += 1
    elif bad == "weights":
        b.manifest.profile.model.weights_sha256 = "a" * 64
    elif bad == "profile":
        b.manifest.profile.settings.seed = 7
    elif bad == "runtime":
        b.manifest.grader_sha256 = "a" * 64
    elif bad == "id":
        b.manifest.id = runs[0].manifest.id
    elif bad == "grade":
        b.samples[0].measurement.answer = "wrong"
    elif bad == "partial":
        b.status = "interrupted"
    elif bad == "truncated":
        b.samples[0].measurement.finish_reason = "length"
    report = quality.compare_quality(runs)
    assert report["decision"] != "pass"
    if bad == "grade":
        assert report["decision"] == "fail" and report["mean_difference"] < -0.02
    else:
        assert report["issues"]
    assert len(report["raw_runs"]) == 2


def test_tied_small_pilot_has_nonzero_uncertainty_but_large_fixed_suite_can_pass():
    small = quality.score_pair([True] * 12, [True] * 12)
    large = quality.score_pair([True] * 300, [True] * 300)
    assert small["decision"] == "inconclusive" and small["difference_ci95"][0] < -0.02
    assert large["decision"] == "pass" and large["difference_ci95"][0] > -0.02
    assert quality.score_pair([True] * 100, [False] * 3 + [True] * 97)["decision"] == "fail"
    assert quality.score_pair([True], [True])["decision"] == "inconclusive"
    with pytest.raises(ValueError):
        quality.score_pair([], [])
    with pytest.raises(ValueError):
        quality.score_pair([True], [])


async def test_required_case_failure_cannot_be_hidden_by_net_quality_gain(tmp_path, manifest_data):
    runs = await run_pair(tmp_path, manifest_data)
    for r in runs:
        r.manifest.required_case_ids = ["case-0"]
    runs[1].samples[0].measurement.answer = "wrong"
    for s in runs[0].samples[1:]:
        s.measurement.answer = "wrong"
    report = quality.compare_quality(runs)
    assert report["mean_difference"] > 0 and report["decision"] == "fail"
    assert "required_case_failed" in report["issues"]


def test_checked_in_pilot_has_coding_tools_and_only_deterministic_cases():
    d = json.loads((ROOT / "experiments/splash/quality-suite.json").read_text())
    suite = quality.parse_suite(d)
    assert len(suite.cases) >= 35
    assert len({c.id for c in suite.cases}) == len(suite.cases)
    assert {"coding", "tools", "instruction", "extraction", "math", "reasoning"} <= {
        c.category for c in suite.cases
    }
    assert all(c.grader.type != "judge" for c in suite.cases)


def source_pair(data):
    from tests.test_splash_phase0 import runs_for

    p = copy.deepcopy(data)
    p["cache_policy"] = "isolated"
    return [quality.bench.Run.model_validate(r.model_dump()) for r in runs_for(p)[:2]]


def fresh_preflight():
    return {
        "complete": True,
        "issues": [],
        "serving_ready": True,
        "review": {"identity_checked": True},
        "captured_at": time.time(),
    }


@pytest.mark.parametrize("cohort", ["stock-8bit", "modified-4bit"])
def test_prepare_writes_unrunnable_drafts_and_preserves_whole_pair_budget(
    tmp_path, manifest_data, cohort
):
    sources = source_pair(manifest_data)
    suite = quality.parse_suite(suite_data())
    out = tmp_path / "prepared"
    result = quality.prepare_pair(sources, suite, out, cohort=cohort)
    assert result["authorization"] == "required"
    assert result["requests_per_backend"] == 12
    assert result["budgeted_total_s"] == 12 * 60 * 2 + 900 + 600
    assert result["native_gate"] == "unassessed"
    assert out.stat().st_mode & 0o777 == 0o700
    drafts = list(out.glob("*.draft.json"))
    assert len(drafts) == 2
    for p in drafts:
        assert p.stat().st_mode & 0o777 == 0o600
        assert quality.QualityManifest.model_validate_json(p.read_text()).preflight_sha256 is None
    with pytest.raises(FileExistsError):
        quality.prepare_pair(sources, suite, out, cohort=cohort)
    with pytest.raises(ValueError, match="fit"):
        quality.prepare_pair(sources, suite, tmp_path / "short", cohort=cohort, window_s=2000)
    assert not (tmp_path / "short").exists()


@pytest.mark.parametrize(
    "bad", ["partial", "error", "pair", "cohort", "deadline", "window", "mismatch"]
)
def test_prepare_rejects_invalid_sources_before_writing(tmp_path, manifest_data, bad):
    sources = source_pair(manifest_data)
    options = {}
    if bad == "partial":
        sources[0].status = "interrupted"
    elif bad == "error":
        sources[0].samples[0].measurement.error = "timeout"
    elif bad == "pair":
        sources.pop()
    elif bad == "cohort":
        options["cohort"] = "REDACTED_TEST_PAYLOAD"
    elif bad == "deadline":
        options["deadline_s"] = float("nan")
    elif bad == "window":
        options["window_s"] = float("inf")
    elif bad == "mismatch":
        sources[1].manifest.model.weights_sha256 = "a" * 64
    with pytest.raises(ValueError):
        quality.prepare_pair(
            sources,
            quality.parse_suite(suite_data()),
            tmp_path / "out",
            **{"cohort": "stock-8bit", **options},
        )
    assert not (tmp_path / "out").exists()


def test_freeze_binds_reviewed_snapshot_without_changing_draft(manifest_data):
    m = manifest(manifest_data)
    m.preflight_sha256 = None
    m.preflight_captured_at = None
    before = m.model_dump()
    snapshot = fresh_preflight()
    result = quality.freeze_manifest(m, snapshot)
    assert result.preflight_sha256 == quality.bench.digest(snapshot)
    assert result.preflight_captured_at == snapshot["captured_at"]
    assert m.model_dump() == before


@pytest.mark.parametrize(
    "bad", ["incomplete", "issues", "not_ready", "not_reviewed", "stale", "future", "type"]
)
def test_freeze_rejects_unreviewed_or_stale_inventory(manifest_data, bad):
    p = fresh_preflight()
    if bad == "incomplete":
        p["complete"] = False
    elif bad == "issues":
        p["issues"] = ["listener_identity_requires_review"]
    elif bad == "not_ready":
        p["serving_ready"] = False
    elif bad == "not_reviewed":
        p["review"] = {}
    elif bad == "stale":
        p["captured_at"] -= 901
    elif bad == "future":
        p["captured_at"] += 31
    elif bad == "type":
        p["captured_at"] = True
    with pytest.raises(ValueError):
        quality.freeze_manifest(manifest(manifest_data), p)


async def test_stale_frozen_manifest_never_connects(tmp_path, manifest_data):
    m = manifest(manifest_data)
    m.preflight_captured_at -= 901
    async with fake_client() as c:
        with pytest.raises(ValueError, match="preflight"):
            await quality.run_quality(m, tmp_path, client=c)
    assert not list(tmp_path.iterdir())


async def test_valid_tool_output_is_captured_and_graded_without_execution(tmp_path, manifest_data):
    m = manifest(manifest_data, n=1)
    d = m.model_dump()
    d["suite"]["cases"][0]["grader"] = {
        "type": "tool_call",
        "name": "echo",
        "arguments": {"text": "READY"},
    }
    d["suite_sha256"] = quality.bench.digest(quality.parse_suite(d["suite"]).model_dump())
    m = quality.QualityManifest.model_validate(d)
    p = response(None)
    p["choices"][0].update(finish_reason="tool_calls")
    p["choices"][0]["message"]["tool_calls"] = [
        {"type": "function", "function": {"name": "echo", "arguments": '{"text":"READY"}'}}
    ]
    async with fake_client(p) as c:
        r = await quality.run_quality(m, tmp_path, client=c)
    assert r.samples[0].measurement.error is None
    assert r.samples[0].tool_calls[0]["function"]["name"] == "echo"


@pytest.mark.parametrize("bad_calls", [False, 0, "", {}, "REDACTED_TEST_PAYLOAD"])
async def test_invalid_empty_tool_field_is_not_treated_as_absence(
    tmp_path, manifest_data, bad_calls
):
    p = response()
    p["choices"][0]["message"]["tool_calls"] = bad_calls
    async with fake_client(p) as c:
        r = await quality.run_quality(manifest(manifest_data, n=1), tmp_path, client=c)
    assert r.samples[0].measurement.error == "invalid_tool_calls"


async def test_compare_revalidates_malformed_saved_tools(tmp_path, manifest_data):
    runs = await run_pair(tmp_path, manifest_data)
    runs[1].samples[0].tool_calls = [{"function": {"name": "echo", "arguments": "INVALID"}}]
    report = quality.compare_quality(runs)
    assert report["issues"] and report["decision"] != "pass"


@pytest.mark.parametrize(
    "failure",
    [
        "timeout",
        "transport",
        "discovery",
        "discovery_status",
        "discovery_bad_json",
        "auth",
        "post_bad_json",
        "reasoning",
    ],
)
async def test_transport_preflight_and_payload_errors_are_checkpointed(
    tmp_path, manifest_data, failure
):
    async def handle(request):
        if request.method == "GET":
            if failure == "discovery":
                return httpx.Response(200, json={"data": []})
            if failure == "discovery_status":
                return httpx.Response(503)
            if failure == "discovery_bad_json":
                return httpx.Response(200, content=b"bad")
            return httpx.Response(200, json={"data": [{"id": "local-qwen"}]})
        if failure == "timeout":
            raise httpx.ReadTimeout("REDACTED_TEST_PAYLOAD")
        if failure == "transport":
            raise httpx.ConnectError("REDACTED_TEST_PAYLOAD")
        if failure == "post_bad_json":
            return httpx.Response(200, content=b"REDACTED_TEST_PAYLOAD")
        p = response()
        p["choices"][0]["message"]["reasoning_content"] = 10
        return httpx.Response(200, json=p)

    m = manifest(manifest_data, n=1)
    if failure == "auth":
        m.profile.api_key_env = "SPLASH_TEST_KEY_MISSING"
    async with httpx.AsyncClient(
        base_url=m.profile.base_url, transport=httpx.MockTransport(handle)
    ) as c:
        r = await quality.run_quality(m, tmp_path, client=c)
    assert r.error or r.samples[0].measurement.error
    assert "REDACTED_TEST_PAYLOAD" not in (r.error or "")
    assert all("REDACTED_TEST_PAYLOAD" not in (s.measurement.error or "") for s in r.samples)


@pytest.mark.parametrize("cohort", ["stock-8bit", "modified-4bit"])
def test_cli_prepare_freeze_compare_and_sanitized_errors(tmp_path, manifest_data, capsys, cohort):
    sources = source_pair(manifest_data)
    for backend, r in zip(["mlx", "splash"], sources, strict=True):
        (tmp_path / f"{backend}.json").write_text(r.model_dump_json())
    sp = tmp_path / "suite.json"
    sp.write_text(json.dumps(suite_data()))
    args = [
        "prepare",
        "--mlx-run",
        str(tmp_path / "mlx.json"),
        "--splash-run",
        str(tmp_path / "splash.json"),
        "--cohort",
        cohort,
        "--suite",
        str(sp),
        "--out",
        str(tmp_path / "prepared"),
    ]
    assert quality.main(args) == 0
    assert quality.main(args) == 2
    draft = next((tmp_path / "prepared").glob("*.draft.json"))
    pf = tmp_path / "preflight.json"
    pf.write_text(json.dumps(fresh_preflight()))
    frozen = tmp_path / "frozen.json"
    assert (
        quality.main(
            ["freeze", "--draft", str(draft), "--preflight", str(pf), "--out", str(frozen)]
        )
        == 0
    )
    assert frozen.stat().st_mode & 0o777 == 0o600
    assert (
        quality.main(
            [
                "compare",
                "--results",
                str(tmp_path / "empty"),
                "--out",
                str(tmp_path / "compare.json"),
            ]
        )
        == 2
    )
    sp.write_text("REDACTED_TEST_PAYLOAD")
    assert quality.main([*args[:-1], str(tmp_path / "other")]) == 2
    assert "REDACTED_TEST_PAYLOAD" not in capsys.readouterr().out


def test_identical_case_inputs_cannot_inflate_quality_sample_size(manifest_data):
    d = manifest(manifest_data).model_dump()
    d["suite"]["cases"][1]["prompt"] = d["suite"]["cases"][0]["prompt"]
    d["suite_sha256"] = quality.bench.digest(d["suite"])
    with pytest.raises(ValueError, match="duplicate.*input"):
        quality.QualityManifest.model_validate(d)


@pytest.mark.parametrize(
    "finish,error,status,expected",
    [
        ("stop", None, "complete", 0),
        ("length", None, "complete", 1),
        ("stop", "timeout", "complete", 1),
        ("stop", None, "interrupted", 1),
    ],
)
def test_cli_run_exit_status_preserves_truncation_and_errors(
    tmp_path, manifest_data, monkeypatch, finish, error, status, expected
):
    m = manifest(manifest_data, n=1)
    path = tmp_path / "manifest.json"
    path.write_text(m.model_dump_json())

    async def capture(given, out):
        assert given.id == m.id
        return quality.QualityRun(
            manifest=m,
            status=status,
            samples=[
                quality.QualitySample(
                    case_id="case-0",
                    measurement=quality.bench.Measurement(finish_reason=finish, error=error),
                )
            ],
        )

    monkeypatch.setattr(quality, "run_quality", capture)
    assert (
        quality.main(["run", "--manifest", str(path), "--out", str(tmp_path / "result")])
        == expected
    )


async def test_compare_cli_reconciles_private_captures(tmp_path, manifest_data):
    await run_pair(tmp_path, manifest_data)
    results = tmp_path / "both"
    results.mkdir()
    for backend in ("mlx", "splash"):
        p = tmp_path / backend / f"{backend}-quality-1.json"
        (results / p.name).write_bytes(p.read_bytes())
    out = tmp_path / "report.json"
    assert quality.main(["compare", "--results", str(results), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["cases"] == 12 and report["decision"] == "inconclusive"
    assert out.stat().st_mode & 0o777 == 0o600


async def test_owned_client_uses_proxy_free_bounded_transport(tmp_path, manifest_data, monkeypatch):
    real_client = httpx.AsyncClient

    def make_client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"].connect == 5

        def handle(request):
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "local-qwen"}]})
            return httpx.Response(200, json=response())

        return real_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(quality.httpx, "AsyncClient", make_client)
    r = await quality.run_quality(manifest(manifest_data, n=1), tmp_path)
    assert r.status == "complete" and r.samples[0].measurement.error is None


async def test_report_identifies_which_case_and_category_regressed(tmp_path, manifest_data):
    runs = await run_pair(tmp_path, manifest_data)
    runs[1].samples[5].measurement.answer = "WRONG"
    report = quality.compare_quality(runs)
    failed = next(c for c in report["per_case"] if c["id"] == "case-5")
    assert failed["mlx_pass"] is True and failed["splash_pass"] is False
    assert report["categories"]["instruction"]["mlx_score"] == 1.0
    assert report["categories"]["instruction"]["splash_score"] == pytest.approx(11 / 12)


@pytest.mark.parametrize("bad", ["error", "truncated"])
async def test_capture_stops_on_first_failed_case_and_retains_it(tmp_path, manifest_data, bad):
    seen = []
    p = response()
    status = 503 if bad == "error" else 200
    if bad == "truncated":
        p["choices"][0]["finish_reason"] = "length"
    async with fake_client(p, status, seen) as c:
        r = await quality.run_quality(manifest(manifest_data), tmp_path, client=c)
    assert len(seen) == len(r.samples) == 1
    assert r.status == "failed" and r.error
    saved = json.loads((tmp_path / "mlx-quality-1.json").read_text())
    assert len(saved["samples"]) == 1 and saved["status"] == "failed"


async def test_new_nested_artifact_directories_are_private(tmp_path, manifest_data):
    out = tmp_path / "capture" / "nested" / "results"
    async with fake_client() as c:
        await quality.run_quality(manifest(manifest_data, n=1), out, client=c)
    for path in [out, out.parent, out.parent.parent]:
        assert path.stat().st_mode & 0o777 == 0o700
    out = tmp_path / "preparation" / "nested" / "cohort"
    quality.prepare_pair(
        source_pair(manifest_data), quality.parse_suite(suite_data()), out, cohort="stock-8bit"
    )
    for path in [out, out.parent, out.parent.parent]:
        assert path.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "bad",
    [
        "choices",
        "choice_type",
        "finish",
        "index",
        "role",
        "message",
        "content",
        "tool_type",
        "function",
        "arguments",
        "argument_object",
        "tool_finish",
        "usage",
    ],
)
async def test_protocol_structure_failures_are_explicit_and_stop_capture(
    tmp_path, manifest_data, bad
):
    p = response()
    choice = p["choices"][0]
    msg = choice["message"]
    if bad == "choices":
        p["choices"] = []
    elif bad == "choice_type":
        p["choices"] = [None]
    elif bad == "finish":
        choice["finish_reason"] = "unknown"
    elif bad == "index":
        choice["index"] = 1
    elif bad == "role":
        msg["role"] = "user"
    elif bad == "message":
        choice["message"] = []
    elif bad == "content":
        msg["content"] = 10
    elif bad in {"tool_type", "function", "arguments", "argument_object"}:
        choice["finish_reason"] = "tool_calls"
        call = {"type": "function", "function": {"name": "echo", "arguments": "{}"}}
        if bad == "tool_type":
            call["type"] = "unknown"
        elif bad == "function":
            call["function"] = {}
        elif bad == "arguments":
            call["function"]["arguments"] = "INVALID"
        elif bad == "argument_object":
            call["function"]["arguments"] = "[]"
        msg["tool_calls"] = [call]
    elif bad == "tool_finish":
        choice["finish_reason"] = "tool_calls"
    elif bad == "usage":
        p.pop("usage")
    async with fake_client(p) as c:
        r = await quality.run_quality(manifest(manifest_data), tmp_path, client=c)
    assert r.status == "failed" and len(r.samples) == 1 and r.samples[0].measurement.error


async def test_boolean_choice_index_is_not_an_integer_index(tmp_path, manifest_data):
    p = response()
    p["choices"][0]["index"] = False
    async with fake_client(p) as c:
        r = await quality.run_quality(manifest(manifest_data, n=1), tmp_path, client=c)
    assert r.samples[0].measurement.error == "invalid_finish_reason"


async def test_required_single_tool_case_cannot_hide_extra_calls(tmp_path, manifest_data):
    runs = await run_pair(tmp_path, manifest_data)
    for run in runs:
        d = run.manifest.model_dump()
        d["suite"]["cases"][0]["grader"] = {"type": "tool_call", "name": "echo", "arguments": {}}
        d["suite_sha256"] = quality.bench.digest(quality.parse_suite(d["suite"]).model_dump())
        d["required_case_ids"] = ["case-0"]
        run.manifest = quality.QualityManifest.model_validate(d)
        s = run.samples[0]
        s.measurement.finish_reason = "tool_calls"
        s.tool_calls = [{"type": "function", "function": {"name": "echo", "arguments": "{}"}}]
    runs[1].samples[0].tool_calls.append(copy.deepcopy(runs[1].samples[0].tool_calls[0]))
    report = quality.compare_quality(runs)
    assert report["decision"] == "fail" and "required_case_failed" in report["issues"]


@pytest.mark.parametrize("failure", ["timeout", "transport", "cancel"])
async def test_discovery_failures_and_cancellation_leave_terminal_private_record(
    tmp_path, manifest_data, failure
):
    ready = asyncio.Event()

    async def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("REDACTED_TEST_PAYLOAD")
        if failure == "transport":
            raise httpx.ConnectError("REDACTED_TEST_PAYLOAD")
        ready.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18080", transport=httpx.MockTransport(handler)
    ) as c:
        if failure == "cancel":
            task = asyncio.create_task(
                quality.run_quality(manifest(manifest_data), tmp_path, client=c)
            )
            await ready.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            run = await quality.run_quality(manifest(manifest_data), tmp_path, client=c)
            assert run.status == "failed" and run.error.startswith("preflight_")
    saved = json.loads((tmp_path / "mlx-quality-1.json").read_text())
    assert saved["status"] in {"failed", "interrupted"} and saved["samples"] == []
    assert "REDACTED_TEST_PAYLOAD" not in (saved["error"] or "")
