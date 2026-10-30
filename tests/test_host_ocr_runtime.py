"""Real subprocess evidence for bounded document worker execution and cleanup."""

from __future__ import annotations

import asyncio
import sys

import pytest

from .test_host_ocr_pipeline import load

WORKER = """
import json, sys, time
config=json.loads(sys.stdin.readline())
print(json.dumps({"event":"ready","warmup":True}),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    if request["media_type"] == "application/pdf":
        time.sleep(30)
    elif request["media_type"] == "image/jpeg":
        print(json.dumps({"event":"result","request_id":request["request_id"],"result":{"complete":False}}),flush=True)
    else:
        print(json.dumps({"event":"page","number":1,"request_id":request["request_id"]}),flush=True)
        print(json.dumps({"event":"result","request_id":request["request_id"],"result":{"complete":True,"pages":[{"number":1}]}}),flush=True)
"""


@pytest.fixture
def config(tmp_path):
    return {"root": str(tmp_path), "limits": {"max_output_bytes": 10000, "page_timeout_s": 0.05,
            "document_timeout_s": 0.15, "queue_depth": 2, "queue_timeout_s": 0.05}}


async def test_runtime_keeps_worker_loaded_and_preserves_protocol(config, tmp_path):
    module = load("runtime")
    runtime = module.Runtime(tmp_path, config, worker_argv=[sys.executable, "-u", "-c", WORKER])
    await runtime.start()
    pid = runtime.worker.pid
    assert runtime.ready
    result = await runtime.recognize("a" * 32, tmp_path / "input", "image/png")
    assert result["complete"] and runtime.worker.pid == pid and runtime.ready
    await runtime.close()
    assert runtime.worker is None


async def test_deadline_terminates_native_work_and_vlm_before_releasing_admission(config, tmp_path):
    module = load("runtime")
    stopped = []
    async def stop_vlm():
        stopped.append(True)
        return True
    runtime = module.Runtime(tmp_path, config, worker_argv=[sys.executable, "-u", "-c", WORKER], stop_vlm=stop_vlm)
    await runtime.start()
    worker = runtime.worker
    with pytest.raises(module.RuntimeError_) as error:
        await runtime.recognize("a" * 32, tmp_path / "input", "application/pdf")
    assert error.value.code == 504
    assert worker.returncode is not None and stopped == [True]
    assert not runtime.ready and not runtime.busy


async def test_failed_vlm_termination_freezes_runtime_admission(config, tmp_path):
    module = load("runtime")
    async def stop_vlm():
        return False
    runtime = module.Runtime(tmp_path, config, worker_argv=[sys.executable, "-u", "-c", WORKER], stop_vlm=stop_vlm)
    await runtime.start()
    with pytest.raises(module.RuntimeError_):
        await runtime.recognize("a" * 32, tmp_path / "input", "application/pdf")
    assert runtime.busy and not runtime.ready
    assert not await runtime.cancel("a" * 32)
    await runtime.close()


async def test_active_cancel_confirms_process_exit(config, tmp_path):
    module = load("runtime")
    runtime = module.Runtime(tmp_path, config, worker_argv=[sys.executable, "-u", "-c", WORKER])
    await runtime.start()
    task = asyncio.create_task(runtime.recognize("a" * 32, tmp_path / "input", "application/pdf"))
    while runtime.active != "a" * 32:
        await asyncio.sleep(0)
    assert await runtime.cancel("a" * 32)
    result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], module.RuntimeError_) and not runtime.ready
    await runtime.close()


async def test_failed_ownership_record_stops_the_new_direct_child(config, tmp_path, monkeypatch):
    module = load("runtime")
    runtime = module.Runtime(tmp_path, config, worker_argv=[sys.executable, "-u", "-c", WORKER])
    def fail_record(*args):
        raise OSError("record unavailable")
    monkeypatch.setattr(runtime.owned, "write_record", fail_record)
    with pytest.raises(module.RuntimeError_):
        await runtime.start()
    assert runtime.worker is None and not runtime.ready and not runtime.busy
