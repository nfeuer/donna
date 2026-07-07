# Deploy Snapshot & Boot Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Donna stack survive reboots and machine moves unattended by replacing the ad-hoc `deploy-main` snapshot with a committed, validated, self-healing deploy script + systemd unit that fails loud on snapshot loss.

**Architecture:** A single committed bash script `scripts/donna-deploy.sh` builds a frozen snapshot of the stack from committed git state (`git archive`), overlays gitignored secrets, validates required files, and atomically swaps it into `/mnt/donna/deploy-main`. A oneshot systemd unit runs the script's `ensure` mode on boot to rebuild a missing snapshot and bring the stack up. Any rebuild or validation failure posts to an alert webhook and logs structured output.

**Tech Stack:** Bash, `git archive`, Docker Compose (project `docker`), systemd, pytest (via `uv run`) using subprocess + a fake `docker` PATH shim and a temp git repo for tests.

**Design reference:** `docs/superpowers/specs/2026-06-23-deploy-snapshot-resilience-design.md`; `spec_v3.md` §3.5 / §3.5.1.

## Global Constraints

- Target host paths: repo `/mnt/donna/donna`, snapshot `/mnt/donna/deploy-main`. All host paths in the script MUST be overridable via env vars so tests run in temp dirs.
- Canonical compose project name is **`docker`** (matches the live containers — do NOT introduce a `donna` project, which would duplicate containers).
- Compose files (in `deploy-main/docker/`): `donna-core.yml`, `donna-app.yml`, `donna-ui.yml`, `donna-monitoring.yml`, `donna-ollama.yml`. Env file: `deploy-main/docker/.env`.
- Snapshot is built from **committed** state (`git archive <ref>`), never the working tree. Gitignored secrets are overlaid separately: `docker/.env`, `docker/google_credentials.json`, `config/google_credentials.json`, `config/token.json`.
- Required-file validation set: `config/donna_models.yaml`, `docker/.env`, `docker/donna-core.yml`. A snapshot missing any of these MUST NOT be published.
- Bash scripts use `set -euo pipefail`. No `print`-style debug; emit single-line JSON via the `log` helper. Tests live under `tests/unit/scripts/` and run with `uv run pytest`.
- Commit messages cite the spec per `CLAUDE.md` and end with the `Co-Authored-By` / `Claude-Session` trailers.

---

## File Structure

- `scripts/donna-deploy.sh` — **new.** The whole deploy tool: `snapshot` / `up` / `deploy` / `ensure`. Single responsibility: build & run the snapshot.
- `systemd/donna.service` — **new.** Oneshot boot unit invoking `donna-deploy.sh ensure`.
- `tests/unit/scripts/test_donna_deploy.py` — **new.** pytest harness driving the script against a temp git repo with a fake `docker`/`curl` shim.
- `tests/unit/scripts/__init__.py` — **new.** Package marker.
- `scripts/donna-up.sh`, `scripts/donna-down.sh` — **modify.** Re-point at the snapshot / project `docker` (or thin-wrap `donna-deploy.sh`) so they stop contradicting the running model.
- `docs/operations/deployment.md` — **new.** Operator doc for the deploy model.
- `docs/operations/docker.md` — **modify.** Cross-link to the new deploy doc.
- `spec_v3.md` — **modify.** Add §3.5.3.
- `docs/superpowers/specs/followups.md` — **modify.** Append the three follow-ups.

---

## Task 1: Snapshot build (`donna-deploy.sh snapshot`)

**Files:**
- Create: `scripts/donna-deploy.sh`
- Create: `tests/unit/scripts/__init__.py`
- Test: `tests/unit/scripts/test_donna_deploy.py`

