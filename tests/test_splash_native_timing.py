"""Synthetic callback traces check a proposed contract, never qualify native performance."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments/splash"))
try:
    SPEC = importlib.util.spec_from_file_location(
        "splash_native", ROOT / "experiments/splash/native_timing.py"
    )
    assert SPEC and SPEC.loader
    native = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = native
    SPEC.loader.exec_module(native)
finally:
    sys.path.pop(0)


def trace_data():
    return {
        "contract": "splash-native-emission-1",
        "backend": "splash",
        "runtime_sha256": "a" * 64,
        "adapter_sha256": "b" * 64,
        "request_sha256": "c" * 64,
        "source": "engine_callback_entry",
        "clock": "monotonic_ns",
        "completion_tokens": 6,
        "completion_ids_sha256": native.bench.digest([1, 2, 3, 4, 5, 6]),
        "finish_reason": "length",
        "batches": [
            {"at_ns": 1_000_000_000, "token_ids": [1, 2, 3]},
            {"at_ns": 2_000_000_000, "token_ids": [4, 5]},
            {"at_ns": 3_000_000_000, "token_ids": [6]},
        ],
    }


def test_excludes_whole_first_speculative_batch_and_last_callback_is_endpoint():
    data = trace_data()
    before = copy.deepcopy(data)
    result = native.inspect_trace(native.NativeTrace.model_validate(data))
    assert result["post_first_batch_tokens"] == 3
    assert result["elapsed_s"] == 2.0
    assert result["emission_tok_s"] == 1.5
    assert result["status"] == "structurally_valid_only"
    assert result["native_gate"] == "unassessed"
    assert data == before


@pytest.mark.parametrize(
    "bad",
    [
        "count",
        "ids",
        "boolean_token",
        "negative_token",
        "float_time",
        "backwards",
        "zero_interval",
        "one_batch",
        "empty_batch",
        "sse",
        "clock",
        "failed",
        "unknown",
    ],
)
def test_rejects_unaccounted_tokens_or_unproven_intervals(bad):
    d = trace_data()
    if bad == "count":
        d["completion_tokens"] = 7
    elif bad == "ids":
        d["completion_ids_sha256"] = "f" * 64
    elif bad == "boolean_token":
        d["batches"][0]["token_ids"][0] = True
    elif bad == "negative_token":
        d["batches"][0]["token_ids"][0] = -1
    elif bad == "float_time":
        d["batches"][0]["at_ns"] = 1.5
    elif bad == "backwards":
        d["batches"][1]["at_ns"] = 0
    elif bad == "zero_interval":
        for b in d["batches"]:
            b["at_ns"] = 10
    elif bad == "one_batch":
        d["batches"] = [{"at_ns": 0, "token_ids": [1, 2, 3, 4, 5, 6]}]
    elif bad == "empty_batch":
        d["batches"][0]["token_ids"] = []
    elif bad == "sse":
        d["source"] = "sse_chunk"
    elif bad == "clock":
        d["clock"] = "wall_clock"
    elif bad == "failed":
        d["finish_reason"] = "cancelled"
    elif bad == "unknown":
        d["predicted_ms"] = 100
    with pytest.raises(ValueError):
        native.NativeTrace.model_validate(d)


def test_distinguishes_token_ids_from_number_of_callbacks():
    d = trace_data()
    d["backend"] = "mlx"
    d["batches"] = [{"at_ns": i * 1_000_000_000, "token_ids": [i + 1]} for i in range(6)]
    result = native.inspect_trace(native.NativeTrace.model_validate(d))
    assert result["post_first_batch_tokens"] == 5 and result["emission_tok_s"] == 1.0


def test_cli_is_private_exclusive_and_sanitizes_failures(tmp_path, capsys):
    src, out = tmp_path / "trace.json", tmp_path / "result.json"
    src.write_text(json.dumps(trace_data()))
    assert native.main(["--trace", str(src), "--out", str(out)]) == 0
    assert out.stat().st_mode & 0o777 == 0o600
    saved = out.read_bytes()
    assert native.main(["--trace", str(src), "--out", str(out)]) == 2
    assert out.read_bytes() == saved
    src.write_text('{"secret":"REDACTED_TEST_PAYLOAD"}')
    assert native.main(["--trace", str(src), "--out", str(tmp_path / "other")]) == 2
    assert "REDACTED_TEST_PAYLOAD" not in capsys.readouterr().out
