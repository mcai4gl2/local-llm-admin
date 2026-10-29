#!/bin/zsh
# Shared reservation for owning wrappers. Source only; never invokes another wrapper.
engine_splash_guard() {
  local root="/Volumes/Models/splash" python helper command process_list
  python="$root/runtime/python/bin/python3"
  helper="$root/lib/control.py"
  if [[ -x "$python" && -r "$helper" ]]; then
    "$python" -I "$helper" "$root" guard && return 0
    echo "Splash owns resources or its ownership is unknown; stop it first." >&2
    return 1
  fi
  [[ ! -e "$root/run/splash.json" ]] || {
    echo "Splash ownership cannot be checked: missing runtime/helper." >&2
    return 1
  }
  if ! process_list="$(ps -axo command= 2>/dev/null)"; then
    echo "Cannot verify Splash process ownership." >&2
    return 1
  fi
  while IFS= read -r command; do
    if [[ "$command" == "$root/runtime/"* ]]; then
      echo "A Splash process still owns resources; stop it first." >&2
      return 1
    fi
  done <<< "$process_list"
}

engine_owns_lock() {
  [[ -f "$1/owner" && "$(sed -n 's/^pid=//p' "$1/owner")" == "$ENGINE_OWNER_PID" \
     && "$(sed -n 's/^start=//p' "$1/owner")" == "$ENGINE_OWNER_START" ]]
}

engine_signal() {
  if [[ "${ENGINE_RELEASING:-0}" == 1 ]]; then
    typeset -g ENGINE_PENDING_SIGNAL="$1"
    return 0
  fi
  engine_release
  trap - "$1"
  kill "-$1" $$
}

engine_release_one() {
  engine_owns_lock "$1" || return 0
  # Keep TERM/INT from re-entering after owner unlink but before directory removal.
  typeset -g ENGINE_RELEASING=1
  rm -f "$1/owner" || true
  rmdir "$1" 2>/dev/null || true
  typeset -g ENGINE_RELEASING=0
  if [[ -n "${ENGINE_PENDING_SIGNAL:-}" ]]; then
    local pending="$ENGINE_PENDING_SIGNAL"
    typeset -g ENGINE_PENDING_SIGNAL=""
    engine_signal "$pending"
  fi
}

engine_release() {
  [[ -n "${ENGINE_OWNER_PID:-}" && -n "${ENGINE_OWNER_START:-}" ]] || return 0
  [[ "${ENGINE_RELEASING:-0}" == 0 ]] || return 0
  local dir
  for dir in "/Volumes/Models/run/mlx.lock" "/Volumes/Models/run/maintenance.lock"; do
    engine_release_one "$dir"
  done
}

engine_write_owner() {
  print -rl -- "pid=$ENGINE_OWNER_PID" "start=$ENGINE_OWNER_START" "cmd=$2" > "$1/owner"
}

engine_run() {
  set -euo pipefail
  umask 077
  zmodload zsh/system
  typeset -g ENGINE_OWNER_PID=$sysparams[pid]
  typeset -g ENGINE_OWNER_START="$(ps -o lstart= -p "$ENGINE_OWNER_PID" | sed 's/^ *//')"
  local gate="/Volumes/Models/run/maintenance.lock" lock="/Volumes/Models/run/mlx.lock"
  [[ -n "$ENGINE_OWNER_START" ]] || { echo "Lifecycle owner identity is unknown." >&2; return 1; }
  mkdir -p "/Volumes/Models/run"
  if ! mkdir "$gate" 2>/dev/null; then
    echo "maintenance.lock is held; maintenance is in progress." >&2
    return 1
  fi
  engine_write_owner "$gate" "$1"
  if ! mkdir "$lock" 2>/dev/null; then
    echo "Another lifecycle command owns mlx.lock; try again later." >&2
    return 1
  fi
  engine_write_owner "$lock" "$1"
  engine_release_one "$gate"
  "$@"
  local rc=$?
  engine_release
  return $rc
}

# Global traps also cover explicit exits inside a function on supported zsh versions.
trap '(( ZSH_SUBSHELL )) || engine_release' EXIT
trap '(( ZSH_SUBSHELL )) || engine_release' ZERR

# Re-raise termination after releasing only this process's reservation.
TRAPTERM() { engine_signal TERM; }
TRAPINT() { engine_signal INT; }
