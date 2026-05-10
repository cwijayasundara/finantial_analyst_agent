#!/usr/bin/env bash
# Spawn the FastAPI shim + the Next.js dev server. Both bind to 127.0.0.1.
# Ctrl-C kills both.

set -e

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "=== personal-finance-helper · dev ==="
echo "API on http://127.0.0.1:8000  ·  web on http://127.0.0.1:3000"
echo "Both processes will exit together on Ctrl-C."
echo

(cd "$ROOT" && .venv/bin/python -m cookbooks.api) &
API_PID=$!
trap "kill $API_PID 2>/dev/null || true" EXIT INT TERM

# Wait briefly for the API to be ready before starting Next.js
for _ in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null; then
    break
  fi
  sleep 0.5
done

(cd "$ROOT/web" && pnpm dev) &
WEB_PID=$!
trap "kill $API_PID $WEB_PID 2>/dev/null || true" EXIT INT TERM

wait $WEB_PID
