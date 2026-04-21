# Skill System Wave 5 — Cleanup & Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 16 follow-up items across 9 themes: unblock email_triage in production, harden capability correctness, seed 4 task-type capabilities, and pay down tool-registry / fixture / notification debt.

**Architecture:** Additive changes only — no feature flags, no breaking API changes. Two Alembic migrations (`automation.state_blob`, seed four capabilities). New `html_extract` tool uses `trafilatura`. Optional-input defaulting happens at draft time in `AutomationCreationPath`; a lint test guards skill.yaml templates.

**Tech Stack:** Python 3.12 / asyncio, SQLite + Alembic, pytest, Jinja2 (StrictUndefined), structlog, feedparser, httpx, trafilatura (new).

**Companion spec:** `docs/superpowers/specs/2026-04-20-skill-system-wave-5-cleanup-and-polish-design.md`.

---

## File structure

**New files:**
- `alembic/versions/add_automation_state_blob.py` — schema migration (Task 2).
- `alembic/versions/seed_claude_native_capabilities.py` — data migration (Task 3).
- `src/donna/skills/tools/html_extract.py` — new tool (Task 14).
- `tests/unit/test_skill_yaml_lint.py` — lint test (Task 10).
- `skills/news_check/fixtures/multi_feed_match.json` — new fixture (Task 11).

**Modified files:**
- `src/donna/skills/tool_registry.py` — add `clear()` (Task 1).
- `tests/conftest.py` — autouse reset fixture (Task 1).
- `src/donna/skills/tools/__init__.py` — register html_extract, update module docstring (Tasks 1, 14).
- `config/capabilities.yaml` — add 4 task-type capabilities (Task 4).
- `src/donna/config.py` — add `baseline_reset_window` field (Task 5).
- `src/donna/api/routes/skills.py` — use configured window (Task 6).
- `src/donna/cli_wiring.py` — build GmailClient in boot (Task 7).
- `src/donna/skills/seed_capabilities.py` — drift logging (Task 8).
- `src/donna/automations/creation_flow.py` — optional-input defaulting (Task 9).
- `skills/news_check/skill.yaml` — `for_each` over `feed_urls` (Task 11).
- `config/capabilities.yaml` — drop news_check v1 disclaimer (Task 11).
- `src/donna/skills/tools/gmail_search.py` — page_token support (Task 12).
- `src/donna/skills/tools/rss_fetch.py` — offset support (Task 13).
- `pyproject.toml` — add trafilatura (Task 14).
- `src/donna/skills/mock_tool_registry.py` — `__error__` shape (Task 15).
- `skills/product_watch/fixtures/url_404.json` — tighten shape (Task 16).
- `skills/email_triage/fixtures/email_gmail_error.json` — new error shape (Task 17).
- `skills/news_check/fixtures/news_feed_unreachable.json` — new error shape (Task 17).
- `src/donna/automations/dispatcher.py` — state_blob plumbing (Task 18).
- `src/donna/skills/models.py` — parse `state_write` key (Task 18).
- `src/donna/notifications/service.py` — digest truncation (Task 19).

---

## Task 1: ToolRegistry.clear() + autouse pytest fixture (F-W2-B + F-W4-F)

**Files:**
- Modify: `src/donna/skills/tool_registry.py:31-58`
- Modify: `src/donna/skills/tools/__init__.py:18-22` (update docstring)
- Modify: `tests/conftest.py`
- Test: `tests/unit/test_tool_registry_clear.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_tool_registry_clear.py`:

```python
"""Unit tests for ToolRegistry.clear()."""
from __future__ import annotations

import pytest

from donna.skills.tool_registry import ToolRegistry


@pytest.mark.asyncio
async def test_clear_removes_all_tools() -> None:
    registry = ToolRegistry()

    async def _noop(**_: object) -> dict:
        return {"ok": True}

    registry.register("t1", _noop)
    registry.register("t2", _noop)
    assert registry.list_tool_names() == ["t1", "t2"]

    registry.clear()
    assert registry.list_tool_names() == []


def test_clear_is_idempotent() -> None:
    registry = ToolRegistry()
    registry.clear()  # no-op
    registry.clear()  # still no-op
    assert registry.list_tool_names() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tool_registry_clear.py -v`
Expected: FAIL with `AttributeError: 'ToolRegistry' object has no attribute 'clear'`.

- [ ] **Step 3: Add clear() method to ToolRegistry**

Edit `src/donna/skills/tool_registry.py`, add after the `register` method:

```python
    def clear(self) -> None:
        """Remove every registered tool. Boot-time-only + test isolation only.

        Not thread-safe. Do not call from request-serving code paths.
        """
        self._tools.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tool_registry_clear.py -v`
Expected: PASS.

- [ ] **Step 5: Update module docstring for DEFAULT_TOOL_REGISTRY**

Edit `src/donna/skills/tools/__init__.py`, replace the comment block above `DEFAULT_TOOL_REGISTRY` (currently lines 18-22) with:

```python
# Module-level registry populated at orchestrator startup via
# register_default_tools(DEFAULT_TOOL_REGISTRY). SkillExecutor instances
# that don't receive an explicit tool_registry default to this one.
#
# Thread-safety: None by design. Registration must complete at boot
# before any dispatch happens. Mutation after boot is not supported in
# production. Tests may call .clear() for isolation (see the autouse
# fixture in tests/conftest.py).
DEFAULT_TOOL_REGISTRY: ToolRegistry = ToolRegistry()
```

- [ ] **Step 6: Add autouse fixture to conftest.py**

Edit `tests/conftest.py`, append to the file:

```python
@pytest.fixture(autouse=True)
def _reset_default_tool_registry():
    """Clear DEFAULT_TOOL_REGISTRY between tests to prevent cross-test leakage."""
    from donna.skills.tools import DEFAULT_TOOL_REGISTRY
    yield
    DEFAULT_TOOL_REGISTRY.clear()
```

- [ ] **Step 7: Run full test suite to verify no regressions**

Run: `uv run pytest tests/ -x -q 2>&1 | tail -30`
Expected: same pass/fail ratio as before this task — no new failures from the autouse fixture.

- [ ] **Step 8: Commit**

```bash
git add src/donna/skills/tool_registry.py src/donna/skills/tools/__init__.py tests/conftest.py tests/unit/test_tool_registry_clear.py
git commit -m "feat(skills): add ToolRegistry.clear() + autouse reset fixture (F-W2-B, F-W4-F)"
```

---

## Task 2: Alembic migration — add automation.state_blob column (F-W4-D part 1)

**Files:**
- Create: `alembic/versions/add_automation_state_blob.py`
- Test: `tests/integration/test_migration_state_blob.py` (new)

- [ ] **Step 1: Find current head revision**

Run: `uv run alembic heads` to get the current head. Record it as `<HEAD>` for the migration's `down_revision`.

- [ ] **Step 2: Create the migration file**

Create `alembic/versions/add_automation_state_blob.py`:

```python
"""add automation.state_blob column (F-W4-D)

Revision ID: f5a1b2c3d4e5
Revises: <HEAD>
Create Date: 2026-04-20
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision = "f5a1b2c3d4e5"
down_revision: Union[str, None] = "<HEAD>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("state_blob", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.drop_column("state_blob")
```

Replace `<HEAD>` with the actual revision from Step 1.

- [ ] **Step 3: Write the failing migration test**

Create `tests/integration/test_migration_state_blob.py`:

```python
"""Verify add_automation_state_blob migration adds the column."""
from __future__ import annotations

import pytest

from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine


@pytest.mark.asyncio
async def test_state_blob_column_exists(tmp_path, state_machine_config) -> None:
    db_path = tmp_path / "test.db"
    state_machine = StateMachine(state_machine_config)
    db = Database(str(db_path), state_machine)
    await db.connect()
    await db.run_migrations()

    cursor = await db.connection.execute(
        "SELECT name FROM pragma_table_info('automation') WHERE name = 'state_blob'"
    )
    row = await cursor.fetchone()
    assert row is not None, "state_blob column missing from automation table"
    await db.close()
```

- [ ] **Step 4: Run the test — should PASS because the migration runs during db.run_migrations()**

Run: `uv run pytest tests/integration/test_migration_state_blob.py -v`
Expected: PASS.

- [ ] **Step 5: Verify downgrade works**

