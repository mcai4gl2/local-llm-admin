from pathlib import Path

import pytest

from local_llm_admin import mlx

FIX = Path(__file__).parent / "fixtures"


def test_parse_active_env_handles_quotes_comments_and_export():
    text = '# comment\nMLX_ACTIVE_NAME=qwen3.8-27b\nexport MLX_ACTIVE_PATH="/Volumes/Models/models/Q"\nBAD LINE\n'
    assert mlx.parse_active_env(text) == {
        "MLX_ACTIVE_NAME": "qwen3.8-27b",
        "MLX_ACTIVE_PATH": "/Volumes/Models/models/Q",
    }


def test_parse_model_list_marks_selected_and_reads_dir():
    text = (
        "  gemma-3   /Volumes/Models/models/gemma\n* qwen3.8-27b\t/Volumes/Models/models/Qwen\n\n"
    )
    entries = mlx.parse_model_list(text)
    assert [(e.name, e.dir, e.selected) for e in entries] == [
        ("gemma-3", "/Volumes/Models/models/gemma", False),
        ("qwen3.8-27b", "/Volumes/Models/models/Qwen", True),
    ]


def test_parse_model_list_skips_invalid_names_and_headers():
    text = "Registered models:\n* ok-name /x\nbad;name /y\n"
    assert [e.name for e in mlx.parse_model_list(text)] == ["ok-name"]


@pytest.mark.parametrize(
    ("text", "running", "pid"),
    [
        ("mlx-server running (pid 4242) model=qwen", True, 4242),
        ("running pid=77", True, 77),
        ("mlx-server stopped", False, None),
        ("not running", False, None),
    ],
)
def test_parse_status(text, running, pid):
    st = mlx.parse_status(text)
    assert (st.running, st.pid) == (running, pid)


def test_parse_server_log_reports_last_rates_and_in_flight():
    lines = (FIX / "mlx-server.log").read_text().splitlines()
    stats = mlx.parse_server_log(lines)
    assert stats.last_prefill_rate == 410.0
    assert stats.last_decode_rate == 17.8
    assert stats.last_generated_tokens == 150
    assert stats.last_request_at == "2026-09-28 10:00:00"
    assert stats.in_flight is True


def test_parse_server_log_request_done_after_decode_or_cancel():
    lines = [
        "2026-09-28 10:00:00,000 INFO Request started id=x",
        "2026-09-28 10:00:02,000 INFO Generation cancelled id=x",
    ]
    assert mlx.parse_server_log(lines).in_flight is False
    lines.append("2026-09-28 10:01:00,000 INFO Request started id=y")
    lines.append("2026-09-28 10:01:09,000 ERROR Request failed id=y")
    assert mlx.parse_server_log(lines).in_flight is False


def test_parse_server_log_empty():
    stats = mlx.parse_server_log([])
    assert stats.last_decode_rate is None and stats.in_flight is False


def test_parse_start_markers():
    lines = (FIX / "mlx-server.log").read_text().splitlines()
    assert mlx.parse_start_markers(lines) == [
        ("2026-09-20 09:00:01", "Qwen3.8-27B-4bit"),
        ("2026-09-25 12:00:00", "qwen3.8-27b"),
    ]


def test_summarize_model_config_reads_nested_text_config():
    cfg = {
        "architectures": ["Qwen3ForCausalLM"],
        "quantization": {"bits": 4, "group_size": 64},
        "text_config": {"max_position_embeddings": 262144},
    }
    assert mlx.summarize_model_config(cfg) == {
        "architecture": "Qwen3ForCausalLM",
        "quant_bits": 4,
        "max_position_embeddings": 262144,
    }


def test_summarize_model_config_falls_back_to_model_type():
    assert mlx.summarize_model_config({"model_type": "gemma3"}) == {
        "architecture": "gemma3",
        "quant_bits": None,
        "max_position_embeddings": None,
    }


@pytest.mark.parametrize("name", ["qwen3.8-27b", "A_b.C-1"])
def test_validate_name_accepts(name):
    assert mlx.validate_name(name) == name


