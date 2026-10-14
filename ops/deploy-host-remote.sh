#!/usr/bin/env bash
# Remote half of ops/deploy-host.sh; runs on the Mac (bash 3.2) via `ssh ... bash -s`.
# Env: STAMP, ROOT (/Volumes/Models), ADMIN_DB, FILES (space-separated absolute targets,
# each staged as <target>.new-$STAMP).
set -euo pipefail
GATE="$ROOT/run/maintenance.lock"
LOCK="$ROOT/run/mlx.lock"
gate_owned=0
replaced=""

cleanup() {
  for f in $FILES; do rm -f "$f.new-$STAMP" "$f.restore-$STAMP"; done
  if [ "$gate_owned" = 1 ]; then
    rm -f "$GATE/owner"
    rmdir "$GATE" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

mkdir -p "$ROOT/run"
for _ in $(seq 50); do
  if mkdir "$GATE" 2>/dev/null; then gate_owned=1; break; fi
  sleep 0.1
done
if [ "$gate_owned" != 1 ]; then
  echo "refusing: the maintenance gate $GATE is held ($(tr '\n' ' ' < "$GATE/owner" 2>/dev/null || echo 'no owner recorded'))"
  exit 1
fi
printf 'pid=%s\nstart=%s\ncmd=deploy-host\n' "$$" "$(ps -o lstart= -p $$ | sed 's/^ *//')" > "$GATE/owner"

preflight() {
  if [ -e "$LOCK" ]; then
    echo "refusing: $LOCK exists; resolve it first (mlx-model unlock)"
    return 1
  fi
  # older mlx-model versions ignore the gate, so look for them directly
  if pgrep -f 'bin/mlx-model (start|stop|switch|select|add|remove)' >/dev/null 2>&1; then
    echo "refusing: an mlx-model command is running"
    return 1
  fi
  if [ -f "$ADMIN_DB" ]; then
    local n
    n=$(sqlite3 -readonly "$ADMIN_DB" "SELECT count(*) FROM jobs WHERE status = 'running'" 2>/dev/null) || {
      echo "refusing: cannot read admin jobs from $ADMIN_DB"
      return 1
    }
    if [ "$n" != 0 ]; then
      echo "refusing: $n admin job(s) running"
      return 1
    fi
  fi
}

syntax_check() {
  local first
  first=$(sed -n 1p "$1")
  case "$first" in
    '#!/bin/zsh'*) zsh -n "$1" ;;
    '#!/bin/bash'* | '#!/usr/bin/env bash'*) bash -n "$1" ;;
    '#!/bin/sh'*) sh -n "$1" ;;
    # parse only: nothing is imported or run, and no .pyc is written
    '#!/usr/bin/python3'*) /usr/bin/python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$1" ;;
    '#!'*) echo "unknown interpreter: $first"; return 1 ;;
    *) return 0 ;;  # not a script (README)
  esac
}

file_mode() {
  stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"
}

restore() {
  local r
  for r in $replaced; do
    if [ -e "$r.bak-$STAMP" ]; then
      cp -p "$r.bak-$STAMP" "$r.restore-$STAMP" && mv -f "$r.restore-$STAMP" "$r"
    else
      rm -f "$r"
    fi
  done
}

preflight
for f in $FILES; do
  [ -f "$f.new-$STAMP" ] || { echo "missing staged file for $f"; exit 1; }
  syntax_check "$f.new-$STAMP" || { echo "syntax check failed: $f"; exit 1; }
done
preflight   # again: state may have changed during staging and checks

mkdir -p "$ROOT/deploy"
manifest="$ROOT/deploy/$STAMP.manifest"
for f in $FILES; do
  if [ -e "$f" ]; then
    mode=$(file_mode "$f")
    [ ! -e "$f.bak-$STAMP" ] || { echo "backup $f.bak-$STAMP already exists"; exit 1; }
    cp -p "$f" "$f.bak-$STAMP"
    chmod "$mode" "$f.new-$STAMP"
    backup="$f.bak-$STAMP"
  else
    case "$(sed -n 1p "$f.new-$STAMP")" in '#!'*) mode=755 ;; *) mode=644 ;; esac
    chmod "$mode" "$f.new-$STAMP"
    backup=-
  fi
  echo "$f $backup $mode $(shasum -a 256 < "$f.new-$STAMP" | cut -d' ' -f1)" >> "$manifest"
done

for f in $FILES; do
  if ! mv -f "$f.new-$STAMP" "$f"; then
    echo "replacing $f failed; restoring the files already replaced"
    restore
    echo "restored; backups kept, manifest $manifest"
    exit 1
  fi
  replaced="$replaced $f"
done
echo "deployed $STAMP; manifest $manifest"
