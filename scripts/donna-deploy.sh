#!/usr/bin/env bash
# donna-deploy.sh — build & run a committed snapshot of the Donna stack.
# Subcommands: snapshot [ref] | up | deploy | ensure
# Design: docs/superpowers/specs/2026-06-23-deploy-snapshot-resilience-design.md
set -euo pipefail

REPO_DIR="${DONNA_REPO_DIR:-/mnt/donna/donna}"
DEPLOY_DIR="${DONNA_DEPLOY_DIR:-/mnt/donna/deploy-main}"
DEPLOY_REF="${DONNA_DEPLOY_REF:-HEAD}"
COMPOSE_PROJECT="${DONNA_COMPOSE_PROJECT:-docker}"
DOCKER_BIN="${DONNA_DOCKER_BIN:-docker}"
ALERT_WEBHOOK="${DONNA_ALERT_WEBHOOK:-}"

ARCHIVE_PATHS=(config prompts schemas docker)
SECRET_FILES=(docker/.env docker/google_credentials.json config/google_credentials.json config/token.json)
REQUIRED_FILES=(config/donna_models.yaml docker/.env docker/donna-core.yml)
COMPOSE_FILES=(donna-core.yml donna-app.yml donna-ui.yml donna-monitoring.yml donna-ollama.yml)

log() {  # event level msg
  local msg="$3"
  msg="${msg//\\/\\\\}"
  msg="${msg//\"/\\\"}"
  printf '{"event":"%s","level":"%s","msg":"%s","ts":"%s"}\n' \
    "$1" "$2" "$msg" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
}

snapshot() {
  local ref="${1:-$DEPLOY_REF}"
  local staging="${DEPLOY_DIR}.staging.$$"
  rm -rf "$staging"; mkdir -p "$staging"
  trap 'rm -rf "${staging:-}"' RETURN

  git -C "$REPO_DIR" archive "$ref" "${ARCHIVE_PATHS[@]}" | tar -x -C "$staging"

  local f
  for f in "${SECRET_FILES[@]}"; do
    if [ -f "$REPO_DIR/$f" ]; then
      mkdir -p "$staging/$(dirname "$f")"
      cp -p "$REPO_DIR/$f" "$staging/$f"
    fi
  done

  local missing=()
  for f in "${REQUIRED_FILES[@]}"; do
    [ -f "$staging/$f" ] || missing+=("$f")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    rm -rf "$staging"
    log "snapshot_aborted" "error" "missing required files: ${missing[*]}"
    return 1
  fi

  git -C "$REPO_DIR" rev-parse "$ref" > "$staging/.deployed-sha"

  local backup="${DEPLOY_DIR}.old.$$"
  if [ -e "$DEPLOY_DIR" ]; then mv "$DEPLOY_DIR" "$backup"; fi
  mv "$staging" "$DEPLOY_DIR"
  rm -rf "$backup"
  log "snapshot_built" "info" "deployed $(cat "$DEPLOY_DIR/.deployed-sha")"
}

is_valid() {
  local f
  for f in "${REQUIRED_FILES[@]}"; do
    [ -f "$DEPLOY_DIR/$f" ] || return 1
  done
  return 0
}

up() {
  local args=(compose --project-name "$COMPOSE_PROJECT" --env-file "$DEPLOY_DIR/docker/.env")
  local f
  for f in "${COMPOSE_FILES[@]}"; do args+=(-f "$DEPLOY_DIR/docker/$f"); done
  args+=(up -d)
  "$DOCKER_BIN" "${args[@]}"
}

ensure() {
  if is_valid; then
    log "ensure_ok" "info" "snapshot valid"
  else
    local ref="HEAD"
    if [ -f "$DEPLOY_DIR/.deployed-sha" ]; then ref="$(cat "$DEPLOY_DIR/.deployed-sha")"; fi
    log "ensure_rebuild" "warning" "snapshot invalid/missing; rebuilding from $ref"
    snapshot "$ref"
  fi
  up
}

main() {
  local cmd="${1:-deploy}"; shift || true
  case "$cmd" in
    snapshot) snapshot "$@" ;;
    up)       up ;;
    deploy)   snapshot; up ;;
    ensure)   ensure ;;
    *) echo "usage: donna-deploy.sh {snapshot|up|deploy|ensure}" >&2; exit 2 ;;
  esac
}

main "$@"
