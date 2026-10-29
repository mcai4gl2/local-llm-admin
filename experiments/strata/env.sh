#!/bin/zsh
# Strata Phase 0 launcher: runs one command confined to a sandbox directory.
# usage: env.sh <sandbox-dir> <command> [args...]
# Starts from an empty environment so nothing from the login shell (Homebrew, the MLX venv,
# uv's Python) leaks in, and points every per-user location into <sandbox>/home.
set -eu
if (( $# < 2 )); then
  print -u2 "usage: env.sh <sandbox-dir> <command> [args...]"
  exit 2
fi
SANDBOX=${1:A}
shift
[[ -d $SANDBOX/home ]] || { print -u2 "env.sh: $SANDBOX/home missing"; exit 2; }
H=$SANDBOX/home
mkdir -p $H/.config $H/.cache $H/.local/share $H/tmp $H/Library/Caches
cd $SANDBOX/app
exec /usr/bin/env -i \
  HOME=$H \
  USER=${USER:-strata} \
  LANG=en_US.UTF-8 \
  PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  XDG_CONFIG_HOME=$H/.config \
  XDG_CACHE_HOME=$H/.cache \
  XDG_DATA_HOME=$H/.local/share \
  TMPDIR=$H/tmp/ \
  HF_HOME=$H/.cache/huggingface \
  PIP_CACHE_DIR=$H/.cache/pip \
  UV_CACHE_DIR=$H/.cache/uv \
  PYTHONNOUSERSITE=1 \
  PIP_REQUIRE_VIRTUALENV=1 \
  STRATA_API_KEY=${STRATA_API_KEY:-phase0-throwaway} \
  "$@"
