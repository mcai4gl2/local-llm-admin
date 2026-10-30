"""Offline release archives contain pinned code/config, never credentials or private env files."""

import json
import tarfile

from local_llm_admin.config import Settings
from local_llm_admin.ocr_release import build_bundle


def test_bundle_is_private_and_contains_hashes_without_admin_secrets(tmp_path):
    settings = Settings(ocr_enabled=True, ocr_port=8097, ocr_vlm_port=8098,
                        ocr_footprint_gb=12, ocr_allowed_mlx="small",
                        litellm_admin_key="private-test-secret")
    path = tmp_path / "ocr.tar.gz"
    summary = build_bundle(settings, path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert summary["catalog_hash"]
    with tarfile.open(path) as archive:
        names = archive.getnames()
        assert "etc/resident-services.json" in names
        assert "ocr/etc/runtime.json" in names
        assert "ocr/app/provision.py" in names
        assert "environment/ocr-app.txt" in names
        assert not any(".env" == name or name.endswith("source.env") for name in names)
        assert all(member.uid == 0 and member.gid == 0 for member in archive.getmembers())
        for member in archive.getmembers():
            assert b"private-test-secret" not in archive.extractfile(member).read()
        manifest = json.load(archive.extractfile("ocr-release.json"))
        assert manifest["qualified"] is False and len(manifest["files"]) > 15
