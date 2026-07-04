#!/usr/bin/env bash
# Start API + widget demo for local development (detached screen sessions).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "Missing .env. Run: cp .env.example .env and set OPENAI_API_KEY."
  exit 1
fi

stop_session() {
  local name="$1"
  screen -ls 2>/dev/null | awk -v n="$name" '$1 ~ n { print $1 }' | while read -r s; do
    screen -X -S "$s" quit 2>/dev/null || true
  done || true
}

echo "Stopping old dev sessions..."
pkill -f "python main.py" 2>/dev/null || true
stop_session "mobcoder-api"
stop_session "mobcoder-widget"
sleep 1

echo "Starting API (screen: mobcoder-api)..."
screen -dmS mobcoder-api bash -lc \
  "cd '$ROOT' && source .venv/bin/activate && API_RELOAD=false python main.py >> /tmp/mobcoder-api.log 2>&1"

echo "Starting widget (screen: mobcoder-widget)..."
screen -dmS mobcoder-widget bash -lc \
  "cd '$ROOT/widget' && python3 -m http.server 8765 >> /tmp/mobcoder-widget.log 2>&1"

echo "Waiting for API..."
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:8001/api/v1/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -sf "http://127.0.0.1:8001/api/v1/health" >/dev/null 2>&1; then
  echo "ERROR: API did not start. Check /tmp/mobcoder-api.log"
  tail -20 /tmp/mobcoder-api.log 2>/dev/null || true
  exit 1
fi

echo ""
echo "Ready:"
echo "  Widget:  http://127.0.0.1:8765/demo.html"
echo "  API:     http://127.0.0.1:8001/docs"
echo "  Health:  http://127.0.0.1:8001/api/v1/health"
echo ""
echo "Logs:  tail -f /tmp/mobcoder-api.log"
echo "Stop:  pkill -f 'python main.py'; pkill -f 'http.server 8765'"
