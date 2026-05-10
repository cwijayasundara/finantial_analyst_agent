#!/usr/bin/env bash
# Production build of the Next.js app.
set -e
cd "$(dirname "$0")/../web"
pnpm install --frozen-lockfile=false
pnpm build
echo "Build done. Run with: cd web && pnpm start"
