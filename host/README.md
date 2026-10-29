# host/ — Mac-side scripts under version control

The Mac mini (`mac-mini.local`) runs the MLX LLM server and ComfyUI from the external drive at `/Volumes/Models`. These scripts were set up by hand and were not version controlled. This folder is the versioned copy. The layout mirrors `/Volumes/Models`.

| Repo path | Mac path | What it is |
|---|---|---|
| `bin/mlx-model` | `/Volumes/Models/bin/mlx-model` | Start, stop, switch, list, add, unlock (owns `run/mlx.lock`, `run/maintenance.lock`, `run/active.env`, `models/active`). Includes the D-C1 `:8188` guard; locks record their owner. |
| `bin/mlx-env.sh` | `/Volumes/Models/bin/mlx-env.sh` | Shared paths for the MLX scripts |
| `bin/chat-qwen.sh` | `/Volumes/Models/bin/chat-qwen.sh` | Gradio chat UI on :7860 (stops the API first) |
| `bin/serve-qwen.sh` | `/Volumes/Models/bin/serve-qwen.sh` | Wrapper for `mlx-model switch qwen3.8-27b` |
| `bin/download-models.sh` | `/Volumes/Models/bin/…` | One-off download scripts (hard-coded repos) |
| `bin/README.md` | `/Volumes/Models/bin/README.md` | Operator notes on the Mac (daily commands, LiteLLM config, context sizes) |
| `comfyui/bin/comfyui` | `/Volumes/Models/comfyui/bin/comfyui` | ComfyUI start, stop, status (port 8188) |
| `decision/bin/openjev`, `decision/README.md` | `/Volumes/Models/decision/…` | The decision sidecar (M6): OpenJev on :8081 |
| `tts/bin/qwen-tts`, `tts/README.md` | `/Volumes/Models/tts/…` | The TTS sidecar (M7): mlx-audio `mlx_audio.server` with Qwen3-TTS on :8083. Its venv (`python -m venv` from the uv CPython 3.12.14, `pip install 'mlx-audio[tts,server]==0.5.7'`) is recorded in `environment/tts-app.txt` |
| `etc/models.conf` | `/Volumes/Models/etc/models.conf` | Model registry snapshot. It is **state**: `mlx-model add` and the admin's Register change it, so drift here is expected. |
| `environment/*.txt` | (recorded, not deployed) | What is installed: Python, both venvs (`uv pip freeze`), the ComfyUI checkout and commit |

Not versioned: weights (`models/`), the venvs, the uv-managed Python, caches, logs and run state.

## Checking for drift

```bash
ops/host-diff.sh       # read-only; exit 0 = every code file matches
ops/host-diff.sh -v    # also print diffs
```

Files are listed in `ops/host-files.list`. `code` entries must match. The `registry` entry (`etc/models.conf`) is reported as `REGISTRY differs` without failing the check, because registering models changes it. The script also lists files in `bin/` or `comfyui/bin/` on the Mac that aren't versioned here (deploy backups `*.bak-*` excepted). There is no `--pull`: refresh the registry snapshot by copying the file by hand.

## Changing a script

1. Edit the file here. Keep it zsh (`#!/bin/zsh`) and backward compatible: existing commands and their output must not change, because the admin parses them.
2. Test and review (`tests/test_host_control.py` runs `mlx-model` under real zsh; `tests/test_host_deploy.py` covers deployment).
3. Deploy: `ops/deploy-host.sh` (dry run: shows diffs), then `ops/deploy-host.sh --apply [path ...]`. It takes the maintenance gate, refuses while an admin job, `run/mlx.lock` or an `mlx-model` command is active, syntax-checks each file with its own interpreter, backs up each target as `<path>.bak-<stamp>` (mode kept), renames the new file into place and writes `/Volumes/Models/deploy/<stamp>.manifest` (path, backup, mode, sha256). If a rename fails, the files already replaced are restored. It never deploys `etc/models.conf`.
4. To undo: copy the `.bak-<stamp>` files listed in the manifest back over their targets.

`ops/apply-mlx-model-guard.sh` is the one-off D-C1 deployment, kept for history.

### First upgrade from the legacy `mlx-model`

The script recorded as `tests/fixtures/mac/mlx-model.dc1` (deployed 2026-09-29) ignores the maintenance gate, so the gate cannot protect the upgrade that installs it. For that first deployment:

1. Pause the admin's mutation entry points: no Engine/Model actions during the window (the running inference server can keep serving).
2. Run no `mlx-model` commands by hand or over SSH.
3. Check that nothing is in progress: no `run/mlx.lock` (if a stale one exists, remove it only after confirming no `mlx-model` process runs), `pgrep -fl 'bin/mlx-model'` prints nothing, and the admin shows no running job.
4. `ops/deploy-host.sh --apply bin/mlx-model` (it repeats these checks), then `/Volumes/Models/bin/mlx-model status` and `ops/host-diff.sh`.

`tests/fixtures/mac/mlx-model.orig` is the script as first recorded (2026-09-29); `tests/test_host_files.py` checks that `mlx-model.orig` plus `ops/patches/mlx-model-comfyui-guard.patch` equals `mlx-model.dc1`.

## Rebuilding the environment

No setup script exists yet; everything was installed by hand. The recorded versions in `environment/` are the reference for writing one:

- **Python:** uv 0.12.19 (Homebrew) with `UV_PYTHON_INSTALL_DIR=/Volumes/Models/python` → CPython 3.12.14.
- **MLX venv:** `uv venv /Volumes/Models/venvs/mlx --python 3.12`, then `uv pip install -r environment/mlx-venv.freeze.txt`.
- **ComfyUI:** clone the repo at the recorded commit into `/Volumes/Models/comfyui/app`, then `uv venv /Volumes/Models/comfyui/venv --python 3.12` and `uv pip install -r environment/comfyui-venv.freeze.txt`. Needs Homebrew `ffmpeg`.


## Optional Splash integration

[The Splash guide](splash/README.md) records the pinned standalone runtime, private
profile/env/audit receipts, explicit 32K profiles, ownership and Phase 3 rollout worksheet.
The shared `bin/engine-guard.sh` and all corresponding owning-wrapper changes must be
deployed together in an approved window. `splash start <profile>` and `splash stop`
share the existing owner-bearing lifecycle reservation; status remains read-only.
Deploying the versioned wrapper/helpers/examples does not install assets, overwrite private
configuration, enable the admin feature or start a service. MLX remains the default.
