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


def _fake_docker(tmp_path: Path) -> Path:
    """A fake `docker` that appends its args to a log file, so `up` is observable."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    logf = tmp_path / "docker.log"
    fake = bindir / "docker"
    fake.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{logf}"\n')
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
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "more")
    r = _run(repo, deploy, "ensure", DONNA_DOCKER_BIN=str(tmp_path / "bin" / "docker"))
    assert r.returncode == 0, r.stderr
    assert (deploy / ".deployed-sha").read_text() == sha_before        # unchanged
    assert "up -d" in logf.read_text()


def _fake_curl(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    logf = tmp_path / "curl.log"
    fake = bindir / "curl"
    fake.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{logf}"\n')
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
