#!/usr/bin/env bash
# Start/stop/status for the Masterwork dev servers, detached from any
# terminal or Claude session (nohup + pidfiles). Idempotent: start skips
# services that are already listening.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/.dev-logs"
mkdir -p "$LOG_DIR"

BACKEND_PORT=8008
FRONTEND_PORT=5192

listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1; }

start_backend() {
  if listening $BACKEND_PORT; then
    echo "backend  already running on :$BACKEND_PORT"
  else
    (cd "$ROOT/backend" && nohup uv run uvicorn app.main:app --port $BACKEND_PORT \
      >"$LOG_DIR/backend.log" 2>&1 & echo $! >"$LOG_DIR/backend.pid")
    echo "backend  started on :$BACKEND_PORT (log: .dev-logs/backend.log)"
  fi
}

start_frontend() {
  if listening $FRONTEND_PORT; then
    echo "frontend already running on :$FRONTEND_PORT"
  else
    (cd "$ROOT/frontend" && nohup npm run dev \
      >"$LOG_DIR/frontend.log" 2>&1 & echo $! >"$LOG_DIR/frontend.pid")
    echo "frontend started on :$FRONTEND_PORT (log: .dev-logs/frontend.log)"
  fi
}

stop_port() {
  local port=$1 name=$2
  local pids
  pids=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "$pids" | xargs kill
    echo "$name stopped"
  else
    echo "$name not running"
  fi
}

case "${1:-start}" in
  start)
    start_backend
    start_frontend
    echo "→ http://localhost:$FRONTEND_PORT"
    ;;
  stop)
    stop_port $FRONTEND_PORT frontend
    stop_port $BACKEND_PORT "backend "
    ;;
  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;
  status)
    listening $BACKEND_PORT && echo "backend  up on :$BACKEND_PORT" || echo "backend  DOWN"
    listening $FRONTEND_PORT && echo "frontend up on :$FRONTEND_PORT" || echo "frontend DOWN"
    ;;
  *)
    echo "usage: scripts/dev.sh [start|stop|restart|status]" >&2
    exit 2
    ;;
esac