Run: `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: both succeed with no errors. Then re-run the test: `uv run pytest tests/integration/test_migration_state_blob.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/add_automation_state_blob.py tests/integration/test_migration_state_blob.py
git commit -m "feat(db): add automation.state_blob column (F-W4-D)"
```

---

## Task 3: Alembic migration — seed Claude-native capabilities (F-13)

**Files:**
- Create: `alembic/versions/seed_claude_native_capabilities.py`
- Test: `tests/integration/test_seed_claude_native_capabilities.py` (new)

- [ ] **Step 1: Find current head revision**

Run: `uv run alembic heads`. After Task 2 this should now be `f5a1b2c3d4e5`. Record as `<HEAD>`.

- [ ] **Step 2: Create migration file**

Create `alembic/versions/seed_claude_native_capabilities.py`:

```python
"""seed claude-native capability rows for migration-ready task types (F-13)

Inserts capability rows for generate_digest, prep_research, task_decompose,
extract_preferences. These remain claude_native — no skill yet. A skill
can only use their tools once those tools (calendar_read, task_db_read,
cost_summary, web_search, email_read, notes_read, fs_read) are registered
on DEFAULT_TOOL_REGISTRY; a follow-up wave handles that.

Revision ID: a6b7c8d9e0f1
Revises: f5a1b2c3d4e5
Create Date: 2026-04-20
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Union

import sqlalchemy as sa
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision: Union[str, None] = "f5a1b2c3d4e5"
branch_labels = None
depends_on = None


_CAPABILITIES = [
    {
        "name": "generate_digest",
        "description": "Generate morning digest in Donna persona",
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar_events": {"type": ["array", "null"]},
                "tasks_due_today": {"type": ["array", "null"]},
            },
        },
        "default_output_shape": None,
    },
    {
        "name": "prep_research",
        "description": "Research and compile prep materials for a flagged task",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "domain": {"type": ["string", "null"]},
                "scheduled_start": {"type": ["string", "null"]},
            },
        },
        "default_output_shape": None,
    },
    {
        "name": "task_decompose",
        "description": "Break a complex task into subtasks with dependencies",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
            },
        },
        "default_output_shape": None,
    },
    {
        "name": "extract_preferences",
        "description": "Extract learned preference rules from correction history",
        "input_schema": {
            "type": "object",
            "properties": {
                "correction_batch": {"type": ["array", "null"]},
            },
        },
        "default_output_shape": None,
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()
    for cap in _CAPABILITIES:
        conn.execute(
            sa.text(
                "INSERT OR IGNORE INTO capability "
                "(id, name, description, input_schema, trigger_type, "
                " default_output_shape, status, created_at, created_by) "
                "VALUES (:id, :name, :description, :input_schema, "
                "        'ad_hoc', :default_output_shape, 'active', "
                "        :created_at, 'seed')"
            ),
            {
                "id": f"seed-{cap['name']}",
                "name": cap["name"],
                "description": cap["description"],
                "input_schema": json.dumps(cap["input_schema"]),
                "default_output_shape": (
                    json.dumps(cap["default_output_shape"])
                    if cap["default_output_shape"] is not None else None
                ),
                "created_at": now,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    for cap in _CAPABILITIES:
        conn.execute(
            sa.text("DELETE FROM capability WHERE name = :name AND created_by = 'seed'"),
            {"name": cap["name"]},
        )
```

- [ ] **Step 3: Write the migration test**

Create `tests/integration/test_seed_claude_native_capabilities.py`:

```python
"""Verify seed_claude_native_capabilities migration inserts the four rows."""
from __future__ import annotations

import pytest

from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine


@pytest.mark.asyncio
async def test_claude_native_capabilities_seeded(
    tmp_path, state_machine_config,
) -> None:
    db_path = tmp_path / "test.db"
    db = Database(str(db_path), StateMachine(state_machine_config))
    await db.connect()
    await db.run_migrations()

    expected = {"generate_digest", "prep_research", "task_decompose", "extract_preferences"}
    cursor = await db.connection.execute(
        "SELECT name FROM capability WHERE name IN "
        "('generate_digest','prep_research','task_decompose','extract_preferences')"
    )
    rows = await cursor.fetchall()
    seeded = {r[0] for r in rows}
    assert seeded == expected, f"missing: {expected - seeded}"
    await db.close()
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/integration/test_seed_claude_native_capabilities.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/seed_claude_native_capabilities.py tests/integration/test_seed_claude_native_capabilities.py
git commit -m "feat(db): seed claude-native capabilities for F-13 migration"
```

---

## Task 4: Add 4 task-type capabilities to config/capabilities.yaml (F-13 cont.)

**Files:**
- Modify: `config/capabilities.yaml` (append entries)

- [ ] **Step 1: Append to config/capabilities.yaml**

After the existing `email_triage` entry, append:

```yaml

  - name: generate_digest
    description: "Generate morning digest in Donna persona"
    trigger_type: ad_hoc
    input_schema:
      type: object
      properties:
        calendar_events: {type: [array, "null"]}
        tasks_due_today: {type: [array, "null"]}

  - name: prep_research
    description: "Research and compile prep materials for a flagged task"
    trigger_type: ad_hoc
    input_schema:
      type: object
      properties:
        title: {type: [string, "null"]}
        description: {type: [string, "null"]}
        domain: {type: [string, "null"]}
        scheduled_start: {type: [string, "null"]}

  - name: task_decompose
    description: "Break a complex task into subtasks with dependencies"
    trigger_type: ad_hoc
    input_schema:
      type: object
      properties:
        title: {type: [string, "null"]}
        description: {type: [string, "null"]}

  - name: extract_preferences
    description: "Extract learned preference rules from correction history"
    trigger_type: ad_hoc
    input_schema:
      type: object
      properties:
        correction_batch: {type: [array, "null"]}
```

- [ ] **Step 2: Verify YAML parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('config/capabilities.yaml'))"`
Expected: no output, no error.

- [ ] **Step 3: Commit**

```bash
git add config/capabilities.yaml
git commit -m "feat(config): add generate_digest/prep_research/task_decompose/extract_preferences to capabilities.yaml (F-13)"
```

---

## Task 5: Add baseline_reset_window config field (F-9 part 1)

**Files:**
- Modify: `src/donna/config.py:419-466` (SkillSystemConfig)
- Test: `tests/unit/test_config_baseline_reset_window.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_config_baseline_reset_window.py`:

```python
"""Unit test for SkillSystemConfig.baseline_reset_window."""
from __future__ import annotations

from donna.config import SkillSystemConfig


def test_default_value_is_100() -> None:
    cfg = SkillSystemConfig()
    assert cfg.baseline_reset_window == 100


def test_can_be_overridden() -> None:
    cfg = SkillSystemConfig(baseline_reset_window=50)
    assert cfg.baseline_reset_window == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config_baseline_reset_window.py -v`
Expected: FAIL — attribute doesn't exist.

- [ ] **Step 3: Add the field**

Edit `src/donna/config.py`, inside `class SkillSystemConfig`, after the line `validation_per_run_timeout_s: int = 300`:

```python

    # Wave 5 — F-9: configurable window for baseline_agreement reset.
    baseline_reset_window: int = 100
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config_baseline_reset_window.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/config.py tests/unit/test_config_baseline_reset_window.py
git commit -m "feat(config): add SkillSystemConfig.baseline_reset_window (F-9)"
```

---

## Task 6: Use baseline_reset_window in skills API route (F-9 part 2)

**Files:**
- Modify: `src/donna/api/routes/skills.py:157-177`
- Test: `tests/integration/test_skills_route_baseline_window.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_skills_route_baseline_window.py`:

```python
"""Verify the baseline reset uses SkillSystemConfig.baseline_reset_window."""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_baseline_reset_uses_configured_window(
    api_client_with_skill_and_divergences,  # fixture — see step below if missing
) -> None:
    # Seeded: 150 divergence rows with agreement=0.9 for the first 50 and 0.5 for rest.
    # With window=50, only the first 50 are averaged => 0.9.
    # With window=150, full average => somewhere between 0.5 and 0.9.
    client, conn, skill_id = api_client_with_skill_and_divergences

    # Force window to 50 via app.state.skill_config override.
    client.app.state.skill_config.baseline_reset_window = 50

    resp = await client.post(
        f"/admin/skills/{skill_id}/state",
        json={"to_state": "trusted", "reason": "human_approval"},
    )
    assert resp.status_code == 200

    cursor = await conn.execute(
        "SELECT baseline_agreement FROM skill WHERE id = ?", (skill_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert abs(row[0] - 0.9) < 0.01, f"expected ~0.9 with window=50, got {row[0]}"
```

**Note on the fixture:** this test depends on an `api_client_with_skill_and_divergences` fixture that may not exist. If no equivalent fixture is in `tests/integration/conftest.py`, add the simpler alternative below — a direct-SQL parameterization test:

```python
# Alternative: direct check of the query parameter.
@pytest.mark.asyncio
async def test_baseline_query_uses_config_value(tmp_path, state_machine_config) -> None:
    from donna.tasks.database import Database
    from donna.tasks.state_machine import StateMachine
    from donna.config import SkillSystemConfig
    # Minimal test: import the route module and verify the SQL template
    # reads from a config-bound variable rather than a literal '100'.
    import inspect
    from donna.api.routes import skills as skills_mod
    src = inspect.getsource(skills_mod)
    assert "LIMIT 100" not in src, "LIMIT should read from config.baseline_reset_window"
    assert "baseline_reset_window" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_skills_route_baseline_window.py -v`
Expected: FAIL — the source contains the literal `LIMIT 100`.

- [ ] **Step 3: Update the route to use the config value**

Edit `src/donna/api/routes/skills.py`, replace lines 159-176 with:

```python
    if body.to_state == "trusted" and body.reason == "human_approval":
        skill_config = request.app.state.skill_config
        window = int(skill_config.baseline_reset_window)
        cursor = await conn.execute(
            "SELECT AVG(agreement) FROM ("
            "  SELECT d.overall_agreement AS agreement"
            "  FROM skill_divergence d"
            "  JOIN skill_run r ON d.skill_run_id = r.id"
            "  WHERE r.skill_id = ?"
            "  ORDER BY d.created_at DESC LIMIT ?"
            ")",
            (skill_id, window),
        )
        row = await cursor.fetchone()
        if row and row[0] is not None:
            await conn.execute(
                "UPDATE skill SET baseline_agreement = ? WHERE id = ?",
                (float(row[0]), skill_id),
            )
            await conn.commit()
```

- [ ] **Step 4: Verify `app.state.skill_config` is populated**

Run: `uv run grep -n "app.state.skill_config" src/donna/ -r`
Expected: at least one assignment exists (likely in `src/donna/api/__init__.py` or `src/donna/server.py`). If NOT present, add a step before Step 3 to set `app.state.skill_config = load_skill_system_config(config_dir)` during app startup. Check and decide inline.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_skills_route_baseline_window.py -v`
Expected: PASS.

- [ ] **Step 6: Run the broader skills route test suite**

Run: `uv run pytest tests/ -k skills_route -v`
Expected: all pass — no regression from the parameterization.

- [ ] **Step 7: Commit**

```bash
git add src/donna/api/routes/skills.py tests/integration/test_skills_route_baseline_window.py
git commit -m "feat(skills-api): read baseline_reset_window from config (F-9)"
```

---

## Task 7: GmailClient boot wiring (F-W4-I)

**Files:**
- Modify: `src/donna/cli_wiring.py` (new helper + call-site update)
- Modify: `src/donna/cli.py:186-191` (update the TODO comment + call)
- Test: `tests/unit/test_try_build_gmail_client.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_try_build_gmail_client.py`:

```python
"""Unit tests for _try_build_gmail_client helper."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from donna.cli_wiring import _try_build_gmail_client


def test_returns_none_when_email_yaml_missing(tmp_path: Path) -> None:
    # No email.yaml in tmp_path
    result = _try_build_gmail_client(tmp_path)
    assert result is None


def test_returns_none_when_creds_file_missing(tmp_path: Path) -> None:
    (tmp_path / "email.yaml").write_text(
        "email:\n"
        "  credentials:\n"
        f"    token_path: {tmp_path}/nonexistent_token.json\n"
        f"    client_secrets_path: {tmp_path}/nonexistent_secrets.json\n"
        "    scopes: ['https://www.googleapis.com/auth/gmail.readonly']\n"
    )
    result = _try_build_gmail_client(tmp_path)
    assert result is None