**Interfaces:**
- Produces: an executable `scripts/donna-deploy.sh` honoring env overrides `DONNA_REPO_DIR`, `DONNA_DEPLOY_DIR`, `DONNA_DEPLOY_REF`, `DONNA_COMPOSE_PROJECT`, `DONNA_DOCKER_BIN`, `DONNA_ALERT_WEBHOOK`. Subcommand `snapshot [ref]` builds a validated snapshot at `$DONNA_DEPLOY_DIR` containing `config/`, `prompts/`, `schemas/`, `docker/`, overlaid secrets, and a `.deployed-sha` file; exits non-zero without publishing if a required file is missing.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/scripts/__init__.py` (empty), then `tests/unit/scripts/test_donna_deploy.py`:

```python
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "donna-deploy.sh"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _make_repo(tmp_path: Path) -> Path:
    """A minimal Donna repo: committed config/prompts/schemas/docker + gitignored secrets."""
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "prompts").mkdir()
    (repo / "schemas").mkdir()
    (repo / "docker").mkdir()
    (repo / "config" / "donna_models.yaml").write_text("models: {}\n")
    (repo / "prompts" / "p.md").write_text("p\n")
    (repo / "schemas" / "s.json").write_text("{}\n")
    (repo / "docker" / "donna-core.yml").write_text("services: {}\n")
    (repo / ".gitignore").write_text("docker/.env\nconfig/token.json\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    # gitignored secrets exist only in the working tree
    (repo / "docker" / ".env").write_text("SECRET=1\n")
    (repo / "config" / "token.json").write_text("{\"tok\":1}\n")
    return repo


def _run(repo: Path, deploy: Path, *args: str, **env_extra):
    env = {**os.environ, "DONNA_REPO_DIR": str(repo),
           "DONNA_DEPLOY_DIR": str(deploy), **env_extra}
    return subprocess.run(["bash", str(SCRIPT), *args], env=env,
                          capture_output=True, text=True)


def test_snapshot_builds_validated_tree(tmp_path):
    repo = _make_repo(tmp_path)
    deploy = tmp_path / "deploy-main"
    r = _run(repo, deploy, "snapshot")
    assert r.returncode == 0, r.stderr
    assert (deploy / "config" / "donna_models.yaml").is_file()
    assert (deploy / "docker" / ".env").read_text() == "SECRET=1\n"      # secret overlaid
    assert (deploy / "config" / "token.json").is_file()                   # secret overlaid
    assert (deploy / ".deployed-sha").read_text().strip()                 # sha recorded


def test_snapshot_aborts_and_keeps_old_when_required_file_missing(tmp_path):
    repo = _make_repo(tmp_path)
    deploy = tmp_path / "deploy-main"
    _run(repo, deploy, "snapshot")  # first good snapshot
    # remove a required file from the committed tree and re-commit
    (repo / "config" / "donna_models.yaml").unlink()
    _git(repo, "commit", "-aqm", "drop models")
    r = _run(repo, deploy, "snapshot")
    assert r.returncode != 0
    # previous valid snapshot is still in place (atomic: never left empty/partial)
    assert (deploy / "config" / "donna_models.yaml").is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/scripts/test_donna_deploy.py -v`
Expected: FAIL — `donna-deploy.sh` does not exist (non-zero return, assertion on `r.returncode`).

- [ ] **Step 3: Write minimal implementation**

Create `scripts/donna-deploy.sh`:

```bash
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
  printf '{"event":"%s","level":"%s","msg":"%s","ts":"%s"}\n' \
    "$1" "$2" "$3" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
}

snapshot() {
  local ref="${1:-$DEPLOY_REF}"
  local staging="${DEPLOY_DIR}.staging.$$"
  rm -rf "$staging"; mkdir -p "$staging"

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

main() {
  local cmd="${1:-deploy}"; shift || true
  case "$cmd" in
    snapshot) snapshot "$@" ;;
    *) echo "usage: donna-deploy.sh {snapshot|up|deploy|ensure}" >&2; exit 2 ;;
  esac
}

main "$@"
```

Make it executable: `chmod +x scripts/donna-deploy.sh`

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/scripts/test_donna_deploy.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/donna-deploy.sh tests/unit/scripts/__init__.py tests/unit/scripts/test_donna_deploy.py
git commit -m "feat(deploy): snapshot builder for donna-deploy.sh

Builds a validated, frozen stack snapshot from committed git state with
secret overlay and atomic swap. Refs spec_v3.md §3.5; design
2026-06-23-deploy-snapshot-resilience.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K7Ppai9Xbhv5iEuGBwi3ty"
```

---

## Task 2: `up`, `deploy`, and self-healing `ensure`

**Files:**
- Modify: `scripts/donna-deploy.sh`
- Test: `tests/unit/scripts/test_donna_deploy.py`

