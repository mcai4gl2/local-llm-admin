import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from local_llm_admin import auth
from local_llm_admin.app import create_app

FIX = Path(__file__).parent / "fixtures" / "comfyui"


@pytest.fixture
async def out(fake_comfy):
    s = fake_comfy.settings
    s.admin_password_hash = auth.hash_password("pw")
    s.admin_session_secret = "t"
    app = create_app(s, start_background=False, comfy_transport=fake_comfy.transport)
    async with app.router.lifespan_context(app):
        await app.state.startup_task  # adoption holds the job slot; a slow runner races it
        await app.state.backfill_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            await c.post("/login", data={"password": "pw"})
            yield c, fake_comfy, app, fake_comfy.root / "output"


def _write(path: Path, data: bytes, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))


async def _items(c, **params):
    r = await c.get("/api/comfyui/outputs", params=params)
    assert r.status_code == 200
    return r.json()


async def test_empty_and_page(out):
    c, _fake, _app, root = out
    body = await _items(c)
    assert body["available"] is True and body["items"] == [] and body["next_offset"] is None
    r = await c.get("/outputs")
    assert r.status_code == 200 and "outputs.js" in r.text


async def test_missing_output_folder_is_unavailable(out):
    c, _fake, _app, root = out
    root.rmdir()
    body = await _items(c, refresh=True)
    assert body["available"] is False and "drive" in body["reason"]


async def test_hung_scan_is_bounded(out, monkeypatch):
    c, _fake, app, _root = out
    monkeypatch.setattr(app.state.outputs, "rescan", lambda: time.sleep(1.0))
    monkeypatch.setattr(app.state.outputs, "fresh", lambda: None)
    s = app.state.control.settings
    s.comfyui_output_timeout_s = 0.05
    first = await _items(c)
    assert first["available"] is False and "in time" in first["reason"]
    second = await _items(c)  # the previous scan still holds the probe
    assert second["available"] is False and "still running" in second["reason"]


async def test_scan_error_is_reported(out, monkeypatch):
    c, _fake, app, _root = out

    def boom():
        raise PermissionError("denied")

    monkeypatch.setattr(app.state.outputs, "rescan", boom)
    body = await _items(c, refresh=True)
    assert body["available"] is False and "could not be read" in body["reason"]


async def test_pagination_newest_first(out):
    c, _fake, _app, root = out
    for i in range(5):
        _write(root / f"img_{i}.png", b"x", 1000 + i)
    _write(root / "notes.html", b"<script>", 2000)
    p1 = await _items(c, limit=2, refresh=True)
    assert [i["name"] for i in p1["items"]] == ["img_4.png", "img_3.png"]
    assert p1["total"] == 5 and p1["next_offset"] == 2 and p1["skipped"] == 1
    p3 = await _items(c, limit=2, offset=4)
    assert [i["name"] for i in p3["items"]] == ["img_0.png"] and p3["next_offset"] is None
    assert (await c.get("/api/comfyui/outputs", params={"limit": 500})).status_code == 422


async def test_content_full_and_headers(out):
    c, _fake, _app, root = out
    _write(root / "sub/a b.png", b"\x89PNGdata", 1000)
    (item,) = (await _items(c, refresh=True))["items"]
    r = await c.get(f"/api/comfyui/outputs/{item['id']}/content")
    assert r.status_code == 200 and r.content == b"\x89PNGdata"
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["content-disposition"] == "inline; filename*=UTF-8''a%20b.png"
    assert r.headers["accept-ranges"] == "bytes" and r.headers["content-length"] == "8"
    r = await c.get(f"/api/comfyui/outputs/{item['id']}/content", params={"download": 1})
    assert r.headers["content-disposition"].startswith("attachment;")


async def test_content_ranges(out):
    c, _fake, _app, root = out
    _write(root / "v.mp4", bytes(range(100)), 1000)
    (item,) = (await _items(c, refresh=True))["items"]
    url = f"/api/comfyui/outputs/{item['id']}/content"
    r = await c.get(url, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20))
    assert r.headers["content-range"] == "bytes 10-19/100"
    assert r.headers["content-type"] == "video/mp4"
    r = await c.get(url, headers={"Range": "bytes=-5"})
    assert r.status_code == 206 and r.content == bytes(range(95, 100))
    r = await c.get(url, headers={"Range": "bytes=200-"})
    assert r.status_code == 416 and r.headers["content-range"] == "bytes */100"
    r = await c.get(url, headers={"Range": "bytes=0-1,5-6"})
    assert r.status_code == 200 and len(r.content) == 100


