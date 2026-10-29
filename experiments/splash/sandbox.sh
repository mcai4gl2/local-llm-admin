#!/usr/bin/env bash
# Invoke an audited Splash command with its writable defaults under an existing spike.
# This redirects defaults; it is not an OS filesystem sandbox. See README.md.
set -euo pipefail
if [ "$#" -lt 2 ] || [ ! -d "$1" ]; then
  echo 'usage: sandbox.sh <existing-spike-directory> <command> [args...]' >&2
  exit 2
fi
SPIKE=$(cd "$1" && pwd -P)
shift
[ "$SPIKE" != / ] || { echo 'Refusing the filesystem root as a spike.' >&2; exit 2; }
umask 077
mkdir -p "$SPIKE/home" "$SPIKE/tmp" "$SPIKE/cache/huggingface/hub" "$SPIKE/cache/uv" \
  "$SPIKE/home/.config" "$SPIKE/home/.local/share" "$SPIKE/home/Library/Caches"
cd "$SPIKE"
# Leading env assignments avoid empty-array expansion under macOS Bash 3 with nounset.
if [ -n "${SPLASH_API_KEY:-}" ]; then
  set -- "SPLASH_API_KEY=$SPLASH_API_KEY" "$@"
fi
exec /usr/bin/env -i \
  HOME="$SPIKE/home" USER="${USER:-you}" LANG=en_US.UTF-8 \
  PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  SPLASH_SPIKE_ROOT="$SPIKE" \
  XDG_CONFIG_HOME="$SPIKE/home/.config" XDG_CACHE_HOME="$SPIKE/cache" \
  XDG_DATA_HOME="$SPIKE/home/.local/share" TMPDIR="$SPIKE/tmp/" \
  HF_HOME="$SPIKE/cache/huggingface" HF_HUB_CACHE="$SPIKE/cache/huggingface/hub" \
  UV_CACHE_DIR="$SPIKE/cache/uv" PIP_CACHE_DIR="$SPIKE/cache/pip" \
  PYTHONNOUSERSITE=1 PYTHONPATH="$SPIKE/runtime" \
  "$@"
