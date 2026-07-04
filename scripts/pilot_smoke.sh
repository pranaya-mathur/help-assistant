#!/usr/bin/env bash
# Pilot smoke: health, stream TTFB, and citation URL quality on local API.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${PILOT_API_URL:-http://127.0.0.1:8001}"
QUERY="${PILOT_SMOKE_QUERY:-I am lookin a soultion based out of RAG}"

echo "Pilot smoke against $API"

health="$(curl -sf "$API/api/v1/health")"
echo "Health: $health"

payload="$(QUERY="$QUERY" python3 - <<'PY'
import json
import os
print(json.dumps({
  "message": os.environ["QUERY"],
  "stream": True,
  "session_id": "pilot-smoke",
}))
PY
)"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

meta_file="$(mktemp)"
trap 'rm -f "$tmp" "$meta_file"' EXIT

curl -sf -N \
  -H "Content-Type: application/json" \
  -d "$payload" \
  -o "$tmp" \
  -w "%{time_starttransfer}" \
  "$API/api/v1/chat/stream" > "$meta_file" || {
    echo "ERROR: stream request failed"
    exit 1
  }

python3 - <<'PY' "$tmp" "$meta_file"
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text(encoding="utf-8")
ttfb_s = float(Path(sys.argv[2]).read_text(encoding="utf-8").strip() or "0")
ttfb_ms = ttfb_s * 1000
citations = []
response = ""
for line in raw.splitlines():
    if not line.startswith("data: "):
        continue
    body = line[6:].strip()
    if body == "[DONE]":
        continue
    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        continue
    if event.get("type") == "meta":
        citations = (event.get("data") or {}).get("citations") or []
    if event.get("type") == "done":
        response = event.get("response") or response

print(f"TTFB: {ttfb_ms:.0f}ms")
if ttfb_ms > 3000:
    raise SystemExit(f"FAIL: TTFB {ttfb_ms:.0f}ms exceeds 3000ms gate")

urls = [str(c.get("source_url") or "") for c in citations]
forbidden = ("contact-us", "in-denver", "in-sydney")
bad = [u for u in urls if any(f in u.lower() for f in forbidden)]
if bad:
    raise SystemExit(f"FAIL: forbidden citation URLs in top results: {bad}")

good_patterns = ("/ai", "/services", "/agent", "/rag", "mobcoder.ai")
if urls and not any(any(p in u.lower() for p in good_patterns) for u in urls):
    raise SystemExit(f"FAIL: no core source URL in citations: {urls}")

if not response.strip():
    raise SystemExit("FAIL: empty streamed response")

print("PASS: pilot smoke")
print(f"Response preview: {response[:160]}...")
if urls:
    print(f"Top citation: {urls[0]}")
PY
