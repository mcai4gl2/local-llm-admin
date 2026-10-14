#!/bin/zsh
# Shared paths. Everything heavy lives on the external volume.
export MODELS_ROOT="/Volumes/Models"
export MLX_VENV="$MODELS_ROOT/venvs/mlx"
export MLX_MODEL="$MODELS_ROOT/models/active"
export HF_HOME="$MODELS_ROOT/hf"
export HF_HUB_CACHE="$HF_HOME/hub"
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export UV_PYTHON_INSTALL_DIR="$MODELS_ROOT/python"
export UV_CACHE_DIR="$MODELS_ROOT/cache/uv"
export PATH="/opt/homebrew/bin:$MLX_VENV/bin:$PATH"
# Machine-local settings that are not versioned (e.g. MLX_PUBLIC_HOST for the API line
# that `mlx-model start` prints); optional.
if [[ -f "$MODELS_ROOT/etc/local.env" ]]; then
  source "$MODELS_ROOT/etc/local.env"
fi

if [[ ! -d "$MODELS_ROOT" ]]; then
  echo "External volume is not mounted at $MODELS_ROOT" >&2
  exit 1
fi
if [[ ! -x "$MLX_VENV/bin/python" ]]; then
  echo "Missing venv at $MLX_VENV" >&2
  exit 1
fi
