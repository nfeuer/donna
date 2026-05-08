#!/usr/bin/env bash
# Bootstrap a fresh Donna checkout into a runnable state.
# Checks system prerequisites, creates a Python venv, installs deps,
# and hands off to `donna setup` for credential configuration.
#
# Usage: ./scripts/bootstrap.sh [--skip-docker-check] [--no-setup]
set -euo pipefail

# ── Tunables ────────────────────────────────────────────────────────
REQUIRED_PYTHON_MINOR=12          # Python 3.x minimum
REQUIRED_DOCKER_MAJOR=24          # Docker Engine minimum
REQUIRED_COMPOSE_MINOR=20         # Docker Compose 2.x minimum

# ── Paths ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_DIR/.venv"

# ── Flags ───────────────────────────────────────────────────────────
SKIP_DOCKER=false
NO_SETUP=false

for arg in "$@"; do
  case "$arg" in
    --skip-docker-check) SKIP_DOCKER=true ;;
    --no-setup)          NO_SETUP=true ;;
    -h|--help)
      echo "Usage: bootstrap.sh [--skip-docker-check] [--no-setup]"
      echo ""
      echo "  --skip-docker-check  Skip Docker/Compose version checks"
      echo "  --no-setup           Stop after venv install (don't launch donna setup)"
      exit 0
      ;;
    *)
      echo "Unknown flag: $arg" >&2
      exit 1
      ;;
  esac
done

# ── Helpers ─────────────────────────────────────────────────────────
ok()   { echo "  [OK]  $1"; }
fail() { echo "  [FAIL] $1" >&2; }
info() { echo "  [INFO] $1"; }

check_command() {
  if ! command -v "$1" &>/dev/null; then
    fail "$1 is not installed."
    return 1
  fi
}

# Extract leading version numbers: "Docker version 29.4.3, ..." → "29.4.3"
parse_version() {
  echo "$1" | grep -oP '\d+\.\d+(\.\d+)?' | head -1
}

version_major() { echo "$1" | cut -d. -f1; }
version_minor() { echo "$1" | cut -d. -f2; }

# ── 1. System dependencies ─────────────────────────────────────────
echo ""
echo "==> Checking system prerequisites..."
echo ""

PREREQ_OK=true

# -- Python ----------------------------------------------------------
if check_command python3; then
  PY_VERSION=$(python3 --version 2>&1)
  PY_PARSED=$(parse_version "$PY_VERSION")
  PY_MAJOR=$(version_major "$PY_PARSED")
  PY_MINOR=$(version_minor "$PY_PARSED")

  if [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -ge "$REQUIRED_PYTHON_MINOR" ]]; then
    ok "Python $PY_PARSED (>= 3.$REQUIRED_PYTHON_MINOR)"
  else
    fail "Python $PY_PARSED found — need >= 3.$REQUIRED_PYTHON_MINOR"
    PREREQ_OK=false
  fi
else
  PREREQ_OK=false
fi

# -- Git --------------------------------------------------------------
if check_command git; then
  ok "git $(parse_version "$(git --version)")"
else
  PREREQ_OK=false
fi

# -- curl -------------------------------------------------------------
if check_command curl; then
  ok "curl $(parse_version "$(curl --version 2>&1 | head -1)")"
else
  PREREQ_OK=false
fi

# -- Docker -----------------------------------------------------------
if [[ "$SKIP_DOCKER" == false ]]; then
  if check_command docker; then
    DOCKER_VERSION=$(parse_version "$(docker --version 2>&1)")
    D_MAJOR=$(version_major "$DOCKER_VERSION")
    if [[ "$D_MAJOR" -ge "$REQUIRED_DOCKER_MAJOR" ]]; then
      ok "Docker $DOCKER_VERSION (>= $REQUIRED_DOCKER_MAJOR)"
    else
      fail "Docker $DOCKER_VERSION found — need >= $REQUIRED_DOCKER_MAJOR"
      PREREQ_OK=false
    fi

    # Docker daemon reachable?
    if docker info &>/dev/null; then
      ok "Docker daemon is running"
    else
      fail "Docker daemon is not reachable (is the service started? is your user in the docker group?)"
      PREREQ_OK=false
    fi
  else
    PREREQ_OK=false
  fi

  # -- Docker Compose ---------------------------------------------------
  if docker compose version &>/dev/null; then
    COMPOSE_VERSION=$(parse_version "$(docker compose version 2>&1)")
    C_MINOR=$(version_minor "$COMPOSE_VERSION")
    if [[ "$C_MINOR" -ge "$REQUIRED_COMPOSE_MINOR" || $(version_major "$COMPOSE_VERSION") -ge 3 ]]; then
      ok "Docker Compose $COMPOSE_VERSION (>= 2.$REQUIRED_COMPOSE_MINOR)"
    else
      fail "Docker Compose $COMPOSE_VERSION found — need >= 2.$REQUIRED_COMPOSE_MINOR"
      PREREQ_OK=false
    fi
  else
    fail "Docker Compose v2 plugin is not installed (docker compose version failed)"
    PREREQ_OK=false
  fi