**Interfaces:**
- Consumes: `snapshot`, `$DEPLOY_DIR`, `$DONNA_DOCKER_BIN`, `$COMPOSE_PROJECT`, `$COMPOSE_FILES`, `$REQUIRED_FILES` from Task 1.
- Produces: `up` runs `"$DOCKER_BIN" compose --project-name docker --env-file <deploy>/docker/.env -f … up -d`; `is_valid` returns 0 iff all required files exist under `$DEPLOY_DIR`; `ensure` rebuilds the snapshot (from `.deployed-sha`, fallback `HEAD`) when invalid, then runs `up`; `deploy` = `snapshot` + `up`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/scripts/test_donna_deploy.py`:

```python
def _fake_docker(tmp_path: Path) -> Path:
    """A fake `docker` that appends its args to a log file, so `up` is observable."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    logf = tmp_path / "docker.log"
    fake = bindir / "docker"
    fake.write_text("#!/usr/bin/env bash\necho \"$@\" >> \"%s\"\n" % logf)
    fake.chmod(0o755)
    return logf


def test_ensure_rebuilds_when_snapshot_missing(tmp_path):
    repo = _make_repo(tmp_path)
    deploy = tmp_path / "deploy-main"
    logf = _fake_docker(tmp_path)
    # deploy dir never built -> ensure must rebuild then call docker compose up
    r = _run(repo, deploy, "ensure", DONNA_DOCKER_BIN=str(tmp_path / "bin" / "docker"))
    assert r.returncode == 0, r.stderr
    assert (deploy / "config" / "donna_models.yaml").is_file()       # rebuilt
    up_log = logf.read_text()
    assert "compose" in up_log and "up -d" in up_log                  # stack brought up
    assert "--project-name docker" in up_log                          # canonical project


def test_ensure_uses_existing_valid_snapshot_without_rebuild(tmp_path):
    repo = _make_repo(tmp_path)
    deploy = tmp_path / "deploy-main"
    logf = _fake_docker(tmp_path)
    _run(repo, deploy, "snapshot")
    sha_before = (deploy / ".deployed-sha").read_text()
    # advance HEAD; a valid snapshot must NOT be rebuilt to the new HEAD
    (repo / "prompts" / "p2.md").write_text("p2\n")
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "more")
    r = _run(repo, deploy, "ensure", DONNA_DOCKER_BIN=str(tmp_path / "bin" / "docker"))
    assert r.returncode == 0, r.stderr
    assert (deploy / ".deployed-sha").read_text() == sha_before        # unchanged
    assert "up -d" in logf.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/scripts/test_donna_deploy.py -k ensure -v`
Expected: FAIL — `ensure`/`up` not implemented (`usage:` error, return code 2).

- [ ] **Step 3: Write minimal implementation**

In `scripts/donna-deploy.sh`, add these functions immediately before `main()`:

```bash
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
```

Then extend the `case` in `main()` to:

```bash
  case "$cmd" in
    snapshot) snapshot "$@" ;;
    up)       up ;;
    deploy)   snapshot; up ;;
    ensure)   ensure ;;
    *) echo "usage: donna-deploy.sh {snapshot|up|deploy|ensure}" >&2; exit 2 ;;
  esac
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/scripts/test_donna_deploy.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/donna-deploy.sh tests/unit/scripts/test_donna_deploy.py
git commit -m "feat(deploy): add up/deploy/ensure with boot self-heal

ensure rebuilds a missing snapshot from the recorded SHA (fallback HEAD)
then brings the stack up under the canonical 'docker' project. Refs
spec_v3.md §3.5.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K7Ppai9Xbhv5iEuGBwi3ty"
```

---

## Task 3: Fail-loud alerting

**Files:**
- Modify: `scripts/donna-deploy.sh`
- Test: `tests/unit/scripts/test_donna_deploy.py`

**Interfaces:**
- Consumes: `$ALERT_WEBHOOK`, `log` from earlier tasks.
- Produces: `alert <msg>` logs at error level and, if `$ALERT_WEBHOOK` is set, POSTs a JSON body to it via `$DONNA_CURL_BIN` (default `curl`). `snapshot` abort and `ensure` rebuild both call `alert`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/scripts/test_donna_deploy.py`:

```python
def _fake_curl(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    logf = tmp_path / "curl.log"
    fake = bindir / "curl"
    fake.write_text("#!/usr/bin/env bash\necho \"$@\" >> \"%s\"\n" % logf)
    fake.chmod(0o755)
    return logf


def test_alert_fires_on_rebuild(tmp_path):
    repo = _make_repo(tmp_path)
    deploy = tmp_path / "deploy-main"
    _fake_docker(tmp_path)
    curl_log = _fake_curl(tmp_path)
    r = _run(repo, deploy, "ensure",
             DONNA_DOCKER_BIN=str(tmp_path / "bin" / "docker"),
             DONNA_CURL_BIN=str(tmp_path / "bin" / "curl"),
             DONNA_ALERT_WEBHOOK="https://hook.example/x")
    assert r.returncode == 0, r.stderr
    assert "hook.example" in curl_log.read_text()           # webhook was posted
    assert "ensure_rebuild" in r.stderr or "rebuild" in r.stderr  # loud log too


def test_no_alert_when_webhook_unset(tmp_path):
    repo = _make_repo(tmp_path)
    deploy = tmp_path / "deploy-main"
    _fake_docker(tmp_path)
    curl_log = _fake_curl(tmp_path)
    _run(repo, deploy, "ensure",
         DONNA_DOCKER_BIN=str(tmp_path / "bin" / "docker"),
         DONNA_CURL_BIN=str(tmp_path / "bin" / "curl"))
    assert not curl_log.exists() or curl_log.read_text() == ""   # no post attempted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/scripts/test_donna_deploy.py -k alert -v`
Expected: FAIL — no `alert`/webhook post (`curl.log` absent / `hook.example` not found).

- [ ] **Step 3: Write minimal implementation**

In `scripts/donna-deploy.sh`, add `CURL_BIN` to the config block (under `ALERT_WEBHOOK`):

```bash
CURL_BIN="${DONNA_CURL_BIN:-curl}"
```

Add the `alert` helper immediately after `log()`:

```bash
alert() {  # msg
  log "deploy_alert" "error" "$1"
  if [ -n "$ALERT_WEBHOOK" ]; then
    "$CURL_BIN" -fsS -m 10 -H 'Content-Type: application/json' \
      -d "{\"content\":\"donna-deploy: $1\"}" "$ALERT_WEBHOOK" >/dev/null 2>&1 \
      || log "deploy_alert_failed" "error" "webhook post failed"
  fi
}
```

In `snapshot()`, replace the abort `log` line with an `alert`:

```bash
    alert "snapshot build aborted; missing required files: ${missing[*]}"
    return 1
```

In `ensure()`, replace the rebuild `log` line with an `alert` (keep the event keyword so the log test still matches):

```bash
    alert "ensure_rebuild: snapshot invalid/missing; rebuilding from $ref"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/scripts/test_donna_deploy.py -v`
Expected: PASS (all tests, including the two alert tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/donna-deploy.sh tests/unit/scripts/test_donna_deploy.py
git commit -m "feat(deploy): fail loud on snapshot loss via alert webhook

Snapshot-abort and ensure-rebuild now post to DONNA_ALERT_WEBHOOK and log
at error level, closing the silent-failure gap behind the 2026-06-22
outage. Refs spec_v3.md §3.5.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K7Ppai9Xbhv5iEuGBwi3ty"
```

---

## Task 4: systemd boot unit + host verification

**Files:**
- Create: `systemd/donna.service`

**Interfaces:**
- Consumes: `scripts/donna-deploy.sh ensure`.
- Produces: an installable oneshot unit that runs `ensure` on boot.

This task has no unit test (it exercises real systemd/docker). It ends with documented host-verification steps that the operator runs once on the actual machine.

- [ ] **Step 1: Create the unit file**

Create `systemd/donna.service`:

```ini
[Unit]
Description=Donna stack ensure (snapshot + compose up)
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=DONNA_ALERT_WEBHOOK=
ExecStart=/mnt/donna/donna/scripts/donna-deploy.sh ensure
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit the unit file**

```bash
git add systemd/donna.service
git commit -m "feat(deploy): systemd oneshot to self-heal snapshot on boot

Runs donna-deploy.sh ensure after docker.service so a missing snapshot is
rebuilt and the stack comes up unattended. Refs spec_v3.md §3.5.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K7Ppai9Xbhv5iEuGBwi3ty"
```

- [ ] **Step 3: Install on the host (operator-run, once)**

Set the real webhook in the unit (edit `Environment=DONNA_ALERT_WEBHOOK=…` to the Discord debug webhook), then:

```bash
sudo cp /mnt/donna/donna/systemd/donna.service /etc/systemd/system/donna.service
sudo systemctl daemon-reload
sudo systemctl enable donna.service
```

- [ ] **Step 4: Verify self-heal without rebooting**

Simulate snapshot loss and confirm `ensure` heals it:

```bash
sudo mv /mnt/donna/deploy-main /mnt/donna/deploy-main.bak
sudo systemctl start donna.service
sudo systemctl status donna.service --no-pager        # expect: active (exited), no failure
docker ps --filter name=donna-orchestrator --format '{{.Status}}'   # expect: Up … (healthy)
```
Expected: `deploy-main` rebuilt, orchestrator `healthy`, and (if webhook configured) a Discord alert about the rebuild. Then remove the backup: `sudo rm -rf /mnt/donna/deploy-main.bak`.

- [ ] **Step 5: Reboot drill (operator-run)**

`sudo reboot`, wait, then:
```bash
docker ps --filter name=donna --format '{{.Names}}: {{.Status}}'
```
Expected: orchestrator and api `Up … (healthy)`, restart count not climbing.

---

## Task 5: Reconcile helper scripts, docs, and spec

**Files:**
- Modify: `scripts/donna-up.sh`, `scripts/donna-down.sh`
- Create: `docs/operations/deployment.md`
- Modify: `docs/operations/docker.md`
- Modify: `spec_v3.md`
- Modify: `docs/superpowers/specs/followups.md`

**Interfaces:**
- Consumes: `donna-deploy.sh` from Tasks 1–3.
- Produces: documentation and a non-contradictory helper-script story.

- [ ] **Step 1: Re-point `donna-up.sh` / `donna-down.sh` at the snapshot**

Make both operate on `/mnt/donna/deploy-main/docker` with project `docker`. In `scripts/donna-up.sh`, replace the `DOCKER_DIR` line and project name:

```bash
# Run the committed snapshot, not the live working tree.
DEPLOY_DIR="${DONNA_DEPLOY_DIR:-/mnt/donna/deploy-main}"
DOCKER_DIR="$DEPLOY_DIR/docker"
export COMPOSE_PROJECT_NAME=docker
```
Apply the identical two-line change (`DOCKER_DIR`, `COMPOSE_PROJECT_NAME=docker`) to `scripts/donna-down.sh`. Add a one-line banner near the top of each: `echo "(operating on snapshot $DEPLOY_DIR — run scripts/donna-deploy.sh deploy to refresh it)"`.

- [ ] **Step 2: Verify the helpers still parse**

Run: `bash -n scripts/donna-up.sh && bash -n scripts/donna-down.sh && echo OK`
Expected: `OK` (no syntax errors).

- [ ] **Step 3: Write the operator doc**

Create `docs/operations/deployment.md`:

```markdown
# Deployment

Design reference: [`spec_v3.md` §3.5.3 Snapshot Deploy & Boot Resilience](../reference-specs/spec-v3.md)
and [design doc](../superpowers/specs/2026-06-23-deploy-snapshot-resilience-design.md).

The production stack runs from a **committed snapshot** at `/mnt/donna/deploy-main`,
not the live git checkout. The snapshot is frozen committed state plus gitignored
secrets, so editing the repo never affects the running stack until you deploy.

## Commands (`scripts/donna-deploy.sh`)

| Command   | Action |
|-----------|--------|
| `deploy`  | Build a fresh snapshot from `HEAD` and bring the stack up. Run this to ship. |
| `snapshot`| Build/validate the snapshot only. |
| `up`      | `docker compose up -d` from the existing snapshot (project `docker`). |
| `ensure`  | Boot mode: rebuild the snapshot if missing/invalid, then `up`. |

Required files (`config/donna_models.yaml`, `docker/.env`, `docker/donna-core.yml`)
are validated before publish; a partial snapshot is never swapped in.

## Boot self-heal

`systemd/donna.service` (oneshot, after `docker.service`) runs `ensure` on every
boot. A missing snapshot (e.g. after a machine move) is rebuilt automatically and
an alert is posted to `DONNA_ALERT_WEBHOOK`.

## Secrets

Gitignored secrets (`docker/.env`, `config/google_credentials.json`,
`config/token.json`, `docker/google_credentials.json`) are overlaid from the repo
working tree during `snapshot`. See follow-up to relocate these to a vault.
```

- [ ] **Step 4: Cross-link from `docker.md`**

In `docs/operations/docker.md`, under the `## Bring-Up` section (after the `Helper scripts:` line), add:

```markdown
> **Production deploys run from a committed snapshot — see [Deployment](deployment.md).**
> `docker compose … up` here targets the live repo and is for local/dev use.
```

- [ ] **Step 5: Add spec §3.5.3**

In `spec_v3.md`, immediately after the §3.5.2 GPU Isolation block, insert:

```markdown
**3.5.3 Snapshot Deploy & Boot Resilience**

The production stack runs from a committed, validated snapshot at
`/mnt/donna/deploy-main`, decoupling the always-on stack from the live
development checkout. `scripts/donna-deploy.sh` is the deploy entry point
(`snapshot` / `up` / `deploy` / `ensure`): it builds the snapshot from
committed git state via `git archive`, overlays gitignored secrets, validates
required files (`config/donna_models.yaml`, `docker/.env`, `docker/donna-core.yml`),
and atomically swaps it into place. A oneshot systemd unit (`donna.service`,
after `docker.service`) runs `ensure` on boot to rebuild a missing snapshot and
bring the stack up. Snapshot loss or missing required config posts to
`DONNA_ALERT_WEBHOOK` and logs at error level, so a failed deploy can never crash
the orchestrator silently. The compose project name is `docker`.
```

- [ ] **Step 6: Append follow-ups**

Append to `docs/superpowers/specs/followups.md`:

```markdown
## 2026-06-24 — Deploy snapshot resilience

- **Secrets out of the dev tree:** `donna-deploy.sh snapshot` overlays secrets
  (`docker/.env`, `config/google_credentials.json`, `config/token.json`,
  `docker/google_credentials.json`) from the repo working tree. Relocate to a
  dedicated secrets dir or `/mnt/donna/vault` so deploys don't read secrets from
  the IDE workspace.
- **Healthwatch alert gap:** `donna-healthwatch` ran throughout the 2026-06-22
  orchestrator crash loop (>18h) without paging. Investigate why and close.
- **Orchestrator startup guard (prevention #2):** as defense-in-depth behind the
  deploy-layer guard, have the orchestrator detect missing config at startup and
  emit a notification before exiting (ref `dispatch_fallback_alert`).
```

- [ ] **Step 7: Commit**

```bash
git add scripts/donna-up.sh scripts/donna-down.sh docs/operations/deployment.md docs/operations/docker.md spec_v3.md docs/superpowers/specs/followups.md
git commit -m "docs(deploy): reconcile helpers, document snapshot model, spec §3.5.3

Re-points donna-up/down at the snapshot, adds operations/deployment.md,
spec_v3.md §3.5.3, and three follow-ups. Refs spec_v3.md §3.5.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01K7Ppai9Xbhv5iEuGBwi3ty"
```

---

## Self-Review

**Spec coverage:**
- Snapshot from committed state + secret overlay + validation + atomic swap → Task 1. ✓
- `up`/`deploy`/`ensure` self-heal, project name `docker` → Task 2. ✓
- Fail-loud alerting → Task 3. ✓
- systemd boot unit + reboot/self-heal verification → Task 4. ✓
- Reconcile `donna-up.sh`, docs, spec §3.5.3, follow-ups (vault, healthwatch, orchestrator guard) → Task 5. ✓
- Non-goal (orchestrator-internal guard) correctly deferred to a follow-up, not a task. ✓

**Placeholder scan:** No TBD/TODO; every code and command step contains literal content. The `donna-up.sh` retire-vs-align decision from the spec is resolved here as "align" (Task 5, Step 1).

**Type/name consistency:** Env vars (`DONNA_REPO_DIR`, `DONNA_DEPLOY_DIR`, `DONNA_DEPLOY_REF`, `DONNA_COMPOSE_PROJECT`, `DONNA_DOCKER_BIN`, `DONNA_ALERT_WEBHOOK`, `DONNA_CURL_BIN`), function names (`snapshot`, `up`, `is_valid`, `ensure`, `log`, `alert`), required-file set, and compose-file list are identical across Tasks 1–5. The `ensure_rebuild` keyword is preserved in the Task 3 alert message so the Task 2 log assertion stays valid.
