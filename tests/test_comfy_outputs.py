import gc
import json
import os
from pathlib import Path

import pytest

from local_llm_admin import comfy_outputs as co

FIX = Path(__file__).parent / "fixtures" / "comfyui"
SECRET = b"k" * 32


def _write(path: Path, data: bytes = b"x", mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _scan(root, **kw):
    return co.scan(
        root, SECRET, max_entries=kw.get("max_entries", 1000), max_depth=kw.get("depth", 6)
    )


def test_scan_lists_allowed_types_newest_first(tmp_path):
    root = tmp_path / "out"
    _write(root / "a.png", mtime=100)
    _write(root / "video/b.MP4", mtime=300)
    _write(root / "c.webp", mtime=200)
    _write(root / "page.html")
    _write(root / "img.svg")
    _write(root / "notes.txt")
    _write(root / ".hidden.png")
    _write(root / ".cache/x.png")
    res = _scan(root)
    assert [e.rel for e in res.entries] == ["video/b.MP4", "c.webp", "a.png"]
    assert res.skipped == 3 and res.truncated is False
    first = res.entries[0].public()
    assert first["name"] == "b.MP4" and first["subfolder"] == "video"
    assert first["kind"] == "video" and first["mime"] == "video/mp4"
    assert co.ID_RE.fullmatch(first["id"])


def test_scan_never_lists_or_follows_symlinks(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    secret = _write(tmp_path / "secret.png")
    (root / "link.png").symlink_to(secret)
    (tmp_path / "elsewhere").mkdir()
    _write(tmp_path / "elsewhere/y.png")
    (root / "dirlink").symlink_to(tmp_path / "elsewhere")
    (root / "loop").symlink_to(root)
    os.mkfifo(root / "pipe.png")
    res = _scan(root)
    assert res.entries == [] and res.skipped == 4  # 3 symlinks and a FIFO


def test_scan_bounds(tmp_path):
    root = tmp_path / "out"
    for i in range(10):
        _write(root / f"{i}.png")
    _write(root / "d1/d2/deep.png")
    res = _scan(root, max_entries=5)
    assert res.truncated is True and len(res.entries) <= 5
    res = _scan(root, depth=1)
    assert "d1/d2/deep.png" not in [e.rel for e in res.entries]
    assert "d1/d2/deep.png" in [e.rel for e in _scan(root, depth=2).entries]


def test_scan_missing_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        _scan(tmp_path / "missing")


def test_scan_unreadable_subdir_is_skipped(tmp_path):
    root = tmp_path / "out"
    _write(root / "ok.png")
    locked = root / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        res = _scan(root)
    finally:
        locked.chmod(0o755)
    assert [e.rel for e in res.entries] == ["ok.png"]
    assert res.skipped == (0 if os.geteuid() == 0 else 1)


def test_ids_are_opaque_and_bound_to_identity(tmp_path):
    a = co.make_id(SECRET, "a.png", 1, 2)
    assert a == co.make_id(SECRET, "a.png", 1, 2)
    assert a != co.make_id(SECRET, "a.png", 1, 3)
    assert a != co.make_id(b"other" * 7, "a.png", 1, 2)
    assert "a.png" not in a


def test_open_entry_verifies_identity(tmp_path):
    root = tmp_path / "out"
    _write(root / "sub/a.png", b"hello")
    (entry,) = _scan(root).entries
    fd, st = co.open_entry(root, entry)
    os.close(fd)
    assert st.st_size == 5
    # replaced by a different file after the scan (new inode)
    os.replace(_write(tmp_path / "new.png", b"other"), root / "sub/a.png")
    with pytest.raises(co.OutputGone):
        co.open_entry(root, entry)


def test_open_entry_refuses_symlink_swap(tmp_path):
    root = tmp_path / "out"
    _write(root / "a.png")
    (entry,) = _scan(root).entries
    (root / "a.png").unlink()
    (root / "a.png").symlink_to(_write(tmp_path / "passwd.png"))
    with pytest.raises(co.OutputGone):
        co.open_entry(root, entry)


def test_open_entry_refuses_parent_directory_swap(tmp_path):
    root = tmp_path / "out"
    _write(root / "sub/a.png")
    (entry,) = _scan(root).entries
    (root / "sub").rename(root / "moved")
    (tmp_path / "outside").mkdir()
    _write(tmp_path / "outside/a.png")
    (root / "sub").symlink_to(tmp_path / "outside")  # intermediate component now escapes
    with pytest.raises(co.OutputGone):
        co.open_entry(root, entry)


def test_open_entry_missing_and_bad_paths(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    for rel in ("gone.png", "../x.png", "/etc/x.png", "a//b.png", "./a.png"):
        with pytest.raises(co.OutputGone):
            co.open_entry(root, co.Entry("0" * 32, rel, 1, 0.0, 0, 0))


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("bytes=0-3", (0, 3)),
        ("bytes=5-", (5, 9)),
        ("bytes=-4", (6, 9)),
        ("bytes=-40", (0, 9)),
        ("bytes=8-100", (8, 9)),
        ("bytes=0-0", (0, 0)),
        ("items=0-3", None),
        ("bytes=0-1,4-5", None),
    ],
)
def test_parse_range(header, expected):
    assert co.parse_range(header, 10) == expected


@pytest.mark.parametrize("header", ["bytes=10-", "bytes=5-2", "bytes=-0", "bytes=-", "bytes=a-b"])
def test_parse_range_unsatisfiable(header):
    with pytest.raises(co.RangeInvalid):
        co.parse_range(header, 10)


def test_parse_range_empty_file():
    with pytest.raises(co.RangeInvalid):
        co.parse_range("bytes=-5", 0)


def _fd_open(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def test_file_stream_bounded_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "CHUNK", 4)
    p = _write(tmp_path / "a.bin", b"0123456789")
    fd = os.open(p, os.O_RDONLY)
    chunks = list(co.FileStream(fd, 2, 7))
    assert chunks == [b"2345", b"678"]
    assert not _fd_open(fd)


def test_file_stream_closes_when_never_started(tmp_path):
    p = _write(tmp_path / "a.bin", b"abc")
    fd = os.open(p, os.O_RDONLY)
    s = co.FileStream(fd, 0, 3)
    del s
    gc.collect()
    assert not _fd_open(fd)


def test_file_stream_truncated_file(tmp_path):
    p = _write(tmp_path / "a.bin", b"abcdef")
    fd = os.open(p, os.O_RDONLY)
    s = co.FileStream(fd, 0, 6)
    os.truncate(p, 2)
    assert list(s) == [b"ab"]
    assert not _fd_open(fd)
    s.close()  # idempotent


def test_file_stream_read_error_closes(tmp_path, monkeypatch):
    p = _write(tmp_path / "a.bin", b"abc")
    fd = os.open(p, os.O_RDONLY)

    def boom(*_a):
        raise OSError("io")

    monkeypatch.setattr(co.os, "read", boom)
    with pytest.raises(OSError):
        next(iter(co.FileStream(fd, 0, 3)))
    monkeypatch.undo()
    assert not _fd_open(fd)


def test_history_index_keeps_saved_outputs_only():
    hist = json.loads((FIX / "history.json").read_text())
    idx = co.history_index(hist)
    job = "0f3e2d1c-6b5a-4978-8a6b-5c4d3e2f1a0b"
    assert idx == {
        ("", "ComfyUI_00001_.png"): {"job_id": job, "finished": 1789999999.0},
        ("video", "clip_00001.mp4"): {"job_id": job, "finished": 1789999999.0},
    }
    assert "SaveImage" not in json.dumps(list(idx.values()))


@pytest.mark.parametrize(
    "hist",
    [
        None,
        [],
        {"a": "x"},
        {"a": {"outputs": "x", "status": "x"}},
        {"a": {"outputs": {"1": "x", "2": {"images": "x"}}, "status": {"messages": "x"}}},
        {"a": {"outputs": {"1": {"images": [{"type": "output", "filename": 3}]}}}},
    ],
)
def test_history_index_tolerates_malformed(hist):
    assert co.history_index(hist) == {}


def test_history_index_non_string_subfolder():
    hist = {
        "j": {
            "outputs": {"1": {"images": [{"type": "output", "filename": "a.png", "subfolder": 5}]}}
        }
    }
    assert co.history_index(hist) == {("", "a.png"): {"job_id": "j", "finished": None}}


def test_output_index_cache_and_lookup(tmp_path):
    root = tmp_path / "out"
    _write(root / "a.png")
    idx = co.OutputIndex(root, max_entries=100, max_depth=2, ttl_s=60)
    assert idx.fresh() is None
    res = idx.rescan()
    assert idx.fresh() is res
    e = res.entries[0]
    assert idx.lookup(e.id) == e and idx.lookup("0" * 32) is None
    idx.ttl_s = 0
    assert idx.fresh() is None


def test_scan_entry_vanishing_mid_scan_is_skipped(tmp_path, monkeypatch):
    root = tmp_path / "out"
    _write(root / "a.png")
    _write(root / "b.png")
    real = os.scandir

    class Vanishing:
        def __init__(self, de):
            self._de = de
            self.name, self.path = de.name, de.path

        def stat(self, follow_symlinks=True):
            if self.name == "a.png":
                raise FileNotFoundError(self.path)
            return self._de.stat(follow_symlinks=follow_symlinks)

    class Wrapped:
        def __init__(self, path):
            self._it = real(path)

        def __iter__(self):
            return (Vanishing(de) for de in self._it)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._it.close()

    monkeypatch.setattr(co.os, "scandir", Wrapped)
    res = _scan(root)
    assert [e.rel for e in res.entries] == ["b.png"] and res.skipped == 1
