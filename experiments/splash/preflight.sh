#!/usr/bin/env bash
# Read-only Mac inventory. Output is private; no remote files are written.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../../ops/deploy-env.sh"
require_deploy_env MAC_HOST MAC_DEST
usage() { echo 'usage: preflight.sh /private/path/preflight.json [--model registered-name] [--hash-weights]' >&2; exit 2; }
[ "$#" -ge 1 ] || usage
OUT=$1
shift
HASH_MODE=metadata-only
MODEL_NAME=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --hash-weights) HASH_MODE=--hash-weights; shift ;;
    --model) [ "$#" -ge 2 ] || usage; MODEL_NAME=$2; shift 2 ;;
    *) usage ;;
  esac
done
umask 077
mkdir -p "$(dirname "$OUT")"
# noclobber also prevents following an existing symlink and overwriting its target.
set -o noclobber
exec 3> "$OUT"
printf -v remote_command 'python3 - %q %q %q' "$MAC_DEST/data/admin.sqlite" "$HASH_MODE" "$MODEL_NAME"
# Only stdlib OS inspection, metadata reads, GETs and read-only SQL execute remotely.
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$MAC_HOST" "$remote_command" < "$HERE/preflight.py" >&3; then
  echo 'Read-only preflight failed; incomplete output remains private for inspection.' >&2
  exit 1
fi
echo 'Private read-only preflight captured; inspect issues before preparing an experiment.'
