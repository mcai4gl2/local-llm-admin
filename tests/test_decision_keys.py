"""M6 Phase 5: per-client gateway keys (hash stored, shown once, revocable)."""

import sqlite3

import pytest

from local_llm_admin import decision_keys as dk
from local_llm_admin.decision_keys import ClientKeys, KeyError_


@pytest.fixture
def keys(tmp_path):
    return ClientKeys(tmp_path / "admin.sqlite")


def test_create_shows_the_key_once_and_stores_only_a_hash(keys, tmp_path):
    c = keys.create("laptop", note="dev box")
    assert c["key"].startswith(f"dsk_{c['prefix']}_") and dk.KEY_RE.fullmatch(c["key"])
    listed = keys.list()
    assert [r["name"] for r in listed] == ["laptop"]
    assert "key" not in listed[0] and "key_hash" not in listed[0]
    raw = (tmp_path / "admin.sqlite").read_bytes()
    assert c["key"].encode() not in raw
    with sqlite3.connect(tmp_path / "admin.sqlite") as conn:
        (h,) = conn.execute("SELECT key_hash FROM decision_clients").fetchone()
    assert h == dk.hash_key(c["key"])


def test_verify_valid_missing_malformed_wrong_and_revoked(keys):
    c = keys.create("laptop")
    assert keys.verify(f"Bearer {c['key']}")["name"] == "laptop"
    assert keys.verify(None) is None
    assert keys.verify("Basic abc") is None
    assert keys.verify("Bearer dsk_short") is None
    wrong = c["key"][:-4] + ("AAAA" if not c["key"].endswith("AAAA") else "BBBB")
    assert keys.verify(f"Bearer {wrong}") is None
    other_prefix = "dsk_zzzzzzzz_" + c["key"].split("_", 2)[2]
    assert keys.verify(f"Bearer {other_prefix}") is None
    keys.revoke(c["id"])
    assert keys.verify(f"Bearer {c['key']}") is None  # at once, no cache
    assert keys.list()[0]["revoked_at"] is not None


def test_verify_uses_constant_time_compare(keys, monkeypatch):
    c = keys.create("laptop")
    seen = []
    real = dk.hmac.compare_digest
    monkeypatch.setattr(dk.hmac, "compare_digest", lambda a, b: seen.append(1) or real(a, b))
    keys.verify(f"Bearer {c['key']}")
    assert seen


def test_names_are_validated_and_unique(keys):
    keys.create("laptop")
    for bad in ("", "has space", "x" * 49, "semi;colon"):
        with pytest.raises(KeyError_) as e:
            keys.create(bad)
        assert e.value.code == 400
    with pytest.raises(KeyError_) as e:
        keys.create("laptop")
    assert e.value.code == 409


def test_rename_and_revoke_errors(keys):
    a = keys.create("a")
    keys.create("b")
    keys.rename(a["id"], "a2")
    assert {r["name"] for r in keys.list()} == {"a2", "b"}
    with pytest.raises(KeyError_) as e:
        keys.rename(a["id"], "b")
    assert e.value.code == 409
    with pytest.raises(KeyError_) as e:
        keys.rename(a["id"], "bad name")
    assert e.value.code == 400
    for call in (lambda: keys.rename(999, "z"), lambda: keys.revoke(999)):
        with pytest.raises(KeyError_) as e:
            call()
        assert e.value.code == 404
    keys.revoke(a["id"])
    keys.revoke(a["id"])  # idempotent


def test_name_of_a_revoked_client_can_be_reused(keys):
    a = keys.create("laptop")
    keys.revoke(a["id"])
    b = keys.create("laptop")
    assert b["id"] != a["id"]


def test_last_used_is_touched_but_throttled(keys, monkeypatch):
    c = keys.create("laptop")
    now = [1000.0]
    monkeypatch.setattr(dk.time, "time", lambda: now[0])
    keys.verify(f"Bearer {c['key']}")
    assert keys.list()[0]["last_used_at"] == 1000.0
    now[0] = 1010.0
    keys.verify(f"Bearer {c['key']}")
    assert keys.list()[0]["last_used_at"] == 1000.0  # within the minute
    now[0] = 1100.0
    keys.verify(f"Bearer {c['key']}")
    assert keys.list()[0]["last_used_at"] == 1100.0


def test_prefix_collision_draws_again(keys, monkeypatch):
    first = keys.create("a")
    letters = iter(list(first["prefix"]) + list("zzzzzzzz"))
    monkeypatch.setattr(dk.secrets, "choice", lambda chars: next(letters))
    second = keys.create("b")
    assert second["prefix"] == "zzzzzzzz"