else
  info "Skipping Docker checks (--skip-docker-check)"
fi

if [[ "$PREREQ_OK" == false ]]; then
  echo ""
  echo "Some prerequisites are missing. Install them and re-run this script."
  echo "See SETUP.md §1 for installation instructions."
  exit 1
fi

echo ""
echo "==> All system prerequisites satisfied."

# ── 2. Python virtual environment ───────────────────────────────────
echo ""
echo "==> Setting up Python virtual environment..."
echo ""

NEED_VENV=false

if [[ ! -d "$VENV_DIR" ]]; then
  info "No .venv found — creating one."
  NEED_VENV=true
elif [[ ! -x "$VENV_DIR/bin/python" ]]; then
  info ".venv exists but has no usable python — recreating."
  rm -rf "$VENV_DIR"
  NEED_VENV=true
else
  # Detect stale venv: check if the Python binary is a dead symlink
  # or points outside this project's expected locations.
  VENV_PY_REAL=$(readlink -f "$VENV_DIR/bin/python" 2>/dev/null || true)
  if [[ -z "$VENV_PY_REAL" || ! -x "$VENV_PY_REAL" ]]; then
    info ".venv contains a broken Python symlink — recreating."
    rm -rf "$VENV_DIR"
    NEED_VENV=true
  else
    # Check the venv's site-packages actually have donna installed
    if "$VENV_DIR/bin/python" -c "import donna" &>/dev/null; then
      ok "Existing .venv is healthy"
    else
      info ".venv exists but donna is not installed — will reinstall deps."
      NEED_VENV=false  # venv is fine, just needs deps
    fi
  fi
fi

# Prefer uv for speed, fall back to pip+venv
HAS_UV=false
if command -v uv &>/dev/null; then
  HAS_UV=true
fi

if [[ "$NEED_VENV" == true ]]; then
  if [[ "$HAS_UV" == true ]]; then
    info "Creating venv with uv..."
    uv venv "$VENV_DIR" --python 3.12
  else
    info "Creating venv with python3 -m venv..."
    python3 -m venv "$VENV_DIR"
  fi
fi

# ── 3. Install dependencies ─────────────────────────────────────────
echo ""
echo "==> Installing dependencies..."
echo ""

if [[ "$HAS_UV" == true ]]; then
  info "Using uv sync (fast path)..."
  (cd "$PROJECT_DIR" && uv sync --extra dev)
else
  info "Using pip install (uv not found — install it for faster installs: pip install uv)..."
  "$VENV_DIR/bin/pip" install --upgrade pip
  (cd "$PROJECT_DIR" && "$VENV_DIR/bin/pip" install -e ".[dev]")
fi

# ── 4. Verify donna CLI ─────────────────────────────────────────────
echo ""
echo "==> Verifying donna CLI..."
echo ""

if "$VENV_DIR/bin/donna" --help &>/dev/null; then
  ok "donna CLI is working"
else
  fail "donna CLI failed to start — check the install output above for errors."
  exit 1
fi

# ── 5. Summary ──────────────────────────────────────────────────────
echo ""
echo "=========================================="
echo "  Bootstrap complete."
echo "=========================================="
echo ""
echo "  Activate the venv:   source .venv/bin/activate"
echo "  Run the setup wizard: donna setup --phase 1"
echo ""

if [[ "$NO_SETUP" == false ]]; then
  echo "Launching the setup wizard now..."
  echo ""
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  exec donna setup
fi
