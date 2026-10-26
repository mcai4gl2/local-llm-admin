"""Native collector tests use explicit engine outputs; no Mac or model is loaded."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops/splash-phase0"))
try:
    import native_capture as capture
    import native_prepare
    import native_timing
finally:
    sys.path.pop(0)


def collector(tmp_path):
    return capture.Collector(tmp_path / "traces", "a" * 64, "b" * 64)


def test_mlx_reconciles_callback_ids_with_independent_queue_items(tmp_path):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "mlx", {"prompt": "synthetic", "max_tokens": 3}, 3)
    for i, token in enumerate([7, 8, 9]):
        c.batch(key, [token], at_ns=100 + i * 10)
        c.observe_mlx(
            key,
            SimpleNamespace(token=token, token_count=1, finish_reason="length" if i == 2 else None),
        )
    paths = list(c.directory.glob("*.trace.json"))
    assert len(paths) == 1
    trace = native_timing.NativeTrace.model_validate_json(paths[0].read_text())
    assert trace.completion_tokens == 3
    assert native_timing.inspect_trace(trace)["emission_tok_s"] == 100_000_000
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert c.directory.stat().st_mode & 0o777 == 0o700
    assert not c.active


def test_splash_preserves_multi_token_batches_and_final_streamer_ids(tmp_path):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "splash", {"prompt_tokens": [1, 2], "max_new_tokens": 6}, 6)
    c.batch(key, [10, 11, 12], at_ns=100)
    c.batch(key, [13, 14], at_ns=200)
    c.batch(key, [15], at_ns=300)
    c.finish(key, [10, 11, 12, 13, 14, 15], 6, "length")
    trace = native_timing.NativeTrace.model_validate_json(
        next(c.directory.glob("*.trace.json")).read_text()
    )
    assert [b.token_ids for b in trace.batches] == [[10, 11, 12], [13, 14], [15]]
    assert trace.request_sha256 == capture.digest({"prompt_tokens": [1, 2], "max_new_tokens": 6})


@pytest.mark.parametrize("bad", ["ids", "count", "cancel", "backwards", "one_batch", "overflow"])
def test_failed_accounting_never_produces_a_successful_trace(tmp_path, bad):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "splash", {"prompt_tokens": [1]}, 3)
    if bad == "overflow":
        with pytest.raises(capture.CaptureError):
            c.batch(key, [1, 2, 3, 4], at_ns=100)
    elif bad == "backwards":
        c.batch(key, [1], at_ns=100)
        with pytest.raises(capture.CaptureError):
            c.batch(key, [2], at_ns=90)
    else:
        c.batch(key, [1], at_ns=100)
        if bad != "one_batch":
            c.batch(key, [2], at_ns=90 if bad == "backwards" else 200)
        with pytest.raises(capture.CaptureError):
            c.finish(
                key,
                [1, 9] if bad == "ids" else [1, 2],
                3 if bad == "count" else 2,
                "cancelled" if bad == "cancel" else "length",
            )
    assert not list(c.directory.glob("*.trace.json"))
    failed = list(c.directory.glob("*.failed.json"))
    assert len(failed) == 1
    assert json.loads(failed[0].read_text())["status"] == "failed"
    assert not c.active


@pytest.mark.parametrize("token,count", [(True, 1), (2, True), (2, None), (2, 2), (-1, 1)])
def test_mlx_rejects_missing_implicit_or_ambiguous_token_accounting(tmp_path, token, count):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "mlx", {"prompt": "synthetic"}, 4)
    c.batch(key, [2], at_ns=100)
    with pytest.raises(capture.CaptureError):
        c.observe_mlx(key, SimpleNamespace(token=token, token_count=count, finish_reason="stop"))
    assert not list(c.directory.glob("*.trace.json"))


def test_mlx_zero_count_terminal_placeholder_is_not_a_token(tmp_path):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "mlx", {"prompt": "synthetic"}, 4)
    for i in [1, 2]:
        c.batch(key, [i], at_ns=i * 100)
        c.observe_mlx(key, SimpleNamespace(token=i, token_count=1, finish_reason=None))
    c.observe_mlx(key, SimpleNamespace(token=0, token_count=0, finish_reason="stop"))
    trace = native_timing.NativeTrace.model_validate_json(
        next(c.directory.glob("*.trace.json")).read_text()
    )
    assert trace.completion_tokens == 2


def test_cancellation_is_visible_and_removes_only_its_own_request(tmp_path):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "mlx", {"prompt": "synthetic"}, 3)
    c.abort(object(), "untracked")
    c.abort(key, "cancelled")
    assert not c.active and len(list(c.directory.glob("*.failed.json"))) == 1


def test_refuses_concurrent_requests_and_unprivate_or_reused_output(tmp_path):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "mlx", {"prompt": "synthetic"}, 3)
    with pytest.raises(capture.CaptureError):
        c.begin(object(), "mlx", {"prompt": "other"}, 3)
    c.abort(key, "cancelled")
    with pytest.raises((OSError, ValueError)):
        capture.Collector(c.directory, "a" * 64, "b" * 64)
    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o755)
    with pytest.raises((OSError, ValueError)):
        capture.Collector(insecure, "a" * 64, "b" * 64)


def test_private_failures_do_not_copy_request_or_token_payloads(tmp_path):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "mlx", {"prompt": "REDACTED_TEST_PAYLOAD"}, 3)
    c.abort(key, "cancelled")
    assert "REDACTED_TEST_PAYLOAD" not in next(c.directory.glob("*.failed.json")).read_text()


def test_prepare_refuses_source_drift_and_never_modifies_input(tmp_path):
    src = tmp_path / "generation.py"
    src.write_text("raise RuntimeError('synthetic')\n")
    before = src.read_bytes()
    with pytest.raises(ValueError, match="source"):
        native_prepare.prepare("mlx", src, tmp_path / "bundle")
    assert src.read_bytes() == before
    assert not (tmp_path / "bundle").exists()


def test_patch_helpers_require_unique_verified_anchors():
    with pytest.raises(ValueError):
        native_prepare.replace_once("a a", "a", "b")
    with pytest.raises(ValueError):
        native_prepare.replace_once("c", "a", "b")


def test_prepared_collector_is_self_contained_and_pins_all_bytes(tmp_path, monkeypatch):
    # Exercise the public bundler with a synthetic source and explicit test-only pin.
    src = tmp_path / "backend.py"
    src.write_text(SPLASH_FIXTURE)
    monkeypatch.setitem(native_prepare.PINS, "splash", hashlib.sha256(src.read_bytes()).hexdigest())
    out = tmp_path / "bundle"
    receipt = native_prepare.prepare("splash", src, out)
    assert receipt["status"] == "prepared_only"
    assert receipt["native_gate"] == "unassessed"
    assert receipt["source_sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    for name, expected in receipt["files"].items():
        path = out / name
        assert path.stat().st_mode & 0o777 == 0o600
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        if path.suffix == ".py":
            compile(path.read_text(), name, "exec")
    with pytest.raises(FileExistsError):
        native_prepare.prepare("splash", src, out)


def test_injected_mlx_iterator_observes_before_terminal_and_aborts_on_close():
    source = native_prepare.instrument("mlx", MLX_FIXTURE)
    tree = ast.parse(source)
    iterator = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_TokenIterator"
    )
    code = compile(ast.Module(body=[iterator], type_ignores=[]), "<synthetic>", "exec")
    calls = []
    hooks = SimpleNamespace(
        observe_mlx=lambda key, item: calls.append(("observe", item.token)),
        abort=lambda key: calls.append(("abort", None)),
    )
    namespace = {"_phase0_native": hooks, "QueueEmpty": RuntimeError}
    exec(code, namespace)
    obj = namespace["_TokenIterator"]()
    obj._rqueue = SimpleNamespace(
        get=lambda **kw: SimpleNamespace(token=42, token_count=1, finish_reason="stop")
    )
    obj._ended = False
    obj._closed = False
    obj._queue_timeout = 1
    obj._lock = __import__("threading").Lock()
    obj._uid = 1
    obj._cancel_fn = lambda uid: calls.append(("cancel", uid))
    assert next(obj).token == 42
    assert calls == [("observe", 42)] and obj._ended
    with pytest.raises(StopIteration):
        next(obj)
    obj._ended = False
    obj.close()
    assert calls[-2:] == [("abort", None), ("cancel", 1)]


def test_injected_splash_timestamps_before_dispatch_and_finalizes_independently():
    source = native_prepare.instrument("splash", SPLASH_FIXTURE)
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NativeBackend")
    calls = []
    hooks = SimpleNamespace(
        splash_batch=lambda key, tokens, stamp: calls.append(("batch", list(tokens), stamp)),
        finish_splash=lambda state, result, error: calls.append(
            ("finish", list(state.streamer.token_ids))
        ),
    )

    class TokensEvent:
        tokens = [1, 2]
        sequence_offset = 0

    namespace = {
        "_phase0_native": hooks,
        "time": SimpleNamespace(monotonic_ns=lambda: 123),
        "wire": SimpleNamespace(TokensEvent=TokensEvent),
    }
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "<synthetic>", "exec"), namespace)
    backend = namespace["NativeBackend"]()
    backend._record = lambda *args, **kwargs: calls.append(("record",))
    state = SimpleNamespace(
        job=object(),
        streamer=SimpleNamespace(
            put_tokens=lambda tokens: calls.append(("decode", list(tokens))), token_ids=[1, 2]
        ),
    )
    backend._on_event(state, TokensEvent())
    backend._finalize(state, None)
    assert calls == [("batch", [1, 2], 123), ("decode", [1, 2]), ("finish", [1, 2]), ("record",)]


MLX_FIXTURE = '''
class _TokenIterator:
    def __next__(self):
        if self._ended:
            raise StopIteration
        try:
            item = self._rqueue.get(timeout=self._queue_timeout)
        except QueueEmpty as exc:
            self.close()
            raise exc
        if item is None:
            self._ended = True
            raise StopIteration
        if isinstance(item, Exception):
            self._ended = True
            raise item
        if getattr(item, "finish_reason", None):
            self._ended = True
        return item

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if not self._ended:
            self._cancel_fn(self._uid)

class ResponseGenerator:
    def generate(self):
        self.requests.put(queued_request)

    def _stream_text(self, info: dict, token: int, finish_reason: Optional[str]) -> str:
        """Convert one generated token into a streaming text segment."""
        return info["streamer"].advance(token, finish_reason)
'''

SPLASH_FIXTURE = """
class NativeBackend:
    def submit(self, job):
        request = self._generation_request(job)

    def _on_event(self, state, event):
        job = state.job
        if False:
            pass
        elif isinstance(event, wire.TokensEvent):
            state.streamer.put_tokens(event.tokens)

    def _finalize(self, state, call):
        job = state.job
        result, error = object(), None
        self._record(job, result=result, error=error)
