"""M3c: ``mlx-model profile`` and the helper with real zsh; flags reach the server."""

from __future__ import annotations

import json
import os

import pytest

from .host_harness import HOST, make_host, redirect


@pytest.fixture
def host(tmp_path):
    h = make_host(tmp_path)
    redirect(HOST / "bin/mlx-profile", h.root / "bin/mlx-profile", h.root)
    (h.state / "healthy").write_text("")
    py = os.environ.get("MLX_DOWNLOAD_PY")  # e.g. a Python 3.9, the Mac's version
    if py:
        h.cli.write_text(h.cli.read_text().replace('HOST_PY="/usr/bin/python3"', f'HOST_PY="{py}"'))
    yield h
    h.cleanup()


def _profile_file(host, name):
    return host.root / "etc/profiles" / f"{name}.json"


def _set(host, name, doc):
    return host.run("profile", "set", name, json.dumps(doc))


def _show(host, name):
    r = host.run("profile", "show", name)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _server_args(host):
    return (host.state / "server_args").read_text().splitlines()


def _env(host):
    return (host.root / "run/active.env").read_text()


def test_schema_lists_supported_settings_and_fixed_concurrency(host):
    r = host.run("profile", "schema")
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    launch = {f["name"]: f for f in s["launch"]}
    assert launch["max_kv_size"]["flag"] == "--max-kv-size" and launch["max_kv_size"]["restart"]
    assert launch["kv_quant_scheme"]["enum"] == ["uniform", "turboquant"]
    assert "max_num_seqs" not in launch and s["fixed"]["max_num_seqs"] == 1
    assert {f["name"] for f in s["chat"]} >= {"temperature", "reasoning_effort"}
    assert all(not f["restart"] for f in s["chat"])
    assert "api_key" not in launch and "draft_model" not in launch


def test_saved_launch_settings_reach_the_server_at_the_next_start(host):
    r = _set(
        host,
        "a",
        {"launch": {"max_kv_size": 32768, "enable_thinking": True}, "chat": {"temperature": 0.2}},
    )
    assert r.returncode == 0, r.stderr
    shown = _show(host, "a")
    assert shown["launch_args"] == ["--max-kv-size", "32768", "--enable-thinking"]
    assert shown["fingerprint"] != "default" and shown["chat"] == {"temperature": 0.2}
    r = host.run("start", "a")
    assert r.returncode == 0, r.stderr
    args = _server_args(host)
    assert args[args.index("--max-kv-size") + 1] == "32768" and "--enable-thinking" in args
    assert args[args.index("--max-num-seqs") + 1] == "1"  # concurrency stays fixed
    assert f"MLX_PROFILE_FINGERPRINT={shown['fingerprint']}" in _env(host)
    assert "profile:" in r.stdout


def test_list_reports_every_saved_fingerprint(host):
    _set(host, "a", {"launch": {"prefill_step_size": 512}})
    (host.root / "etc/profiles/b.json").write_text("{bad")
    r = host.run("profile", "list")
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["a"] == _show(host, "a")["fingerprint"] and got["b"] == "invalid"


def test_another_model_keeps_its_defaults(host):
    assert _set(host, "a", {"launch": {"prefill_step_size": 512}}).returncode == 0
    assert host.run("start", "b").returncode == 0
    assert "--prefill-step-size" not in _server_args(host)
    assert "MLX_PROFILE_FINGERPRINT=default" in _env(host)
    assert _show(host, "b")["saved"] is None


@pytest.mark.parametrize(
    "doc",
    [
        {"launch": {"api_key": "x"}},
        {"launch": {"max_kv_size": "32768; rm -rf /"}},
        {"launch": {"max_kv_size": "32768"}},
        {"launch": {"max_kv_size": 10}},
        {"launch": {"max_kv_size": 1.5}},
        {"launch": {"enable_thinking": "yes"}},
        {"launch": {"kv_quant_scheme": "fp4"}},
        {"launch": {"kv_bits": 3.5}},
        {"launch": {"kv_bits": 3.5, "kv_quant_scheme": "turboquant", "kv_group_size": 64}},
        {"launch": {"max_num_seqs": 4}},
        {"launch": {"extra_args": ["--api-key", "x"]}},
        {"chat": {"chat_template": "{% for m in messages %}{{ m }}{% endfor %}"}},
        {"chat": {"reasoning_effort": "extreme"}},
        {"chat": {"temperature": float("nan")}},
        {"launch": [], "chat": {}},
        {"version": 2},
        {"shell": "$(id)"},
        [],
    ],
)
def test_unsupported_or_unsafe_settings_are_refused(host, doc):
    assert _set(host, "a", {"launch": {"prefill_step_size": 512}}).returncode == 0
    before = _profile_file(host, "a").read_text()
    r = host.run("profile", "set", "a", json.dumps(doc, allow_nan=True))
    assert r.returncode == 2 and "invalid profile" in r.stderr
    assert _profile_file(host, "a").read_text() == before
    assert not host.lock.exists()