@pytest.mark.parametrize(
    "path",
    [
        "/api/comfyui/outputs/..%2F..%2Fetc%2Fpasswd/content",
        "/api/comfyui/outputs/%2e%2e/content",
        "/api/comfyui/outputs/ABCDEF0123456789ABCDEF0123456789/content",
        "/api/comfyui/outputs/abc/content",
    ],
)
async def test_content_rejects_paths_and_bad_ids(out, path):
    c, _fake, _app, _root = out
    r = await c.get(path)
    assert r.status_code in (400, 404)


async def test_unknown_id_after_rescan(out):
    c, _fake, _app, _root = out
    r = await c.get(f"/api/comfyui/outputs/{'0' * 32}/content")
    assert r.status_code == 404 and "refresh" in r.json()["detail"]


async def test_ids_survive_cache_expiry(out):
    c, _fake, app, root = out
    _write(root / "a.png", b"img", 1000)
    (item,) = (await _items(c, refresh=True))["items"]
    app.state.outputs.ttl_s = 0  # cache expired; the id must still resolve after a rescan
    app.state.outputs._by_id.clear()
    r = await c.get(f"/api/comfyui/outputs/{item['id']}/content")
    assert r.status_code == 200 and r.content == b"img"


async def test_replaced_or_removed_file_is_gone(out):
    c, _fake, _app, root = out
    _write(root / "a.png", b"one", 1000)
    _write(root / "b.png", b"two", 900)
    items = (await _items(c, refresh=True))["items"]
    by_name = {i["name"]: i["id"] for i in items}
    os.replace(root / "b.png", root / "a.png")  # a.png is now another inode
    r = await c.get(f"/api/comfyui/outputs/{by_name['a.png']}/content")
    assert r.status_code == 410
    r = await c.get(f"/api/comfyui/outputs/{by_name['b.png']}/content")
    assert r.status_code == 410


async def test_hung_open_is_bounded(out, monkeypatch):
    c, _fake, app, root = out
    _write(root / "a.png", b"one", 1000)
    (item,) = (await _items(c, refresh=True))["items"]
    from local_llm_admin import app as app_mod

    real = app_mod.open_entry
    opened = []

    def slow(r, e):
        time.sleep(0.3)
        res = real(r, e)
        opened.append(res[0])
        return res

    monkeypatch.setattr(app_mod, "open_entry", slow)
    app.state.control.settings.probe_timeout_s = 0.05
    r = await c.get(f"/api/comfyui/outputs/{item['id']}/content")
    assert r.status_code == 504
    for _ in range(50):
        if opened:
            break
        time.sleep(0.05)
    time.sleep(0.05)
    import asyncio

    await asyncio.sleep(0.05)  # let the late-close callback run
    with pytest.raises(OSError):
        os.fstat(opened[0])


async def test_history_metadata_when_comfy_running(out):
    c, fake, _app, root = out
    _write(root / "ComfyUI_00001_.png", b"a", 1000)
    _write(root / "video/clip_00001.mp4", b"b", 1001)
    _write(root / "orphan.png", b"c", 999)
    body = await _items(c, refresh=True)
    assert body["history"] == "stopped" and "job" not in body["items"][0]
    subprocess.run([str(fake.settings.comfyui_path), "start"], capture_output=True, check=False)
    fake.history.update(json.loads((FIX / "history.json").read_text()))
    body = await _items(c)
    assert body["history"] == "ok"
    jobs = {i["name"]: i["job"] for i in body["items"]}
    assert jobs["clip_00001.mp4"]["job_id"].startswith("0f3e2d1c")
    assert jobs["ComfyUI_00001_.png"]["finished"] == 1789999999.0
    assert jobs["orphan.png"] is None
    (fake.state / "comfy_ready").unlink()
    body = await _items(c)
    assert body["history"] == "unavailable"


async def test_outputs_require_login(fake_comfy):
    s = fake_comfy.settings
    app = create_app(s, start_background=False, comfy_transport=fake_comfy.transport)
    async with app.router.lifespan_context(app):
        await app.state.startup_task  # adoption holds the job slot; a slow runner races it
        await app.state.backfill_task
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            r = await c.get(f"/api/comfyui/outputs/{'0' * 32}/content")
            assert r.status_code == 401