"""


@pytest.mark.parametrize("tokens", [[], [True], [-1], [1.5], "ids"])
def test_invalid_callback_payloads_fail_closed(tmp_path, tokens):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "splash", {}, 3)
    with pytest.raises(capture.CaptureError):
        c.batch(key, tokens, at_ns=10)
    assert len(list(c.directory.glob("*.failed.json"))) == 1


@pytest.mark.parametrize("stamp", [True, -1, 2**63, 1.5])
def test_invalid_callback_clocks_fail_closed(tmp_path, stamp):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "mlx", {}, 3)
    with pytest.raises(capture.CaptureError):
        c.batch(key, [1], at_ns=stamp)


def test_unknown_requests_cannot_create_traces(tmp_path):
    c = collector(tmp_path)
    with pytest.raises(capture.CaptureError):
        c.batch(object(), [1], at_ns=1)
    with pytest.raises(capture.CaptureError):
        c.observe_mlx(object(), SimpleNamespace(token=1))
    with pytest.raises(capture.CaptureError):
        c.finish(object(), [1, 2], 2, "stop")
    assert not list(c.directory.iterdir())


def test_configuration_requires_new_absolute_private_output_and_single_install(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(capture, "_collector", None)
    monkeypatch.delenv("PHASE0_NATIVE_OUT", raising=False)
    with pytest.raises(capture.CaptureError):
        capture.current()
    with pytest.raises(capture.CaptureError):
        capture.configure("mlx", "a" * 64, "b" * 64)
    monkeypatch.setenv("PHASE0_NATIVE_OUT", "relative")
    with pytest.raises(capture.CaptureError):
        capture.configure("mlx", "a" * 64, "b" * 64)
    monkeypatch.setenv("PHASE0_NATIVE_OUT", str(tmp_path / "nested" / "traces"))
    capture.configure("mlx", "a" * 64, "b" * 64)
    assert capture.current().directory.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(capture.CaptureError):
        capture.configure("mlx", "a" * 64, "b" * 64)
    with pytest.raises(ValueError):
        capture.Collector(tmp_path / "bad", "bad", "b" * 64)


def test_mlx_runtime_wrappers_capture_explicit_queue_sequence(tmp_path, monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class Args:
        max_tokens: int = 2
        logits_processors: object = None

        def diffusion_kwargs(self):
            return {}

    c = collector(tmp_path)
    monkeypatch.setattr(capture, "_collector", c)
    clock = iter([10, 20])
    monkeypatch.setattr(capture.time, "monotonic_ns", lambda: next(clock))
    key = object()
    capture.begin_mlx(key, "synthetic", Args(), None, None, None, None, False)
    for token in [1, 2]:
        capture.mlx_batch(key, token)
        capture.observe_mlx(
            key,
            SimpleNamespace(
                token=token, token_count=1, finish_reason="length" if token == 2 else None
            ),
        )
    assert len(list(c.directory.glob("*.trace.json"))) == 1
    capture.abort(object())


@pytest.mark.parametrize("bad", ["image", "audio", "video", "draft", "grammar", "diffusion"])
def test_mlx_rejects_uninstrumented_generation_paths(tmp_path, monkeypatch, bad):
    c = collector(tmp_path)
    monkeypatch.setattr(capture, "_collector", c)
    args = SimpleNamespace(
        logits_processors=[] if bad == "grammar" else None,
        diffusion_kwargs=lambda: {},
        max_tokens=3,
    )
    with pytest.raises(capture.CaptureError):
        capture.begin_mlx(
            object(),
            "synthetic",
            args,
            [1] if bad == "image" else None,
            [1] if bad == "audio" else None,
            [1] if bad == "video" else None,
            object() if bad == "draft" else None,
            bad == "diffusion",
        )
    assert not c.active


def splash_state():
    from dataclasses import dataclass
    from threading import Event

    @dataclass
    class Sampling:
        temperature: float = 0.0

    job = SimpleNamespace(
        prompt_tokens=[30, 31],
        max_new_tokens=3,
        seed=0,
        sampling=Sampling(),
        thinking=False,
        thinking_token_budget=None,
        thinking_display="summarized",
        cache_salt=0,
        flags=0,
        image_pixels=b"",
        image_spans=(),
        score_tokens=(),
        stop_sequences=(),
        constraint=None,
        cancelled=Event(),
    )
    return SimpleNamespace(job=job, streamer=SimpleNamespace(token_ids=[1, 2, 3]))


@pytest.mark.parametrize(
    "bad", ["image_pixels", "image_spans", "score_tokens", "stop_sequences", "constraint"]
)
def test_splash_rejects_unsupported_generation_paths(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(capture, "_collector", collector(tmp_path))
    state = splash_state()
    setattr(state.job, bad, [1])
    with pytest.raises(capture.CaptureError):
        capture.begin_splash(state.job)


@pytest.mark.parametrize("outcome", ["success", "error", "cancelled", "bounded", "mismatch"])
def test_splash_finalizer_retains_runtime_delivery_and_marks_failed_traces(
    tmp_path, monkeypatch, outcome
):
    c = collector(tmp_path)
    monkeypatch.setattr(capture, "_collector", c)
    state = splash_state()
    capture.begin_splash(state.job)
    capture.splash_batch(state.job, [1, 2], 100)
    capture.splash_batch(state.job, [3], 200)
    result = SimpleNamespace(completion_tokens=3, reason="length", bounded=outcome == "bounded")
    if outcome == "cancelled":
        state.job.cancelled.set()
    if outcome == "mismatch":
        state.streamer.token_ids = [1, 2, 4]
    # A capture failure must not kill Splash's finalizer thread or suppress its response.
    capture.finish_splash(state, result, object() if outcome == "error" else None)
    assert not c.active
    assert len(list(c.directory.glob("*.trace.json"))) == (1 if outcome == "success" else 0)
    assert len(list(c.directory.glob("*.failed.json"))) == (0 if outcome == "success" else 1)


def test_success_keeps_private_native_request_for_independent_trace_association(tmp_path):
    c = collector(tmp_path)
    key = object()
    request = {"rendered_prompt": "nonce-123 synthetic", "arguments": {"max_tokens": 2}}
    c.begin(key, "mlx", request, 2)
    request["rendered_prompt"] = "mutated after admission"
    c.batch(key, [1], at_ns=10)
    c.batch(key, [2], at_ns=20)
    c.finish(key, [1, 2], 2, "length")
    trace_path = next(c.directory.glob("*.trace.json"))
    associated = trace_path.with_name(trace_path.name.replace(".trace.json", ".request.json"))
    receipt = json.loads(associated.read_text())
    assert receipt["native_request"]["rendered_prompt"] == "nonce-123 synthetic"
    assert (
        capture.digest(receipt["native_request"])
        == json.loads(trace_path.read_text())["request_sha256"]
    )
    assert associated.stat().st_mode & 0o777 == 0o600


def test_prepare_cli_checks_pins_and_sanitizes_failures(tmp_path, monkeypatch, capsys):
    src = tmp_path / "backend.py"
    src.write_text(SPLASH_FIXTURE)
    monkeypatch.setitem(native_prepare.PINS, "splash", hashlib.sha256(src.read_bytes()).hexdigest())
    out = tmp_path / "bundle"
    argv = ["--backend", "splash", "--source", str(src), "--out", str(out)]
    assert native_prepare.main(argv) == 0
    assert native_prepare.main(argv) == 2
    src.write_text("REDACTED_TEST_PAYLOAD")
    assert native_prepare.main(argv) == 2
    assert "REDACTED_TEST_PAYLOAD" not in capsys.readouterr().out


def test_prepare_preserves_future_imports_and_mlx_module_shape(tmp_path, monkeypatch):
    src = tmp_path / "generation.py"
    src.write_text('"""Synthetic."""\nfrom __future__ import annotations\n' + MLX_FIXTURE)
    monkeypatch.setitem(native_prepare.PINS, "mlx", hashlib.sha256(src.read_bytes()).hexdigest())
    receipt = native_prepare.prepare("mlx", src, tmp_path / "bundle")
    assert "generation.py" in receipt["files"]
    with pytest.raises(ValueError):
        native_prepare.instrument("unknown", "")


@pytest.mark.parametrize("backend,limit", [("other", 3), ("mlx", True), ("mlx", 1), ("mlx", 32769)])
def test_begin_rejects_unsupported_backend_or_memory_bound(tmp_path, backend, limit):
    c = collector(tmp_path)
    with pytest.raises(capture.CaptureError):
        c.begin(object(), backend, {}, limit)
    assert not c.active


def test_native_request_descriptor_has_explicit_memory_bound(tmp_path):
    c = collector(tmp_path)
    with pytest.raises(capture.CaptureError):
        c.begin(object(), "mlx", {"prompt": "x" * (4 * 1024**2)}, 2)
    assert not c.active


def test_finalized_queue_and_token_ids_are_bounded_independently(tmp_path):
    c = collector(tmp_path)
    key = object()
    c.begin(key, "mlx", {}, 2)
    for _ in range(2):
        c.observe_mlx(key, SimpleNamespace(token=1, token_count=1, finish_reason=None))
    with pytest.raises(capture.CaptureError):
        c.observe_mlx(key, SimpleNamespace(token=1, token_count=1, finish_reason=None))
    c.begin(key, "splash", {}, 2)
    with pytest.raises(capture.CaptureError):
        c.finish(key, [], 0, "stop")


def test_splash_storage_failure_does_not_kill_runtime_finalization(tmp_path, monkeypatch):
    c = collector(tmp_path)
    monkeypatch.setattr(capture, "_collector", c)
    state = splash_state()
    capture.begin_splash(state.job)
    capture.splash_batch(state.job, [1], 100)
    capture.splash_batch(state.job, [2, 3], 200)

    def disk_failure(*args, **kwargs):
        raise OSError("synthetic unavailable storage")

    monkeypatch.setattr(c, "_write", disk_failure)
    capture.finish_splash(
        state, SimpleNamespace(completion_tokens=3, reason="length", bounded=False), None
    )
    assert not list(c.directory.glob("*.trace.json"))


def test_private_capture_storage_failure_preserves_mlx_cancellation(tmp_path, monkeypatch):
    c = collector(tmp_path)
    monkeypatch.setattr(capture, "_collector", c)
    key = object()
    c.begin(key, "mlx", {}, 2)

    def disk_failure(*args, **kwargs):
        raise OSError("synthetic unavailable storage")

    monkeypatch.setattr(c, "_write", disk_failure)
    capture.abort(key)
    assert not c.active
