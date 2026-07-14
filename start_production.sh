#!/bin/bash
# Nexus Bot — Production startup script
# Runs the API server in the background and the Discord bot in the foreground.
# The bot process keeps the VM alive.
set -e

echo "[Nexus] Starting production environment..."

# Start API server in background
echo "[Nexus] Starting API server on port ${PORT:-8080}..."
node --enable-source-maps artifacts/api-server/dist/index.mjs &
API_PID=$!

echo "[Nexus] API server started (PID $API_PID)"
echo "[Nexus] Starting Discord bot..."

# Run Discord bot as the foreground process (keeps VM alive)
cd bot
exec python3 main.py
