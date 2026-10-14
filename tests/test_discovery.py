import json
import os
import time
from pathlib import Path

import pytest

from local_llm_admin import mlx

NOW = time.time()


def _model(
    root: Path,
    name: str,
    shards: dict[str, int],
    total_size: int | None,
    *,
    age_s: float = 3600,
    write: dict[str, int] | None = None,
    config: bool = True,
    index: bool = True,
) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if config:
        (d / "config.json").write_text(
            json.dumps({"model_type": "qwen3", "quantization": {"bits": 4}})
        )
    if index:
        meta = {"total_size": total_size} if total_size is not None else {}
        weight_map = {f"t{i}": f for i, f in enumerate(shards)}
        (d / "model.safetensors.index.json").write_text(
            json.dumps({"metadata": meta, "weight_map": weight_map})
        )
    for f, size in (write if write is not None else shards).items():
        with open(d / f, "wb") as fh:
            fh.truncate(size)  # sparse: size without disk use
    old = NOW - age_s
    for p in d.iterdir():
        os.utime(p, (old, old))
    return d


def test_complete_when_all_shards_reach_total_size(tmp_path):
    d = _model(tmp_path, "M", {"a.safetensors": 600, "b.safetensors": 410}, 1000)
    st = mlx.weights_state(d, now=NOW)
    assert st["state"] == "complete"
    assert (st["present_bytes"], st["expected_bytes"], st["progress"]) == (1010, 1000, 1.0)
    assert st["missing_shards"] == 0


def test_missing_shard_recent_is_downloading(tmp_path):
    d = _model(
        tmp_path,
        "M",
        {"a.safetensors": 600, "b.safetensors": 410},
        1000,
        write={"a.safetensors": 600},
        age_s=10,
    )
    st = mlx.weights_state(d, now=NOW)
    assert st["state"] == "downloading" and st["missing_shards"] == 1
    assert st["progress"] == 0.6


def test_missing_shard_stale_is_incomplete(tmp_path):
    d = _model(
        tmp_path,
        "M",
        {"a.safetensors": 600, "b.safetensors": 410},
        1000,
        write={"a.safetensors": 600},
        age_s=3600,
    )
    assert mlx.weights_state(d, now=NOW)["state"] == "incomplete"


def test_partial_last_shard_recent_is_downloading(tmp_path):
    # curl -C - writes straight to the final name: the file exists but is short
    d = _model(
        tmp_path,
        "M",
        {"a.safetensors": 600, "b.safetensors": 410},
        1000,
        write={"a.safetensors": 600, "b.safetensors": 100},
        age_s=5,
    )
    st = mlx.weights_state(d, now=NOW)
    assert st["state"] == "downloading" and st["missing_shards"] == 0 and st["progress"] == 0.7


def test_single_file_without_index(tmp_path):
    d = _model(tmp_path, "M", {"model.safetensors": 500}, None, index=False)
    st = mlx.weights_state(d, now=NOW)
    assert st["state"] == "complete" and st["expected_bytes"] is None and st["progress"] is None


def test_no_weights(tmp_path):
    d = _model(tmp_path, "M", {}, None, index=False)
    assert mlx.weights_state(d, now=NOW)["state"] == "no-weights"


def test_unreadable_index_is_unknown(tmp_path):
    d = _model(tmp_path, "M", {"a.safetensors": 5}, 5)
    (d / "model.safetensors.index.json").write_text("{broken")
    assert mlx.weights_state(d, now=NOW)["state"] == "unknown"


def test_index_without_total_size_uses_shard_presence(tmp_path):
    d = _model(tmp_path, "M", {"a.safetensors": 5, "b.safetensors": 5}, None)
    assert mlx.weights_state(d, now=NOW)["state"] == "complete"


@pytest.mark.parametrize(
    ("dirname", "name"),
    [
        (
            "Qwen3.6-35B-A3B-MLX-4bit",
            "qwen3.6-35b-a3b-mlx-4bit",
        ),
        ("gemma 4 (test)", "gemma-4-test"),
        ("---", "model"),
    ],
)
def test_suggest_name(dirname, name):
    assert mlx.suggest_name(dirname) == name
    mlx.validate_name(mlx.suggest_name(dirname))


def test_discover_models_lists_dirs_marks_registered_and_skips_links(tmp_path):
    root = tmp_path / "models"
    a = _model(root, "Qwen-A", {"a.safetensors": 10}, 10)
    _model(root, "gemma-B", {"a.safetensors": 10}, 20, write={"a.safetensors": 5}, age_s=1)
    (root / "active").symlink_to(a)
    (root / ".DS_Store").write_text("")
    (root / ".hidden").mkdir()
    found = mlx.discover_models(root, registered={str(a.resolve()): "qwen-a"}, now=NOW)
    assert [f["dir_name"] for f in found] == ["gemma-B", "Qwen-A"]
    g, q = found
    assert (
        g["registered_as"] is None
        and g["state"] == "downloading"
        and g["suggested_name"] == "gemma-b"
    )
    assert q["registered_as"] == "qwen-a" and q["state"] == "complete"
    assert q["has_config"] is True and q["architecture"] == "qwen3" and q["quant_bits"] == 4
    assert q["path"] == str(a)


def test_discover_models_missing_root(tmp_path):
    assert mlx.discover_models(tmp_path / "nope", registered={}, now=NOW) == []


def test_build_command_add():
    b = Path("/x/mlx-model")
    assert mlx.build_command(b, "add", "new-model", path="/Volumes/Models/models/New") == [
        str(b),
        "add",
        "new-model",
        "/Volumes/Models/models/New",
    ]
    with pytest.raises(ValueError):
        mlx.build_command(b, "add", "new-model")  # path required
    with pytest.raises(ValueError):
        mlx.build_command(b, "add", "new-model", path="relative/dir")
    with pytest.raises(ValueError):
        mlx.build_command(b, "switch", "x", path="/a")  # path only for add


def test_weights_state_of_vanished_dir(tmp_path):
    st = mlx.weights_state(tmp_path / "gone", now=NOW)
    assert st["state"] == "no-weights" and st["last_write_ago_s"] is None


def test_discover_tolerates_broken_symlinked_entries(tmp_path):
    root = tmp_path / "models"
    d = _model(root, "M", {"a.safetensors": 10}, 10)
    (d / "dangling").symlink_to(tmp_path / "nowhere")  # stat() fails on this entry
    (found,) = mlx.discover_models(root, registered={}, now=NOW)
    assert found["state"] == "complete"
