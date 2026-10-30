"""Preview-first OCR route operations preserve other deployments and credentials."""

import json

import httpx
import pytest

from local_llm_admin.ocr_route import OcrRouteManager, RouteError, route_template


def test_template_targets_only_ocr_with_pdf_and_no_ambiguous_retries():
    route = route_template("http://mac-mini.local:8082/v1", "SIDECAR_GATEWAY_API_KEY")
    assert route["model_name"] == "local-ocr"
    assert route["litellm_params"]["model"] == "openai/local-ocr"
    assert route["litellm_params"]["num_retries"] == 0
    assert route["litellm_params"]["max_retries"] == 0
    assert route["model_info"]["supports_pdf_input"] is True
    assert route["model_info"]["supports_streaming"] is False


@pytest.fixture
def server():
    unrelated = {"model_name": "local-qwen", "litellm_params": {"model": "openai/local-model"},
                 "model_info": {"id": "model-a", "db_model": True}}
    models = [unrelated]
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"data": models})
        body = json.loads(request.content)
        if request.url.path == "/model/new":
            models.append({**body, "model_info": {**body["model_info"], "id": "ocr-deployment", "db_model": True}})
            return httpx.Response(200, json={"model_info": {"id": "ocr-deployment"}})
        if request.url.path == "/model/delete":
            models[:] = [model for model in models if model["model_info"]["id"] != body["id"]]
            return httpx.Response(200, json={"success": True})
        if request.method == "PATCH":
            for model in models:
                if model["model_info"]["id"] == "ocr-deployment":
                    model["litellm_params"].update(body["litellm_params"])
                    model["model_info"].update(body["model_info"])
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404)
    manager = OcrRouteManager("http://litellm.example.com:4000", "test-admin-key",
                              transport=httpx.MockTransport(handle))
    return manager, models, calls, json.loads(json.dumps(unrelated))


def test_preview_performs_only_reads(server):
    manager, models, calls, original = server
    preview = manager.preview(route_template("http://mac-mini.local:8082/v1"))
    assert preview["action"] == "create" and calls == [("GET", "/model/info")]
    assert models == [original]


def test_create_snapshot_and_rollback_touch_only_requested_route(server, tmp_path):
    manager, models, calls, original = server
    snapshot = tmp_path / "rollback.json"
    desired = route_template("http://mac-mini.local:8082/v1")
    preview = manager.preview(desired)
    manager.apply(preview, snapshot)
    assert models[0] == original and len(models) == 2
    assert snapshot.stat().st_mode & 0o777 == 0o600
    assert json.loads(snapshot.read_text())["state"] == "applied"
    manager.rollback(snapshot)
    assert models == [original]
    assert [path for method, path in calls if method != "GET"] == ["/model/new", "/model/delete"]


def test_existing_opaque_route_requires_a_complete_private_rollback_definition(server):
    manager, models, _, _ = server
    models.append({"model_name": "local-ocr", "litellm_params": {"model": "openai/local-ocr"},
                   "model_info": {"id": "ocr-deployment", "db_model": True}})
    with pytest.raises(RouteError, match="rollback"):
        manager.preview(route_template("http://mac-mini.local:8082/v1"))


def test_recheck_prevents_overwriting_a_route_that_appeared_after_preview(server, tmp_path):
    manager, models, calls, _ = server
    desired = route_template("http://mac-mini.local:8082/v1")
    preview = manager.preview(desired)
    models.append({**desired, "model_info": {**desired["model_info"], "id": "other-ocr"}})
    with pytest.raises(RouteError, match="changed"):
        manager.apply(preview, tmp_path / "rollback.json")
    assert not any(method != "GET" for method, _ in calls)


@pytest.mark.parametrize("base", [
    "file:///tmp/service", "http://user:password@mac-mini.local:8082/v1",
    "http://mac-mini.local:8082/private", "http://mac-mini.local:8082/v1?x=1",
])
def test_invalid_api_base_is_not_registered(base):
    with pytest.raises(RouteError):
        route_template(base)


def test_update_and_rollback_restore_every_patched_parameter(server, tmp_path):
    manager, models, _, unrelated = server
    old = route_template("http://mac-mini.local:8082/v1")
    old["litellm_params"]["timeout"] = 180
    old["model_info"]["supports_pdf_input"] = False
    old["model_info"].update(id="ocr-deployment", db_model=True)
    models.append(json.loads(json.dumps(old)))
    preview = manager.preview(route_template("http://mac-mini.local:8082/v1"), rollback=old)
    snapshot = tmp_path / "rollback.json"
    manager.apply(preview, snapshot)
    assert models[1]["litellm_params"]["timeout"] == 300
    manager.rollback(snapshot)
    assert models == [unrelated, old]


