import json
from pathlib import Path

import httpx
import pytest

from local_llm_admin import comfy_queue as cq
from local_llm_admin.engines import ComfyClient

FIX = Path(__file__).parent / "fixtures" / "comfyui"
RUNNING = "2b1f0c4e-9a53-4d8e-a1a4-3e2d6f7a8b90"
PENDING_FIRST = "5c8d2e11-4a3b-4c2d-8e7f-9a0b1c2d3e4f"  # number 8
PENDING_SECOND = "7d9e1a22-5b6c-4f3a-9e8d-0a1b2c3d4e5f"  # number 9


def recorded():
    return json.loads((FIX / "queue.json").read_text())


def test_parse_recorded_queue_keeps_metadata_only():
    q = cq.parse_queue(recorded())
    assert q["running_count"] == 1 and q["pending_count"] == 2 and q["unparsed"] == 0
    assert [r["id"] for r in q["pending"]] == [PENDING_FIRST, PENDING_SECOND]  # execution order
    run = q["running"][0]
    assert run == {
        "id": RUNNING,
        "status": "running",
        "number": 7,
        "created": 1790000000.0,
        "nodes": 2,
        "output_nodes": 1,
    }
    text = json.dumps(q)
    for private in ("KSampler", "seed", "c0ffee", "filename_prefix", "wf-1"):
        assert private not in text


@pytest.mark.parametrize(
    "data",
    [None, [], "x", {}, {"queue_running": []}, {"queue_running": {}, "queue_pending": []}],
)
def test_malformed_queue_is_unknown_not_empty(data):
    assert cq.parse_queue(data) is None


def test_bad_items_are_counted_not_dropped_silently():
    q = cq.parse_queue(
        {
            "queue_running": [[1, "not-a-uuid"], "junk"],
            "queue_pending": [[True, PENDING_FIRST, None, {"create_time": "x"}, None], [2]],
        }
    )
    assert q["running"] == [] and q["running_count"] == 2 and q["unparsed"] == 3
    (p,) = q["pending"]
    assert p["number"] is None and p["created"] is None and p["nodes"] is None
    assert p["output_nodes"] is None


def test_large_queue_counts_exact_listing_bounded():
    pending = [
        [i, f"00000000-0000-4000-8000-{i:012d}", {}, {}, []] for i in range(cq.MAX_LISTED + 5)
    ]
    q = cq.parse_queue({"queue_running": [], "queue_pending": pending})
    view = cq.public_view({"state": "ok", **q})
    assert view["pending_count"] == cq.MAX_LISTED + 5
    assert len(view["pending"]) == cq.MAX_LISTED and view["truncated"] is True
    assert cq.public_view({"state": "unknown"}) == {"state": "unknown"}


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        (RUNNING, True),
        (RUNNING.upper(), False),
        (RUNNING.replace("-", ""), False),
        ("../../etc/passwd", False),
        (7, False),
        (None, False),
        ("z" * 36, False),
    ],
)
def test_valid_job_id(value, ok):
    assert cq.valid_job_id(value) is ok


def test_validate_ids_bounds():
    assert cq.validate_ids([RUNNING, RUNNING]) == [RUNNING]
    for bad in ([], "x", [1], [RUNNING] + ["x"], [RUNNING] * (cq.MAX_IDS + 1)):
        with pytest.raises(cq.QueueError) as ei:
            cq.validate_ids(bad)
        assert ei.value.code == 400


def test_session_token_uses_process_generation(monkeypatch):
    import os

    assert cq.session_token(None) is None
    token = cq.session_token(os.getpid())
    assert token is not None and token.startswith(f"{os.getpid()}-")
    assert cq.session_token(2**22 + 12345) is None  # no such process


def _client(handler, fake_comfy):
    return ComfyClient(fake_comfy.settings, transport=httpx.MockTransport(handler))


async def test_client_capability_probe(fake_comfy):
    def status(code):
        return lambda _r: httpx.Response(code, json={})

    assert await _client(status(200), fake_comfy).per_job_cancel() is True
    assert await _client(status(404), fake_comfy).per_job_cancel() is False
    assert await _client(status(500), fake_comfy).per_job_cancel() is None

    def down(_r):
        raise httpx.ConnectError("x")

    assert await _client(down, fake_comfy).per_job_cancel() is None
    assert await _client(down, fake_comfy).history(5) is None
    assert await _client(down, fake_comfy).queue_raw() is None


async def test_client_raw_reads(fake_comfy):
    hist = json.loads((FIX / "history.json").read_text())

    def handler(r):
        if r.url.path == "/history":
            assert r.url.params["max_items"] == "5"
            return httpx.Response(200, json=hist)
        return httpx.Response(200, json=["not", "a", "dict"])

    c = _client(handler, fake_comfy)
    assert await c.history(5) == hist
    assert await c.queue_raw() is None and await c.queue() is None


async def test_client_post_raises_on_error(fake_comfy):
    c = _client(lambda _r: httpx.Response(500), fake_comfy)
    with pytest.raises(httpx.HTTPStatusError):
        await c.post("/queue", {"delete": []})
