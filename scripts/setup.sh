#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

uv venv --python 3.12
uv pip install -e ".[dev,graph]"

mkdir -p data parsed wiki/{merchants,statements,subscriptions,memos,decisions,annotations} \
         graph/{snapshots} out

echo "Setup complete. Activate with: source .venv/bin/activate"