def test_patch_cannot_add_fields_that_rollback_cannot_remove(server):
    manager, models, _, _ = server
    old = route_template("http://mac-mini.local:8082/v1")
    old["model_info"].pop("supports_pdf_input")
    old["model_info"].update(id="ocr-deployment", db_model=True)
    models.append(old)
    with pytest.raises(RouteError, match="rollback"):
        manager.preview(route_template("http://mac-mini.local:8082/v1"), rollback=old)


def test_ambiguous_write_is_not_retried_or_automatically_rolled_back(tmp_path):
    called = []
    def endpoint(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        called.append(request.method)
        raise httpx.ReadTimeout("management outcome unknown")
    manager = OcrRouteManager("http://litellm.example.com:4000", "test-key", transport=httpx.MockTransport(endpoint))
    preview = manager.preview(route_template("http://mac-mini.local:8082/v1"))
    snapshot = tmp_path / "receipt.json"
    with pytest.raises(RouteError):
        manager.apply(preview, snapshot)
    assert json.loads(snapshot.read_text())["state"] == "unknown" and called == ["POST"]
    with pytest.raises(RouteError):
        manager.rollback(snapshot)


@pytest.mark.parametrize("payload", [[], {"data": [None]}, {"data": "invalid"}])
def test_invalid_management_inventory_is_refused_without_writes(payload):
    calls = []
    def endpoint(request):
        calls.append(request.method)
        return httpx.Response(200, json=payload)
    manager = OcrRouteManager("http://litellm.example.com:4000", "test-key", transport=httpx.MockTransport(endpoint))
    with pytest.raises(RouteError):
        manager.preview(route_template("http://mac-mini.local:8082/v1"))
    assert calls == ["GET"]


def test_cli_preview_apply_and_rollback_never_print_admin_credentials(server, tmp_path, monkeypatch, capsys):
    from local_llm_admin import ocr_route
    manager, models, calls, unrelated = server
    monkeypatch.setattr(ocr_route, "OcrRouteManager", lambda *args: manager)
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.example.com:4000")
    monkeypatch.setenv("LITELLM_ADMIN_KEY", "private-admin-token")
    base = ["route", "--gateway-base", "http://mac-mini.local:8082/v1"]
    monkeypatch.setattr("sys.argv", base)
    assert ocr_route.main() == 0 and not any(method != "GET" for method, _ in calls)
    snapshot = tmp_path / "snapshot.json"
    monkeypatch.setattr("sys.argv", [*base, "--apply", "--snapshot", str(snapshot)])
    assert ocr_route.main() == 0 and len(models) == 2
    monkeypatch.setattr("sys.argv", ["route", "--rollback", "--snapshot", str(snapshot)])
    assert ocr_route.main() == 0 and models == [unrelated]
    assert "private-admin-token" not in capsys.readouterr().out


def test_rollback_refuses_receipt_permissions_and_live_drift(server, tmp_path):
    manager, models, _, _ = server
    snapshot = tmp_path / "receipt.json"
    manager.apply(manager.preview(route_template("http://mac-mini.local:8082/v1")), snapshot)
    snapshot.chmod(0o644)
    with pytest.raises(RouteError, match="private"):
        manager.rollback(snapshot)
    snapshot.chmod(0o600)
    models[1]["litellm_params"]["timeout"] = 1
    with pytest.raises(RouteError, match="changed"):
        manager.rollback(snapshot)


def test_local_key_resolution_applies_only_at_the_network_boundary(tmp_path):
    sent = []
    models = []
    def endpoint(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": models})
        body = json.loads(request.content)
        sent.append(body)
        models.append({**body, "model_info": {"id": "ocr-deployment", "db_model": True}})
        return httpx.Response(200, json={"model_info": {"id": "ocr-deployment"}})
    manager = OcrRouteManager("http://litellm.example.com:4000", "test-key",
        transport=httpx.MockTransport(endpoint), gateway_key=("SIDECAR_GATEWAY_API_KEY", "dsk_test_secret"))
    preview = manager.preview(route_template("http://mac-mini.local:8082/v1"))
    assert sent == [] and preview["desired"]["litellm_params"]["api_key"].startswith("os.environ/")
    manager.apply(preview, tmp_path / "receipt.json")
    assert sent[0]["litellm_params"]["api_key"] == "dsk_test_secret"
    assert preview["desired"]["litellm_params"]["api_key"].startswith("os.environ/")
