#!/usr/bin/env bash
# Stop all Donna Docker Compose stacks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# Run the committed snapshot, not the live working tree.
DEPLOY_DIR="${DONNA_DEPLOY_DIR:-/mnt/donna/deploy-main}"
DOCKER_DIR="$DEPLOY_DIR/docker"
export COMPOSE_PROJECT_NAME=docker
echo "(operating on snapshot $DEPLOY_DIR — run scripts/donna-deploy.sh deploy to refresh it)"

echo "==> Stopping Management UI..."
docker compose -f "$DOCKER_DIR/donna-ui.yml" --env-file "$DOCKER_DIR/.env" down 2>/dev/null || true

echo "==> Stopping API backend..."
docker compose -f "$DOCKER_DIR/donna-app.yml" --env-file "$DOCKER_DIR/.env" down 2>/dev/null || true

echo "==> Stopping Ollama..."
docker compose -f "$DOCKER_DIR/donna-ollama.yml" --env-file "$DOCKER_DIR/.env" down 2>/dev/null || true

echo "==> Stopping monitoring stack..."
docker compose -f "$DOCKER_DIR/donna-monitoring.yml" --env-file "$DOCKER_DIR/.env" down 2>/dev/null || true

echo "==> Stopping Donna core..."
docker compose -f "$DOCKER_DIR/donna-core.yml" --env-file "$DOCKER_DIR/.env" down --remove-orphans

echo "Donna stopped."
