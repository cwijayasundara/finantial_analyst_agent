#!/usr/bin/env bash
# Smoke test: assert no outbound TCP traffic during a representative run.
# Whitelist 127.0.0.1 (Ollama) only.
set -euo pipefail

if ! command -v lsof >/dev/null 2>&1; then
    echo "lsof not available; skipping egress check" >&2
    exit 0
fi

cmd=("$@")
if [[ ${#cmd[@]} -eq 0 ]]; then
    echo "Usage: check-egress.sh <command...>" >&2
    exit 2
fi

"${cmd[@]}" &
pid=$!

trap 'kill $pid 2>/dev/null || true' EXIT

sleep 2
remote=$(lsof -p $pid -i -nP 2>/dev/null \
  | awk '/->/ {print $9}' \
  | grep -Ev '127\.0\.0\.1|::1|localhost' || true)

if [[ -n "$remote" ]]; then
    echo "EGRESS DETECTED:"
    echo "$remote"
    kill $pid 2>/dev/null || true
    exit 1
fi

wait $pid
echo "OK: no remote egress observed"