def test_turboquant_fraction_is_accepted(host):
    r = _set(host, "a", {"launch": {"kv_bits": 3.5, "kv_quant_scheme": "turboquant"}})
    assert r.returncode == 0, r.stderr


def test_non_json_and_unknown_models_are_refused(host):
    assert host.run("profile", "set", "a", "not json").returncode == 2
    r = _set(host, "zzz", {"launch": {}})
    assert r.returncode == 1 and "Unknown model" in r.stderr
    assert host.run("profile", "show", "../a").returncode == 1
    assert not (host.root / "etc/profiles/zzz.json").exists()


def test_reset_and_revert_keep_one_previous_version(host):
    _set(host, "a", {"launch": {"prefill_step_size": 512}})
    _set(host, "a", {"launch": {"prefill_step_size": 1024}})
    prev = json.loads((host.root / "etc/profiles/a.json.prev").read_text())
    assert prev["launch"] == {"prefill_step_size": 512}
    r = host.run("profile", "revert", "a")
    assert r.returncode == 0, r.stderr
    assert _show(host, "a")["launch"] == {"prefill_step_size": 512}
    r = host.run("profile", "reset", "a")
    assert r.returncode == 0 and _show(host, "a")["saved"] is None
    assert _show(host, "a")["has_previous"] is True
    assert host.run("profile", "revert", "a").returncode == 0
    assert _show(host, "a")["launch"] == {"prefill_step_size": 512}


def test_a_corrupt_profile_never_blocks_a_start(host):
    d = host.root / "etc/profiles"
    d.mkdir(parents=True)
    (d / "a.json").write_text('{"launch": {"max_kv_size": "oops"}}')
    r = host.run("start", "a")
    assert r.returncode == 0, r.stderr
    assert "not applied" in r.stderr
    assert "--max-kv-size" not in _server_args(host)
    assert "MLX_PROFILE_FINGERPRINT=default-fallback" in _env(host)
    assert "profile for a not applied" in (host.root / "logs/mlx-server.log").read_text()


def test_a_missing_helper_starts_with_built_in_flags(host):
    (host.root / "bin/mlx-profile").unlink()
    assert host.run("start", "a").returncode == 0
    assert "MLX_PROFILE_FINGERPRINT=default" in _env(host)


def test_symlinked_profiles_are_refused(host, tmp_path):
    d = host.root / "etc/profiles"
    d.mkdir(parents=True)
    target = tmp_path / "elsewhere.json"
    target.write_text('{"launch": {}}')
    os.symlink(target, d / "a.json")
    assert host.run("profile", "set", "a", '{"launch": {}}').returncode == 2
    assert target.read_text() == '{"launch": {}}'


def test_a_failed_write_keeps_the_old_profile(host):
    _set(host, "a", {"launch": {"prefill_step_size": 512}})
    d = host.root / "etc/profiles"
    d.chmod(0o555)
    try:
        r = _set(host, "a", {"launch": {"prefill_step_size": 1024}})
    finally:
        d.chmod(0o755)
    assert r.returncode == 1 and "storage failed" in r.stderr
    assert _show(host, "a")["launch"] == {"prefill_step_size": 512}
    assert [p.name for p in d.iterdir() if p.name.startswith(".")] == []


def test_profile_writes_take_the_lifecycle_lock(host):
    host.lock.mkdir()
    r = _set(host, "a", {"launch": {}})
    assert r.returncode == 1 and "Another mlx-model command" in r.stderr


def test_only_well_formed_helper_output_becomes_server_flags(host):
    # a helper that warns on stderr or prints junk must never inject arguments
    helper = host.root / "bin/mlx-profile"
    helper.write_text(
        "#!/usr/bin/python3\nimport sys\nprint('warning: x', file=sys.stderr)\n"
        "print('default\\n--api-key\\nsecret')\n"
    )
    r = host.run("start", "a")
    assert r.returncode == 0, r.stderr
    args = _server_args(host)
    assert "--api-key" not in args and "secret" not in args and "warning:" not in " ".join(args)
    assert "MLX_PROFILE_FINGERPRINT=default-fallback" in _env(host)
