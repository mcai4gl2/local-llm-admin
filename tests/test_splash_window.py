"""Safety checks for the temporary, approved experiment supervisor."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The guard is sent with its preflight helper to the isolated directory.
sys.path.insert(0, str(ROOT / "ops/splash-phase0"))
try:
    import window_guard as guard
finally:
    sys.path.pop(0)


def test_generation_requires_pid_start_and_complete_command_identity():
    original = {"pid": 123, "created": 12.5, "command": ["python", "owned-test"]}
    assert guard.same_generation(original, dict(original))
    for field, value in (("pid", 124), ("created", 13.5), ("command", ["python", "unrelated"])):
        assert not guard.same_generation(original, {**original, field: value})
    assert not guard.same_generation(original, None)
    assert not guard.same_generation({}, {})


def test_stop_checks_ownership_again_before_every_signal(monkeypatch):
    record = {"pid": 123, "created": 12.5, "command": ["python", "owned-test"]}
    unrelated = {**record, "created": 13.5}
    signals = []
    monkeypatch.setattr(guard, "identity", lambda _: unrelated)
    monkeypatch.setattr(guard, "signal_process", lambda pid, sig: signals.append((pid, sig)))
    assert guard.stop_records([record], grace_s=0)
    assert signals == []


def test_stop_terminates_then_kills_only_a_still_matching_generation(monkeypatch):
    record = {"pid": 123, "created": 12.5, "command": ["python", "owned-test"]}
    alive = True
    signals = []

    def send(pid, sig):
        nonlocal alive
        signals.append((pid, sig))
        if sig == guard.signal.SIGKILL:
            alive = False

    monkeypatch.setattr(guard, "identity", lambda _: record if alive else None)
    monkeypatch.setattr(guard, "signal_process", send)
    assert guard.stop_records([record], grace_s=0)
    assert signals == [(123, guard.signal.SIGTERM), (123, guard.signal.SIGKILL)]


def test_restoration_refuses_external_intent_before_running_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "preserved", lambda _: False)
    monkeypatch.setattr(guard, "run_command", lambda argv, **kwargs: calls.append(argv))
    monkeypatch.setattr(guard, "port_open", lambda _: False)
    state = {"records": [], "activated": True, "admin_unloaded": True, "strata_stopped": True}
    result = guard.restore({"original_loaded": True}, state)
    assert result == "external_state_changed" and calls == []


def test_restoration_does_not_start_services_while_owned_processes_remain(monkeypatch):
    monkeypatch.setattr(guard, "stop_records", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        guard, "run_command", lambda *_args, **_kwargs: pytest.fail("must not restore")
    )
    assert guard.restore({}, {"records": [], "activated": True}) == "experiment_processes_remain"


def test_restoration_order_and_original_pack(monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "preserved", lambda _: True)
    monkeypatch.setattr(guard, "port_open", lambda _: False)
    monkeypatch.setattr(guard, "run_command", lambda argv, **kwargs: calls.append(argv))
    monkeypatch.setattr(guard, "loaded", lambda _: False)
    cfg = {
        "original_loaded": True,
        "original_pack": "IQ3_XXS",
        "admin_label": "example.admin",
        "admin_plist": "/Users/you/example.plist",
        "uid": 501,
    }
    state = {"records": [], "activated": True, "strata_stopped": True, "admin_unloaded": True}
    assert guard.restore(cfg, state) == "restored"
    assert calls == [
        ["/Volumes/Models/strata/bin/strata", "start", "IQ3_XXS"],
        ["launchctl", "bootstrap", "gui/501", "/Users/you/example.plist"],
    ]


def test_guard_does_not_restore_an_originally_unloaded_admin_job(monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "preserved", lambda _: True)
    monkeypatch.setattr(guard, "port_open", lambda _: False)
    monkeypatch.setattr(guard, "run_command", lambda argv, **kwargs: calls.append(argv))
    state = {"records": [], "activated": True, "strata_stopped": False, "admin_unloaded": True}
    assert guard.restore({"original_loaded": False}, state) == "restored"
    assert not calls


def test_resource_abort_has_sustained_swap_threshold():
    assert (
        guard.resource_reason({"available": 5 * 1024**3, "pressure": 1, "swap": 0}, 0, 0)
        == "low_headroom"
    )
    assert (
        guard.resource_reason({"available": 8 * 1024**3, "pressure": 4, "swap": 0}, 0, 0)
        == "critical_pressure"
    )
    sample = {"available": 8 * 1024**3, "pressure": 1, "swap": 32 * 1024**2}
    assert guard.resource_reason(sample, 0, 0) is None
    assert guard.resource_reason(sample, 0, 1) == "sustained_swap_growth"


def test_unrecorded_experiment_process_blocks_restore_without_signalling_it(monkeypatch):
    monkeypatch.setattr(guard, "experiment_present", lambda _: True)
    monkeypatch.setattr(guard, "run_command", lambda *_a, **_kw: pytest.fail("must not restore"))
    assert (
        guard.restore({"spike": "/private/spike"}, {"records": [], "activated": True})
        == "experiment_processes_remain"
    )


def test_preserved_checks_intent_config_content_and_selection(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "active"
    link.symlink_to(target)
    config = tmp_path / "config"
    config.write_text("original")
    source = {"ok": True, "state": {"intent": {"value": "strata", "ts": 123}}, "active_jobs": []}
    monkeypatch.setattr(guard, "database_state", lambda _: source)
    cfg = {
        "admin_db": "/private/admin.sqlite",
        "expected_intent": {
            "intent": {"value": "strata"},
            "sidecar_intent": None,
            "embed_intent": None,
            "recovery": None,
        },
        "config_hashes": {str(config): guard.file_sha(config)},
        "selected_path": str(target),
        "selected_link": str(link),
    }
    assert guard.preserved(cfg)
    config.write_text("changed")
    assert not guard.preserved(cfg)
    config.write_text("original")
    source["state"]["intent"]["value"] = "off"
    assert not guard.preserved(cfg)
    source["state"]["intent"]["value"] = "strata"
    link.unlink()
    link.symlink_to(tmp_path / "other")
    assert not guard.preserved(cfg)


def test_reused_pid_between_term_and_escalation_is_never_killed(monkeypatch):
    expected = {"pid": 123, "created": 12.5, "command": ["owned"]}
    current = dict(expected)
    signals = []

    def send(pid, sig):
        signals.append((pid, sig))
        current["created"] = 20.0

    monkeypatch.setattr(guard, "identity", lambda _: current)
    monkeypatch.setattr(guard, "signal_process", send)
    assert guard.stop_records([expected], grace_s=0)
    assert signals == [(123, guard.signal.SIGTERM)]


def test_stop_records_returns_after_graceful_termination(monkeypatch):
    expected = {"pid": 123, "created": 12.5, "command": ["owned"]}
    alive = True
    calls = []

    def send(pid, sig):
        nonlocal alive
        calls.append((pid, sig))
        alive = False

    monkeypatch.setattr(guard, "identity", lambda _: expected if alive else None)
    monkeypatch.setattr(guard, "signal_process", send)
    assert guard.stop_records([expected], grace_s=0.1)
    assert calls == [(123, guard.signal.SIGTERM)]


def test_restore_skips_an_already_running_original_strata(monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "strata_running", lambda _: True)
    monkeypatch.setattr(guard, "preserved", lambda _: True)
    monkeypatch.setattr(guard, "port_open", lambda _: False)
    monkeypatch.setattr(guard, "loaded", lambda _: False)
    monkeypatch.setattr(guard, "run_command", lambda argv, **kwargs: calls.append(argv))
    cfg = {"original_loaded": True, "uid": 501, "admin_plist": "/Users/you/example.plist"}
    state = {"records": [], "activated": True, "strata_stopped": True, "admin_unloaded": True}
    assert guard.restore(cfg, state) == "restored"
    assert calls == [["launchctl", "bootstrap", "gui/501", "/Users/you/example.plist"]]


@pytest.mark.parametrize("failure", ["state", "busy", "generation", "bootout", "stop", None])
def test_activation_checks_and_partial_failure_restore_flags(tmp_path, monkeypatch, failure):
    record = {"pid": 123, "created": 12.5, "command": ["owned"]}
    cfg = {
        "spike": str(tmp_path),
        "uid": 501,
        "admin_label": "example.admin",
        "admin_db": "/private/admin.sqlite",
        "original_generation": record,
    }
    state = {"phase": "armed", "activated": False, "admin_unloaded": False, "strata_stopped": False}
    calls = []
    monkeypatch.setattr(guard, "preserved", lambda _: failure != "state")
    monkeypatch.setattr(
        guard, "database_state", lambda _: {"active_jobs": ["job"] if failure == "busy" else []}
    )
    monkeypatch.setattr(guard, "port_open", lambda _: False)
    monkeypatch.setattr(guard, "identity", lambda _: None if failure == "generation" else record)

    def run(argv, **kwargs):
        calls.append(argv)
        if (failure == "bootout" and "bootout" in argv) or (failure == "stop" and "stop" in argv):
            raise RuntimeError("partial command failure")

    monkeypatch.setattr(guard, "run_command", run)
    if failure:
        with pytest.raises(RuntimeError):
            guard.perform(cfg, state, {"action": "activate"})
    else:
        guard.perform(cfg, state, {"action": "activate"})
        assert state["phase"] == "idle"
    if failure in {"state", "busy", "generation"}:
        assert not calls and not state["activated"]
    if failure in {"bootout", "stop"}:
        assert state["admin_unloaded"] is True
    if failure == "stop":
        assert state["strata_stopped"] is True


def test_launch_and_stop_are_confined_to_the_configured_experiment(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()

    class Child:
        pid = 123

        def poll(self):
            return None

        def terminate(self):
            pytest.fail("unexpected terminate")

    seen = []

    def launch(argv, **kwargs):
        seen.append((argv, kwargs))
        return Child()

    monkeypatch.setattr(guard.subprocess, "Popen", launch)
    monkeypatch.setattr(guard, "_CHILDREN", [])
    monkeypatch.setattr(
        guard,
        "identity",
        lambda _: {
            "pid": 123,
            "created": 12.5,
            "command": ["python", str(tmp_path), "server.server"],
        },
    )
    monkeypatch.setattr(guard, "port_open", lambda _: False)
    monkeypatch.setattr(guard, "descendants", lambda value: value)
    monkeypatch.setattr(guard, "stop_records", lambda _: True)
    monkeypatch.setattr(guard, "experiment_present", lambda _: False)
    cfg = {"spike": str(tmp_path), "commands": {"model": ["approved-engine"]}}
    state = {"phase": "idle", "records": []}
    guard.perform(cfg, state, {"action": "launch", "name": "model"})
    assert state["phase"] == "serving" and state["active"] == "model"
    assert seen[0][0] == ["approved-engine"] and seen[0][1]["start_new_session"] is True
    with pytest.raises(RuntimeError):
        guard.perform(cfg, state, {"action": "launch", "name": "model"})
    guard.perform(cfg, state, {"action": "stop"})
    assert state["phase"] == "idle" and state["records"] == [] and state["active"] is None


@pytest.mark.parametrize("trigger", ["deadline", "resource", "request", "failure"])
def test_daemon_always_records_cleanup_after_abort(tmp_path, monkeypatch, trigger):
    import json
    import time
    from types import SimpleNamespace

    monkeypatch.setattr(guard, "_STOP", False)
    monkeypatch.setattr(guard, "_CHILDREN", [])
    monkeypatch.setattr(guard.signal, "signal", lambda *_: None)
    monkeypatch.setattr(guard.psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(guard, "descendants", lambda value: value)
    sample = {
        "available": 5 * 1024**3 if trigger == "resource" else 8 * 1024**3,
        "pressure": 1,
        "swap": 0,
    }

    def sampling(_):
        if trigger == "failure":
            raise RuntimeError("sampler failed")
        return sample

    monkeypatch.setattr(guard, "sample_resources", sampling)
    restored = []

    def restore(cfg, state):
        restored.append(dict(state))
        return "restored"

    monkeypatch.setattr(guard, "restore", restore)
    if trigger == "request":
        (tmp_path / "guard-command.json").write_text(
            json.dumps({"id": "restore-1", "action": "restore"})
        )
    cfg = {
        "spike": str(tmp_path),
        "collect_until": time.time() - 1 if trigger == "deadline" else time.time() + 60,
    }
    code = guard.daemon(cfg)
    state = json.loads((tmp_path / "guard-state.json").read_text())
    assert state["phase"] == "restored" and len(restored) == 1
    assert code == (1 if trigger == "failure" else 0)
    assert restored[0]["phase"] == "restoring"


def test_stubborn_owned_generation_blocks_service_restoration(monkeypatch):
    record = {"pid": 123, "created": 12.5, "command": ["owned-test"]}
    signals = []
    monkeypatch.setattr(guard, "identity", lambda _: record)
    monkeypatch.setattr(guard, "signal_process", lambda pid, sig: signals.append((pid, sig)))
    assert not guard.stop_records([record], grace_s=0)
    assert signals == [(123, guard.signal.SIGTERM), (123, guard.signal.SIGKILL)]


def test_unowned_experiment_listener_blocks_original_service_start(monkeypatch):
    calls = []
    monkeypatch.setattr(guard, "port_open", lambda port: port == 8086)
    monkeypatch.setattr(guard, "experiment_present", lambda _: False)
    monkeypatch.setattr(guard, "run_command", lambda argv, **kwargs: calls.append(argv))
    assert (
        guard.restore({"spike": "/private/spike"}, {"records": [], "activated": True})
        == "experiment_port_remains"
    )
    assert calls == []


@pytest.mark.parametrize("outcome", ["experiment_processes_remain", "external_state_changed", "raise"])
def test_daemon_records_failed_cleanup_without_claiming_restored(tmp_path, monkeypatch, outcome):
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(guard, "_STOP", False)
    monkeypatch.setattr(guard, "_CHILDREN", [])
    monkeypatch.setattr(guard.signal, "signal", lambda *_: None)
    monkeypatch.setattr(guard.psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(guard, "descendants", lambda value: value)
    monkeypatch.setattr(
        guard, "sample_resources", lambda _: {"available": 8 * 1024**3, "pressure": 1, "swap": 0}
    )

    def restore(cfg, state):
        if outcome == "raise":
            raise RuntimeError("original wrapper failed")
        return outcome

    monkeypatch.setattr(guard, "restore", restore)
    assert guard.daemon({"spike": str(tmp_path), "collect_until": 0}) == 1
    state = json.loads((tmp_path / "guard-state.json").read_text())
    assert state["phase"] == ("restore_failed" if outcome == "raise" else outcome)
    assert state["reason"] == "collection_deadline"
    if outcome == "raise":
        assert state["error"] == "RuntimeError:original wrapper failed"


def test_daemon_detects_new_operator_intent_and_never_restarts_services(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(guard, "_STOP", False)
    monkeypatch.setattr(guard, "_CHILDREN", [])
    monkeypatch.setattr(guard.signal, "signal", lambda *_: None)
    monkeypatch.setattr(guard.psutil, "swap_memory", lambda: SimpleNamespace(used=0))
    monkeypatch.setattr(guard, "descendants", lambda value: value)
    monkeypatch.setattr(guard.time, "sleep", lambda _: None)
    monkeypatch.setattr(guard, "preserved", lambda _: False)
    monkeypatch.setattr(guard, "port_open", lambda _: False)
    monkeypatch.setattr(guard, "experiment_present", lambda _: False)
    monkeypatch.setattr(
        guard, "sample_resources", lambda _: {"available": 8 * 1024**3, "pressure": 1, "swap": 0}
    )
    calls = []
    monkeypatch.setattr(guard, "run_command", lambda argv, **kwargs: calls.append(argv))

    def activate(cfg, state, request):
        state.update(activated=True, phase="idle", admin_unloaded=True, strata_stopped=True)

    monkeypatch.setattr(guard, "perform", activate)
    (tmp_path / "guard-command.json").write_text(
        json.dumps({"id": "activate-1", "action": "activate"})
    )
    assert guard.daemon({"spike": str(tmp_path), "collect_until": 1e20}) == 1
    state = json.loads((tmp_path / "guard-state.json").read_text())
    assert state["phase"] == state["reason"] == "external_state_changed"
    assert state["last_command"] == "activate-1"
    assert calls == []
