#!/usr/bin/env bash
# Deploy versioned host/ files to the Mac mini with per-file backups.
# Usage: ops/deploy-host.sh [--apply] [path ...]
#   Dry run by default: lists files that differ and prints their diffs.
#   Paths are host-relative code entries of ops/host-files.list (default: all of them).
#   The registry (etc/models.conf) is state and is never deployed.
# --apply stages each changed file next to its target, then one remote session takes the
# maintenance gate, refuses while an admin job, mlx.lock or mlx-model command is active,
# syntax-checks with each file's interpreter, rechecks, backs up (<path>.bak-<stamp>,
# mode kept), renames into place and writes $MODELS_ROOT/deploy/<stamp>.manifest.
# A failed replacement restores the files already replaced.
set -euo pipefail
. "$(dirname "$0")/deploy-env.sh"
require_deploy_env MAC_HOST MAC_DEST
MAC=$MAC_HOST
MODELS_ROOT=/Volumes/Models
ADMIN_DB="$MAC_DEST/data/admin.sqlite"
OPS="$(cd "$(dirname "$0")" && pwd)"
HOST_DIR="$(dirname "$OPS")/host"

apply=0
requested=()
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    *) requested+=("$arg") ;;
  esac
done

code_files=()
while read -r kind rel; do
  [[ "$kind" == code ]] && code_files+=("$rel")
done < "$OPS/host-files.list"

files=()
if [ ${#requested[@]} -eq 0 ]; then
  files=("${code_files[@]}")
else
  for arg in "${requested[@]}"; do
    ok=0
    for rel in "${code_files[@]}"; do [ "$arg" = "$rel" ] && ok=1; done
    [ "$ok" = 1 ] || { echo "not deployable: $arg (only code entries of ops/host-files.list)" >&2; exit 2; }
    files+=("$arg")
  done
fi

remote() { ssh -o BatchMode=yes "$MAC" "$@"; }
sha() { shasum -a 256 | cut -d' ' -f1; }

changed=()
for rel in "${files[@]}"; do
  local_sha=$(sha < "$HOST_DIR/$rel")
  remote_sha=$(remote "if [ -f '$MODELS_ROOT/$rel' ]; then shasum -a 256 < '$MODELS_ROOT/$rel' | cut -d' ' -f1; else echo missing; fi")
  if [ "$remote_sha" = "$local_sha" ]; then
    echo "same     $rel"
    continue
  fi
  changed+=("$rel")
  if [ "$remote_sha" = missing ]; then
    echo "NEW      $rel"
  else
    echo "DIFFERS  $rel"
    if [ "$apply" = 0 ]; then
      diff -u --label "mac:$rel" --label "repo:$rel" <(remote "cat '$MODELS_ROOT/$rel'") "$HOST_DIR/$rel" || true
    fi
  fi
done

if [ ${#changed[@]} -eq 0 ]; then
  echo "nothing to deploy"
  exit 0
fi
if [ "$apply" = 0 ]; then
  echo "dry run: ${#changed[@]} file(s) would change; re-run with --apply"
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)-$$"
targets=()
for rel in "${changed[@]}"; do
  # a file in a new directory (decision/bin/openjev on its first deploy) needs its parent
  remote "mkdir -p '$(dirname "$MODELS_ROOT/$rel")' && cat > '$MODELS_ROOT/$rel.new-$STAMP'" < "$HOST_DIR/$rel"
  targets+=("$MODELS_ROOT/$rel")
done
# the remote side removes the staged files on every exit path
remote "STAMP='$STAMP' ROOT='$MODELS_ROOT' ADMIN_DB='$ADMIN_DB' FILES='${targets[*]}' bash -s" \
  < "$OPS/deploy-host-remote.sh"

bad=0
for rel in "${changed[@]}"; do
  remote_sha=$(remote "shasum -a 256 < '$MODELS_ROOT/$rel' | cut -d' ' -f1")
  if [ "$remote_sha" = "$(sha < "$HOST_DIR/$rel")" ]; then
    echo "verified $rel"
  else
    echo "MISMATCH $rel after deploy" >&2; bad=1
  fi
done
exit $bad
