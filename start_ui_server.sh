#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST="${SUPPORT_COPILOT_HOST:-127.0.0.1}"
PORT="${SUPPORT_COPILOT_PORT:-8765}"
LOG_DIR="${ROOT_DIR}/logs"
LOG_FILE="${LOG_DIR}/web_ui.log"

mkdir -p "$LOG_DIR"

if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  exit 0
fi

nohup /usr/bin/python3 "${ROOT_DIR}/web_ui.py" --host "$HOST" --port "$PORT" --no-open >>"$LOG_FILE" 2>&1 &

for _ in $(seq 1 25); do
  if curl -fsS "http://${HOST}:${PORT}/api/state?cursor=0" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 0.4
done

echo "Support Copilot UI failed to start. Check ${LOG_FILE}" >&2
exit 1
