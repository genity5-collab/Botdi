#!/bin/bash
# Nexus Bot — Production build script
set -e

echo "[Build] Installing dependencies..."
pnpm install --frozen-lockfile

echo "[Build] Building API server..."
pnpm --filter @workspace/api-server run build

echo "[Build] Building Dashboard..."
pnpm --filter @workspace/dashboard run build

echo "[Build] Done."
