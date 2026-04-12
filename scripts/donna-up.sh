#!/usr/bin/env bash
# Start all Donna Docker Compose stacks in order.
# Usage: ./donna-up.sh [--with-monitoring] [--with-ollama]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_DIR/docker"

WITH_MONITORING=false
WITH_OLLAMA=false

for arg in "$@"; do
  case "$arg" in
    --with-monitoring) WITH_MONITORING=true ;;
    --with-ollama)     WITH_OLLAMA=true ;;
    --all)             WITH_MONITORING=true; WITH_OLLAMA=true ;;
    -h|--help)
      echo "Usage: donna-up.sh [--with-monitoring] [--with-ollama] [--all]"
      exit 0
      ;;
    *)
      echo "Unknown flag: $arg" >&2
      exit 1
      ;;
  esac
done

echo "==> Starting Donna core (orchestrator + DB)..."
docker compose -f "$DOCKER_DIR/docker-compose.yml" --env-file "$DOCKER_DIR/.env" up -d

if [ "$WITH_MONITORING" = true ]; then
  echo "==> Starting monitoring stack (Grafana + Loki + Promtail)..."
  docker compose -f "$DOCKER_DIR/donna-monitoring.yml" --env-file "$DOCKER_DIR/.env" up -d
fi

if [ "$WITH_OLLAMA" = true ]; then
  echo "==> Starting Ollama (local LLM)..."
  docker compose -f "$DOCKER_DIR/donna-ollama.yml" --env-file "$DOCKER_DIR/.env" up -d
fi

echo "==> Starting API backend..."
docker compose -f "$DOCKER_DIR/donna-app.yml" --env-file "$DOCKER_DIR/.env" up -d

echo "==> Starting Management UI..."
docker compose -f "$DOCKER_DIR/donna-ui.yml" --env-file "$DOCKER_DIR/.env" up -d

echo ""
echo "Donna is running."
echo "  API:        http://localhost:8200/docs"
echo "  Dashboard:  http://localhost:8400"
if [ "$WITH_MONITORING" = true ]; then
  echo "  Grafana:    http://localhost:3000"
fi