@pytest.mark.parametrize("name", ["", "a b", "x;rm -rf /", "$(id)", "../x", "a/b", "-rf", "é"])
def test_validate_name_rejects(name):
    with pytest.raises(ValueError):
        mlx.validate_name(name)


def test_build_command_uses_fixed_argv():
    b = Path("/Volumes/Models/bin/mlx-model")
    assert mlx.build_command(b, "switch", "qwen") == [str(b), "switch", "qwen"]
    assert mlx.build_command(b, "start", "qwen") == [str(b), "start", "qwen"]
    assert mlx.build_command(b, "start", None) == [str(b), "start"]
    assert mlx.build_command(b, "stop", None) == [str(b), "stop"]
    assert mlx.build_command(b, "list", None) == [str(b), "list"]
    assert mlx.build_command(b, "status", None) == [str(b), "status"]
    assert mlx.build_command(b, "select", "qwen") == [str(b), "select", "qwen"]
    with pytest.raises(ValueError):
        mlx.build_command(b, "select", None)


def test_build_command_rejects_bad_action_and_names():
    b = Path("/x/mlx-model")
    with pytest.raises(ValueError, match="unsupported action"):
        mlx.build_command(b, "remove", "x")
    with pytest.raises(ValueError):
        mlx.build_command(b, "switch", None)
    with pytest.raises(ValueError):
        mlx.build_command(b, "stop", "x")
    with pytest.raises(ValueError):
        mlx.build_command(b, "switch", "x;y")


@pytest.mark.parametrize(
    ("kw", "state"),
    [
        (dict(drive_mounted=False), "drive-missing"),
        (dict(job_running=True), "switching"),
        (dict(process_running=False, chat_ui=True), "chat-ui"),
        (dict(process_running=False), "stopped"),
        (dict(process_running=True, health=None), "loading"),
        (dict(process_running=True, health={"status": "healthy", "loaded_model": "m"}), "serving"),
        (dict(process_running=True, health={"status": "loading"}), "loading"),
        (dict(process_running=True, health={"status": "error"}), "error"),
        (dict(process_running=False, health={"status": "healthy", "loaded_model": "m"}), "serving"),
    ],
)
def test_resolve_state(kw, state):
    base = dict(
        drive_mounted=True, job_running=False, process_running=False, chat_ui=False, health=None
    )
    base.update(kw)
    assert mlx.resolve_state(**base) == state


# --- recorded on the Mac mini, 2026-09-29 (tests/fixtures/mac) ---------------

MAC = FIX / "mac"


def test_recorded_list():
    entries = mlx.parse_model_list((MAC / "mlx-model-list.txt").read_text())
    assert [(e.name, e.selected) for e in entries] == [
        ("qwen3.8-27b", True),
        ("qwen3.8-27b-8bit", False),
        ("gemma-4-26b-a4b", False),
    ]
    assert entries[0].dir == "/Volumes/Models/models/Qwen3.8-27B-4bit"


def test_recorded_status_running_and_stopped():
    st = mlx.parse_status((MAC / "mlx-model-status-running.txt").read_text())
    assert (st.running, st.pid) == (True, 7540)
    st = mlx.parse_status((MAC / "mlx-model-status-stopped.txt").read_text())
    assert (st.running, st.pid) == (False, None)


def test_status_uses_the_process_line_only():
    st = mlx.parse_status("process: running pid 12\nnote: previously stopped\n")
    assert (st.running, st.pid) == (True, 12)
    st = mlx.parse_status('process: stopped\nhealth: {"status":"running","pid":5}\n')
    assert (st.running, st.pid) == (False, None)


def test_recorded_server_log():
    stats = mlx.parse_server_log((MAC / "mlx-server.log").read_text().splitlines())
    assert stats.last_prefill_rate == 389.2
    assert stats.last_decode_rate == 17.5
    assert stats.last_generated_tokens == 10
    assert stats.last_ttft_s == 5.678
    assert stats.last_request_at == "2026-09-29 06:34:23"
    assert stats.in_flight is False and stats.in_flight_count == 0


