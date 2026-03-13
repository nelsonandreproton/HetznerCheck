#!/usr/bin/env bash
#
# deploy.sh - Pull HetznerCheck, rebuild and restart hetzner-monitor
#
# Usage: bash deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/../homeserver/docker-compose.yml"

cd "$SCRIPT_DIR"

echo "=== HetznerCheck Deploy ==="

echo "[1/3] Pulling latest code..."
git pull

echo "[2/3] Rebuilding and restarting hetzner-monitor..."
docker compose -f "$COMPOSE_FILE" up -d --build hetzner-monitor

echo "[3/3] Showing logs (Ctrl+C to stop watching)..."
docker compose -f "$COMPOSE_FILE" logs -f --tail=30 hetzner-monitor
