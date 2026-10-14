#!/bin/zsh
# Phase 0 only: start/stop the OpenJev MLX sidecar by hand. Phase 1 replaces this with bin/openjev.
set -euo pipefail
D=/Volumes/Models/decision
case "${1:-}" in
  start)
    [[ -f $D/run/openjev.pid ]] && kill -0 "$(<$D/run/openjev.pid)" 2>/dev/null && { echo "already running"; exit 1; }
    lsof -nP -iTCP:8081 -sTCP:LISTEN >/dev/null 2>&1 && { echo "port 8081 busy"; exit 1; }
    set -a; source $D/etc/openjev.env; set +a
    export PYTHONUNBUFFERED=1 HF_HOME=$D/cache/hf HF_HUB_OFFLINE=1
    nohup $D/venv/bin/python -m openjev >> $D/logs/openjev.log 2>&1 &
    echo $! > $D/run/openjev.pid; echo "started pid $!";;
  stop)
    pid="$(<$D/run/openjev.pid)"; kill "$pid" 2>/dev/null || true
    for i in {1..20}; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid"; rm -f $D/run/openjev.pid; echo stopped;;
  *) echo "usage: $0 start|stop"; exit 2;;
esac