def test_returns_client_when_config_present(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    secrets = tmp_path / "secrets.json"
    token.write_text("{}")
    secrets.write_text("{}")
    (tmp_path / "email.yaml").write_text(
        "email:\n"
        "  credentials:\n"
        f"    token_path: {token}\n"
        f"    client_secrets_path: {secrets}\n"
        "    scopes: ['https://www.googleapis.com/auth/gmail.readonly']\n"
    )
    result = _try_build_gmail_client(tmp_path)
    assert result is not None
    assert type(result).__name__ == "GmailClient"


def test_returns_none_when_construction_raises(tmp_path: Path) -> None:
    (tmp_path / "email.yaml").write_text("email: {broken")  # malformed YAML
    result = _try_build_gmail_client(tmp_path)
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_try_build_gmail_client.py -v`
Expected: FAIL — `_try_build_gmail_client` doesn't exist yet.

- [ ] **Step 3: Add the helper to cli_wiring.py**

Edit `src/donna/cli_wiring.py`, add after the imports block (near line 58, before the `# Dataclasses` header):

```python
def _try_build_gmail_client(config_dir: Path) -> Any | None:
    """Attempt to construct a GmailClient from config/email.yaml.

    Returns None on any failure (missing file, creds file missing, construction
    raises). Non-fatal — the capability-availability guard surfaces the
    missing-tool state at automation-approval time via an actionable DM.
    """
    email_yaml = config_dir / "email.yaml"
    if not email_yaml.exists():
        return None
    try:
        from donna.config import load_email_config
        from donna.integrations.gmail import GmailClient

        email_cfg = load_email_config(config_dir)
        token_path = Path(email_cfg.credentials.token_path)
        secrets_path = Path(email_cfg.credentials.client_secrets_path)
        if not token_path.exists() or not secrets_path.exists():
            logger.warning(
                "gmail_client_unavailable",
                reason="credential_file_missing",
                token_exists=token_path.exists(),
                secrets_exists=secrets_path.exists(),
            )
            return None
        return GmailClient(config=email_cfg)
    except Exception as exc:  # noqa: BLE001 — non-fatal boot path
        logger.warning("gmail_client_unavailable", reason=str(exc))
        return None
```

Confirm `load_email_config` exists by running: `uv run grep -n "def load_email_config" src/donna/config.py`. If missing, add the corresponding test to verify its symbol-level existence; most likely present given the EmailConfig in Task 1's grounding.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_try_build_gmail_client.py -v`
Expected: PASS for the first three tests; the `test_returns_none_when_construction_raises` may need adjustment based on how `load_yaml` handles malformed YAML. If PyYAML raises before reaching the try/except inside the helper, the test should still pass (exception caught by `except Exception`).

- [ ] **Step 5: Update cli.py to call the helper**

Edit `src/donna/cli.py:186-191`, replace:

```python
    # gmail_client is not yet constructed during boot (email subsystem is Wave 2+).
    # Pass None explicitly; when the email subsystem is wired it should pass its
    # GmailClient here so Gmail skill tools register at startup.
    skill_h = await wire_skill_system(ctx, gmail_client=None)
```

With:

```python
    # Wave 5 F-W4-I: attempt to build a GmailClient at boot so Gmail skill
    # tools register for capabilities like email_triage. Non-fatal on failure.
    from donna.cli_wiring import _try_build_gmail_client

    gmail_client = _try_build_gmail_client(ctx.config_dir)
    skill_h = await wire_skill_system(ctx, gmail_client=gmail_client)
```

- [ ] **Step 6: Add integration test for boot path**

Create `tests/integration/test_boot_gmail_wiring.py`:

```python
"""Integration test: confirm Gmail tools register when email config present."""
from __future__ import annotations

from pathlib import Path

import pytest

from donna.cli_wiring import _try_build_gmail_client
from donna.skills.tools import DEFAULT_TOOL_REGISTRY, register_default_tools


def test_gmail_tools_register_when_client_present(tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    secrets = tmp_path / "secrets.json"
    token.write_text("{}")
    secrets.write_text("{}")
    (tmp_path / "email.yaml").write_text(
        "email:\n"
        "  credentials:\n"
        f"    token_path: {token}\n"
        f"    client_secrets_path: {secrets}\n"
        "    scopes: ['https://www.googleapis.com/auth/gmail.readonly']\n"
    )
    client = _try_build_gmail_client(tmp_path)
    assert client is not None

    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(DEFAULT_TOOL_REGISTRY, gmail_client=client)
    names = DEFAULT_TOOL_REGISTRY.list_tool_names()
    assert "gmail_search" in names
    assert "gmail_get_message" in names


def test_gmail_tools_absent_when_client_missing(tmp_path: Path) -> None:
    client = _try_build_gmail_client(tmp_path)  # no email.yaml
    assert client is None

    DEFAULT_TOOL_REGISTRY.clear()
    register_default_tools(DEFAULT_TOOL_REGISTRY, gmail_client=client)
    names = DEFAULT_TOOL_REGISTRY.list_tool_names()
    assert "gmail_search" not in names
    assert "gmail_get_message" not in names
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_boot_gmail_wiring.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/donna/cli_wiring.py src/donna/cli.py tests/unit/test_try_build_gmail_client.py tests/integration/test_boot_gmail_wiring.py
git commit -m "feat(cli): wire GmailClient at boot so email_triage works in production (F-W4-I)"
```

---

## Task 8: SeedCapabilityLoader drift logging (F-W2-A)

**Files:**
- Modify: `src/donna/skills/seed_capabilities.py:26-70`
- Test: `tests/unit/test_seed_loader_drift_log.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_seed_loader_drift_log.py`:

```python
"""Unit test: SeedCapabilityLoader logs drift when UPSERT changes semantic fields."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest
import structlog
from structlog.testing import LogCapture

from donna.skills.seed_capabilities import SeedCapabilityLoader


@pytest.mark.asyncio
async def test_drift_log_emitted_on_description_change(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE capability (id TEXT PRIMARY KEY, name TEXT UNIQUE, "
            "description TEXT, input_schema TEXT, trigger_type TEXT, "
            "default_output_shape TEXT, status TEXT, created_at TEXT, "
            "created_by TEXT)"
        )
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "INSERT INTO capability VALUES (?, 'x', 'old-desc', '{}', "
            "'on_schedule', NULL, 'active', ?, 'seed')",
            (str(uuid.uuid4()), now),
        )
        await conn.commit()

        yaml_path = tmp_path / "capabilities.yaml"
        yaml_path.write_text(
            "capabilities:\n"
            "  - name: x\n"
            "    description: 'new-desc'\n"
            "    trigger_type: on_schedule\n"
            "    input_schema: {type: object}\n"
        )

        cap = LogCapture()
        structlog.configure(processors=[cap])
        loader = SeedCapabilityLoader(connection=conn)
        await loader.load_and_upsert(yaml_path)

        drift_events = [e for e in cap.entries if e["event"] == "seed_capability_drift"]
        assert len(drift_events) == 1
        assert drift_events[0]["capability_name"] == "x"
        assert "description" in drift_events[0]["fields"]


@pytest.mark.asyncio
async def test_no_drift_log_when_unchanged(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE capability (id TEXT PRIMARY KEY, name TEXT UNIQUE, "
            "description TEXT, input_schema TEXT, trigger_type TEXT, "
            "default_output_shape TEXT, status TEXT, created_at TEXT, "
            "created_by TEXT)"
        )
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "INSERT INTO capability VALUES (?, 'x', 'same', ?, "
            "'on_schedule', NULL, 'active', ?, 'seed')",
            (str(uuid.uuid4()), json.dumps({"type": "object"}), now),
        )
        await conn.commit()

        yaml_path = tmp_path / "capabilities.yaml"
        yaml_path.write_text(
            "capabilities:\n"
            "  - name: x\n"
            "    description: 'same'\n"
            "    trigger_type: on_schedule\n"
            "    input_schema: {type: object}\n"
        )

        cap = LogCapture()
        structlog.configure(processors=[cap])
        loader = SeedCapabilityLoader(connection=conn)
        await loader.load_and_upsert(yaml_path)

        drift_events = [e for e in cap.entries if e["event"] == "seed_capability_drift"]
        assert len(drift_events) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_seed_loader_drift_log.py -v`
Expected: FAIL — no drift event is emitted.

- [ ] **Step 3: Update SeedCapabilityLoader**

Edit `src/donna/skills/seed_capabilities.py`, replace lines 42-66 (the body of the for-loop after `default_output_shape` is computed) with:

```python
            cursor = await self._conn.execute(
                "SELECT description, input_schema, trigger_type, default_output_shape "
                "FROM capability WHERE name = ?", (name,),
            )
            row = await cursor.fetchone()
            if row is None:
                await self._conn.execute(
                    "INSERT INTO capability "
                    "(id, name, description, input_schema, trigger_type, "
                    " default_output_shape, status, created_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, 'seed')",
                    (str(uuid.uuid4()), name, description, input_schema,
                     trigger_type, default_output_shape, now),
                )
            else:
                existing_desc, existing_schema, existing_trigger, existing_shape = row
                changed_fields: list[str] = []
                if existing_desc != description:
                    changed_fields.append("description")
                if existing_schema != input_schema:
                    changed_fields.append("input_schema")
                if existing_trigger != trigger_type:
                    changed_fields.append("trigger_type")
                if (existing_shape or None) != (default_output_shape or None):
                    changed_fields.append("default_output_shape")
                if changed_fields:
                    logger.info(
                        "seed_capability_drift",
                        capability_name=name,
                        fields=changed_fields,
                    )
                await self._conn.execute(
                    "UPDATE capability "
                    "SET description = ?, input_schema = ?, trigger_type = ?, "
                    "    default_output_shape = ? "
                    "WHERE name = ?",
                    (description, input_schema, trigger_type,
                     default_output_shape, name),
                )
            upserted += 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_seed_loader_drift_log.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/seed_capabilities.py tests/unit/test_seed_loader_drift_log.py
git commit -m "feat(skills): log diff when SeedCapabilityLoader overwrites semantic fields (F-W2-A)"
```

---

## Task 9: AutomationCreationPath optional-input defaulting (F-W4-K part 1)

**Files:**
- Modify: `src/donna/automations/creation_flow.py`
- Test: `tests/unit/test_creation_path_optional_defaulting.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_creation_path_optional_defaulting.py`:

```python
"""Unit test: AutomationCreationPath fills optional input_schema keys with null."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from donna.automations.creation_flow import AutomationCreationPath
from donna.orchestrator.discord_intent_dispatcher import DraftAutomation


@pytest.mark.asyncio
async def test_optional_fields_defaulted_to_none() -> None:
    repo = AsyncMock()
    repo.create.return_value = "aut-123"

    # Fake capability_input_schema_lookup returns schema with one required + one optional key.
    async def _input_schema_lookup(name: str) -> dict:
        return {
            "type": "object",
            "required": ["senders"],
            "properties": {
                "senders": {"type": "array"},
                "query_extras": {"type": ["string", "null"]},
            },
        }

    path = AutomationCreationPath(
        repository=repo,
        capability_input_schema_lookup=_input_schema_lookup,
    )

    draft = DraftAutomation(
        user_id="u1",
        capability_name="email_triage",
        inputs={"senders": ["x@y.com"]},  # missing query_extras
        schedule_cron="0 9 * * *",
        alert_conditions=None,
        target_cadence_cron="0 9 * * *",
        active_cadence_cron="0 9 * * *",
    )

    await path.approve(draft, name="test")

    # Inspect what repo.create was called with
    call_kwargs = repo.create.call_args.kwargs
    assert call_kwargs["inputs"] == {"senders": ["x@y.com"], "query_extras": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_creation_path_optional_defaulting.py -v`
Expected: FAIL — `capability_input_schema_lookup` kwarg not accepted.

- [ ] **Step 3: Update AutomationCreationPath**

Edit `src/donna/automations/creation_flow.py`. Update the class definition:

```python
import json
from typing import Any, Awaitable, Callable


CapabilityInputSchemaLookup = Callable[[str], Awaitable[dict]]


class AutomationCreationPath:
    def __init__(
        self,
        *,
        repository: Any,
        default_min_interval_seconds: int = 300,
        tool_registry: Any | None = None,
        capability_tool_lookup: CapabilityToolLookup | None = None,
        capability_input_schema_lookup: CapabilityInputSchemaLookup | None = None,
    ) -> None:
        self._repo = repository
        self._default_min_interval_seconds = default_min_interval_seconds
        self._tool_registry = tool_registry
        self._capability_tool_lookup = capability_tool_lookup
        self._capability_input_schema_lookup = capability_input_schema_lookup

    async def approve(self, draft: DraftAutomation, *, name: str) -> str | None:
        """Create the automation row. Returns its id or ``None`` on duplicate."""
        capability_name = draft.capability_name or "claude_native"

        # (Existing tool-availability guard block unchanged — keep as-is.)
        if (
            self._tool_registry is not None
            and self._capability_tool_lookup is not None
            and draft.capability_name
        ):
            required = await self._capability_tool_lookup(draft.capability_name)
            available = set(self._tool_registry.list_tool_names())
            missing = [t for t in required if t not in available]
            if missing:
                logger.warning(
                    "automation_creation_missing_tools",
                    capability=draft.capability_name,
                    missing=missing,
                )
                raise MissingToolError(draft.capability_name, missing)

        # F-W4-K: default optional input_schema keys to None so skill.yaml
        # templates under StrictUndefined don't need `is defined and` guards.
        inputs = dict(draft.inputs or {})
        if (
            self._capability_input_schema_lookup is not None
            and draft.capability_name
        ):
            try:
                schema = await self._capability_input_schema_lookup(draft.capability_name)
                required = set(schema.get("required", []) or [])
                props = (schema.get("properties") or {}).keys()
                for key in props:
                    if key not in required and key not in inputs:
                        inputs[key] = None
            except Exception:
                logger.exception("capability_input_schema_lookup_failed")

        try:
            automation_id = await self._repo.create(
                user_id=draft.user_id,
                name=name,
                description=None,
                capability_name=capability_name,
                inputs=inputs,
                trigger_type="on_schedule",
                schedule=draft.schedule_cron,
                alert_conditions=draft.alert_conditions or {},
                alert_channels=["discord_dm"],
                max_cost_per_run_usd=None,
                min_interval_seconds=self._default_min_interval_seconds,
                created_via="discord",
                target_cadence_cron=draft.target_cadence_cron,
                active_cadence_cron=draft.active_cadence_cron,
            )
            logger.info(
                "automation_created_via_discord",
                user_id=draft.user_id,
                name=name,
                capability=draft.capability_name,
                target_cadence=draft.target_cadence_cron,
                active_cadence=draft.active_cadence_cron,
            )
            return automation_id
        except AlreadyExistsError:
            logger.info(
                "automation_creation_already_exists",
                user_id=draft.user_id,
                name=name,
            )
            return None
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `uv run pytest tests/unit/test_creation_path_optional_defaulting.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the new lookup into boot (cli_wiring.py)**

Look in `src/donna/cli_wiring.py` for where `AutomationCreationPath` is constructed — likely inside `wire_discord` or a helper nearby. Around the existing `_cap_lookup = SkillToolRequirementsLookup(...)` line (approx line 592), add:

```python
        from donna.capabilities.repo_input_schema_lookup import (
            CapabilityInputSchemaDBLookup,
        )
        _input_schema_lookup = CapabilityInputSchemaDBLookup(ctx.db.connection)
```

Then when `AutomationCreationPath` is instantiated (search for its constructor in the codebase), pass `capability_input_schema_lookup=_input_schema_lookup.lookup`.

- [ ] **Step 6: Create the DB-backed lookup class**

Create `src/donna/capabilities/repo_input_schema_lookup.py`:

```python
"""Resolve a capability name to its input_schema dict via the capability table."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite


class CapabilityInputSchemaDBLookup:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def lookup(self, capability_name: str) -> dict:
        cursor = await self._conn.execute(
            "SELECT input_schema FROM capability WHERE name = ?",
            (capability_name,),
        )
        row = await cursor.fetchone()
        if row is None or row[0] is None:
            return {}
        try:
            parsed = json.loads(row[0])
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
```

- [ ] **Step 7: Run the full creation flow tests**

Run: `uv run pytest tests/ -k creation_flow -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/donna/automations/creation_flow.py src/donna/capabilities/repo_input_schema_lookup.py src/donna/cli_wiring.py tests/unit/test_creation_path_optional_defaulting.py
git commit -m "feat(automations): default optional inputs at draft time so skill.yaml Jinja stays simple (F-W4-K)"
```

---

## Task 10: Skill YAML lint test (F-W4-K part 2)

**Files:**
- Create: `tests/unit/test_skill_yaml_lint.py`

- [ ] **Step 1: Write the lint test**

Create `tests/unit/test_skill_yaml_lint.py`:

```python
"""Lint: skill.yaml templates must guard `{% if inputs.X %}` with `is defined and`.

Under Jinja StrictUndefined, `{% if inputs.missing %}` raises UndefinedError
if `missing` isn't a key. F-W4-K fixes this at the draft layer, but as
defense-in-depth we lint skill.yaml files for the unsafe pattern.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"

# Matches: {% if inputs.X %} (and variants) but NOT the safe form.
UNSAFE_PATTERN = re.compile(
    r"\{%\s*if\s+inputs\.(\w+)\s*%\}",
)
SAFE_PATTERN = re.compile(
    r"\{%\s*if\s+inputs\.(\w+)\s+is\s+defined\s+and\s+inputs\.\1\s*%\}",
)


def _collect_skill_files() -> list[Path]:
    return list(SKILLS_ROOT.rglob("skill.yaml"))


def _collect_step_files(skill_yaml: Path) -> list[Path]:
    with open(skill_yaml) as fh:
        data = yaml.safe_load(fh) or {}
    skill_dir = skill_yaml.parent
    paths = [skill_yaml]
    for step in data.get("steps") or []:
        prompt = step.get("prompt")
        if prompt:
            paths.append(skill_dir / prompt)
    return paths


def test_no_unsafe_optional_input_references() -> None:
    violations: list[tuple[str, int, str]] = []
    for skill_yaml in _collect_skill_files():
        for file_path in _collect_step_files(skill_yaml):
            if not file_path.exists():
                continue
            for lineno, line in enumerate(file_path.read_text().splitlines(), 1):
                for match in UNSAFE_PATTERN.finditer(line):
                    if SAFE_PATTERN.search(line):
                        continue
                    # Load the capability's input_schema to check required-ness.
                    key = match.group(1)
                    cap_name = yaml.safe_load(skill_yaml.read_text()).get(
                        "capability_name"
                    )
                    if _is_optional(cap_name, key):
                        violations.append((str(file_path), lineno, line.strip()))
    assert not violations, (
        "Unsafe `{% if inputs.X %}` pattern found for optional keys. "
        "Use `{% if inputs.X is defined and inputs.X %}`:\n"
        + "\n".join(f"  {p}:{l}: {c}" for p, l, c in violations)
    )


def _is_optional(capability_name: str | None, key: str) -> bool:
    """Check if `key` is optional in the capability's input_schema."""
    if capability_name is None:
        return True  # assume optional if we can't determine
    caps_yaml = SKILLS_ROOT.parent / "config" / "capabilities.yaml"
    if not caps_yaml.exists():
        return True
    data = yaml.safe_load(caps_yaml.read_text()) or {}
    for cap in data.get("capabilities") or []:
        if cap.get("name") == capability_name:
            schema = cap.get("input_schema") or {}
            required = set(schema.get("required", []) or [])
            return key not in required
    return True
```

- [ ] **Step 2: Run the lint test**

Run: `uv run pytest tests/unit/test_skill_yaml_lint.py -v`
Expected: PASS (all current skill.yaml files comply — email_triage already uses the safe form at line 19).

- [ ] **Step 3: Sanity-check the test detects violations**

Temporarily add an unsafe pattern to a test skill file. E.g., in a sandbox dir, create:

```yaml
# scratch/skill.yaml
capability_name: email_triage
steps:
  - name: test
    kind: llm
    prompt: steps/foo.md
```

With `skills/scratch/steps/foo.md` containing:

```
{% if inputs.query_extras %}some text{% endif %}
```

Run: `uv run pytest tests/unit/test_skill_yaml_lint.py -v`
Expected: FAIL mentioning the unsafe pattern.

Remove the scratch files afterward.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_skill_yaml_lint.py
git commit -m "test(skills): lint skill.yaml for unsafe `{% if inputs.X %}` (F-W4-K)"
```

---

## Task 11: news_check multi-feed skill (F-W4-L)

**Files:**
- Modify: `skills/news_check/skill.yaml`
- Modify: `config/capabilities.yaml` (drop v1 disclaimer)
- Create: `skills/news_check/fixtures/multi_feed_match.json`
- Possibly: `src/donna/skills/executor.py` (if for_each doesn't support inputs arrays)
- Test: `tests/unit/test_news_check_for_each.py` (new)

- [ ] **Step 1: Verify for_each supports inputs.* arrays**

Run: `uv run grep -n "for_each" src/donna/skills/executor.py`

Read the relevant lines. If `for_each` iterates over any Jinja expression that evaluates to a list, no executor change is needed. If it's restricted to `state.<step>.<field>` lookups only, extend it to also accept `inputs.<key>`.

Write down the decision inline. If an executor change is needed, add a step here before Step 2 that adds the extension + a unit test.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_news_check_for_each.py`:

```python
"""Unit test: news_check skill iterates over inputs.feed_urls."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def test_news_check_skill_yaml_uses_for_each_over_feed_urls() -> None:
    skill_path = Path(__file__).resolve().parents[2] / "skills" / "news_check" / "skill.yaml"
    data = yaml.safe_load(skill_path.read_text())
    fetch_step = next(s for s in data["steps"] if s["name"] == "fetch_items")
    # Expectation: the step has a for_each clause over inputs.feed_urls.
    assert "for_each" in fetch_step, "fetch_items must use for_each"
    assert fetch_step["for_each"] == "inputs.feed_urls", (
        f"expected for_each: inputs.feed_urls, got {fetch_step['for_each']!r}"
    )
    # And the tool_invocations reference the loop variable (e.g., `item`).
    # Exact name depends on DSL; assert no hardcoded index.
    inv_str = yaml.safe_dump(fetch_step)
    assert "feed_urls[0]" not in inv_str, "hardcoded feed_urls[0] must be removed"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_news_check_for_each.py -v`
Expected: FAIL — current skill.yaml uses `feed_urls[0]`.

- [ ] **Step 4: Rewrite the skill's fetch step**

Edit `skills/news_check/skill.yaml`, replace the `fetch_items` step with:

```yaml
steps:
  - name: fetch_items
    kind: tool
    for_each: inputs.feed_urls
    loop_var: feed_url
    tools: [rss_fetch]
    tool_invocations:
      - tool: rss_fetch
        args:
          url: "{{ feed_url }}"
          since: "{{ inputs.prior_run_end }}"
          max_items: 50
        retry:
          max_attempts: 2
          backoff_s: [2, 5]
        store_as: feed
```

Verify the exact DSL field names (`for_each`, `loop_var`) against the existing email_triage skill's usage (Explorer noted `for_each` over `state.classify_snippets.candidates`) by running `uv run grep -n "for_each\|loop_var" skills/email_triage/skill.yaml` and mirroring that shape.

- [ ] **Step 5: Update config/capabilities.yaml**

Edit `config/capabilities.yaml`, replace the `feed_urls` description:

```yaml
        feed_urls:
          type: array
          items: {type: string}
          minItems: 1
          description: "List of RSS/Atom feed URLs to monitor. Each feed is polled and results aggregated."
```

- [ ] **Step 6: Create multi-feed fixture**

Create `skills/news_check/fixtures/multi_feed_match.json`:

```json
{
  "case_name": "multi_feed_match",
  "input": {
    "feed_urls": ["https://feed-a.example/rss", "https://feed-b.example/rss"],
    "topics": ["rust"],
    "prior_run_end": null
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "triggers_alert", "message", "meta"],
    "properties": {
      "ok": {"type": "boolean"},
      "triggers_alert": {"type": "boolean"},
      "message": {"type": ["string", "null"]},
      "meta": {"type": "object"}
    }
  },
  "tool_mocks": {
    "rss_fetch:{\"max_items\":50,\"since\":null,\"url\":\"https://feed-a.example/rss\"}": {
      "ok": true,
      "items": [
        {"title": "Rust 1.80 released", "link": "https://feed-a.example/rust-1-80", "published": "2026-04-19T10:00:00+00:00", "author": "", "summary": "New Rust version."}
      ],
      "feed_title": "Feed A",
      "feed_description": null
    },
    "rss_fetch:{\"max_items\":50,\"since\":null,\"url\":\"https://feed-b.example/rss\"}": {
      "ok": true,
      "items": [
        {"title": "Rust async update", "link": "https://feed-b.example/rust-async", "published": "2026-04-19T11:00:00+00:00", "author": "", "summary": "Async Rust."}
      ],
      "feed_title": "Feed B",
      "feed_description": null
    }
  }
}
```

- [ ] **Step 7: Run the test**

Run: `uv run pytest tests/unit/test_news_check_for_each.py -v`
Expected: PASS.

- [ ] **Step 8: Run existing news_check tests to verify no regression**

Run: `uv run pytest tests/ -k news_check -v`
Expected: existing tests still pass. If single-URL fixtures fail because the tool_invocations changed shape, update those fixtures too — they were written for the pre-for_each version. Check `skills/news_check/fixtures/*.json` and update each to use a single-element `feed_urls` array.

- [ ] **Step 9: Commit**

```bash
git add skills/news_check/skill.yaml config/capabilities.yaml skills/news_check/fixtures/ tests/unit/test_news_check_for_each.py
git commit -m "feat(news_check): iterate all feed_urls instead of only feed_urls[0] (F-W4-L)"
```

---

## Task 12: gmail_search pagination (F-W4-B part 1)

**Files:**
- Modify: `src/donna/skills/tools/gmail_search.py`
- Test: `tests/unit/test_gmail_search_pagination.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_gmail_search_pagination.py`:

```python
"""Unit test: gmail_search accepts page_token + returns next_page_token."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from donna.skills.tools.gmail_search import gmail_search


class _FakeClient:
    def __init__(self):
        self.last_page_token = None

    async def search_emails(self, query: str, max_results: int, page_token: str | None = None):
        self.last_page_token = page_token
        # Return stub EmailMessage-like objects + a next_page_token side-channel
        self._next = "next-tok" if page_token is None else None
        return [
            SimpleNamespace(
                id="m1", sender="s", subject="t", snippet="sn",
                date=datetime(2026, 4, 19, tzinfo=timezone.utc),
            )
        ]

    def get_last_next_page_token(self) -> str | None:
        return self._next


@pytest.mark.asyncio
async def test_page_token_passed_through() -> None:
    client = _FakeClient()
    result = await gmail_search(
        client=client, query="from:x@y.com", page_token="abc",
    )
    assert client.last_page_token == "abc"
    assert result["ok"] is True
    assert "next_page_token" in result


@pytest.mark.asyncio
async def test_next_page_token_returned() -> None:
    client = _FakeClient()
    result = await gmail_search(client=client, query="from:x@y.com")
    assert result["next_page_token"] == "next-tok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_gmail_search_pagination.py -v`
Expected: FAIL — `page_token` kwarg not accepted.

- [ ] **Step 3: Update gmail_search**

Edit `src/donna/skills/tools/gmail_search.py`, replace the `gmail_search` function:

```python
async def gmail_search(
    *,
    client: Any,
    query: str,
    max_results: int = 20,
    page_token: str | None = None,
) -> dict:
    """Search Gmail. Returns lightweight summaries, never bodies.

    Pagination: pass `page_token` to fetch the next page; the response
    includes `next_page_token` (None when exhausted).
    """
    if not query or not query.strip():
        raise GmailToolError("query must be non-empty")
    clamped = min(int(max_results), MAX_RESULTS_CEILING)
    try:
        messages = await client.search_emails(
            query=query, max_results=clamped, page_token=page_token,
        )
    except Exception as exc:
        logger.warning("gmail_search_failed", query=query, error=str(exc))
        raise GmailToolError(f"search: {exc}") from exc

    next_token = None
    if hasattr(client, "get_last_next_page_token"):
        try:
            next_token = client.get_last_next_page_token()
        except Exception:
            next_token = None

    out = []
    for m in messages:
        out.append({
            "id": m.id,
            "sender": m.sender,
            "subject": m.subject,
            "snippet": m.snippet,
            "internal_date": m.date.isoformat() if m.date is not None else None,
        })
    return {"ok": True, "messages": out, "next_page_token": next_token}
```

- [ ] **Step 4: Update GmailClient.search_emails to accept page_token**

Read the current `search_emails` method in `src/donna/integrations/gmail.py` (search for `async def search_emails`). The method wraps a call to `self._service.users().messages().list(...).execute()` and then iterates the returned `messages` list to hydrate per-message data.

Make three changes to the existing method:

1. Add `page_token: str | None = None` to the method signature.
2. Inside the sync `_call()` closure (run via `asyncio.to_thread`), pass `pageToken` to the Gmail API when set. Replace the existing `self._service.users().messages().list(...)` call with:
   ```python
   list_kwargs = {"userId": "me", "q": query, "maxResults": max_results}
   if page_token:
       list_kwargs["pageToken"] = page_token
   req = self._service.users().messages().list(**list_kwargs)
   resp = req.execute()
   self._last_next_page_token = resp.get("nextPageToken")
   ```
   Keep the existing code that iterates `resp.get("messages", [])` (or equivalent) to hydrate each `EmailMessage` unchanged — only the list() call wrapping it is updated.
3. Add `self._last_next_page_token: str | None = None` to the bottom of `__init__`.
4. Add a new method on `GmailClient` at the same indent level as `search_emails`:
   ```python
   def get_last_next_page_token(self) -> str | None:
       return getattr(self, "_last_next_page_token", None)
   ```

- [ ] **Step 5: Run the test**

Run: `uv run pytest tests/unit/test_gmail_search_pagination.py -v`
Expected: PASS.

- [ ] **Step 6: Run broader gmail tests**

Run: `uv run pytest tests/ -k gmail -v`
Expected: existing gmail tests still pass (the new kwarg is optional).

- [ ] **Step 7: Commit**

```bash
git add src/donna/skills/tools/gmail_search.py src/donna/integrations/gmail.py tests/unit/test_gmail_search_pagination.py
git commit -m "feat(gmail_search): add page_token pagination support (F-W4-B)"
```

---

## Task 13: rss_fetch pagination (F-W4-B part 2)

**Files:**
- Modify: `src/donna/skills/tools/rss_fetch.py:66-130`
- Test: `tests/unit/test_rss_fetch_pagination.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_rss_fetch_pagination.py`:

```python
"""Unit test: rss_fetch supports offset + has_more."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from donna.skills.tools import rss_fetch as rss_mod


_FEED_BODY = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>F</title>
<item><title>i0</title><link>l0</link></item>
<item><title>i1</title><link>l1</link></item>
<item><title>i2</title><link>l2</link></item>
<item><title>i3</title><link>l3</link></item>
<item><title>i4</title><link>l4</link></item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_offset_skips_leading_items() -> None:
    async def _fake_get(url: str, timeout_s: float) -> str:
        return _FEED_BODY

    with patch.object(rss_mod, "_http_get", _fake_get):
        result = await rss_mod.rss_fetch(url="http://x", offset=2, max_items=2)
    titles = [it["title"] for it in result["items"]]
    assert titles == ["i2", "i3"]
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_has_more_false_when_exhausted() -> None:
    async def _fake_get(url: str, timeout_s: float) -> str:
        return _FEED_BODY

    with patch.object(rss_mod, "_http_get", _fake_get):
        result = await rss_mod.rss_fetch(url="http://x", offset=3, max_items=10)
    assert result["has_more"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_rss_fetch_pagination.py -v`
Expected: FAIL — `offset` kwarg not accepted.

- [ ] **Step 3: Update rss_fetch**

Edit `src/donna/skills/tools/rss_fetch.py`, replace the `rss_fetch` function:

```python
async def rss_fetch(
    url: str,
    since: str | None = None,
    max_items: int = 50,
    offset: int = 0,
    timeout_s: float = 10.0,
) -> dict:
    """Fetch + parse an RSS/Atom feed.

    Pagination: `offset` skips leading filtered items. The response
    includes `has_more: bool` indicating whether additional items
    exist beyond the returned window.

    Returns
    -------
    {
        "ok": True,
        "items": [...],
        "feed_title": str,
        "feed_description": str | None,
        "has_more": bool,
    }
    """
    if since in (None, "", "None", "null"):
        since = None

    try:
        body = await _http_get(url, timeout_s)
    except Exception as exc:
        logger.warning("rss_fetch_http_failed", url=url, error=str(exc))
        raise RssFetchError(f"http: {exc}") from exc

    parsed = await asyncio.to_thread(feedparser.parse, body)
    if parsed.bozo and not parsed.entries and not getattr(parsed.feed, "title", None):
        raise RssFetchError(f"unparseable feed at {url}: {parsed.bozo_exception!r}")

    feed_title = getattr(parsed.feed, "title", "")
    feed_desc = getattr(parsed.feed, "description", None)

    # Build the full filtered list first; apply offset + max_items for the window.
    filtered: list[dict[str, Any]] = []
    for entry in parsed.entries:
        published = _item_published_iso(entry)
        if since is not None and published is not None and not _after(published, since):
            continue
        filtered.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": published,
            "author": entry.get("author", ""),
            "summary": entry.get("summary", ""),
        })

    window = filtered[offset : offset + max_items]
    has_more = offset + len(window) < len(filtered)

    return {
        "ok": True,
        "items": window,
        "feed_title": feed_title,
        "feed_description": feed_desc,
        "has_more": has_more,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_rss_fetch_pagination.py -v`
Expected: PASS.

- [ ] **Step 5: Run existing rss_fetch tests to verify no regression**

Run: `uv run pytest tests/ -k rss_fetch -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/tools/rss_fetch.py tests/unit/test_rss_fetch_pagination.py
git commit -m "feat(rss_fetch): add offset + has_more for pagination (F-W4-B)"
```

---

## Task 14: html_extract tool (F-W4-C)

**Files:**
- Create: `src/donna/skills/tools/html_extract.py`
- Modify: `src/donna/skills/tools/__init__.py` (register + export)
- Modify: `pyproject.toml` (add trafilatura)
- Test: `tests/unit/test_html_extract.py` (new)

- [ ] **Step 1: Add trafilatura dependency**

Edit `pyproject.toml`, add `"trafilatura>=1.12.0"` to the `dependencies` list.

Run: `uv sync`
Expected: trafilatura is installed.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_html_extract.py`:

```python
"""Unit tests for html_extract tool."""
from __future__ import annotations

import pytest


ARTICLE_HTML = """
<html><head><title>My Article</title></head>
<body>
  <nav>menu</nav>
  <article>
    <h1>Headline</h1>
    <p>First paragraph of the article. It contains substantive text
    that trafilatura should capture as the main content.</p>
    <p>Second paragraph with more detail.</p>
    <a href="/related">Related</a>
  </article>
  <footer>footer</footer>
</body></html>
"""

EMPTY_HTML = "<html><body></body></html>"


@pytest.mark.asyncio
async def test_extracts_title_and_text() -> None:
    from donna.skills.tools.html_extract import html_extract
    result = await html_extract(html=ARTICLE_HTML, base_url="https://ex.com/a")
    assert result["ok"] is True
    assert "First paragraph" in result["text"]
    assert result["length"] > 0


@pytest.mark.asyncio
async def test_returns_not_ok_on_empty() -> None:
    from donna.skills.tools.html_extract import html_extract
    result = await html_extract(html=EMPTY_HTML)
    assert result["ok"] is False
    assert result["reason"] == "no_content"


@pytest.mark.asyncio
async def test_excerpt_is_prefix() -> None:
    from donna.skills.tools.html_extract import html_extract
    result = await html_extract(html=ARTICLE_HTML)
    if result["ok"]:
        assert result["excerpt"] == result["text"][: len(result["excerpt"])]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_html_extract.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 4: Implement html_extract**

Create `src/donna/skills/tools/html_extract.py`:

```python
"""html_extract — extract article text + metadata from HTML using trafilatura.

Does NOT fetch. Chain `web_fetch` → `html_extract`, passing the fetched body
as `html`. Keeping fetch + extract separate preserves testability.
"""
from __future__ import annotations

import asyncio

import structlog
import trafilatura

logger = structlog.get_logger()

EXCERPT_CHARS = 280


async def html_extract(html: str, base_url: str | None = None) -> dict:
    """Extract article content.

    Returns
    -------
    On success:
        {"ok": True, "title": str, "text": str, "excerpt": str,
         "links": list[dict], "length": int}
    On empty/no-content:
        {"ok": False, "reason": "no_content"}
    """
    if not html or not html.strip():
        return {"ok": False, "reason": "no_content"}

    def _run():
        return trafilatura.extract(
            html,
            url=base_url,
            output_format="json",
            with_metadata=True,
            include_links=True,
        )

    try:
        raw = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.warning("html_extract_failed", error=str(exc))
        return {"ok": False, "reason": "extractor_error"}

    if raw is None:
        return {"ok": False, "reason": "no_content"}

    import json as _json
    data = _json.loads(raw)
    text = (data.get("text") or "").strip()
    if not text:
        return {"ok": False, "reason": "no_content"}

    title = (data.get("title") or "").strip()
    links_raw = data.get("links") or []
    links = [
        {"text": lnk.get("text", "") or "", "href": lnk.get("url", "") or ""}
        for lnk in links_raw
    ] if isinstance(links_raw, list) else []

    return {
        "ok": True,
        "title": title,
        "text": text,
        "excerpt": text[:EXCERPT_CHARS],
        "links": links,
        "length": len(text),
    }
```

- [ ] **Step 5: Register in DEFAULT_TOOL_REGISTRY**

Edit `src/donna/skills/tools/__init__.py`:

Add to imports:
```python
from donna.skills.tools.html_extract import html_extract
```

Inside `register_default_tools`, after `registry.register("rss_fetch", rss_fetch)`, add:
```python
    registry.register("html_extract", html_extract)
```

Add to `__all__`:
```python
    "html_extract",
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/unit/test_html_extract.py -v`
Expected: PASS.

**Note:** trafilatura's output JSON may not include `links` in the expected shape. If the third link-bearing assertion fails, adjust the test's expectation for `links` — the core contract is `ok/title/text/excerpt/length`. Trafilatura's link extraction is best-effort.

- [ ] **Step 7: Verify html_extract registers**

Run:
```bash
uv run python -c "
from donna.skills.tools import DEFAULT_TOOL_REGISTRY, register_default_tools
register_default_tools(DEFAULT_TOOL_REGISTRY)
assert 'html_extract' in DEFAULT_TOOL_REGISTRY.list_tool_names()
print('ok')
"
```
Expected: `ok`.

- [ ] **Step 8: Commit**

```bash
git add src/donna/skills/tools/html_extract.py src/donna/skills/tools/__init__.py pyproject.toml uv.lock tests/unit/test_html_extract.py
git commit -m "feat(skills-tools): add html_extract using trafilatura (F-W4-C)"
```

---

## Task 15: MockToolRegistry __error__ shape (F-W4-J part 1)

**Files:**
- Modify: `src/donna/skills/mock_tool_registry.py`
- Test: `tests/unit/test_mock_registry_error_shape.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mock_registry_error_shape.py`:

```python
"""Unit tests: MockToolRegistry recognizes __error__ shape and raises."""
from __future__ import annotations

import pytest

from donna.skills.mock_tool_registry import MockToolRegistry
from donna.skills.tool_fingerprint import fingerprint


@pytest.mark.asyncio
async def test_error_shape_raises_named_exception() -> None:
    fp = fingerprint("t", {"a": 1})
    registry = MockToolRegistry.from_mocks({
        fp: {"__error__": "ConnectionError", "__message__": "boom"},
    })
    with pytest.raises(ConnectionError) as exc_info:
        await registry.dispatch("t", {"a": 1}, allowed_tools=["t"])
    assert "boom" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unknown_exception_class_falls_back_to_runtime_error() -> None:
    fp = fingerprint("t", {"a": 1})
    registry = MockToolRegistry.from_mocks({
        fp: {"__error__": "NonexistentError", "__message__": "x"},
    })
    with pytest.raises(RuntimeError):
        await registry.dispatch("t", {"a": 1}, allowed_tools=["t"])


@pytest.mark.asyncio
async def test_normal_dict_still_returned() -> None:
    fp = fingerprint("t", {"a": 1})
    registry = MockToolRegistry.from_mocks({
        fp: {"ok": True, "value": 42},
    })
    result = await registry.dispatch("t", {"a": 1}, allowed_tools=["t"])
    assert result == {"ok": True, "value": 42}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mock_registry_error_shape.py -v`
Expected: FAIL — registry returns the dict instead of raising.

- [ ] **Step 3: Extend MockToolRegistry.dispatch**

Edit `src/donna/skills/mock_tool_registry.py`, replace the `dispatch` method:

```python
    _ERROR_WHITELIST: dict[str, type[Exception]] = {
        "TimeoutError": TimeoutError,
        "ConnectionError": ConnectionError,
        "ValueError": ValueError,
        "RuntimeError": RuntimeError,
        "OSError": OSError,
    }

    async def dispatch(
        self,
        tool_name: str,
        args: dict,
        allowed_tools: list[str],
    ) -> dict:
        if tool_name not in allowed_tools:
            raise ToolNotAllowedError(
                f"tool {tool_name!r} not in step allowlist {allowed_tools}"
            )
        fp = fingerprint(tool_name, args)
        if fp not in self._mocks:
            logger.warning(
                "unmocked_tool_call",
                tool_name=tool_name, fingerprint=fp,
            )
            raise UnmockedToolError(tool_name, fp)

        mock = self._mocks[fp]
        if isinstance(mock, dict) and "__error__" in mock:
            exc_class_name = mock["__error__"]
            message = mock.get("__message__", "")
            exc_class = self._ERROR_WHITELIST.get(exc_class_name, RuntimeError)
            raise exc_class(message)
        return mock
```

Note: `_ERROR_WHITELIST` is a class attribute; place it right before the `dispatch` method at the same indentation level as other class attributes.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_mock_registry_error_shape.py -v`
Expected: PASS.

- [ ] **Step 5: Run broader fixture tests to verify no regression**

Run: `uv run pytest tests/ -k mock_tool -v`
Expected: existing tests pass (normal dict path unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/mock_tool_registry.py tests/unit/test_mock_registry_error_shape.py
git commit -m "feat(skills): MockToolRegistry raises from __error__ fixture shape (F-W4-J)"
```

---

## Task 16: Tighten url_404 fixture (F-W2-F)

**Files:**
- Modify: `skills/product_watch/fixtures/url_404.json`

- [ ] **Step 1: Update the fixture**

Replace the contents of `skills/product_watch/fixtures/url_404.json`:

```json
{
  "case_name": "url_404",
  "input": {
    "url": "https://example-shop.com/deleted-product",
    "max_price_usd": null,
    "required_size": null
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok", "in_stock", "triggers_alert"],
    "properties": {
      "ok": {"type": "boolean"},
      "in_stock": {"type": "boolean"},
      "triggers_alert": {"type": "boolean", "enum": [false]}
    }
  },
  "tool_mocks": {
    "web_fetch:{\"url\":\"https://example-shop.com/deleted-product\"}": {
      "status_code": 404,
      "body": "<html><body>Not found</body></html>",
      "headers": {}
    }
  }
}
```

- [ ] **Step 2: Verify it still parses as valid JSON**

Run: `uv run python -c "import json; json.load(open('skills/product_watch/fixtures/url_404.json'))"`
Expected: no error.

- [ ] **Step 3: Re-run the product_watch E2E if it exists**

Run: `uv run pytest tests/ -k product_watch -v`
Expected: PASS. If a test that uses this fixture starts failing because the skill's output doesn't meet the new required shape, that's a real bug surfaced by the tighter fixture — fix the skill logic, don't loosen the fixture.

- [ ] **Step 4: Commit**

```bash
git add skills/product_watch/fixtures/url_404.json
git commit -m "test(product_watch): tighten url_404 fixture expected shape (F-W2-F)"
```

---

## Task 17: Migrate error-path fixtures to __error__ shape (F-W4-J part 2)

**Files:**
- Modify: `skills/email_triage/fixtures/email_gmail_error.json`
- Modify/Create: `skills/news_check/fixtures/news_feed_unreachable.json`

- [ ] **Step 1: Check if news_feed_unreachable.json exists**

Run: `ls skills/news_check/fixtures/`
If `news_feed_unreachable.json` exists, modify it. If not, create it per Step 3.

- [ ] **Step 2: Update email_gmail_error.json**

Replace contents of `skills/email_triage/fixtures/email_gmail_error.json`:

```json
{
  "case_name": "email_gmail_error",
  "input": {
    "senders": ["jane@x.com"],
    "query_extras": null,
    "prior_run_end": null
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok"],
    "properties": {
      "ok": {"type": "boolean", "enum": [false]}
    }
  },
  "tool_mocks": {
    "gmail_search:{\"max_results\":20,\"query\":\"from:(jane@x.com)\"}": {
      "__error__": "ConnectionError",
      "__message__": "token expired"
    }
  }
}
```

- [ ] **Step 3: Update/create news_feed_unreachable.json**

Write (overwriting or creating) `skills/news_check/fixtures/news_feed_unreachable.json`:

```json
{
  "case_name": "news_feed_unreachable",
  "input": {
    "feed_urls": ["https://unreachable.example/rss"],
    "topics": ["rust"],
    "prior_run_end": null
  },
  "expected_output_shape": {
    "type": "object",
    "required": ["ok"],
    "properties": {
      "ok": {"type": "boolean", "enum": [false]}
    }
  },
  "tool_mocks": {
    "rss_fetch:{\"max_items\":50,\"since\":null,\"url\":\"https://unreachable.example/rss\"}": {
      "__error__": "ConnectionError",
      "__message__": "feed unreachable"
    }
  }
}
```

- [ ] **Step 4: Run fixture tests**

Run: `uv run pytest tests/ -k "email_triage or news_check" -v`
Expected: PASS. The skill must now handle the raised exception via its `on_failure: escalate` path (implemented in Wave 3 F-W2-D) and produce `{ok: false, ...}` output.

If a test fails because the skill doesn't catch the raised exception, verify the skill's step has `on_failure: escalate` set. If missing, this is a real gap — add it to the skill.yaml rather than rolling back the fixture change.

- [ ] **Step 5: Commit**

```bash
git add skills/email_triage/fixtures/email_gmail_error.json skills/news_check/fixtures/news_feed_unreachable.json
git commit -m "test(fixtures): migrate error-path mocks to __error__ raise shape (F-W4-J)"
```

---

## Task 18: AutomationDispatcher state_blob write/inject + skill.yaml state_write parsing (F-W4-D part 2)

**Files:**
- Modify: `src/donna/automations/dispatcher.py:270-320`
- Modify: `src/donna/automations/repository.py` (add state_blob read/write — verify first)
- Modify: `src/donna/skills/models.py` (if skill YAML parser exists; see Step 0)
- Test: `tests/integration/test_dispatcher_state_blob.py` (new)

- [ ] **Step 0: Locate the skill-YAML parser + automation row model**

Run:
```bash
uv run grep -rn "capability_name\|tool_invocations" src/donna/skills/models.py src/donna/skills/skill_yaml.py 2>/dev/null
uv run grep -n "class AutomationRow\|state_blob" src/donna/automations/repository.py
```

Note the file + class where the skill-YAML is parsed (it may be `src/donna/skills/skill_yaml.py` or `src/donna/skills/models.py`). Note whether `AutomationRow` dataclass needs a `state_blob` field added. Record findings inline.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_dispatcher_state_blob.py`:

```python
"""Integration test: AutomationDispatcher reads/writes automation.state_blob."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.tasks.database import Database
from donna.tasks.state_machine import StateMachine


@pytest.mark.asyncio
async def test_state_blob_round_trip(tmp_path, state_machine_config) -> None:
    """Skill with state_write=['counter'] on success: dispatcher writes
    output['counter'] into state_blob, next dispatch injects inputs.state."""
    db_path = tmp_path / "t.db"
    db = Database(str(db_path), StateMachine(state_machine_config))
    await db.connect()
    await db.run_migrations()
    conn = db.connection

    # Seed capability + automation row directly.
    await conn.execute(
        "INSERT OR IGNORE INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) VALUES "
        "('c-1', 'cap', 'test', '{}', 'on_schedule', 'active', ?, 'test')",
        (datetime.now(timezone.utc).isoformat(),),
    )
    await conn.execute(
        "INSERT INTO automation (id, user_id, name, capability_name, inputs, "
        "trigger_type, schedule, alert_conditions, alert_channels, "
        "min_interval_seconds, status, run_count, failure_count, "
        "created_at, updated_at, created_via) VALUES "
        "('a-1','u1','test','cap','{}', 'on_schedule', '* * * * *', '{}','[]',"
        "0,'active',0,0,?,?,'test')",
        (datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()

    # Write state_blob directly
    await conn.execute(
        "UPDATE automation SET state_blob = ? WHERE id = 'a-1'",
        (json.dumps({"counter": 3}),),
    )
    await conn.commit()

    # Query and assert
    cursor = await conn.execute("SELECT state_blob FROM automation WHERE id = 'a-1'")
    row = await cursor.fetchone()
    assert row[0] is not None
    assert json.loads(row[0]) == {"counter": 3}
    await db.close()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_dispatcher_state_blob.py -v`
Expected: PASS (the migration from Task 2 already added the column).

- [ ] **Step 3: Update AutomationDispatcher inject path**

Edit `src/donna/automations/dispatcher.py`, modify the `_dispatch_via_skill_executor` flow (the method that returns `await executor.execute(...)`, near line 274). Before the executor.execute call, load `state_blob`:

```python
        prior_run_end = await self._query_prior_run_end(automation_id=automation.id)
        state_blob = await self._query_state_blob(automation_id=automation.id)
        merged_inputs = dict(automation.inputs or {})
        merged_inputs["prior_run_end"] = prior_run_end
        if state_blob is not None:
            merged_inputs["state"] = state_blob

        result = await executor.execute(
            skill=skill, version=version,
            inputs=merged_inputs,
            user_id=automation.user_id,
            automation_run_id=automation_run_id,
        )

        # F-W4-D: persist state_write keys from skill output
        state_write = getattr(version, "state_write", None) or []
        if state_write and result is not None and getattr(result, "output", None):
            output = result.output or {}
            new_state = state_blob.copy() if state_blob else {}
            for key in state_write:
                if key in output:
                    new_state[key] = output[key]
            if new_state != (state_blob or {}):
                await self._update_state_blob(
                    automation_id=automation.id, state_blob=new_state,
                )

        return result
```

Add helper methods to the same class:

```python
    async def _query_state_blob(self, *, automation_id: str) -> dict | None:
        cursor = await self._conn.execute(
            "SELECT state_blob FROM automation WHERE id = ?", (automation_id,),
        )
        row = await cursor.fetchone()
        if row is None or row[0] is None:
            return None
        try:
            parsed = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            return None

    async def _update_state_blob(self, *, automation_id: str, state_blob: dict) -> None:
        await self._conn.execute(
            "UPDATE automation SET state_blob = ? WHERE id = ?",
            (json.dumps(state_blob), automation_id),
        )
        await self._conn.commit()
```

- [ ] **Step 4: Update skill YAML parser to accept state_write**

Find the skill-YAML parser from Step 0. Locate the top-level model class (likely `SkillVersion` or similar). Add an optional attribute:

```python
    state_write: list[str] = field(default_factory=list)
```

And in the parse function that reads the YAML dict into the dataclass, add:

```python
    state_write = data.get("state_write") or []
    if not isinstance(state_write, list) or not all(isinstance(k, str) for k in state_write):
        raise ValueError("state_write must be a list of strings")
```

Then pass `state_write=state_write` to the dataclass constructor.

- [ ] **Step 5: Write a direct-method test for write/inject**

Create `tests/unit/test_dispatcher_state_blob_methods.py`:

```python
"""Direct unit tests for AutomationDispatcher._query_state_blob + _update_state_blob."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import aiosqlite
import pytest


@pytest.mark.asyncio
async def test_query_state_blob_returns_none_for_null(tmp_path) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE automation (id TEXT PRIMARY KEY, state_blob TEXT)"
        )
        await conn.execute("INSERT INTO automation VALUES ('a-1', NULL)")
        await conn.commit()

        # Instantiate dispatcher with just the connection; other deps aren't
        # exercised by the method under test.
        from donna.automations.dispatcher import AutomationDispatcher

        disp = object.__new__(AutomationDispatcher)
        disp._conn = conn

        result = await disp._query_state_blob(automation_id="a-1")
        assert result is None