def test_in_flight_counter_is_authoritative_with_continuous_batching():
    lines = [
        "2026-09-29 06:00:00,000 - INFO - Request started: endpoint=/chat/completions stream=True in_flight=1",
        "2026-09-29 06:00:00,100 - INFO - Request started: endpoint=/chat/completions stream=True in_flight=2",
        "2026-09-29 06:00:05,000 - INFO - Decode completed: request=a generated_tokens=3 elapsed=1s rate=17.0 tok/s",
        "2026-09-29 06:00:05,001 - INFO - Request completed: endpoint=/chat/completions in_flight=1",
    ]
    stats = mlx.parse_server_log(lines)
    assert stats.in_flight is True and stats.in_flight_count == 1
    lines.append(
        "2026-09-29 06:00:09,000 - WARNING - Request failed: error=stream_closed_before_completion in_flight=0"
    )
    assert mlx.parse_server_log(lines).in_flight_count == 0


@pytest.mark.parametrize(("listeners", "args", "expected"), [
    ([], [], None),
    ([(None, 8080)], ["mlx_vlm.server"], None),
    ([(41, 8080), (42, 8080)], ["mlx_vlm.server"], None),
    ([(41, 8080)], ["/opt/venv/bin/mlx_vlm.server"], 41),
    ([(41, 8080)], ["python", "-m", "mlx_vlm.chat_ui"], 41),
    ([(41, 8080)], ["python", "-m", "mlx_lm.server"], 41),
    ([(41, 9999)], ["mlx_vlm.server"], None),
    ([(41, 8080)], ["unrelated-server"], None),
    ([(41, 8080)], ["python", "-m", "unrelated"], None),
    ([(41, 8080)], ["python", "unrelated.py", "mlx_vlm.server"], None),
])
def test_listener_identity_requires_one_recognized_owner(monkeypatch, listeners, args, expected):
    from types import SimpleNamespace

    import psutil
    connections = [SimpleNamespace(pid=pid, status=psutil.CONN_LISTEN,
                                  laddr=SimpleNamespace(port=port)) for pid, port in listeners]
    monkeypatch.setattr(psutil, "net_connections", lambda kind: connections)
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(cmdline=lambda: args))
    assert mlx.MlxClient.owned_listener_pid(8080) == expected


def test_listener_identity_is_unknown_when_process_visibility_is_denied(monkeypatch):
    import psutil
    def denied(kind):
        raise psutil.AccessDenied()
    monkeypatch.setattr(psutil, "net_connections", denied)
    assert mlx.MlxClient.owned_listener_pid(8080) is None


def test_chat_ui_owner_can_be_read_without_global_socket_permission(monkeypatch):
    from types import SimpleNamespace

    import psutil
    def denied(kind):
        raise psutil.AccessDenied()
    connection = SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=7860))
    owner = SimpleNamespace(pid=42, info={"cmdline": ["python", "-m", "mlx_vlm.chat_ui"]},
                            net_connections=lambda kind: [connection])
    monkeypatch.setattr(psutil, "net_connections", denied)
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter([owner]))
    assert mlx.MlxClient.owned_listener_pid(7860) == 42


@pytest.mark.parametrize("scenario", ("none", "not_listening", "denied", "ambiguous", "iteration_denied"))
def test_restricted_chat_ui_ownership_remains_unknown_when_unproven(monkeypatch, scenario):
    from types import SimpleNamespace

    import psutil
    def denied(*args, **kwargs):
        raise psutil.AccessDenied()
    connection = SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=7860))
    def owner(pid):
        return SimpleNamespace(
            pid=pid, info={"cmdline": ["mlx_vlm.chat_ui"]},
            net_connections=denied if scenario == "denied" else
                lambda kind: [] if scenario == "not_listening" else [connection],
        )
    unrelated = SimpleNamespace(pid=50, info={"cmdline": ["unrelated-server"]})
    processes = [unrelated] if scenario == "none" else [owner(42), owner(43)] if scenario == "ambiguous" else [owner(42)]
    monkeypatch.setattr(psutil, "net_connections", denied)
    monkeypatch.setattr(psutil, "process_iter", denied if scenario == "iteration_denied"
                        else lambda attrs: iter(processes))
    assert mlx.MlxClient.owned_listener_pid(7860) is None
