#!/usr/bin/env bash
# Read-only drift check: compare the versioned Mac-side files in host/ with the Mac.
# Exit 0 when every code file matches, 1 when a code file differs or is missing, or an
# unversioned file sits in bin/. The registry (etc/models.conf) is state: a difference
# there is reported but does not count as drift.
# Usage: ops/host-diff.sh [-v]   (-v prints the diff for files that differ)
set -uo pipefail
. "$(dirname "$0")/deploy-env.sh"
require_deploy_env MAC_HOST
MAC=$MAC_HOST
MODELS_ROOT=/Volumes/Models
OPS="$(cd "$(dirname "$0")" && pwd)"
HERE="$(dirname "$OPS")/host"
VERBOSE=${1:-}
drift=0
# fd 3, and ssh -n: ssh would otherwise read the list from stdin and end the loop early
while read -r -u 3 kind rel; do
  [[ -z "$kind" || "$kind" == \#* ]] && continue
  remote="$MODELS_ROOT/$rel"
  remote_text=$(ssh -n -o BatchMode=yes "$MAC" "cat '$remote'" 2>/dev/null) || {
    echo "MISSING on Mac: $remote"; [ "$kind" = code ] && drift=1; continue; }
  if diff -q <(printf '%s\n' "$remote_text") <(cat "$HERE/$rel"; [ -n "$(tail -c1 "$HERE/$rel")" ] && echo) >/dev/null; then
    echo "same     $rel"
  elif [ "$kind" = registry ]; then
    echo "REGISTRY differs  $rel  (expected state drift; refresh the snapshot by hand, never deployed)"
  else
    echo "DIFFERS  $rel  <->  $remote"; drift=1
  fi
  if [ "$VERBOSE" = "-v" ] && ! diff -q <(printf '%s\n' "$remote_text") <(cat "$HERE/$rel"; [ -n "$(tail -c1 "$HERE/$rel")" ] && echo) >/dev/null; then
    diff -u --label "repo:$rel" --label "mac:$remote" "$HERE/$rel" <(printf '%s\n' "$remote_text")
  fi
done 3< "$OPS/host-files.list"
# names the manifest versions, plus HOST_DIFF_IGNORE (space-separated, e.g. from
# ops/deploy.env) for private files kept on the Mac outside this repo
known=" README.md comfyui ${HOST_DIFF_IGNORE:-} "
while read -r _ rel; do
  case "$rel" in bin/*|comfyui/bin/*) known="$known${rel##*/} " ;; esac
done < "$OPS/host-files.list"
# backups and interrupted-deploy leftovers are expected next to deployed files
extra=$(ssh -n -o BatchMode=yes "$MAC" "ls '$MODELS_ROOT/bin' '$MODELS_ROOT/comfyui/bin'" 2>/dev/null \
  | grep -vE '^$|:$|\.bak-|\.new-' \
  | while read -r name; do case "$known" in *" $name "*) ;; *) echo "$name" ;; esac; done)
[ -n "$extra" ] && { echo "NOT VERSIONED on Mac:"; echo "$extra" | sed 's/^/  /'; drift=1; }
exit $drift