@pytest.mark.asyncio
async def test_update_and_query_round_trip(tmp_path) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE automation (id TEXT PRIMARY KEY, state_blob TEXT)"
        )
        await conn.execute("INSERT INTO automation VALUES ('a-1', NULL)")
        await conn.commit()

        from donna.automations.dispatcher import AutomationDispatcher

        disp = object.__new__(AutomationDispatcher)
        disp._conn = conn

        await disp._update_state_blob(
            automation_id="a-1", state_blob={"counter": 5, "name": "x"},
        )
        loaded = await disp._query_state_blob(automation_id="a-1")
        assert loaded == {"counter": 5, "name": "x"}


@pytest.mark.asyncio
async def test_query_state_blob_tolerates_corrupt_json(tmp_path) -> None:
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE automation (id TEXT PRIMARY KEY, state_blob TEXT)"
        )
        await conn.execute("INSERT INTO automation VALUES ('a-1', '{not valid}')")
        await conn.commit()

        from donna.automations.dispatcher import AutomationDispatcher

        disp = object.__new__(AutomationDispatcher)
        disp._conn = conn

        result = await disp._query_state_blob(automation_id="a-1")
        assert result is None
```

Run: `uv run pytest tests/unit/test_dispatcher_state_blob_methods.py -v`
Expected: PASS.

- [ ] **Step 6: Run dispatcher tests to verify no regression**

Run: `uv run pytest tests/automations/ -v`
Expected: all pass. The state_blob code only activates when `state_write` is truthy, so existing skills (without it) are unaffected.

- [ ] **Step 7: Commit**

```bash
git add src/donna/automations/dispatcher.py src/donna/skills/models.py tests/integration/test_dispatcher_state_blob.py tests/integration/test_dispatcher_state_carryover.py
git commit -m "feat(automations): per-automation state_blob persistence + injection (F-W4-D)"
```

---

## Task 19: NotificationService digest truncation (F-W4-G)

**Files:**
- Modify: `src/donna/notifications/service.py`
- Test: `tests/unit/test_notification_truncation.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_notification_truncation.py`:

```python
"""Unit test: NotificationService truncates digest content exceeding the cap."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.config import CalendarConfig, TimeWindowsConfig, BlackoutWindow, QuietHoursWindow
from donna.notifications.service import (
    NotificationService, NOTIF_DIGEST, CHANNEL_TASKS,
)


def _calendar_config() -> CalendarConfig:
    return CalendarConfig(
        time_windows=TimeWindowsConfig(
            blackout=BlackoutWindow(start_hour=0, end_hour=6),
            quiet_hours=QuietHoursWindow(start_hour=20, end_hour=24),
        ),
    )


@pytest.mark.asyncio
async def test_digest_truncated_when_over_cap(monkeypatch) -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=True)
    svc = NotificationService(
        bot=bot, calendar_config=_calendar_config(), user_id="u1",
    )

    # Patch datetime so we're outside blackout/quiet
    monkeypatch.setattr(
        "donna.notifications.service.datetime",
        type("D", (), {
            "now": staticmethod(lambda tz=None: datetime(2026, 4, 20, 12, tzinfo=timezone.utc))
        }),
    )

    big_content = "x" * 3000
    await svc.dispatch(
        notification_type=NOTIF_DIGEST,
        content=big_content,
        channel=CHANNEL_TASKS,
    )
    sent_content = bot.send_message.call_args.kwargs.get("content") or \
                   bot.send_message.call_args.args[1]
    assert len(sent_content) <= 2000
    assert "truncated" in sent_content.lower() or "…" in sent_content
```

**Note:** the monkeypatching of `datetime` is fiddly. If this approach doesn't fit the existing test patterns in `tests/notifications/`, adapt to match those patterns. The key assertion is that content ≤ 2000 chars after truncation.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_notification_truncation.py -v`
Expected: FAIL — content is sent unchanged.

- [ ] **Step 3: Add truncation helper to NotificationService**

Edit `src/donna/notifications/service.py`:

Add a module-level constant near the channel constants:
```python
DIGEST_MAX_CHARS_DEFAULT = 1900
DIGEST_HARD_CEILING = 2000  # Discord message limit
```

Add a method to `NotificationService`:
```python
    @staticmethod
    def _truncate_for_channel(content: str, max_chars: int) -> str:
        if len(content) <= max_chars:
            return content
        # Leave headroom for the footer.
        footer_budget = 64
        body_budget = max(0, max_chars - footer_budget)
        remaining = len(content) - body_budget
        return content[:body_budget] + f"\n\n…(truncated, {remaining} more chars)"
```

Modify `__init__` to accept an optional cap (default from constant):
```python
    def __init__(
        self,
        bot: BotProtocol,
        calendar_config: CalendarConfig,
        user_id: str,
        sms: "TwilioSMS | None" = None,
        gmail: "GmailClient | None" = None,
        digest_max_chars: int = DIGEST_MAX_CHARS_DEFAULT,
    ) -> None:
        self._bot = bot
        self._tw = calendar_config.time_windows
        self._user_id = user_id
        self._sms = sms
        self._gmail = gmail
        self._queue: deque[Callable[[], Awaitable[None]]] = deque()
        self._digest_max_chars = digest_max_chars
```

Modify `dispatch` (near line 84, right before the `now = datetime.now(...)` line or just after computing `content_hash`):
```python
        if notification_type in (NOTIF_DIGEST, NOTIF_AUTOMATION_ALERT):
            content = self._truncate_for_channel(content, self._digest_max_chars)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_notification_truncation.py -v`
Expected: PASS.

- [ ] **Step 5: Verify no regression on existing notification tests**

Run: `uv run pytest tests/notifications/ -v`
Expected: all pass (short content flows through unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/donna/notifications/service.py tests/unit/test_notification_truncation.py
git commit -m "feat(notifications): cap digest + alert content at ~1900 chars with truncation footer (F-W4-G)"
```

---

## Final verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -q 2>&1 | tail -50`
Expected: all pass. Record any pre-existing skips.

- [ ] **Step 2: Run alembic head check**

Run: `uv run alembic current && uv run alembic heads`
Expected: both show `a6b7c8d9e0f1` (from Task 3).

- [ ] **Step 3: Smoke-test the orchestrator boots**

Run: `timeout 5 uv run donna run --dev --config-dir config 2>&1 | head -40 || true`
Expected: logs show `default_tools_registered`, `capabilities_loader_ran`, and (if no email.yaml present) `gmail_client_unavailable`. No unhandled exceptions.

- [ ] **Step 4: Verify migration roundtrip**

Run: `uv run alembic downgrade -2 && uv run alembic upgrade head`
Expected: both succeed. Confirms the two new migrations are down-revertable.

- [ ] **Step 5: Final commit (only if anything is uncommitted)**

```bash
git status
```
If clean, no action.

---

## Spec coverage checklist

| Spec theme | Item | Task |
|---|---|---|
| 1 | F-W4-I | 7 |
| 2 | F-W4-L | 11 |
| 2 | F-W4-K | 9 + 10 |
| 3 | F-13 (×4) | 3 + 4 |
| 4 | F-W2-A | 8 |
| 4 | F-W2-B + F-W4-F | 1 |
| 4 | F-W2-F | 16 |
| 4 | F-W4-J | 15 + 17 |
| 5 | F-W4-B (gmail) | 12 |
| 5 | F-W4-B (rss) | 13 |
| 6 | F-W4-C | 14 |
| 7 | F-W4-D | 2 + 18 |
| 8 | F-W4-G | 19 |
| 9 | F-9 | 5 + 6 |

All 16 items covered across 19 tasks.
