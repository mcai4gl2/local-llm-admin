#!/usr/bin/env bash
# Deploy local-llm-admin to the Mac mini. Needs explicit approval: it is the first write on the Mac.
# Usage: ops/deploy-mac.sh   (MAC_HOST/MAC_DEST from the environment or ops/deploy.env)
set -euo pipefail
. "$(dirname "$0")/deploy-env.sh"
require_deploy_env MAC_HOST MAC_DEST
MAC=$MAC_HOST
DEST=$MAC_DEST
LABEL=com.homelab.local-llm-admin
cd "$(dirname "$0")/.."

echo "==> preparing $MAC:$DEST (previous version kept as app.prev)"
ssh "$MAC" "set -e; mkdir -p '$DEST'/{data,logs}; if [ -d '$DEST/app' ]; then rm -rf '$DEST/app.prev'; cp -a '$DEST/app' '$DEST/app.prev'; fi; mkdir -p '$DEST/app'"

echo "==> syncing source"
rsync -a --delete --exclude __pycache__ src pyproject.toml uv.lock "$MAC:$DEST/app/"
rsync -a ops/$LABEL.plist.template "$MAC:$DEST/app/"

echo "==> installing and reloading"
ssh "$MAC" DEST="$DEST" LABEL="$LABEL" bash -s <<'REMOTE'
set -euo pipefail
export UV_PYTHON_INSTALL_DIR="$DEST/python" UV_PROJECT_ENVIRONMENT="$DEST/.venv"
cd "$DEST/app"
/opt/homebrew/bin/uv sync --frozen --no-dev --python 3.12
test -f "$HOME/.config/local-llm-admin/env" || { echo "missing ~/.config/local-llm-admin/env (see ops/env.example)"; exit 1; }
chmod 600 "$HOME/.config/local-llm-admin/env"
mkdir -p "$HOME/Library/LaunchAgents"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
sed -e "s|@DEST@|$DEST|g" -e "s|@HOME@|$HOME|g" "$LABEL.plist.template" > "$PLIST"
plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
# bootout is asynchronous; bootstrap fails with "Input/output error" until the old job is gone
for _ in $(seq 20); do
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
  sleep 0.5
done
launchctl bootstrap "gui/$(id -u)" "$PLIST"
for _ in $(seq 30); do
  if curl -sf http://127.0.0.1:8090/healthz >/dev/null; then echo "healthz OK"; exit 0; fi
  sleep 1
done
echo "healthz did not come up; see $DEST/logs/"; exit 1
REMOTE
