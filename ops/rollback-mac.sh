#!/usr/bin/env bash
# Restore the previous deploy (app.prev) on the Mac mini and reload the LaunchAgent.
set -euo pipefail
. "$(dirname "$0")/deploy-env.sh"
require_deploy_env MAC_HOST MAC_DEST
MAC=$MAC_HOST
DEST=$MAC_DEST
LABEL=com.homelab.local-llm-admin
ssh "$MAC" DEST="$DEST" LABEL="$LABEL" bash -s <<'REMOTE'
set -euo pipefail
test -d "$DEST/app.prev" || { echo "no app.prev to roll back to"; exit 1; }
rm -rf "$DEST/app.failed"; mv "$DEST/app" "$DEST/app.failed"; mv "$DEST/app.prev" "$DEST/app"
export UV_PYTHON_INSTALL_DIR="$DEST/python" UV_PROJECT_ENVIRONMENT="$DEST/.venv"
cd "$DEST/app" && /opt/homebrew/bin/uv sync --frozen --no-dev --python 3.12
launchctl kickstart -k "gui/$(id -u)/$LABEL"
for _ in $(seq 30); do
  if curl -sf http://127.0.0.1:8090/healthz >/dev/null; then echo "rolled back; healthz OK"; exit 0; fi
  sleep 1
done
echo "healthz did not come up after rollback"; exit 1
REMOTE
