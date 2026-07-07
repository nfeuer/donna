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
CURL_BIN="${DONNA_CURL_BIN:-curl}"

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

alert() {  # msg
  log "deploy_alert" "error" "$1"
  if [ -n "$ALERT_WEBHOOK" ]; then
    "$CURL_BIN" -fsS -m 10 -H 'Content-Type: application/json' \
      -d "{\"content\":\"donna-deploy: $1\"}" "$ALERT_WEBHOOK" >/dev/null 2>&1 \
      || log "deploy_alert_failed" "error" "webhook post failed"
  fi
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
    alert "snapshot build aborted; missing required files: ${missing[*]}"
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
  # --remove-orphans clears containers for services no longer in the compose set
  # (all live donna-* services are defined in COMPOSE_FILES, so nothing live is
  # removed) — keeps big orphaned containers from accumulating across deploys.
  args+=(up -d --remove-orphans)
  "$DOCKER_BIN" "${args[@]}"
}

# After an atomic snapshot swap, an already-running container still holds a bind
# mount to the pre-swap directory inode, so its /donna/config is stale and it
# keeps serving the old in-memory config. Restart (never recreate) the containers
# whose mount source is under $DEPLOY_DIR so they re-resolve the mount and reload
# config — no new containers or orphans, and services that don't mount the
# snapshot (e.g. the heavy Ollama GPU model) are left running untouched.
restart_snapshot_consumers() {
  local names="" c
  for c in $("$DOCKER_BIN" ps --format '{{.Names}}' 2>/dev/null); do
    if "$DOCKER_BIN" inspect "$c" \
        --format '{{range .Mounts}}{{println .Source}}{{end}}' 2>/dev/null \
        | grep -q "^${DEPLOY_DIR}/"; then
      names="$names $c"
    fi
  done
  if [ -n "${names// /}" ]; then
    log "restart_snapshot_consumers" "info" "restarting:${names}"
    # shellcheck disable=SC2086
    "$DOCKER_BIN" restart $names >/dev/null
  else
    log "restart_snapshot_consumers" "info" "no running snapshot-mounted containers"
  fi
}

ensure() {
  local rebuilt=0
  if is_valid; then
    log "ensure_ok" "info" "snapshot valid"
  else
    local ref="HEAD"
    if [ -f "$DEPLOY_DIR/.deployed-sha" ]; then ref="$(cat "$DEPLOY_DIR/.deployed-sha")"; fi
    alert "ensure_rebuild: snapshot invalid/missing; rebuilding from $ref"
    snapshot "$ref"
    rebuilt=1
  fi
  up
  if [ "$rebuilt" = 1 ]; then restart_snapshot_consumers; fi
}

main() {
  local cmd="${1:-deploy}"; shift || true
  case "$cmd" in
    snapshot) snapshot "$@" ;;
    up)       up ;;
    deploy)   snapshot; up; restart_snapshot_consumers ;;
    ensure)   ensure ;;
    *) echo "usage: donna-deploy.sh {snapshot|up|deploy|ensure}" >&2; exit 2 ;;
  esac
}

main "$@"
