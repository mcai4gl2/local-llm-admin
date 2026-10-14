from local_llm_admin import auth


def test_hash_and_verify():
    h = auth.hash_password("correct horse")
    assert h.startswith("$argon2")
    assert auth.verify_password(h, "correct horse") is True
    assert auth.verify_password(h, "wrong") is False
    assert auth.verify_password("", "anything") is False
    assert auth.verify_password("not-a-hash", "x") is False


def test_session_roundtrip_and_tamper():
    sm = auth.SessionManager("s3cret", max_age_s=60)
    cookie, csrf = sm.issue()
    assert sm.load(cookie) == {"csrf": csrf}
    assert sm.load(cookie + "x") is None
    assert sm.load("") is None
    assert sm.load(None) is None
    assert auth.SessionManager("other", 60).load(cookie) is None


def test_session_expiry(monkeypatch):
    sm = auth.SessionManager("s3cret", max_age_s=10)
    cookie, _ = sm.issue()
    import itsdangerous.timed as t

    real = t.time.time
    monkeypatch.setattr(t.time, "time", lambda: real() + 11)
    assert sm.load(cookie) is None


def test_empty_secret_gets_random_one():
    a, b = auth.SessionManager("", 60), auth.SessionManager("", 60)
    cookie, _ = a.issue()
    assert b.load(cookie) is None


def test_csrf_check():
    assert auth.csrf_ok({"csrf": "abc"}, "abc") is True
    assert auth.csrf_ok({"csrf": "abc"}, "abd") is False
    assert auth.csrf_ok({"csrf": "abc"}, None) is False


def test_login_limiter():
    now = [0.0]
    lim = auth.LoginLimiter(max_failures=3, window_s=60, clock=lambda: now[0])
    for _ in range(3):
        assert lim.allowed("1.2.3.4")
        lim.fail("1.2.3.4")
    assert not lim.allowed("1.2.3.4")
    assert lim.allowed("5.6.7.8")
    now[0] = 61
    assert lim.allowed("1.2.3.4")
    lim.fail("1.2.3.4")
    lim.reset("1.2.3.4")
    assert lim.allowed("1.2.3.4")


def test_hash_password_cli(monkeypatch, capsys):
    answers = iter(["pw1", "pw1"])
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt="": next(answers))
    assert auth.hash_password_cli() == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("ADMIN_PASSWORD_HASH='$argon2")
    assert auth.verify_password(out.split("=", 1)[1].strip("'"), "pw1")


def test_hash_password_cli_mismatch(monkeypatch):
    answers = iter(["a", "b"])
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt="": next(answers))
    assert auth.hash_password_cli() == 1
