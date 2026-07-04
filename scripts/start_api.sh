#!/usr/bin/env bash
# Start the MobCoder Sales & Help API (foreground — keep this terminal open).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

if [[ ! -f .env ]]; then
  echo "Missing .env. Run: cp .env.example .env and set OPENAI_API_KEY."
  exit 1
fi

export API_RELOAD=false
echo "Starting API at http://127.0.0.1:8001 (Ctrl+C to stop)..."
exec python main.py
