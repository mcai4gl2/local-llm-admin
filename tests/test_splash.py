"""Private Splash profiles and pinned protocol contracts (no installed runtime)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from local_llm_admin.splash_profiles import (
    RUNTIME_SHA256,
    ProfileError,
    load_profiles,
    private_text,
    read_env,
    read_ownership,
)


def catalog(root: Path) -> tuple[Path, dict]:
    assets = root / "assets"
    profiles = {}
    for cohort in ("stock-8bit", "modified-4bit"):
        for name in (cohort, "draft"):
            asset = assets / name
            asset.mkdir(parents=True, exist_ok=True)
            (asset / "config.json").write_text("{}")
            (asset / "model.safetensors").write_bytes(b"test weights")
        prepared = root / "prepared" / (cohort + "-32k")
        (prepared / "tokenizer").mkdir(parents=True)
        (prepared / "target").mkdir()
        (prepared / "draft").mkdir()
        receipt = root / "etc/receipts" / (cohort + "-32k.json")
        receipt.parent.mkdir(parents=True, exist_ok=True)
        write(
            receipt,
            {
                "runtime_sha256": RUNTIME_SHA256,
                "prepared": str(prepared),
                "target_sha256": "a" * 64,
                "draft_sha256": "b" * 64,
                "target_revision": "a" * 40,
                "draft_revision": "b" * 40,
            },
        )
        profiles[cohort + "-32k"] = {
            "label": cohort + " · 32K",
            "cohort": cohort,
            "model": "example/" + cohort,
            "aliases": ["local-qwen"],
            "target": {"path": str(assets / cohort), "revision": "a" * 40, "sha256": "a" * 64},
            "draft": {"path": str(assets / "draft"), "revision": "b" * 40, "sha256": "b" * 64},
            "prepared": cohort + "-32k",
            "max_context": 32768,
            "memory_gib": 40,
            "kv_format": "bf16",
            "state_format": "fp32",
            "reasoning": True,
        }
    doc = {
        "schema_version": 1,
        "runtime_version": "1.4.0",
        "runtime_sha256": RUNTIME_SHA256,
        "asset_roots": [str(assets)],
        "profiles": profiles,
    }
    path = root / "etc" / "profiles.json"
    path.parent.mkdir(exist_ok=True)
    write(path, doc)
    return (path, doc)


def write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc))
    path.chmod(384)


def test_profiles_keep_asset_cohorts_separate_and_stable_fingerprint(tmp_path: Path) -> None:
    path, doc = catalog(tmp_path)
    profiles = load_profiles(path, tmp_path)
    a, b = (profiles["stock-8bit-32k"], profiles["modified-4bit-32k"])
    assert a.cohort == "stock-8bit" and b.cohort == "modified-4bit"
    assert a.target.path != b.target.path and a.prepared != b.prepared
    assert a.max_context == 32768 and a.reasoning is True
    assert a.fingerprint != b.fingerprint
    assert "assets" not in repr(a)
    assert load_profiles(path, tmp_path)["stock-8bit-32k"].fingerprint == a.fingerprint
    doc["profiles"]["stock-8bit-32k"]["aliases"].append("app-a")
    write(path, doc)
    assert load_profiles(path, tmp_path)["stock-8bit-32k"].fingerprint != a.fingerprint
    with pytest.raises(TypeError):
        profiles["bad"] = a


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_context", True),
        ("max_context", 65536),
        ("memory_gib", 0),
        ("memory_gib", 257),
        ("memory_gib", "40"),
        ("kv_format", "int8"),
        ("state_format", "bf16"),
        ("reasoning", False),
        ("prepared", "../escape"),
        ("prepared", "/tmp/prepared"),
        ("model", "--bad"),
        ("aliases", []),
        ("aliases", ["x", "x"]),
        ("aliases", ["/Volumes/Models/models/active"]),
        ("aliases", ["x?y"]),
        ("aliases", ["bad name"]),
        ("label", "<script>"),
        ("extra_flags", ["--unsafe"]),
    ],
)
def test_reject_invalid_profile(tmp_path: Path, field: str, value: Any) -> None:
    path, doc = catalog(tmp_path)
    doc["profiles"]["stock-8bit-32k"][field] = value
    write(path, doc)
    with pytest.raises(ProfileError):
        load_profiles(path, tmp_path)


@pytest.mark.parametrize(
    "mutation", ["pin", "digest", "unknown", "missing", "id", "revision", "asset", "prepared"]
)
def test_reject_invalid_catalog_and_missing_assets(tmp_path: Path, mutation: str) -> None:
    path, doc = catalog(tmp_path)
    p = doc["profiles"]["stock-8bit-32k"]
    if mutation == "pin":
        doc["runtime_version"] = "latest"
    if mutation == "digest":
        doc["runtime_sha256"] = "0" * 64
    if mutation == "unknown":
        doc["shell"] = "echo bad"
    if mutation == "missing":
        del p["target"]
    if mutation == "id":
        doc["profiles"]["../bad"] = doc["profiles"].pop("stock-8bit-32k")
    if mutation == "revision":
        p["draft"]["revision"] = "main"
    if mutation == "asset":
        p["target"]["path"] = str(tmp_path / "foreign")
    if mutation == "prepared":
        (tmp_path / "prepared/stock-8bit-32k/tokenizer").rmdir()
    write(path, doc)
    with pytest.raises(ProfileError):
        load_profiles(path, tmp_path)


def test_reject_symlink_escape_duplicate_json_and_public_modes(tmp_path: Path) -> None:
    path, doc = catalog(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "assets/escape"
    link.symlink_to(outside, target_is_directory=True)
    doc["profiles"]["stock-8bit-32k"]["target"]["path"] = str(link)
    write(path, doc)
    with pytest.raises(ProfileError):
        load_profiles(path, tmp_path)
    path.write_text('{"schema_version": 1, "schema_version": 1}')
    with pytest.raises(ProfileError):
        load_profiles(path, tmp_path)
    path.chmod(420)
    with pytest.raises(ProfileError):
        load_profiles(path, tmp_path)
    path.unlink()
    with pytest.raises(ProfileError):
        load_profiles(path, tmp_path)


def test_env_is_parsed_never_sourced_and_secrets_are_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "splash.env"
    key = "s" * 40
    path.write_text(
        f"SPLASH_API_KEY={key}\nSPLASH_HOST=0.0.0.0\nSPLASH_ALLOWED_HOSTS=mac-mini.local,llm.example.com\n"
    )
    path.chmod(384)
    monkeypatch.setenv("SPLASH_API_KEY", "ambient-not-used")
    env = read_env(path)
    assert env.api_key == key and env.host == "0.0.0.0"
    assert env.allowed_hosts == ("mac-mini.local", "llm.example.com")
    assert key not in repr(env)
    for text in [
        "SPLASH_API_KEY=$(touch bad)",
        "export SPLASH_HOST=127.0.0.1",
        "SPLASH_HOST=127.0.0.1\nSPLASH_HOST=0.0.0.0",
        "UNKNOWN=x",
        "SPLASH_API_KEY=x",
        "SPLASH_ALLOWED_HOSTS=bad/host",
    ]:
        path.write_text(text)
        with pytest.raises(ProfileError):
            read_env(path)
    path.chmod(420)
    with pytest.raises(ProfileError):
        read_env(path)


def test_profile_data_does_not_leak_into_errors(tmp_path: Path) -> None:
    path, doc = catalog(tmp_path)
    secret = "private-input-do-not-echo"
    doc["profiles"]["stock-8bit-32k"]["model"] = secret + "\n"
    write(path, doc)
    with pytest.raises(ProfileError) as exc:
        load_profiles(path, tmp_path)
    assert secret not in str(exc.value)


def test_original_capture_provenance_is_retained() -> None:
    fixture = Path(__file__).parent / "fixtures/splash-phase0/native-probes.json"
    doc = json.loads(fixture.read_text())["captures"]["stock-8bit-splash"]
    assert doc["ready_body"] == {"status": "ready"}
    assert doc["status_projection"]["maximum_context_tokens"] == 32768


def test_deployed_host_profile_reader_matches_admin_contract() -> None:
    root = Path(__file__).resolve().parent.parent
    assert (root / "host/splash/lib/profiles.py").read_bytes() == (
        root / "src/local_llm_admin/splash_profiles.py"
    ).read_bytes()


@pytest.mark.parametrize(
    "mutation",
    [
        "bad-json",
        "list",
        "schema-bool",
        "roots-empty",
        "root-relative",
        "root-broad",
        "profiles-empty",
        "bad-label",
        "bad-cohort",
        "model",
        "alias-type",
        "asset-revision",
        "asset-hash",
        "asset-missing",
        "receipt-missing",
        "receipt-mismatch",
        "shared-prepared",
        "prepared-escape",
        "env-missing",
    ],
)
def test_remaining_private_profile_refusals(tmp_path: Path, mutation: str) -> None:
    path, doc = catalog(tmp_path)
    profile = doc["profiles"]["stock-8bit-32k"]
    if mutation == "bad-json":
        path.write_text("{")
    elif mutation == "list":
        write(path, [])
    else:
        if mutation == "schema-bool":
            doc["schema_version"] = True
        if mutation == "roots-empty":
            doc["asset_roots"] = []
        if mutation == "root-relative":
            doc["asset_roots"] = ["relative"]
        if mutation == "root-broad":
            doc["asset_roots"] = ["/"]
        if mutation == "profiles-empty":
            doc["profiles"] = {}
        if mutation == "bad-label":
            profile["label"] = ""
        if mutation == "bad-cohort":
            profile["cohort"] = "automatic"
        if mutation == "model":
            profile["model"] = "single-name"
        if mutation == "alias-type":
            profile["aliases"] = "local-qwen"
        if mutation == "asset-revision":
            profile["target"]["revision"] = "main"
        if mutation == "asset-hash":
            profile["target"]["sha256"] = "missing"
        if mutation == "asset-missing":
            (tmp_path / "assets/stock-8bit/model.safetensors").unlink()
        if mutation == "receipt-missing":
            (tmp_path / "etc/receipts/stock-8bit-32k.json").unlink()
        if mutation == "receipt-mismatch":
            write(tmp_path / "etc/receipts/stock-8bit-32k.json", {"wrong": True})
        if mutation == "shared-prepared":
            profile["prepared"] = "modified-4bit-32k"
        if mutation == "prepared-escape":
            (tmp_path / "prepared/escape").symlink_to(tmp_path / "assets")
            profile["prepared"] = "escape"
        if mutation == "env-missing":
            with pytest.raises(ProfileError):
                read_env(tmp_path / "missing.env")
            return
        write(path, doc)
    with pytest.raises(ProfileError):
        load_profiles(path, tmp_path)


def test_inventory_can_be_read_without_loading_or_hashing_assets(tmp_path: Path) -> None:
    path, _ = catalog(tmp_path)
    (tmp_path / "assets/stock-8bit/model.safetensors").unlink()
    assert (
        load_profiles(path, tmp_path, require_assets=False)["stock-8bit-32k"].max_context == 32768
    )


def test_private_symlink_and_oversize_files_are_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text("x")
    real.chmod(384)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ProfileError):
        private_text(link)
    real.write_bytes(b"x" * 1048577)
    with pytest.raises(ProfileError):
        private_text(real)
    real.write_bytes(b"\xff")
    with pytest.raises(ProfileError):
        private_text(real)


def test_env_defaults_ignore_ambient_values_and_accept_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "splash.env"
    path.write_text("# private\n\nSPLASH_API_KEY=" + "k" * 40 + "\n")
    path.chmod(384)
    monkeypatch.setenv("SPLASH_HOST", "0.0.0.0")
    assert read_env(path).host == "127.0.0.1"
    assert read_env(path).allowed_hosts == ()
    path.write_text("SPLASH_API_KEY=" + "k" * 40 + "\nSPLASH_HOST=invalid")
    with pytest.raises(ProfileError):
        read_env(path)


def test_public_projection_shared_prepared_and_host_validation(tmp_path: Path) -> None:
    path, doc = catalog(tmp_path)
    public = load_profiles(path, tmp_path)["stock-8bit-32k"].public()
    assert public["cohort"] == "stock-8bit"
    assert "target" not in public and "prepared" not in public
    doc["profiles"]["stock-8bit-32k"]["prepared"] = "modified-4bit-32k"
    write(path, doc)
    with pytest.raises(ProfileError):
        load_profiles(path, tmp_path, require_assets=False)
    env = tmp_path / "splash.env"
    env.write_text("SPLASH_API_KEY=" + "k" * 40 + "\nSPLASH_ALLOWED_HOSTS=bad/host")
    env.chmod(384)
    with pytest.raises(ProfileError):
        read_env(env)


def test_profile_fingerprint_detects_prepared_selection_retarget(tmp_path: Path) -> None:
    path, doc = catalog(tmp_path)
    selection = tmp_path / "prepared/stock-8bit-32k"
    original = tmp_path / "prepared/assembly-a"
    selection.rename(original)
    selection.symlink_to(original, target_is_directory=True)
    receipt = tmp_path / "etc/receipts/stock-8bit-32k.json"
    audit = json.loads(receipt.read_text())
    audit["prepared"] = str(original)
    write(receipt, audit)
    before = load_profiles(path, tmp_path)["stock-8bit-32k"].fingerprint
    replacement = tmp_path / "prepared/assembly-b"
    (replacement / "tokenizer").mkdir(parents=True)
    selection.unlink()
    selection.symlink_to(replacement, target_is_directory=True)
    audit["prepared"] = str(replacement)
    write(receipt, audit)
    assert load_profiles(path, tmp_path)["stock-8bit-32k"].fingerprint != before


@pytest.fixture(autouse=True, params=("admin", "host"))
def profile_contract_copy(request: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param == "host":
        import importlib.util
        import sys

        path = Path(__file__).resolve().parents[1] / "host/splash/lib/profiles.py"
        spec = importlib.util.spec_from_file_location("splash_profile_contract_test", path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
        for name in ("load_profiles", "read_env", "private_text", "read_ownership", "ProfileError"):
            monkeypatch.setitem(globals(), name, getattr(module, name))


@pytest.mark.parametrize("mutation", ["link", "schema", "metadata", "generation"])
def test_shared_record_reader_refuses_untrusted_records(tmp_path: Path, mutation: str) -> None:
    run = tmp_path / "run"
    run.mkdir()
    record = run / "splash.json"
    if mutation == "link":
        record.symlink_to(tmp_path / "missing")
    else:
        member = {"pid": 100, "started": 1.0, "executable": "/runtime/python", "role": "controller"}
        doc = {
            "version": 1,
            "profile": "stock-8bit-32k",
            "fingerprint": "f" * 20,
            "model": "example/model",
            "prepared": str(tmp_path / "prepared/model"),
            "controller": member,
            "native": [],
        }
        if mutation == "schema":
            doc["version"] = 2
        elif mutation == "metadata":
            doc["fingerprint"] = "bad"
        else:
            member["started"] = False
        write(record, doc)
    with pytest.raises(ProfileError):
        read_ownership(tmp_path)
