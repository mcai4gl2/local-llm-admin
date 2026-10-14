#!/usr/bin/env bash
# Sourced by the ops scripts: resolve the Mac target. No host is built in.
# Values come from the environment first, then from $DEPLOY_ENV (default ops/deploy.env,
# gitignored; see ops/deploy.env.example).
# Usage: . "$(dirname "$0")/deploy-env.sh"; require_deploy_env MAC_HOST [MAC_DEST]
_deploy_env_file=${DEPLOY_ENV:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy.env"}
_env_mac_host=${MAC_HOST-}
_env_mac_dest=${MAC_DEST-}
if [ -f "$_deploy_env_file" ]; then
  # shellcheck disable=SC1090
  . "$_deploy_env_file"
fi
MAC_HOST=${_env_mac_host:-${MAC_HOST-}}
MAC_DEST=${_env_mac_dest:-${MAC_DEST-}}
unset _env_mac_host _env_mac_dest

require_deploy_env() {
  local name
  for name in "$@"; do
    if [ -z "${!name-}" ]; then
      echo "$name is not set: export it, or copy ops/deploy.env.example to ops/deploy.env" \
        "(or point DEPLOY_ENV at a file); tried $_deploy_env_file" >&2
      exit 2
    fi
  done
}
