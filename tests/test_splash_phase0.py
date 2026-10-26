"""Splash experiment contracts; synthetic streams are not Mac performance evidence."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("splash_bench", ROOT / "ops/splash-phase0/bench.py")
assert SPEC and SPEC.loader
bench = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bench
SPEC.loader.exec_module(bench)


@pytest.fixture
def manifest_data():
    cases = [
        {
            "id": c,
            "category": c,
            "kind": "fresh",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": f"Explain {c}."}],
            "primary": True,
        }
        for c in ("short", "2k", "8k")
    ]
    return {
        "schema_version": 1,
        "id": "mlx-01",
        "session": 1,
        "order": "mlx-first",
        "backend": "mlx",
        "base_url": "http://127.0.0.1:18080",
        "api_key_env": None,
        "request_model": "local-qwen",
        "response_model": "local-qwen",
        "runtime": {"version": "test-1", "sha256": "a" * 64},
        "draft": None,
        "model": {
            "checkpoint": "example/Qwen3.8-27B",
            "revision": "frozen",
            "weights_sha256": "b" * 64,
            "tokenizer_sha256": "c" * 64,
            "template_sha256": "d" * 64,
            "quantization": "affine-4bit",
        },
        "settings": {"temperature": 0.0, "seed": 0, "reasoning": False},
        "context_tokens": 32768,
        "kv_precision": "fp16",
        "cache_policy": "disabled",
        "concurrency": 1,
        "repeats": 5,
        "warmups": 1,
        "deadline_s": 30.0,
        "corpus_version": "test-1",
        "corpus_sha256": bench.digest(cases),
        "cases": cases,
        "rate_metric": "backend_decode_tok_s",
        "evidence": {
            "preflight_sha256": "e" * 64,
            "cache_control": "disabled and verified",
            "native_timing_audit_sha256": "f" * 64,
        },
    }


def manifest(data):
    return bench.Manifest.model_validate(data)


def event(**data):
    return "data: " + json.dumps(data) + "\r\n\r\n"


def delta(**fields):
    return event(model="local-qwen", choices=[{"index": 0, "delta": fields}])


def ending(usage=None, finish="length"):
    if usage is None:
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 512,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 2},
        }
    return (
        event(choices=[{"index": 0, "delta": {}, "finish_reason": finish}])
        + event(choices=[], usage=usage, timings={"predicted_per_second": 50.0})
        + "data: [DONE]\r\n\r\n"
    )


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, text, *, stall=False):
        self.data = text.encode()
        self.closed = False
        self.stall = stall
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        if self.stall:
            await asyncio.Event().wait()
        for offset in range(0, len(self.data), 3):
            yield self.data[offset : offset + 3]

    async def aclose(self):
        self.closed = True


async def measurement(text, *, status=200, deadline=1.0, clock=None):
    stream = BytesStream(text)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18080",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ),
    ) as client:
        kw = {"clock": clock} if clock else {}
        sample = await bench.measure(
            client, {"max_tokens": 512}, response_model="local-qwen", deadline_s=deadline, **kw
        )
    assert stream.closed
    return sample


async def test_fragmented_multitoken_reasoning_final_usage_and_visible_answer():
    ticks = iter([0.0, 1.0, 2.0, 4.0])
    sample = await measurement(
        ": keepalive\r\n\r\n"
        + delta(reasoning_content="Think twice")
        + delta(content="Many tokens in one event: café")
        + ending(),
        clock=lambda: next(ticks),
    )
    assert sample.error is None
    assert sample.ttft_s == 1.0 and sample.first_answer_s == 2.0 and sample.total_s == 4.0
    assert sample.completion_tokens == 512 and sample.reasoning_tokens == 2
    assert sample.client_output_wall_tok_s == 128.0
    assert sample.backend_decode_tok_s == 50.0 and sample.cached_tokens == 0
    assert sample.answer.endswith("café")


async def test_reasoning_only_is_ttft_but_not_visible_answer():
    sample = await measurement(delta(reasoning="thinking") + ending())
    assert sample.ttft_s is not None and sample.first_answer_s is None


async def test_no_token_counts_never_falls_back_to_events():
    sample = await measurement(delta(content="several tokens") + ending(usage={}))
    assert sample.completion_tokens is None and sample.client_output_wall_tok_s is None
    assert sample.backend_decode_tok_s is None
    assert sample.error == "missing_token_counts"


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": True, "completion_tokens": 512},
        {"prompt_tokens": 100, "completion_tokens": -1},
        {"prompt_tokens": 100, "completion_tokens": 1.5},
        {"prompt_tokens": 100, "completion_tokens": 513},
        {
            "prompt_tokens": 100,
            "completion_tokens": 512,
            "prompt_tokens_details": {"cached_tokens": 101},
        },
        {
            "prompt_tokens": 100,
            "completion_tokens": 512,
            "completion_tokens_details": {"reasoning_tokens": 513},
        },
    ],
)
async def test_invalid_usage_is_ineligible(usage):
    sample = await measurement(delta(content="ok") + ending(usage))
    assert sample.error == "invalid_token_counts"
    assert sample.client_output_wall_tok_s is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (delta(content="partial"), "incomplete_stream"),
        (delta(content="partial") + "data: [DONE]\n\n", "missing_finish_reason"),
        ("data: {broken}\n\n", "malformed_sse"),
        ("data: []\n\n", "malformed_sse"),
        (event(error={"message": "private diagnostic"}), "sse_error"),
        (event(model="wrong-model", choices=[]), "model_mismatch"),
        (delta(content=42), "malformed_sse"),
        (event(choices=[{"index": 1, "delta": {"content": "wrong choice"}}]), "malformed_sse"),
    ],
)
async def test_protocol_failures_stay_visible(text, expected):
    sample = await measurement(text)
    assert sample.error == expected
    assert sample.backend_decode_tok_s is None


async def test_http_error_does_not_record_response_body():
    sample = await measurement("private diagnostic", status=503)
    assert sample.error == "http_503" and "private" not in sample.model_dump_json()


async def test_total_timeout_and_cancellation_close_stream():
    for cancel in (False, True):
        stream = BytesStream("", stall=True)
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:18080",
            transport=httpx.MockTransport(
                lambda _, current=stream: httpx.Response(200, stream=current)
            ),
        ) as client:
            task = asyncio.create_task(
                bench.measure(
                    client, {"max_tokens": 512}, response_model="local-qwen", deadline_s=0.02
                )
            )
            await stream.started.wait()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert (await task).error == "timeout"
        assert stream.closed


@pytest.mark.parametrize("backend", ["mlx", "splash"])
@pytest.mark.parametrize("reasoning", [False, True])
def test_settings_translation_and_fresh_prefixes(manifest_data, backend, reasoning):
    manifest_data["backend"] = backend
    manifest_data["settings"]["reasoning"] = reasoning
    if backend == "splash":
        manifest_data["draft"] = {
            "checkpoint": "example/draft",
            "revision": "fixed",
            "sha256": "f" * 64,
        }
    m = manifest(manifest_data)
    body = bench.request_body(m, m.cases[0], 1)
    assert body["temperature"] == 0 and body["seed"] == 0
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 512
    if backend == "mlx":
        assert body["enable_thinking"] is reasoning
        assert "reasoning_effort" not in body
    else:
        assert body["chat_template_kwargs"] == {"enable_thinking": reasoning}
        assert (body.get("reasoning_effort") == "none") is (not reasoning)
    assert body["messages"] != bench.request_body(m, m.cases[0], 2)["messages"]
    original = copy.deepcopy(m.cases[0].messages)
    bench.request_body(m, m.cases[0], 3)
    assert m.cases[0].messages == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url", "http://user:secret@127.0.0.1:8080"),
        ("base_url", "http://192.0.2.1:8080"),
        ("base_url", "http://127.0.0.1:8080?api_key=secret"),
        ("repeats", 0),
        ("warmups", 0),
        ("concurrency", 3),
        ("corpus_sha256", "0" * 64),
        ("deadline_s", float("inf")),
        ("api_key_env", "literal-key-with-dashes"),
        ("schema_version", 2),
    ],
)
def test_manifest_rejects_unsafe_or_unfrozen_inputs(manifest_data, field, value):
    manifest_data[field] = value
    with pytest.raises(ValueError):
        manifest(manifest_data)


def test_replay_does_not_change_prefix_and_disallows_parallel_sequences(manifest_data):
    manifest_data["cache_policy"] = "replay"
    for case in manifest_data["cases"]:
        case.update(kind="replay", primary=False)
    manifest_data["corpus_sha256"] = bench.digest(manifest_data["cases"])
    m = manifest(manifest_data)
    assert bench.request_body(m, m.cases[0], 1) == bench.request_body(m, m.cases[0], 2)
    manifest_data["concurrency"] = 2
    with pytest.raises(ValueError):
        manifest(manifest_data)


async def test_runner_checkpoints_errors_and_refuses_overwrite(
    tmp_path, manifest_data, monkeypatch
):
    monkeypatch.setenv("BENCH_TEST_KEY", "private-key")
    manifest_data["api_key_env"] = "BENCH_TEST_KEY"
    manifest_data["cache_policy"] = "isolated"
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "local-qwen"}]})
        return httpx.Response(503, text="private diagnostic")

    m = manifest(manifest_data)
    async with httpx.AsyncClient(
        base_url=m.base_url, transport=httpx.MockTransport(handler)
    ) as client:
        run = await bench.run_manifest(m, tmp_path, client=client)
        assert run.status == "complete" and len(run.samples) == 18
        assert all(s.measurement.error == "http_503" for s in run.samples)
        with pytest.raises(FileExistsError):
            await bench.run_manifest(m, tmp_path, client=client)
    salts = [req.headers["X-APC-Tenant"] for req in calls if req.method == "POST"]
    assert len(salts) == len(set(salts)) == 18
    saved = (tmp_path / "mlx-01.json").read_text()
    assert "private-key" not in saved and "private diagnostic" not in saved
    assert calls[0].headers["authorization"] == "Bearer private-key"
    assert (tmp_path / "mlx-01.json").stat().st_mode & 0o777 == 0o600
    assert bench.load_runs(tmp_path)[0].status == "complete"


async def test_runner_records_interruption_before_reraising(tmp_path, manifest_data):
    stream = BytesStream("", stall=True)

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "local-qwen"}]})
        return httpx.Response(200, stream=stream)

    m = manifest(manifest_data)
    async with httpx.AsyncClient(
        base_url=m.base_url, transport=httpx.MockTransport(handler)
    ) as client:
        task = asyncio.create_task(bench.run_manifest(m, tmp_path, client=client))
        await stream.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run = bench.load_runs(tmp_path)[0]
    assert run.status == "interrupted"
    assert run.samples[-1].measurement.error == "cancelled" and stream.closed


def runs_for(data, *, speed=1.3):
    runs = []
    for session in range(1, 4):
        for backend in ("mlx", "splash"):
            d = copy.deepcopy(data)
            d.update(
                id=f"{backend}-{session}",
                session=session,
                backend=backend,
                order="mlx-first" if session % 2 else "splash-first",
            )
            if backend == "splash":
                d["draft"] = {
                    "checkpoint": "example/draft",
                    "revision": "fixed",
                    "sha256": "f" * 64,
                }
            m = manifest(d)
            measurements = []
            for c in m.cases:
                for rep in range(0, 6):
                    rate = 50.0 * (speed if backend == "splash" else 1.0)
                    measurements.append(
                        bench.Sample(
                            case_id=c.id,
                            rep=rep,
                            warmup=rep == 0,
                            measurement=bench.Measurement(
                                total_s=512 / rate,
                                ttft_s=1.0,
                                first_answer_s=1.1,
                                prompt_tokens={"short": 100, "2k": 2048, "8k": 8192}[c.id],
                                completion_tokens=512,
                                cached_tokens=0,
                                finish_reason="length",
                                backend_decode_tok_s=rate,
                                client_output_wall_tok_s=rate,
                            ),
                        )
                    )
            runs.append(bench.Run(manifest=m, status="complete", samples=measurements))
    return runs


def test_comparison_pairs_prompts_and_keeps_adoption_pending(manifest_data):
    report = bench.compare_runs(runs_for(manifest_data))
    assert report["speed_gate"] == "pass" and report["decision"] == "inconclusive"
    assert report["median_paired_ratio"] == pytest.approx(1.3)
    assert report["ratio_ci95"] == pytest.approx([1.3, 1.3])
    assert report["ttft_gate"] == "pass" and report["e2e_gate"] == "pass"
    assert report["samples"]["measured"] == 90 and report["samples"]["warmup"] == 18
    assert "Quality" in bench.render_report(report)


@pytest.mark.parametrize(
    "path", ["weights_sha256", "tokenizer_sha256", "template_sha256", "quantization", "revision"]
)
def test_comparison_rejects_model_mismatches(manifest_data, path):
    runs = runs_for(manifest_data)
    setattr(runs[1].manifest.model, path, "f" * 64 if path.endswith("sha256") else "different")
    report = bench.compare_runs(runs)
    assert report["decision"] == "inconclusive" and report["speed_gate"] == "unassessed"
    assert "incompatible_manifests" in report["issues"]


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "partial",
        "nan",
        "no-usage",
        "error",
        "early-stop",
        "short-session",
        "order",
    ],
)
def test_incomplete_or_invalid_samples_cannot_pass(manifest_data, change):
    runs = runs_for(manifest_data)
    if change == "missing":
        runs[0].samples.pop()
    elif change == "duplicate":
        runs[0].samples[-1] = runs[0].samples[-2]
    elif change == "partial":
        runs[0].status = "interrupted"
    elif change == "nan":
        runs[0].samples[-1].measurement.backend_decode_tok_s = float("nan")
    elif change == "no-usage":
        runs[0].samples[-1].measurement.completion_tokens = None
    elif change == "error":
        runs[0].samples[-1].measurement.error = "timeout"
    elif change == "early-stop":
        runs[0].samples[-1].measurement.finish_reason = "stop"
        runs[0].samples[-1].measurement.completion_tokens = 4
    elif change == "short-session":
        runs = runs[:4]
    else:
        runs[2].manifest.order = "mlx-first"
        runs[3].manifest.order = "mlx-first"
    report = bench.compare_runs(runs)
    assert report["decision"] == "inconclusive" and report["issues"]
    assert report["speed_gate"] != "pass"


def test_real_slowdown_fails_and_category_regressions_are_visible(manifest_data):
    report = bench.compare_runs(runs_for(manifest_data, speed=0.8))
    assert report["speed_gate"] == "fail" and report["decision"] == "fail"
    assert all(c["paired_ratio"] == pytest.approx(0.8) for c in report["categories"].values())


def test_proxy_rates_never_claim_native_decode_gate(manifest_data):
    manifest_data["rate_metric"] = "client_output_wall_tok_s"
    report = bench.compare_runs(runs_for(manifest_data))
    assert report["speed_gate"] == "unassessed"
    assert report["median_paired_ratio"] == pytest.approx(1.3)
    assert report["decision"] == "inconclusive"


def test_truncated_result_and_conflicting_ids_are_rejected(tmp_path, manifest_data):
    (tmp_path / "partial.json").write_text('{"manifest":')
    with pytest.raises(ValueError):
        bench.load_runs(tmp_path)
    runs = runs_for(manifest_data)
    runs.append(runs[0])
    assert "duplicate_run" in bench.compare_runs(runs)["issues"]


def test_restoration_verifier_requires_same_intent_configs_and_ready_original():
    before = {
        "restore": {
            "intent": {"value": "mlx:qwen-4bit"},
            "selected": {"MLX_ACTIVE_NAME": "qwen-4bit"},
            "sidecar_intent": {"value": "stopped"},
            "embed_intent": {"value": "stopped"},
            "recovery": {"enabled": False},
            "config_hashes": {"registry": "abc"},
        },
        "complete": True,
        "experiment_port_open": False,
        "experiment_processes": [],
    }
    after = copy.deepcopy(before)
    after["serving_ready"] = True
    assert bench.verify_restoration(before, after) == []
    after["restore"]["intent"]["value"] = "mlx:qwen-8bit"
    assert "restore_state_changed" in bench.verify_restoration(before, after)
    after = copy.deepcopy(before)
    after.update(serving_ready=False, experiment_port_open=True, experiment_processes=[123])
    assert set(bench.verify_restoration(before, after)) == {
        "original_not_ready",
        "experiment_port_open",
        "experiment_processes_remain",
    }
    before["complete"] = False
    assert "incomplete_snapshot" in bench.verify_restoration(before, after)


def preflight_module():
    spec = importlib.util.spec_from_file_location(
        "splash_preflight", ROOT / "ops/splash-phase0/preflight.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preflight_hashes_assets_and_never_serializes_environment_secrets(tmp_path, monkeypatch):
    import sqlite3

    preflight = preflight_module()
    root = tmp_path / "Models"
    for name in ("run", "etc", "models/qwen"):
        (root / name).mkdir(parents=True)
    model = root / "models/qwen"
    (model / "config.json").write_text(
        '{"architectures":["QwenForCausalLM"],"quantization":{"bits":4}}'
    )
    (model / "tokenizer.json").write_text('{"test":true}')
    (model / "tokenizer_config.json").write_text('{"chat_template":"test template"}')
    (model / "model.safetensors").write_bytes(b"test weights")
    (root / "models/active").symlink_to(model)
    (root / "run/active.env").write_text(
        f"MLX_ACTIVE_NAME=qwen\nMLX_ACTIVE_PATH={model}\nSECRET=private-key\n"
    )
    (root / "etc/models.conf").write_text(f"qwen {model}\n")
    db = tmp_path / "admin.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE kv(key TEXT, value TEXT, updated REAL)")
        for key, value in (
            ("intent", {"value": "mlx:qwen", "ts": 1}),
            ("recovery_suspended", {"reason": "manual"}),
            ("sidecar_intent", {"value": "stopped"}),
            ("embed_intent", {"value": "stopped"}),
        ):
            conn.execute("INSERT INTO kv VALUES(?,?,1)", (key, json.dumps(value)))
        conn.execute("CREATE TABLE jobs(id TEXT,status TEXT)")
    before = db.read_bytes()
    monkeypatch.setattr(
        preflight, "command", lambda *args: {"ok": True, "stdout": "test", "returncode": 0}
    )
    monkeypatch.setattr(
        preflight,
        "probe",
        lambda *args: {"ok": True, "status": 200, "body": {"data": [{"id": "qwen"}]}},
    )
    result = preflight.collect(root, db, hash_weights=True)
    assert result["assets"]["weights_sha256"] and result["assets"]["tokenizer_sha256"]
    assert result["assets"]["template_sha256"]
    assert "private-key" not in json.dumps(result)
    assert result["restore"]["intent"]["value"] == "mlx:qwen"
    assert db.read_bytes() == before
    (model / "model.safetensors").write_bytes(b"changed weights")
    assert (
        preflight.collect(root, db, hash_weights=True)["assets"]["weights_sha256"]
        != result["assets"]["weights_sha256"]
    )


def test_preflight_missing_database_and_unhashed_weights_are_explicit(tmp_path, monkeypatch):
    preflight = preflight_module()
    monkeypatch.setattr(
        preflight, "command", lambda *args: {"ok": False, "stdout": "", "returncode": 1}
    )
    monkeypatch.setattr(
        preflight, "probe", lambda *args: {"ok": False, "status": None, "body": None}
    )
    result = preflight.collect(tmp_path, tmp_path / "absent.sqlite", hash_weights=False)
    assert result["complete"] is False and result["issues"]
    assert result["assets"]["weights_sha256"] is None
    assert not (tmp_path / "absent.sqlite").exists()


def test_preflight_shell_uses_environment_target_and_private_artifact(tmp_path):
    import os
    import subprocess

    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$@" > "$SSH_ARGS"\ncat >/dev/null\nprintf \'{"complete":false}\\n\'\n'
    )
    ssh.chmod(0o755)
    deploy = tmp_path / "deploy.env"
    deploy.write_text("MAC_HOST=file-target\nMAC_DEST=/Users/you/admin\n")
    output = tmp_path / "private/preflight.json"
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "DEPLOY_ENV": str(deploy),
        "MAC_HOST": "env-target",
        "MAC_DEST": "/Users/you/admin",
        "SSH_ARGS": str(tmp_path / "args"),
    }
    result = subprocess.run(
        ["bash", str(ROOT / "ops/splash-phase0/preflight.sh"), str(output)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    args = (tmp_path / "args").read_text()
    assert "env-target" in args and "file-target" not in args
    assert "BatchMode=yes" in args and "python3" in args
    assert output.stat().st_mode & 0o777 == 0o600
    assert output.parent.stat().st_mode & 0o777 == 0o700
    assert "env-target" not in result.stdout
    result = subprocess.run(
        ["bash", str(ROOT / "ops/splash-phase0/preflight.sh"), str(output)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


@pytest.mark.parametrize("value", [False, 0, [], {}])
async def test_even_falsy_nontext_deltas_are_protocol_failures(value):
    assert (await measurement(delta(content=value) + ending())).error == "malformed_sse"


async def test_transport_error_is_a_sample_without_private_error_text():
    def handler(_):
        raise httpx.ConnectError("private diagnostic")

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18080", transport=httpx.MockTransport(handler)
    ) as client:
        result = await bench.measure(
            client, {"max_tokens": 512}, response_model="local-qwen", deadline_s=1
        )
    assert result.error == "transport" and "private" not in result.model_dump_json()


def test_report_uses_median_of_paired_ratios_not_ratio_of_medians(manifest_data):
    runs = runs_for(manifest_data)
    for run in runs:
        rates = [1, 1, 100, 100, 100] if run.manifest.backend == "mlx" else [2, 2, 2, 200, 200]
        for sample in run.samples:
            if not sample.warmup:
                sample.measurement.backend_decode_tok_s = float(rates[sample.rep - 1])
    assert bench.compare_runs(runs)["median_paired_ratio"] == pytest.approx(2.0)


@pytest.mark.parametrize(
    "change", ["ttft", "e2e", "category", "prompt-size", "prompt-count-mismatch"]
)
def test_report_detects_regressions_and_unmatched_workloads(manifest_data, change):
    runs = runs_for(manifest_data)
    for run in runs:
        if run.manifest.backend != "splash":
            continue
        for sample in run.samples:
            if sample.warmup:
                continue
            if change == "ttft":
                sample.measurement.ttft_s = 1.2
                sample.measurement.first_answer_s = 1.3
            elif change == "e2e" and sample.case_id == "short":
                sample.measurement.total_s = 20.0
            elif change == "category" and sample.case_id == "short":
                sample.measurement.backend_decode_tok_s = 40.0
            elif change == "prompt-size":
                sample.measurement.prompt_tokens = 10
            elif change == "prompt-count-mismatch":
                sample.measurement.prompt_tokens += 1
    report = bench.compare_runs(runs)
    assert report["decision"] in {"fail", "inconclusive"}
    if change in {"ttft", "e2e", "category"}:
        assert (
            report[{"ttft": "ttft_gate", "e2e": "e2e_gate", "category": "speed_gate"}[change]]
            == "fail"
        )
    else:
        assert report["issues"] and report["speed_gate"] != "pass"


async def test_missing_credentials_and_wrong_model_fail_before_inference(
    tmp_path, manifest_data, monkeypatch
):
    manifest_data["api_key_env"] = "ABSENT_BENCH_KEY"
    monkeypatch.delenv("ABSENT_BENCH_KEY", raising=False)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"data": [{"id": "wrong-model"}]})

    async with httpx.AsyncClient(
        base_url=manifest_data["base_url"], transport=httpx.MockTransport(handler)
    ) as client:
        result = await bench.run_manifest(manifest(manifest_data), tmp_path, client=client)
        assert result.error == "missing_api_key_environment" and not calls
        manifest_data.update(id="wrong-model", api_key_env=None)
        result = await bench.run_manifest(manifest(manifest_data), tmp_path, client=client)
        assert result.error == "requested_model_not_served" and len(calls) == 1


def test_checked_in_schema_and_example_match_the_executable_contract():
    schema = json.loads((ROOT / "ops/splash-phase0/manifest.schema.json").read_text())
    assert schema == bench.Manifest.model_json_schema()
    example = bench.Manifest.model_validate_json(
        (ROOT / "ops/splash-phase0/manifest.example.json").read_text()
    )
    assert example.model.weights_sha256 == "0" * 64  # deliberately unfinished, never evidence


def test_sandbox_redirects_writable_paths_and_drops_ambient_environment(tmp_path):
    import os
    import subprocess

    root = tmp_path / "spike with spaces"
    root.mkdir()
    script = ROOT / "ops/splash-phase0/sandbox.sh"
    result = subprocess.run(
        [
            "bash",
            str(script),
            str(root),
            sys.executable,
            "-c",
            "import json,os; print(json.dumps(dict(os.environ)))",
        ],
        env={
            **os.environ,
            "UNRELATED_SECRET": "private-secret",
            "SPLASH_API_KEY": "test-only-key",
            "PYTHONPATH": "/outside",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert "UNRELATED_SECRET" not in env and "private-secret" not in result.stdout
    assert env["SPLASH_API_KEY"] == "test-only-key"
    for name in (
        "HOME",
        "TMPDIR",
        "HF_HOME",
        "HF_HUB_CACHE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "UV_CACHE_DIR",
        "PYTHONPATH",
    ):
        assert Path(env[name]).is_relative_to(root)
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["SPLASH_SPIKE_ROOT"] == str(root)


def test_native_decode_comparison_requires_a_timing_definition_audit(manifest_data):
    manifest_data["evidence"]["native_timing_audit_sha256"] = None
    with pytest.raises(ValueError):
        manifest(manifest_data)


def test_explicit_preflight_target_never_falls_back_to_selected_model(tmp_path, monkeypatch):
    module = preflight_module()
    monkeypatch.setattr(
        module, "command", lambda *args: {"ok": False, "stdout": "", "returncode": 1}
    )
    monkeypatch.setattr(module, "probe", lambda *args: {"ok": False, "status": None, "body": None})
    for part in ("models/selected", "run", "etc"):
        (tmp_path / part).mkdir(parents=True)
    (tmp_path / "models/active").symlink_to(tmp_path / "models/selected")
    (tmp_path / "etc/models.conf").write_text(f"selected {tmp_path}/models/selected\n")
    result = module.collect(
        tmp_path, tmp_path / "absent.sqlite", hash_weights=True, model_name="missing"
    )
    assert "benchmark_target_not_registered" in result["issues"]
    assert result["assets"]["path"] is None and result["assets"]["weights_sha256"] is None


def test_restoration_rejects_empty_or_incomplete_state_even_when_both_sides_agree():
    snapshot = {
        "complete": True,
        "restore": {"intent": {}},
        "serving_ready": True,
        "experiment_port_open": False,
        "experiment_processes": [],
    }
    assert "incomplete_snapshot" in bench.verify_restoration(snapshot, snapshot)


def test_preflight_cli_passes_the_explicit_registered_target(monkeypatch, capsys):
    module = preflight_module()
    seen = {}

    def collect(root, database, **kwargs):
        seen.update(kwargs)
        return {"complete": False}

    monkeypatch.setattr(module, "collect", collect)
    monkeypatch.setattr(
        sys, "argv", ["preflight.py", "/private/admin.sqlite", "--hash-weights", "qwen-27b"]
    )
    assert module.main() == 0
    assert seen == {"hash_weights": True, "model_name": "qwen-27b"}
    assert json.loads(capsys.readouterr().out) == {"complete": False}


@pytest.mark.parametrize(
    "command", ["schema", "compare", "invalid-compare", "invalid-run", "restore", "not-restored"]
)
def test_cli_writes_reports_and_returns_nonzero_for_invalid_evidence(
    tmp_path, manifest_data, monkeypatch, capsys, command
):
    if command == "schema":
        output = tmp_path / "schema.json"
        args = ["schema", "--out", str(output)]
        expected = 0
    elif command in {"compare", "invalid-compare"}:
        inputs = tmp_path / "results"
        inputs.mkdir()
        for run in runs_for(manifest_data):
            (inputs / f"{run.manifest.id}.json").write_text(run.model_dump_json())
        if command == "invalid-compare":
            (inputs / "broken.json").write_text('{"private-input":')
        output = tmp_path / "report.md"
        args = ["compare", "--results", str(inputs), "--out", str(output)]
        expected = 2 if command == "invalid-compare" else 0
    elif command == "invalid-run":
        input_path = tmp_path / "manifest.json"
        input_path.write_text('{"base_url":"private-input"}')
        args = ["run", "--manifest", str(input_path), "--out", str(tmp_path / "out")]
        expected = 2
    else:
        snapshot = {
            "complete": True,
            "restore": {
                "intent": {"value": "mlx:qwen"},
                "sidecar_intent": None,
                "embed_intent": None,
                "recovery": None,
                "selected": {},
                "config_hashes": {"registry": "test"},
            },
            "serving_ready": True,
            "experiment_port_open": command == "not-restored",
            "experiment_processes": [],
        }
        (tmp_path / "before.json").write_text(json.dumps(snapshot))
        (tmp_path / "after.json").write_text(json.dumps(snapshot))
        args = [
            "verify-restoration",
            "--before",
            str(tmp_path / "before.json"),
            "--after",
            str(tmp_path / "after.json"),
        ]
        expected = 1 if command == "not-restored" else 0
    monkeypatch.setattr(sys, "argv", ["bench.py", *args])
    assert bench.main() == expected
    assert "private-input" not in capsys.readouterr().out
    if command in {"schema", "compare"}:
        assert output.is_file()


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": 100},
        {"prompt_tokens": 0, "completion_tokens": 512},
        {"prompt_tokens": 100, "completion_tokens": 0},
        {"prompt_tokens": 100, "completion_tokens": 512, "prompt_tokens_details": []},
        {"prompt_tokens": 100, "completion_tokens": 512, "completion_tokens_details": []},
        {
            "prompt_tokens": 100,
            "completion_tokens": 512,
            "prompt_tokens_details": {"cached_tokens": False},
        },
    ],
)
async def test_accounting_rejects_missing_zero_and_malformed_details(usage):
    result = await measurement(delta(content="answer") + ending(usage))
    assert result.error in {"missing_token_counts", "invalid_token_counts"}
    assert result.client_output_wall_tok_s is None and result.backend_decode_tok_s is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('event: error\ndata: {"message":"private diagnostic"}\n\n', "sse_error"),
        (event(choices=[{"index": 0, "delta": {}, "finish_reason": []}]), "invalid_finish_reason"),
        (
            delta(content="answer")
            + ending().replace(
                '"predicted_per_second": 50.0', '"predicted_per_second": 50.0, "predicted_n": 5'
            ),
            "inconsistent_timing_counts",
        ),
    ],
)
async def test_named_error_events_and_conflicting_server_counts_are_failures(text, expected):
    result = await measurement(text)
    assert result.error == expected
    assert result.backend_decode_tok_s is None


def test_comparison_rejects_impossible_timestamps(manifest_data):
    runs = runs_for(manifest_data)
    runs[0].samples[-1].measurement.ttft_s = 1000.0
    report = bench.compare_runs(runs)
    assert report["issues"] and report["speed_gate"] != "pass"


@pytest.mark.parametrize(
    ("semantics", "native_prompt", "cached", "expected"),
    [
        ("uncached", 80, 20, None),
        ("total", 100, 20, None),
        ("total", 80, 20, "inconsistent_timing_counts"),
        ("uncached", 100, 20, "inconsistent_timing_counts"),
        ("uncached", 80, None, "missing_cached_count_for_timings"),
    ],
)
async def test_runtime_specific_prompt_timing_counts(semantics, native_prompt, cached, expected):
    usage = {"prompt_tokens": 100, "completion_tokens": 512}
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    text = (
        delta(content="answer")
        + event(
            choices=[{"index": 0, "delta": {}, "finish_reason": "length"}],
            usage=usage,
            timings={
                "prompt_n": native_prompt,
                "cache_n": cached,
                "predicted_n": 512,
                "predicted_per_second": 50.0,
            },
        )
        + "data: [DONE]\n\n"
    )
    stream = BytesStream(text)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:18080",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
    ) as client:
        result = await bench.measure(
            client,
            {"max_tokens": 512},
            response_model="local-qwen",
            deadline_s=1,
            prompt_timing_counts=semantics,
        )
    assert result.error == expected
    if expected is None:
        assert result.prompt_tokens == 100 and result.cached_tokens == 20
        assert result.backend_decode_tok_s == 50.0


@pytest.mark.parametrize("backend", ["mlx", "splash"])
def test_fresh_cache_isolation_uses_the_audited_runtime_salt(manifest_data, backend):
    manifest_data.update(backend=backend, cache_policy="isolated")
    if backend == "splash":
        manifest_data["draft"] = {
            "checkpoint": "example/draft",
            "revision": "fixed",
            "sha256": "f" * 64,
        }
    m = manifest(manifest_data)
    case = m.cases[0]
    body = bench.request_body(m, case, 1)
    headers = bench.request_headers(m, case, 1)
    expected = bench.digest([m.corpus_sha256, m.session, case.id, 1])[:24]
    if backend == "mlx":
        assert headers == {"X-APC-Tenant": expected}
        assert "cache_salt" not in body
    else:
        assert body["cache_salt"] == expected and headers == {}
    assert (body, headers) != (bench.request_body(m, case, 2), bench.request_headers(m, case, 2))
    assert (body, headers) == (bench.request_body(m, case, 1), bench.request_headers(m, case, 1))


def test_replayed_history_keeps_its_cache_namespace(manifest_data):
    manifest_data["cache_policy"] = "replay"
    for case in manifest_data["cases"]:
        case.update(kind="replay", primary=False)
    manifest_data["corpus_sha256"] = bench.digest(manifest_data["cases"])
    m = manifest(manifest_data)
    assert bench.request_headers(m, m.cases[0], 1) == {}
    assert "cache_salt" not in bench.request_body(m, m.cases[0], 1)


def test_sandbox_omits_unset_optional_api_key(tmp_path, monkeypatch):
    import os
    import subprocess

    monkeypatch.delenv("SPLASH_API_KEY", raising=False)
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "ops/splash-phase0/sandbox.sh"),
            str(tmp_path),
            sys.executable,
            "-c",
            'import os; print("SPLASH_API_KEY" in os.environ)',
        ],
        env=dict(os.environ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


@pytest.mark.parametrize("prefix", ["", "modified-4bit-"])
@pytest.mark.parametrize("backend", ["mlx", "splash"])
async def test_captured_native_streams_use_final_usage_and_timings(backend, prefix):
    text = (ROOT / "tests/fixtures/splash-phase0" / f"{prefix}{backend}-chat.sse").read_text()
    sample = await measurement(text)
    events = [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    timings = [event["timings"] for event in events if "timings" in event][-1]
    assert sample.error is None
    assert sample.prompt_tokens == 108
    assert sample.completion_tokens == 96
    assert sample.cached_tokens == 0
    assert sample.finish_reason == "length"
    assert sample.backend_decode_tok_s == timings["predicted_per_second"]
    assert sample.backend_decode_tok_s > 0
    assert sample.ttft_s is not None and sample.first_answer_s is not None
    assert sample.client_output_wall_tok_s > 0
