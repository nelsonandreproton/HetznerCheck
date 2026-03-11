#!/usr/bin/env bash
#
# deploy.sh - Pull latest code, rebuild, and restart HetznerCheck
#
# Usage: bash deploy.sh
set -euo pipefail
cd "$(dirname "$0")"
echo "=== HetznerCheck Deploy ==="
echo "[1/3] Pulling latest code..."
git pull
echo "[2/3] Rebuilding and restarting..."
docker compose up -d --build
echo "[3/3] Showing logs (Ctrl+C to stop watching)..."
docker compose logs -f --tail=30
