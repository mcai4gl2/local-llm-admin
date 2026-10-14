#!/bin/zsh
set -euo pipefail
log="/Volumes/Models/logs/download-models.log"
mkdir -p /Volumes/Models/logs /Volumes/Models/models
exec >> "$log" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') start ====="

download_repo() {
  local repo="$1" dest="$2"
  shift 2
  mkdir -p "$dest"
  local base="https://huggingface.co/${repo}/resolve/main"
  local f
  for f in "$@"; do
    echo "GET $repo/$f"
    curl -fL --retry 8 --retry-all-errors --retry-delay 2 -C - \
      -o "$dest/$f" "$base/$f"
  done
}

download_repo mlx-community/Qwen3.8-27B-8bit /Volumes/Models/models/Qwen3.8-27B-8bit \
  .gitattributes README.md chat_template.jinja config.json generation_config.json merges.txt \
  model.safetensors.index.json preprocessor_config.json processor_config.json \
  tokenizer.json tokenizer_config.json video_preprocessor_config.json vocab.json \
  model-00001-of-00006.safetensors \
  model-00002-of-00006.safetensors \
  model-00003-of-00006.safetensors \
  model-00004-of-00006.safetensors \
  model-00005-of-00006.safetensors \
  model-00006-of-00006.safetensors &
pid_qwen=$!

download_repo mlx-community/gemma-4-26B-A4B-it-4bit /Volumes/Models/models/gemma-4-26B-A4B-it-4bit \
  .gitattributes README.md chat_template.jinja config.json generation_config.json \
  model.safetensors.index.json processor_config.json tokenizer.json tokenizer_config.json \
  model-00001-of-00003.safetensors \
  model-00002-of-00003.safetensors \
  model-00003-of-00003.safetensors &
pid_gemma=$!

fail=0
wait "$pid_qwen" || fail=1
wait "$pid_gemma" || fail=1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') done fail=$fail ====="
exit "$fail"
