#!/usr/bin/env bash
# D-C1: add the ComfyUI (:8188) guard to /Volumes/Models/bin/mlx-model on the Mac mini.
# Backs up the original, dry-runs, applies, and syntax-checks. Safe to re-run.
set -euo pipefail
. "$(dirname "$0")/deploy-env.sh"
require_deploy_env MAC_HOST
MAC=$MAC_HOST
PATCH="$(cd "$(dirname "$0")" && pwd)/patches/mlx-model-comfyui-guard.patch"
REMOTE_PATCH=/tmp/mlx-model-comfyui-guard.patch
scp -q "$PATCH" "$MAC:$REMOTE_PATCH"
ssh "$MAC" REMOTE_PATCH="$REMOTE_PATCH" bash -s <<'REMOTE'
set -euo pipefail
F=/Volumes/Models/bin/mlx-model
trap 'rm -f "$REMOTE_PATCH"' EXIT
if grep -q 'iTCP:8188' "$F"; then echo "guard already present"; exit 0; fi
patch --dry-run --forward "$F" "$REMOTE_PATCH"
cp -p "$F" "$F.bak-20260929"
patch --forward "$F" "$REMOTE_PATCH"   # Apple BSD patch 2.0: no GNU long options
zsh -n "$F"   # mlx-model is a zsh script
"$F" status | sed -n 1p   # sed reads everything: no SIGPIPE under pipefail
echo "applied; backup at $F.bak-20260929"
REMOTE
