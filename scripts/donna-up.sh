#!/usr/bin/env bash
# Start Donna Docker Compose stacks in order.
# Usage: ./donna-up.sh [--with-monitoring] [--with-ollama] [--with-dashboard] [--all]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# Run the committed snapshot, not the live working tree.
DEPLOY_DIR="${DONNA_DEPLOY_DIR:-/mnt/donna/deploy-main}"
DOCKER_DIR="$DEPLOY_DIR/docker"
export COMPOSE_PROJECT_NAME=docker
echo "(operating on snapshot $DEPLOY_DIR — run scripts/donna-deploy.sh deploy to refresh it)"

WITH_MONITORING=false
WITH_OLLAMA=false
WITH_DASHBOARD=false

for arg in "$@"; do
  case "$arg" in
    --with-monitoring) WITH_MONITORING=true ;;
    --with-ollama)     WITH_OLLAMA=true ;;
    --with-dashboard)  WITH_DASHBOARD=true ;;
    --all)             WITH_MONITORING=true; WITH_OLLAMA=true; WITH_DASHBOARD=true ;;
    -h|--help)
      echo "Usage: donna-up.sh [--with-monitoring] [--with-ollama] [--with-dashboard] [--all]"
      echo ""
      echo "  (no flags)         Start core orchestrator only"
      echo "  --with-monitoring  Add Grafana + Loki + Promtail"
      echo "  --with-ollama      Add local LLM (Phase 3)"
      echo "  --with-dashboard   Add API backend + management UI (requires Immich auth)"
      echo "  --all              Start everything"
      exit 0
      ;;
    *)
      echo "Unknown flag: $arg" >&2
      exit 1
      ;;
  esac
done

echo "==> Starting Donna core (orchestrator)..."
docker compose -f "$DOCKER_DIR/donna-core.yml" --env-file "$DOCKER_DIR/.env" up --build -d

if [ "$WITH_MONITORING" = true ]; then
  echo "==> Starting monitoring stack (Grafana + Loki + Promtail)..."
  docker compose -f "$DOCKER_DIR/donna-monitoring.yml" --env-file "$DOCKER_DIR/.env" up -d
fi

if [ "$WITH_OLLAMA" = true ]; then
  echo "==> Starting Ollama (local LLM)..."
  docker compose -f "$DOCKER_DIR/donna-ollama.yml" --env-file "$DOCKER_DIR/.env" up -d
fi

if [ "$WITH_DASHBOARD" = true ]; then
  echo "==> Starting API backend..."
  docker compose -f "$DOCKER_DIR/donna-app.yml" --env-file "$DOCKER_DIR/.env" up --build -d
  echo "==> Starting Management UI..."
  docker compose -f "$DOCKER_DIR/donna-ui.yml" --env-file "$DOCKER_DIR/.env" up --build -d
fi

echo ""
echo "Donna is running."
echo "  Health:     http://localhost:8100/health"
if [ "$WITH_DASHBOARD" = true ]; then
  echo "  API:        http://localhost:8200/docs"
  echo "  Dashboard:  http://localhost:8400"
fi
if [ "$WITH_MONITORING" = true ]; then
  echo "  Grafana:    http://localhost:3000"
fi
