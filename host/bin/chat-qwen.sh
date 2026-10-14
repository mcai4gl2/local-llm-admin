#!/bin/zsh
# Gradio chat UI. Do not run this at the same time as serve-qwen.sh;
# each process loads the full model (~16 GB plus cache).
set -euo pipefail
source /Volumes/Models/bin/mlx-env.sh
/Volumes/Models/bin/mlx-model stop
if [[ ! -d "$MLX_MODEL" ]]; then
  echo "No active model. Run: mlx-model switch qwen3.8-27b" >&2
  exit 1
fi
mkdir -p "$MODELS_ROOT/logs"
export PYTHONUNBUFFERED=1
export GRADIO_SERVER_NAME="0.0.0.0"
export GRADIO_SERVER_PORT="7860"
exec "$MLX_VENV/bin/mlx_vlm.chat_ui" --model "$MLX_MODEL"
