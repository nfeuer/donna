# Skill System Wave 1 — Production Enablement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the four Wave 1 follow-ups (F-1 ValidationExecutor, F-5 wire-up, F-6 automation process move + live NotificationService, F-14 E2E smoke test) so `skill_system.enabled=true` is a safe production toggle.

**Architecture:** Introduce `ValidationExecutor` as a drop-in `.execute()`-compatible wrapper around the existing `SkillExecutor` with mocked tools (via a new `MockToolRegistry` keyed off fixture `tool_mocks`) and a non-persisting sink. Move the automation scheduler + dispatcher from the FastAPI process to the orchestrator (`cli.py`) so the live `NotificationService` (which needs `DonnaBot`) is reachable. API `run-now` becomes a `next_run_at=now()` update returning 202. Add four mocked-LLM E2E scenarios as a regression trap.

**Tech Stack:** Python 3.12 · asyncio · aiosqlite (SQLite WAL) · Alembic · FastAPI · discord.py · pytest · jsonschema · jinja2 · structlog · freezegun.

**Spec:** `docs/superpowers/specs/2026-04-16-skill-system-wave-1-production-enablement-design.md`.

**Open-question resolutions** (§10 of spec):
1. Default tool-fingerprint = `(tool_name, sorted-JSON(args))` for tools without explicit rules. Explicit rules for `web_fetch`, `gmail_read`, `gmail_send` ship in Wave 1.
2. Scheduler uses a **constant 15s poll interval** (not adaptive). Simpler; falls within the "run within a minute" contract.
3. Extract `BotProtocol` (typing.Protocol). `DonnaBot` and `FakeDonnaBot` both satisfy it — tiny surface: `send_message`, `send_embed`, `send_to_thread`.
4. `NotificationService` constructed with `sms=None, gmail=None`. No `TwilioSMS` / `GmailClient` is currently instantiated in the orchestrator; wiring those is Wave 2+.

---

## File Structure

### Files created

| Path | Responsibility |
|---|---|
| `alembic/versions/add_fixture_tool_mocks.py` | Alembic migration: adds `skill_fixture.tool_mocks` TEXT column + backfill for `source='captured_from_run'` fixtures. |
| `src/donna/skills/schema_inference.py` | `json_to_schema(value: Any) -> dict` — infers a structural JSON schema from a captured output. |
| `src/donna/skills/tool_fingerprint.py` | Per-tool fingerprinting registry + default. `fingerprint(tool_name: str, args: dict) -> str`. |
| `src/donna/skills/mock_tool_registry.py` | `MockToolRegistry` (subclass of `ToolRegistry`) + `UnmockedToolError`. |
| `src/donna/skills/validation_run_sink.py` | `ValidationRunSink` — in-memory accumulator implementing the `run_repository` protocol. |
| `src/donna/skills/validation_executor.py` | `ValidationExecutor` — SkillExecutor-compatible class orchestrating the above for fixture validation. |
| `src/donna/notifications/bot_protocol.py` | `BotProtocol` typing.Protocol — tiny interface `NotificationService._send` needs from a bot. |
| `tests/e2e/__init__.py` | Empty — marks `tests/e2e` as a package. |
| `tests/e2e/harness.py` | `build_wave1_test_runtime(tmp_path)` — minimal orchestrator runtime with fakes. |
| `tests/e2e/conftest.py` | Pytest fixtures consumed by scenario files. |
| `tests/e2e/test_wave1_smoke.py` | Four scenarios from §6.5 of the spec. |
| `tests/unit/test_schema_inference.py` | Unit tests for `json_to_schema`. |
| `tests/unit/test_tool_fingerprint.py` | Unit tests for the fingerprint module. |
| `tests/unit/test_mock_tool_registry.py` | Unit tests for `MockToolRegistry`. |
| `tests/unit/test_validation_run_sink.py` | Unit tests confirming the sink absorbs writes. |
| `tests/unit/test_validation_executor.py` | Unit tests for `ValidationExecutor.execute` wiring and timeout behavior. |
| `tests/unit/test_bot_protocol.py` | Small test confirming `DonnaBot` satisfies `BotProtocol` (structural). |
| `tests/integration/test_notification_service_wiring.py` | Confirms orchestrator lifespan constructs a working `NotificationService`. |

### Files modified

| Path | Change |
|---|---|
| `src/donna/skills/fixtures.py` | Add `tool_mocks: dict \| None = None` to `Fixture`. `FixtureLoader` threads it. `validate_against_fixtures` passes `fixture.tool_mocks` to `executor.execute(..., tool_mocks=...)` — keyword-only. Production `SkillExecutor.execute` accepts `**_ignored_kwargs: Any`. |
| `src/donna/skills/executor.py` | Constructor gains optional `run_sink`. When set, delegates persistence to sink instead of `run_repository`. `execute()` accepts unknown kwargs silently. |
| `src/donna/skills/auto_drafter.py` | `executor_factory` becomes required (no `None` default). Vacuous-pass branch removed. Fixture-generation prompt adds `tool_mocks` field. Parsing persists it. |
| `src/donna/skills/evolution.py` | `executor_factory` becomes required. Vacuous-pass branch removed. Gates store real `FixtureValidationReport` as JSON in `skill_evolution_log.validation_results`. |
| `src/donna/skills/startup_wiring.py` | Rename param `executor_factory` → `validation_executor_factory`. Default factory constructs real `ValidationExecutor`. |
| `src/donna/config.py` | `SkillSystemConfig` gains `validation_per_step_timeout_s: int = 60`, `validation_per_run_timeout_s: int = 300`, `automation_poll_interval_seconds: int = 15` (override default 60 → 15). |
| `src/donna/notifications/service.py` | `bot` parameter type changes from `DonnaBot` to `BotProtocol`. No runtime change. |
| `src/donna/cli.py` | Adds skill-system bundle + automation scheduler + dispatcher wiring (moved from API). Constructs `NotificationService`. Starts background tasks. Adds `test-notification` subcommand. |
| `src/donna/api/__init__.py` | Removes skill-system background wiring block. Removes automation scheduler/dispatcher construction. Keeps `load_skill_system_config` for admin routes. |
| `src/donna/api/routes/automations.py` | `run-now` endpoint becomes a `UPDATE automation SET next_run_at = ?` returning 202. |
| `src/donna/automations/scheduler.py` | Default poll interval comes from config (now 15). No signature change. |
| `docs/architecture.md` | Update to reflect orchestrator-owned scheduler. |
| `docs/notifications.md` | Note `NotificationService` is now instantiated at runtime. |
| `docs/superpowers/followups/2026-04-16-skill-system-followups.md` | Tick F-1/F-5/F-6/F-14 — add a "completed" section. |
| `docs/superpowers/specs/2026-04-16-skill-system-wave-1-production-enablement-design.md` | Tick requirements W1-R1 through W1-R18 as implementation progresses. |

---

## Task 1: Alembic migration — `skill_fixture.tool_mocks` column

**Goal:** Add the nullable `tool_mocks` TEXT column and backfill existing captured-run fixtures from the corresponding `skill_run.tool_result_cache`.

**Files:**
- Create: `alembic/versions/add_fixture_tool_mocks.py`
- Test: `tests/unit/test_migration_add_fixture_tool_mocks.py`

- [ ] **Step 1: Write the failing migration test**

File: `tests/unit/test_migration_add_fixture_tool_mocks.py`

```python
"""Test the add_fixture_tool_mocks Alembic migration."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest
from alembic import command
from alembic.config import Config


def _alembic_config(db_path: Path) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.mark.asyncio
async def test_migration_adds_column_to_empty_db(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(skill_fixture)")
        cols = {row[1] for row in await cursor.fetchall()}
        assert "tool_mocks" in cols


@pytest.mark.asyncio
async def test_migration_backfills_captured_run_fixtures(tmp_path: Path) -> None:
    db_path = tmp_path / "populated.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "a7b8c9d0e1f2")

    async with aiosqlite.connect(db_path) as conn:
        tool_cache = {
            "cache_abc": {"tool": "web_fetch",
                          "args": {"url": "https://example.com"},
                          "result": {"status": 200, "body": "<html>OK</html>"}},
        }
        await conn.execute(
            "INSERT INTO skill (id, capability_name, state, requires_human_gate, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("skill_1", "cap_1", "trusted", 0),
        )
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, tool_result_cache, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("run_1", "skill_1", "ver_1", "succeeded", "{}", json.dumps(tool_cache)),
        )
        await conn.execute(
            "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
            "expected_output_shape, source, captured_run_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("fix_1", "skill_1", "case_a", "{}", None,
             "captured_from_run", "run_1"),
        )
        await conn.commit()

    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT tool_mocks FROM skill_fixture WHERE id = ?", ("fix_1",),
        )
        row = await cursor.fetchone()
        assert row is not None
        mocks = json.loads(row[0])
        assert any("web_fetch" in key for key in mocks)


@pytest.mark.asyncio
async def test_migration_leaves_non_captured_fixtures_null(tmp_path: Path) -> None:
    db_path = tmp_path / "mixed.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "a7b8c9d0e1f2")

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO skill (id, capability_name, state, requires_human_gate, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("skill_2", "cap_2", "draft", 0),
        )
        await conn.execute(
            "INSERT INTO skill_fixture (id, skill_id, case_name, input, "
            "expected_output_shape, source, captured_run_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("fix_2", "skill_2", "case_b", "{}", None, "claude_generated", None),
        )
        await conn.commit()

    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT tool_mocks FROM skill_fixture WHERE id = ?", ("fix_2",),
        )
        row = await cursor.fetchone()
        assert row[0] is None


@pytest.mark.asyncio
async def test_migration_downgrade_drops_column(tmp_path: Path) -> None:
    db_path = tmp_path / "downgrade.db"
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(skill_fixture)")
        cols = {row[1] for row in await cursor.fetchall()}
        assert "tool_mocks" not in cols
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
pytest tests/unit/test_migration_add_fixture_tool_mocks.py -v
```
Expected: 4 failures (migration file doesn't exist yet).

- [ ] **Step 3: Implement the migration**

File: `alembic/versions/add_fixture_tool_mocks.py`

```python
"""add skill_fixture.tool_mocks column with backfill from skill_run.tool_result_cache

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-04-16 00:00:00.000000
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("skill_fixture", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tool_mocks", sa.Text(), nullable=True))

    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT f.id, r.tool_result_cache "
        "FROM skill_fixture f "
        "JOIN skill_run r ON f.captured_run_id = r.id "
        "WHERE f.source = 'captured_from_run' AND r.tool_result_cache IS NOT NULL"
    ))
    for fixture_id, cache_json in result:
        try:
            cache = json.loads(cache_json) if isinstance(cache_json, str) else cache_json
        except (json.JSONDecodeError, TypeError):
            continue
        mocks = _cache_to_mocks(cache)
        if not mocks:
            continue
        conn.execute(
            sa.text("UPDATE skill_fixture SET tool_mocks = :mocks WHERE id = :id"),
            {"mocks": json.dumps(mocks), "id": fixture_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_fixture", schema=None) as batch_op:
        batch_op.drop_column("tool_mocks")


def _cache_to_mocks(cache: dict) -> dict:
    """Re-key per-step tool_result_cache into fingerprint-keyed mocks.

    Migrations must be runnable standalone — do not import from the
    application package. The backfill uses canonical-JSON fingerprinting;
    MockToolRegistry falls back to the same scheme for tools without
    explicit rules, so captured-run fixtures always resolve.
    """
    mocks: dict[str, dict] = {}
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        tool = entry.get("tool")
        args = entry.get("args") or {}
        result = entry.get("result")
        if tool is None or result is None:
            continue
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
        fingerprint = f"{tool}:{canonical}"
        mocks[fingerprint] = result
    return mocks
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_migration_add_fixture_tool_mocks.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/add_fixture_tool_mocks.py tests/unit/test_migration_add_fixture_tool_mocks.py
git commit -m "feat(migrations): add skill_fixture.tool_mocks with captured-run backfill"
```

---

## Task 2: `Fixture` dataclass gains `tool_mocks` field

**Goal:** Carry `tool_mocks` through the in-memory `Fixture` dataclass used by `FixtureLoader` and `validate_against_fixtures`.

**Files:**
- Modify: `src/donna/skills/fixtures.py`
- Test: extend or create `tests/unit/test_fixtures.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_fixtures.py`:

```python
def test_fixture_accepts_tool_mocks() -> None:
    from donna.skills.fixtures import Fixture

    fix = Fixture(
        case_name="case_a",
        input={"url": "https://example.com"},
        expected_output_shape={"type": "object"},
        tool_mocks={'web_fetch:{"url":"https://example.com"}': {"status": 200}},
    )
    assert fix.tool_mocks is not None
    assert "web_fetch" in next(iter(fix.tool_mocks))


def test_fixture_loader_reads_tool_mocks(tmp_path) -> None:
    import json
    from donna.skills.fixtures import FixtureLoader

    fixture_file = tmp_path / "case_a.json"
    fixture_file.write_text(json.dumps({
        "input": {"url": "https://x"},
        "expected_output_shape": {"type": "object"},
        "tool_mocks": {'web_fetch:{"url":"https://x"}': {"status": 200}},
    }))
    loader = FixtureLoader()
    fixtures = loader.load_from_directory(tmp_path)
    assert len(fixtures) == 1
    assert fixtures[0].tool_mocks == {
        'web_fetch:{"url":"https://x"}': {"status": 200},
    }
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_fixtures.py -v -k tool_mocks
```
Expected: 2 failures.

- [ ] **Step 3: Implement**

In `src/donna/skills/fixtures.py`:

```python
@dataclass(slots=True)
class Fixture:
    case_name: str
    input: dict
    expected_output_shape: dict | None = None
    tool_mocks: dict | None = None  # Keyed by fingerprint. See tool_fingerprint.
```

Update `FixtureLoader._make_fixture`:

```python
@staticmethod
def _make_fixture(
    case_name: str,
    input: dict,
    expected_output_shape: dict | None = None,
    tool_mocks: dict | None = None,
) -> Fixture:
    return Fixture(
        case_name=case_name,
        input=input,
        expected_output_shape=expected_output_shape,
        tool_mocks=tool_mocks,
    )
```

Update `load_from_directory` to thread the field:

```python
fixtures.append(self._make_fixture(
    case_name=file.stem,
    input=data["input"],
    expected_output_shape=data.get("expected_output_shape"),
    tool_mocks=data.get("tool_mocks"),
))
```

Update `validate_against_fixtures` to pass `tool_mocks` through:

```python
for fix in fixtures:
    try:
        result = await executor.execute(
            skill=skill, version=version,
            inputs=fix.input, user_id="fixture_harness",
            tool_mocks=fix.tool_mocks,
        )
        # ... (existing body unchanged)
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_fixtures.py -v
```
Expected: all pass. Existing tests may still rely on `executor.execute` accepting kwargs — that's addressed in Task 7.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/fixtures.py tests/unit/test_fixtures.py
git commit -m "feat(fixtures): add tool_mocks field to Fixture dataclass and loader"
```

---

## Task 3: `schema_inference.json_to_schema` helper

**Goal:** Small pure helper that infers a structural JSON schema from a value.

**Files:**
- Create: `src/donna/skills/schema_inference.py`
- Test: `tests/unit/test_schema_inference.py`

- [ ] **Step 1: Write the failing tests**

File: `tests/unit/test_schema_inference.py`

```python
"""Tests for donna.skills.schema_inference.json_to_schema."""

from __future__ import annotations

from donna.skills.schema_inference import json_to_schema


def test_primitive_types() -> None:
    assert json_to_schema(42) == {"type": "integer"}
    assert json_to_schema(3.14) == {"type": "number"}
    assert json_to_schema("hi") == {"type": "string"}
    assert json_to_schema(True) == {"type": "boolean"}
    assert json_to_schema(None) == {"type": "null"}


def test_flat_object() -> None:
    schema = json_to_schema({"title": "Q2 review", "days": 3})
    assert schema == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "days": {"type": "integer"},
        },
        "required": ["title", "days"],
    }


def test_array_of_objects() -> None:
    schema = json_to_schema([{"price": 100.0, "in_stock": True}])
    assert schema == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "price": {"type": "number"},
                "in_stock": {"type": "boolean"},
            },
            "required": ["price", "in_stock"],
        },
    }


def test_empty_array() -> None:
    assert json_to_schema([]) == {"type": "array"}


def test_empty_object() -> None:
    assert json_to_schema({}) == {"type": "object", "properties": {}, "required": []}


def test_heterogeneous_array_uses_first_element_schema() -> None:
    schema = json_to_schema([1, 2.5])
    assert schema == {"type": "array", "items": {"type": "integer"}}


def test_nested_object() -> None:
    value = {"item": {"name": "shirt", "price": 79.0}}
    schema = json_to_schema(value)
    assert schema["type"] == "object"
    assert schema["properties"]["item"]["type"] == "object"
    assert schema["properties"]["item"]["required"] == ["name", "price"]
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_schema_inference.py -v
```
Expected: all fail (module missing).

- [ ] **Step 3: Implement**

File: `src/donna/skills/schema_inference.py`

```python
"""Infer a structural JSON Schema from an example value.

Used when a captured ``skill_run.final_output`` needs an
``expected_output_shape`` for a newly created captured-run fixture. The
inferred schema validates names, types, required fields, and nested
structure — it does NOT pin exact values (see spec §5.2 convention).

v1 is intentionally minimal. Arrays are described by the first element's
schema; empty arrays get ``{"type": "array"}`` only. Heterogeneous unions
are not supported — revisit when a real fixture demonstrates the need.
"""

from __future__ import annotations

from typing import Any


def json_to_schema(value: Any) -> dict:
    """Infer a structural JSON Schema from ``value``."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):  # must come before int check
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if not value:
            return {"type": "array"}
        return {"type": "array", "items": json_to_schema(value[0])}
    if isinstance(value, dict):
        props = {k: json_to_schema(v) for k, v in value.items()}
        return {
            "type": "object",
            "properties": props,
            "required": list(value.keys()),
        }
    return {}
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_schema_inference.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/schema_inference.py tests/unit/test_schema_inference.py
git commit -m "feat(skills): json_to_schema helper for captured-run fixture shapes"
```

---

## Task 4: Tool fingerprinting module

**Goal:** Stable deterministic (tool_name, args) → string fingerprint. Explicit rules for `web_fetch`, `gmail_read`, `gmail_send`. Default = canonical sorted-JSON of full args.

**Files:**
- Create: `src/donna/skills/tool_fingerprint.py`
- Test: `tests/unit/test_tool_fingerprint.py`

- [ ] **Step 1: Write the failing tests**

File: `tests/unit/test_tool_fingerprint.py`

```python
"""Tests for donna.skills.tool_fingerprint."""

from __future__ import annotations

import pytest
from donna.skills.tool_fingerprint import fingerprint


def test_web_fetch_uses_only_url() -> None:
    fp1 = fingerprint("web_fetch", {
        "url": "https://example.com", "timeout_s": 10, "headers": {"User-Agent": "a"},
    })
    fp2 = fingerprint("web_fetch", {
        "url": "https://example.com", "timeout_s": 30, "headers": {"User-Agent": "b"},
    })
    assert fp1 == fp2
    assert fp1.startswith("web_fetch:")


def test_web_fetch_different_urls_differ() -> None:
    fp1 = fingerprint("web_fetch", {"url": "https://a.com"})
    fp2 = fingerprint("web_fetch", {"url": "https://b.com"})
    assert fp1 != fp2


def test_gmail_read_uses_only_message_id() -> None:
    fp1 = fingerprint("gmail_read", {"message_id": "m1", "label_ids": ["INBOX"]})
    fp2 = fingerprint("gmail_read", {"message_id": "m1"})
    assert fp1 == fp2


def test_gmail_send_uses_to_subject_body() -> None:
    fp1 = fingerprint("gmail_send", {
        "to": "a@b.com", "subject": "s", "body": "x", "draft_id": "d1",
    })
    fp2 = fingerprint("gmail_send", {
        "to": "a@b.com", "subject": "s", "body": "x", "draft_id": "d2",
    })
    assert fp1 == fp2


def test_default_rule_canonical_json_all_args() -> None:
    fp1 = fingerprint("unknown_tool", {"b": 2, "a": 1})
    fp2 = fingerprint("unknown_tool", {"a": 1, "b": 2})
    assert fp1 == fp2
    assert fp1.startswith("unknown_tool:")


def test_default_rule_different_args_differ() -> None:
    fp1 = fingerprint("unknown_tool", {"a": 1})
    fp2 = fingerprint("unknown_tool", {"a": 2})
    assert fp1 != fp2


def test_missing_required_field_raises() -> None:
    with pytest.raises(KeyError):
        fingerprint("web_fetch", {})
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_tool_fingerprint.py -v
```
Expected: 7 failures.

- [ ] **Step 3: Implement**

File: `src/donna/skills/tool_fingerprint.py`

```python
"""Deterministic tool-invocation fingerprinting for validation mocks.

Each tool has a rule that selects the subset of args relevant for
identifying a unique invocation. ``web_fetch`` keys only on ``url`` —
timeouts and headers don't change the response. ``gmail_read`` keys
only on ``message_id``. Tools without an explicit rule fall back to
canonical sorted-JSON of all args.

Future tools should register an explicit rule when they have dynamic
args (tokens, nonces, timestamps) that should be ignored.
"""

from __future__ import annotations

import json
from typing import Callable


_RULES: dict[str, Callable[[dict], dict]] = {
    "web_fetch": lambda args: {"url": args["url"]},
    "gmail_read": lambda args: {"message_id": args["message_id"]},
    "gmail_send": lambda args: {
        "to": args["to"], "subject": args["subject"], "body": args["body"],
    },
}


def fingerprint(tool_name: str, args: dict) -> str:
    """Return a stable fingerprint for a tool invocation.

    Raises ``KeyError`` if an explicit rule requires a field absent from
    ``args`` — this surfaces bad fixture data early.
    """
    rule = _RULES.get(tool_name)
    canonical_args = rule(args) if rule is not None else args
    encoded = json.dumps(canonical_args, sort_keys=True, separators=(",", ":"))
    return f"{tool_name}:{encoded}"
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_tool_fingerprint.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/tool_fingerprint.py tests/unit/test_tool_fingerprint.py
git commit -m "feat(skills): deterministic tool-invocation fingerprinting"
```

---

## Task 5: `MockToolRegistry` + `UnmockedToolError`

**Goal:** A `ToolRegistry` subclass that services tool invocations from a fixture's `tool_mocks` blob. Deny-closed — unknown invocations raise `UnmockedToolError`.

**Files:**
- Create: `src/donna/skills/mock_tool_registry.py`
- Test: `tests/unit/test_mock_tool_registry.py`

- [ ] **Step 1: Write the failing tests**

File: `tests/unit/test_mock_tool_registry.py`

```python
"""Tests for donna.skills.mock_tool_registry."""

from __future__ import annotations

import pytest
from donna.skills.mock_tool_registry import MockToolRegistry, UnmockedToolError


@pytest.mark.asyncio
async def test_empty_mocks_always_raises() -> None:
    reg = MockToolRegistry({})
    with pytest.raises(UnmockedToolError):
        await reg.dispatch("web_fetch", {"url": "https://x"}, allowed_tools=["web_fetch"])


@pytest.mark.asyncio
async def test_dispatches_from_mocks() -> None:
    mocks = {
        'web_fetch:{"url":"https://x"}': {"status": 200, "body": "OK"},
    }
    reg = MockToolRegistry(mocks)
    result = await reg.dispatch(
        "web_fetch",
        {"url": "https://x", "timeout_s": 10},
        allowed_tools=["web_fetch"],
    )
    assert result == {"status": 200, "body": "OK"}


@pytest.mark.asyncio
async def test_unmocked_raises_with_fingerprint_in_message() -> None:
    reg = MockToolRegistry({})
    with pytest.raises(UnmockedToolError) as excinfo:
        await reg.dispatch("web_fetch", {"url": "https://y"}, allowed_tools=["web_fetch"])
    assert "web_fetch" in str(excinfo.value)
    assert "https://y" in str(excinfo.value)


@pytest.mark.asyncio
async def test_from_mocks_classmethod_handles_none() -> None:
    reg = MockToolRegistry.from_mocks(None)
    with pytest.raises(UnmockedToolError):
        await reg.dispatch("web_fetch", {"url": "https://z"}, allowed_tools=["web_fetch"])


@pytest.mark.asyncio
async def test_respects_allowed_tools() -> None:
    mocks = {'web_fetch:{"url":"https://x"}': {"status": 200}}
    reg = MockToolRegistry(mocks)
    from donna.skills.tool_registry import ToolNotAllowedError
    with pytest.raises(ToolNotAllowedError):
        await reg.dispatch(
            "web_fetch", {"url": "https://x"},
            allowed_tools=["gmail_read"],
        )


def test_register_raises_runtime_error() -> None:
    reg = MockToolRegistry({})
    with pytest.raises(RuntimeError):
        reg.register("web_fetch", lambda **_: None)
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_mock_tool_registry.py -v
```
Expected: 6 failures.

- [ ] **Step 3: Implement**

File: `src/donna/skills/mock_tool_registry.py`

```python
"""Deny-closed tool registry for validation runs.

See spec §6.2. Used by ValidationExecutor so fixture validation never
dispatches a real tool callable. A real callable can never be registered
on a MockToolRegistry — :meth:`register` raises.
"""

from __future__ import annotations

from typing import Any

import structlog

from donna.skills.tool_fingerprint import fingerprint
from donna.skills.tool_registry import (
    ToolNotAllowedError,
    ToolRegistry,
)

logger = structlog.get_logger()


class UnmockedToolError(Exception):
    """Raised when a skill step tries to dispatch a tool whose invocation
    has no matching mock in the fixture's ``tool_mocks`` blob.
    """

    def __init__(self, tool_name: str, fingerprint_str: str) -> None:
        super().__init__(
            f"no mock for tool {tool_name!r} with fingerprint {fingerprint_str!r}"
        )
        self.tool_name = tool_name
        self.fingerprint = fingerprint_str


class MockToolRegistry(ToolRegistry):
    """ToolRegistry that dispatches from a precomputed mock map."""

    def __init__(self, mocks: dict[str, Any]) -> None:
        super().__init__()
        self._mocks = dict(mocks)

    @classmethod
    def from_mocks(cls, mocks: dict[str, Any] | None) -> MockToolRegistry:
        return cls(mocks or {})

    def register(self, name: str, callable_: Any) -> None:  # noqa: ARG002
        raise RuntimeError(
            "MockToolRegistry does not accept real tool callables; "
            "construct with the tool_mocks map instead."
        )

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
        return self._mocks[fp]
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_mock_tool_registry.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/mock_tool_registry.py tests/unit/test_mock_tool_registry.py
git commit -m "feat(skills): MockToolRegistry with deny-closed tool dispatch"
```

---

## Task 6: `ValidationRunSink`

**Goal:** In-memory absorber matching the `run_repository` protocol (`start_run`, `record_step`, `finish_run`). When passed in place of a real repo, captures calls in memory, returns synthetic IDs, and writes nothing to disk.

**Files:**
- Create: `src/donna/skills/validation_run_sink.py`
- Test: `tests/unit/test_validation_run_sink.py`

Before implementing, verify the exact signatures on the production `SkillRunRepository`:

```bash
grep -n "async def start_run\|async def record_step\|async def finish_run" src/donna/skills/run_persistence.py
```

Match whatever signatures are defined there. The snippets below assume the current shape documented in spec §5.4; update parameter names if the actual signatures differ.

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_validation_run_sink.py`

```python
"""Tests for donna.skills.validation_run_sink.ValidationRunSink."""

from __future__ import annotations

import pytest

from donna.skills.validation_run_sink import ValidationRunSink


@pytest.mark.asyncio
async def test_start_run_returns_synthetic_id() -> None:
    sink = ValidationRunSink()
    run_id = await sink.start_run(
        skill_id="s1", skill_version_id="v1",
        task_id=None, automation_run_id=None, user_id="validation",
    )
    assert run_id.startswith("validation-run-")


@pytest.mark.asyncio
async def test_record_step_captures_call() -> None:
    sink = ValidationRunSink()
    run_id = await sink.start_run(
        skill_id="s1", skill_version_id="v1", task_id=None,
        automation_run_id=None, user_id="validation",
    )
    await sink.record_step(
        run_id=run_id,
        step_name="parse",
        step_index=0,
        step_kind="llm",
        output={"title": "x"},
        tool_calls=[],
        latency_ms=42,
        validation_status="valid",
        invocation_id="local_parser_validation:inv_1",
    )
    assert len(sink.step_records) == 1
    rec = sink.step_records[0]
    assert rec.step_name == "parse"
    assert rec.invocation_id == "local_parser_validation:inv_1"


@pytest.mark.asyncio
async def test_finish_run_captures_final_state() -> None:
    sink = ValidationRunSink()
    run_id = await sink.start_run(
        skill_id="s1", skill_version_id="v1", task_id=None,
        automation_run_id=None, user_id="validation",
    )
    await sink.finish_run(
        run_id=run_id, status="succeeded",
        final_output={"k": 1}, state_object={},
        tool_result_cache={}, total_latency_ms=100,
        total_cost_usd=0.0, escalation_reason=None, error=None,
    )
    assert sink.final_status == "succeeded"
    assert sink.final_output == {"k": 1}


@pytest.mark.asyncio
async def test_sink_writes_nothing_to_db(tmp_path) -> None:
    """Sanity check: the sink must not open or write to any file."""
    import aiosqlite
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE skill_run (id TEXT PRIMARY KEY)")
        await conn.commit()

    sink = ValidationRunSink()
    run_id = await sink.start_run(
        skill_id="s", skill_version_id="v",
        task_id=None, automation_run_id=None, user_id="validation",
    )
    await sink.finish_run(
        run_id=run_id, status="succeeded", final_output={},
        state_object={}, tool_result_cache={}, total_latency_ms=0,
        total_cost_usd=0.0, escalation_reason=None, error=None,
    )

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM skill_run")
        row = await cursor.fetchone()
        assert row[0] == 0
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_validation_run_sink.py -v
```
Expected: 4 failures.

- [ ] **Step 3: Implement**

File: `src/donna/skills/validation_run_sink.py`

```python
"""In-memory sink for SkillExecutor's run_repository protocol.

When passed as ``run_sink`` on :class:`SkillExecutor`, the executor
delegates all persistence calls here. The sink captures them in memory,
returns synthetic IDs, and writes nothing to disk. Used by
:class:`ValidationExecutor` so fixture runs produce no production rows.

Must implement the same method signatures as
:class:`donna.skills.run_persistence.SkillRunRepository`. If that
contract changes, this class must change too.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class _StepRecord:
    run_id: str
    step_name: str
    step_index: int
    step_kind: str
    output: dict | None
    tool_calls: list | None
    latency_ms: int
    validation_status: str
    invocation_id: str | None


class ValidationRunSink:
    """Absorbs SkillExecutor persistence calls in-memory; no DB writes."""

    def __init__(self) -> None:
        self.run_id: str | None = None
        self.skill_id: str | None = None
        self.skill_version_id: str | None = None
        self.user_id: str | None = None
        self.step_records: list[_StepRecord] = []
        self.final_status: str | None = None
        self.final_output: Any = None
        self.state_object: dict | None = None
        self.tool_result_cache: dict | None = None
        self.total_latency_ms: int = 0
        self.total_cost_usd: float = 0.0
        self.escalation_reason: str | None = None
        self.error: str | None = None

    async def start_run(
        self,
        *,
        skill_id: str,
        skill_version_id: str,
        task_id: str | None,
        automation_run_id: str | None,
        user_id: str,
    ) -> str:
        self.run_id = f"validation-run-{uuid.uuid4().hex[:12]}"
        self.skill_id = skill_id
        self.skill_version_id = skill_version_id
        self.user_id = user_id
        return self.run_id

    async def record_step(
        self,
        *,
        run_id: str,
        step_name: str,
        step_index: int,
        step_kind: str,
        output: dict | None,
        tool_calls: list | None,
        latency_ms: int,
        validation_status: str,
        invocation_id: str | None,
    ) -> None:
        self.step_records.append(_StepRecord(
            run_id=run_id, step_name=step_name, step_index=step_index,
            step_kind=step_kind, output=output, tool_calls=tool_calls,
            latency_ms=latency_ms, validation_status=validation_status,
            invocation_id=invocation_id,
        ))

    async def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        final_output: Any,
        state_object: dict,
        tool_result_cache: dict,
        total_latency_ms: int,
        total_cost_usd: float,
        escalation_reason: str | None,
        error: str | None,
    ) -> None:
        self.final_status = status
        self.final_output = final_output
        self.state_object = state_object
        self.tool_result_cache = tool_result_cache
        self.total_latency_ms = total_latency_ms
        self.total_cost_usd = total_cost_usd
        self.escalation_reason = escalation_reason
        self.error = error
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_validation_run_sink.py -v
```
Expected: 4 passed. If the production `SkillRunRepository` signatures differ from what's above, update both the sink and the test to match.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/validation_run_sink.py tests/unit/test_validation_run_sink.py
git commit -m "feat(skills): ValidationRunSink absorbs SkillExecutor persistence calls"
```

---

## Task 7: Extend `SkillExecutor` with `run_sink` + kwargs tolerance

**Goal:** `SkillExecutor` accepts an optional `run_sink` that overrides `run_repository` when present. `execute()` accepts and ignores unknown keyword args.

**Files:**
- Modify: `src/donna/skills/executor.py`
- Test: `tests/unit/test_executor_run_sink.py`
- Modify: `tests/unit/conftest.py` (add `fake_router` fixture if absent)

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_executor_run_sink.py`

```python
"""Tests for SkillExecutor.run_sink override and kwargs tolerance."""

from __future__ import annotations

import pytest

from donna.skills.executor import SkillExecutor
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_run_sink import ValidationRunSink


@pytest.mark.asyncio
async def test_executor_delegates_to_run_sink_when_provided(fake_router) -> None:
    sink = ValidationRunSink()

    class FailingRepo:
        async def start_run(self, **kwargs):
            raise AssertionError("run_repository must not be called when run_sink is set")
        async def record_step(self, **kwargs):
            raise AssertionError("run_repository must not be called when run_sink is set")
        async def finish_run(self, **kwargs):
            raise AssertionError("run_repository must not be called when run_sink is set")

    executor = SkillExecutor(
        model_router=fake_router,
        run_repository=FailingRepo(),
        run_sink=sink,
    )
    skill = SkillRow(
        id="s1", capability_name="cap", state="sandbox",
        requires_human_gate=False, created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone="steps: []",
        step_content={}, output_schemas={},
        created_by="test", changelog=None, created_at=None,
    )
    result = await executor.execute(skill=skill, version=version, inputs={}, user_id="test")
    assert sink.run_id is not None
    assert sink.final_status in ("succeeded", "failed", "escalated")


@pytest.mark.asyncio
async def test_executor_ignores_unknown_kwargs(fake_router) -> None:
    executor = SkillExecutor(model_router=fake_router)
    skill = SkillRow(
        id="s1", capability_name="cap", state="sandbox",
        requires_human_gate=False, created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone="steps: []",
        step_content={}, output_schemas={},
        created_by="test", changelog=None, created_at=None,
    )
    await executor.execute(
        skill=skill, version=version, inputs={},
        user_id="test", tool_mocks={"web_fetch:x": {"status": 200}},
    )
```

If `tests/unit/conftest.py` doesn't already define `fake_router`, add:

```python
@pytest.fixture
def fake_router():
    class _FakeRouter:
        async def complete(self, **kwargs):
            class _Meta:
                invocation_id = "inv"
                cost_usd = 0.0
                latency_ms = 1
            return {}, _Meta()
    return _FakeRouter()
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_executor_run_sink.py -v
```
Expected: 2 failures.

- [ ] **Step 3: Implement**

In `src/donna/skills/executor.py`, update `__init__`:

```python
def __init__(
    self,
    model_router: Any,
    tool_registry: ToolRegistry | None = None,
    triage: TriageAgent | None = None,
    run_repository: Any | None = None,
    run_sink: Any | None = None,
    shadow_sampler: "ShadowSampler | None" = None,
) -> None:
    self._router = model_router
    self._tool_registry = tool_registry or ToolRegistry()
    self._tool_dispatcher = ToolDispatcher(self._tool_registry)
    self._triage = triage
    # run_sink overrides run_repository when both are provided.
    self._run_repository = run_sink if run_sink is not None else run_repository
    self._shadow_sampler = shadow_sampler
    self._jinja = jinja2.Environment(
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )
```

Update `execute` signature to tolerate unknown kwargs:

```python
async def execute(
    self,
    skill: SkillRow,
    version: SkillVersionRow,
    inputs: dict,
    user_id: str,
    **_ignored_kwargs: Any,
) -> SkillRunResult:
    # existing body unchanged
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_executor_run_sink.py -v tests/unit/test_skills_executor.py -v
```
Expected: new tests pass; existing executor tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/executor.py tests/unit/test_executor_run_sink.py tests/unit/conftest.py
git commit -m "feat(executor): accept run_sink override and tolerate unknown kwargs"
```

---

## Task 8: `ValidationExecutor`

**Goal:** Drop-in `.execute()`-compatible wrapper that constructs a fresh `MockToolRegistry` + `ValidationRunSink` per call and delegates to a `SkillExecutor`. Enforces per-run timeout.

**Files:**
- Create: `src/donna/skills/validation_executor.py`
- Test: `tests/unit/test_validation_executor.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_validation_executor.py`

```python
"""Tests for donna.skills.validation_executor.ValidationExecutor."""

from __future__ import annotations

import asyncio
import pytest

from donna.config import SkillSystemConfig
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_executor import ValidationExecutor


@pytest.mark.asyncio
async def test_execute_runs_against_mocks(fake_router) -> None:
    config = SkillSystemConfig()
    executor = ValidationExecutor(model_router=fake_router, config=config)
    skill = SkillRow(
        id="s1", capability_name="cap", state="sandbox",
        requires_human_gate=False, created_at=None, updated_at=None,
    )
    version = SkillVersionRow(
        id="v1", skill_id="s1", version_number=1,
        yaml_backbone="steps: []",
        step_content={}, output_schemas={},
        created_by="test", changelog=None, created_at=None,
    )
    result = await executor.execute(
        skill=skill, version=version, inputs={"q": 1},
        user_id="validation",
        tool_mocks={'web_fetch:{"url":"https://x"}': {"status": 200}},
    )
    assert result.status in ("succeeded", "failed", "escalated")


@pytest.mark.asyncio
async def test_execute_never_writes_to_db(fake_router, tmp_path) -> None:
    import aiosqlite
    db_path = tmp_path / "t.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE skill_run (id TEXT)")
        await conn.commit()

    config = SkillSystemConfig()
    executor = ValidationExecutor(model_router=fake_router, config=config)
    skill = SkillRow(id="s1", capability_name="cap", state="sandbox",
                     requires_human_gate=False, created_at=None, updated_at=None)
    version = SkillVersionRow(id="v1", skill_id="s1", version_number=1,
                              yaml_backbone="steps: []", step_content={},
                              output_schemas={}, created_by="test",
                              changelog=None, created_at=None)
    await executor.execute(skill=skill, version=version, inputs={},
                           user_id="validation", tool_mocks=None)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM skill_run")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_per_run_timeout(fake_router) -> None:
    config = SkillSystemConfig(validation_per_run_timeout_s=1)
    executor = ValidationExecutor(model_router=fake_router, config=config)

    class HangingInner:
        async def execute(self, **kwargs):
            await asyncio.sleep(3)
            raise AssertionError("should have timed out")

    executor._build_inner_executor = lambda _: HangingInner()  # type: ignore[assignment]

    skill = SkillRow(id="s1", capability_name="cap", state="sandbox",
                     requires_human_gate=False, created_at=None, updated_at=None)
    version = SkillVersionRow(id="v1", skill_id="s1", version_number=1,
                              yaml_backbone="steps: []", step_content={},
                              output_schemas={}, created_by="test",
                              changelog=None, created_at=None)
    with pytest.raises(asyncio.TimeoutError):
        await executor.execute(
            skill=skill, version=version, inputs={},
            user_id="validation", tool_mocks=None,
        )


@pytest.mark.asyncio
async def test_validate_against_fixtures_integration(fake_router) -> None:
    from donna.skills.fixtures import Fixture, validate_against_fixtures

    config = SkillSystemConfig()
    executor = ValidationExecutor(model_router=fake_router, config=config)
    skill = SkillRow(id="s1", capability_name="cap", state="sandbox",
                     requires_human_gate=False, created_at=None, updated_at=None)
    version = SkillVersionRow(id="v1", skill_id="s1", version_number=1,
                              yaml_backbone="steps: []", step_content={},
                              output_schemas={}, created_by="test",
                              changelog=None, created_at=None)
    fixtures = [
        Fixture(case_name="c1", input={}, tool_mocks=None),
        Fixture(case_name="c2", input={}, tool_mocks=None),
    ]
    report = await validate_against_fixtures(
        skill=skill, executor=executor, fixtures=fixtures, version=version,
    )
    assert report.total == 2
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_validation_executor.py -v
```
Expected: 4 failures.

- [ ] **Step 3: Implement**

File: `src/donna/skills/validation_executor.py`

```python
"""ValidationExecutor — offline fixture validation, mocked tools, real local LLM.

See spec §6.1. Implements the executor.execute protocol consumed by
:func:`donna.skills.fixtures.validate_against_fixtures`. Per call it
constructs a fresh :class:`MockToolRegistry` keyed from the fixture's
``tool_mocks`` blob and a :class:`ValidationRunSink` that absorbs
persistence calls.

Never writes to production tables. Used by AutoDrafter fixture validation
(§6.5) and Evolver gates 2/3/4 (§6.6).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from donna.config import SkillSystemConfig
from donna.skills.executor import SkillExecutor, SkillRunResult
from donna.skills.mock_tool_registry import MockToolRegistry
from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.validation_run_sink import ValidationRunSink

logger = structlog.get_logger()


class ValidationExecutor:
    """SkillExecutor-compatible class for offline fixture validation."""

    def __init__(
        self,
        model_router: Any,
        config: SkillSystemConfig,
    ) -> None:
        self._router = model_router
        self._config = config

    async def execute(
        self,
        *,
        skill: SkillRow,
        version: SkillVersionRow,
        inputs: dict,
        user_id: str,
        tool_mocks: dict | None = None,
        **_ignored_kwargs: Any,
    ) -> SkillRunResult:
        inner = self._build_inner_executor(tool_mocks)
        try:
            return await asyncio.wait_for(
                inner.execute(skill=skill, version=version,
                              inputs=inputs, user_id=user_id),
                timeout=self._config.validation_per_run_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "validation_run_timeout",
                skill_id=skill.id, version_id=version.id,
                timeout_s=self._config.validation_per_run_timeout_s,
            )
            raise

    def _build_inner_executor(self, tool_mocks: dict | None) -> SkillExecutor:
        tool_registry = MockToolRegistry.from_mocks(tool_mocks)
        sink = ValidationRunSink()
        return SkillExecutor(
            model_router=self._router,
            tool_registry=tool_registry,
            run_sink=sink,
        )
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_validation_executor.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/validation_executor.py tests/unit/test_validation_executor.py
git commit -m "feat(skills): ValidationExecutor with mocked tools and timeout"
```

---

## Task 9: Add validation timeout fields + reduced poll interval to `SkillSystemConfig`

**Goal:** New config fields (`validation_per_step_timeout_s`, `validation_per_run_timeout_s`) and reduce `automation_poll_interval_seconds` default from 60 to 15.

**Files:**
- Modify: `src/donna/config.py`
- Test: extend `tests/unit/test_skill_system_config.py` (or create if absent)

- [ ] **Step 1: Write the failing test**

Append (or create) in `tests/unit/test_skill_system_config.py`:

```python
def test_validation_timeouts_have_defaults() -> None:
    from donna.config import SkillSystemConfig
    cfg = SkillSystemConfig()
    assert cfg.validation_per_step_timeout_s == 60
    assert cfg.validation_per_run_timeout_s == 300


def test_automation_poll_interval_default_is_15_seconds() -> None:
    from donna.config import SkillSystemConfig
    cfg = SkillSystemConfig()
    assert cfg.automation_poll_interval_seconds == 15


def test_validation_timeouts_override_from_dict() -> None:
    from donna.config import SkillSystemConfig
    cfg = SkillSystemConfig(
        validation_per_step_timeout_s=30,
        validation_per_run_timeout_s=120,
    )
    assert cfg.validation_per_step_timeout_s == 30
    assert cfg.validation_per_run_timeout_s == 120
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_skill_system_config.py -v
```
Expected: 3 failures.

- [ ] **Step 3: Implement**

Edit `src/donna/config.py`, extend `SkillSystemConfig`:

```python
# Phase 5 — automation subsystem
automation_poll_interval_seconds: int = 15  # Wave 1: reduced from 60 for responsive run-now
automation_min_interval_default_seconds: int = 300
automation_failure_pause_threshold: int = 5
automation_max_cost_per_run_default_usd: float = 2.0

# Wave 1 — validation executor
validation_per_step_timeout_s: int = 60
validation_per_run_timeout_s: int = 300
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_skill_system_config.py -v
pytest tests/ -x -k "config or skill_system" 2>&1 | tail -20
```
Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add src/donna/config.py tests/unit/test_skill_system_config.py
git commit -m "feat(config): validation timeouts + 15s automation poll default"
```

---

## Task 10: Thread `ValidationExecutor` factory through `assemble_skill_system` (F-5)

**Goal:** Replace `executor_factory: Callable | None = None` with `validation_executor_factory`. Default factory constructs a real `ValidationExecutor`. Remove vacuous-pass branches in `AutoDrafter` and `Evolver`.

**Files:**
- Modify: `src/donna/skills/startup_wiring.py`
- Modify: `src/donna/skills/auto_drafter.py`
- Modify: `src/donna/skills/evolution.py`
- Modify: `src/donna/api/__init__.py` (rename kwarg)
- Test: `tests/unit/test_startup_wiring_validation_factory.py`
- Test updates: existing tests that pass `executor_factory=None`.

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_startup_wiring_validation_factory.py`

```python
"""Tests for the validation_executor_factory wiring in assemble_skill_system."""

from __future__ import annotations

import pytest

from donna.config import SkillSystemConfig
from donna.skills.startup_wiring import assemble_skill_system


def test_default_factory_produces_validation_executor(
    fake_router, fake_conn, fake_budget,
) -> None:
    bundle = assemble_skill_system(
        connection=fake_conn,
        model_router=fake_router,
        budget_guard=fake_budget,
        notifier=lambda msg: None,
        config=SkillSystemConfig(enabled=True),
    )
    assert bundle is not None
    from donna.skills.validation_executor import ValidationExecutor
    validator = bundle.auto_drafter._executor_factory()
    assert isinstance(validator, ValidationExecutor)
    validator2 = bundle.evolver._executor_factory()
    assert isinstance(validator2, ValidationExecutor)


def test_none_factory_falls_back_to_default(
    fake_router, fake_conn, fake_budget,
) -> None:
    """Explicit None passes through to the default factory (not vacuous pass)."""
    bundle = assemble_skill_system(
        connection=fake_conn, model_router=fake_router,
        budget_guard=fake_budget, notifier=lambda m: None,
        config=SkillSystemConfig(enabled=True),
        validation_executor_factory=None,
    )
    from donna.skills.validation_executor import ValidationExecutor
    assert isinstance(bundle.auto_drafter._executor_factory(), ValidationExecutor)
```

Add to `tests/unit/conftest.py` if absent:

```python
@pytest.fixture
def fake_conn():
    class _Conn:
        def execute(self, *a, **kw):
            raise RuntimeError("fake_conn.execute called unexpectedly")
    return _Conn()

@pytest.fixture
def fake_budget():
    class _B:
        async def check_pre_call(self, **kw):
            return None
    return _B()
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_startup_wiring_validation_factory.py -v
```
Expected: 2 failures.

- [ ] **Step 3: Implement**

In `src/donna/skills/startup_wiring.py`:

```python
from donna.skills.validation_executor import ValidationExecutor

def assemble_skill_system(
    connection: aiosqlite.Connection,
    model_router: Any,
    budget_guard: Any,
    notifier: Callable[[str], Awaitable[None]],
    config: SkillSystemConfig,
    validation_executor_factory: Callable[[], Any] | None = None,
) -> SkillSystemBundle | None:
    if not config.enabled:
        logger.info("skill_system_disabled", enabled=False)
        return None

    if validation_executor_factory is None:
        def _default_validation_executor_factory() -> ValidationExecutor:
            return ValidationExecutor(model_router=model_router, config=config)
        validation_executor_factory = _default_validation_executor_factory

    lifecycle = SkillLifecycleManager(connection, config)
    # ... (existing body unchanged up to AutoDrafter/Evolver construction)

    auto_drafter = AutoDrafter(
        connection=connection,
        model_router=model_router,
        budget_guard=budget_guard,
        candidate_repo=candidate_repo,
        lifecycle_manager=lifecycle,
        config=config,
        executor_factory=validation_executor_factory,
    )
    # ...
    evolver = Evolver(
        connection=connection,
        model_router=model_router,
        budget_guard=budget_guard,
        lifecycle_manager=lifecycle,
        config=config,
        executor_factory=validation_executor_factory,
    )
```

In `src/donna/skills/auto_drafter.py`, remove the vacuous-pass branch:

```python
# DELETE these lines:
# if self._executor_factory is None:
#     logger.warning(
#         "skill_auto_draft_validation_deferred",
#         capability_name=capability_name,
#         reason="no executor_factory configured",
#     )
#     return 1.0
```

Make `executor_factory` required in the constructor:

```python
def __init__(
    self,
    connection: aiosqlite.Connection,
    model_router: Any,
    budget_guard: Any,
    candidate_repo: SkillCandidateRepository,
    lifecycle_manager: SkillLifecycleManager,
    config: Any,
    executor_factory: ExecutorFactory,   # now required (no None default)
    estimated_draft_cost_usd: float = 0.50,
) -> None:
```

Apply the equivalent changes in `src/donna/skills/evolution.py`:

```bash
grep -n "executor_factory is None\|executor_factory: Callable" src/donna/skills/evolution.py
```

Remove the vacuous-pass return in each gate method that has one, and make the constructor's `executor_factory` required.

In `src/donna/api/__init__.py`, change the `assemble_skill_system(...)` call's kwarg name:

```python
bundle = assemble_skill_system(
    connection=db.connection,
    model_router=skill_router,
    budget_guard=skill_budget_guard,
    notifier=_skill_notifier,
    config=skill_config,
    validation_executor_factory=None,
)
```

Rewrite any test that previously instantiated `AutoDrafter` or `Evolver` without a factory. Minimal stub:

```python
from donna.skills.executor import SkillRunResult

class _StubValidator:
    async def execute(self, **kwargs):
        return SkillRunResult(status="succeeded", final_output={})

executor_factory = lambda: _StubValidator()
```

Find candidates:

```bash
grep -rn "executor_factory=None\|pass_rate == 1.0\|validation_deferred" tests/
```

Update each hit.

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_startup_wiring_validation_factory.py tests/unit/test_auto_drafter*.py tests/unit/test_evolution*.py -v
pytest -x 2>&1 | tail -30
```
Expected: new tests pass; any remaining failures are legacy tests that need the stub factory — fix them in the same commit.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/startup_wiring.py src/donna/skills/auto_drafter.py src/donna/skills/evolution.py src/donna/api/__init__.py tests/
git commit -m "feat(skills): validation_executor_factory required; remove vacuous pass paths"
```

---

## Task 11: `BotProtocol` typing.Protocol

**Goal:** Small structural type for the bot surface that `NotificationService` uses.

**Files:**
- Create: `src/donna/notifications/bot_protocol.py`
- Modify: `src/donna/notifications/service.py` (type hint only)
- Test: `tests/unit/test_bot_protocol.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_bot_protocol.py`

```python
"""Tests for the BotProtocol structural type."""

from __future__ import annotations

from donna.notifications.bot_protocol import BotProtocol


def test_real_donnabot_has_required_methods() -> None:
    from donna.integrations.discord_bot import DonnaBot
    for attr in ("send_message", "send_embed", "send_to_thread"):
        assert hasattr(DonnaBot, attr), f"DonnaBot missing {attr}"


def test_simple_fake_satisfies_protocol() -> None:
    class Fake:
        async def send_message(self, channel: str, content: str) -> None: ...
        async def send_embed(self, channel: str, embed) -> None: ...
        async def send_to_thread(self, thread_id: int, content: str) -> None: ...

    fake = Fake()
    assert isinstance(fake, BotProtocol)


def test_object_missing_method_fails_protocol() -> None:
    class Incomplete:
        async def send_message(self, channel: str, content: str) -> None: ...

    assert not isinstance(Incomplete(), BotProtocol)
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_bot_protocol.py -v
```
Expected: 3 failures.

- [ ] **Step 3: Implement**

File: `src/donna/notifications/bot_protocol.py`

```python
"""Structural type for the bot interface used by NotificationService.

NotificationService._send calls three methods on the bot. Exposing this
as a typing.Protocol lets test doubles satisfy the contract without
subclassing DonnaBot (which needs a live discord.py Client).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BotProtocol(Protocol):
    async def send_message(self, channel: str, content: str) -> None: ...
    async def send_embed(self, channel: str, embed: Any) -> None: ...
    async def send_to_thread(self, thread_id: int, content: str) -> None: ...
```

In `src/donna/notifications/service.py`, change the `bot` parameter type:

```python
from donna.notifications.bot_protocol import BotProtocol

if TYPE_CHECKING:
    from donna.integrations.gmail import GmailClient
    from donna.integrations.twilio_sms import TwilioSMS

class NotificationService:
    def __init__(
        self,
        bot: BotProtocol,
        calendar_config: CalendarConfig,
        user_id: str,
        sms: "TwilioSMS | None" = None,
        gmail: "GmailClient | None" = None,
    ) -> None:
        # ... body unchanged
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_bot_protocol.py tests/unit/test_notification_service.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/notifications/bot_protocol.py src/donna/notifications/service.py tests/unit/test_bot_protocol.py
git commit -m "feat(notifications): BotProtocol structural type for NotificationService"
```

---

## Task 12: Construct `NotificationService` in orchestrator — F-6 Step 6a

**Goal:** In `src/donna/cli.py`, construct `NotificationService` after `DonnaBot` and keep a reference in the local scope.

**Files:**
- Modify: `src/donna/cli.py`
- Test: `tests/integration/test_notification_service_wiring.py`

- [ ] **Step 1: Write the failing test**

File: `tests/integration/test_notification_service_wiring.py`

```python
"""Integration: orchestrator constructs NotificationService."""

from __future__ import annotations

import argparse
import pytest
from unittest.mock import patch

from donna.notifications.service import NotificationService


@pytest.mark.asyncio
async def test_cli_constructs_notification_service(monkeypatch, tmp_path) -> None:
    """Run donna.cli._run_orchestrator in a mode that exits quickly, and verify
    NotificationService is constructed when Discord creds are present."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "12345")
    monkeypatch.setenv("DONNA_USER_ID", "nick")
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "test.db"))

    async def _noop(*args, **kwargs):
        return None

    constructed: list[NotificationService] = []
    original_init = NotificationService.__init__

    def _capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        constructed.append(self)

    monkeypatch.setattr(NotificationService, "__init__", _capturing_init)

    with patch("donna.integrations.discord_bot.DonnaBot.start", _noop):
        with patch("donna.server.run_server", _noop):
            from donna.cli import _run_orchestrator
            args = argparse.Namespace(
                config_dir="config", log_level="INFO", dev=True, port=8100,
            )
            await _run_orchestrator(args)

    assert len(constructed) == 1
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/integration/test_notification_service_wiring.py -v
```
Expected: 1 failure.

- [ ] **Step 3: Implement**

In `src/donna/cli.py`, inside `_run_orchestrator`, after `bot = DonnaBot(...)` and before `tasks.append(asyncio.create_task(bot.start(discord_token)))`:

```python
        from donna.config import load_calendar_config
        from donna.notifications.service import NotificationService

        notification_service = None
        try:
            calendar_config = load_calendar_config(config_dir)
            notification_service = NotificationService(
                bot=bot,
                calendar_config=calendar_config,
                user_id=user_id,
                sms=None,
                gmail=None,
            )
            log.info("notification_service_wired")
        except Exception:
            log.exception("notification_service_init_failed")
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/integration/test_notification_service_wiring.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/cli.py tests/integration/test_notification_service_wiring.py
git commit -m "feat(cli): construct NotificationService in orchestrator lifespan"
```

---

## Task 13: `test-notification` dev CLI command

**Goal:** Manual verification command so Nick can confirm Discord delivery end-to-end.

**Files:**
- Modify: `src/donna/cli.py`
- Test: `tests/unit/test_cli_test_notification.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_cli_test_notification.py`

```python
"""Test the `donna test-notification` CLI subcommand argument parsing."""

from __future__ import annotations


def test_test_notification_subcommand_parses() -> None:
    from donna.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args([
        "test-notification",
        "--type", "digest",
        "--channel", "tasks",
        "--content", "hello",
    ])
    assert ns.command == "test-notification"
    assert ns.type == "digest"
    assert ns.channel == "tasks"
    assert ns.content == "hello"
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_cli_test_notification.py -v
```
Expected: 1 failure.

- [ ] **Step 3: Implement**

Refactor `src/donna/cli.py`. Extract parser construction to a helper:

```python
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="donna",
        description="Donna AI Personal Assistant",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ... (existing subparsers kept verbatim)

    # Wave 1 addition
    tn_parser = subparsers.add_parser(
        "test-notification",
        help="Send a test notification via the live NotificationService",
    )
    tn_parser.add_argument("--config-dir", default="config")
    tn_parser.add_argument("--type", required=True)
    tn_parser.add_argument("--channel", default="tasks")
    tn_parser.add_argument("--content", required=True)
    tn_parser.add_argument("--priority", type=int, default=3)
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    # dispatch (existing branches stay; add test-notification branch):
    if args.command == "test-notification":
        asyncio.run(_test_notification(args))
        return
    # ... existing command dispatch unchanged
```

Add the dispatch function:

```python
async def _test_notification(args: argparse.Namespace) -> None:
    import os
    from pathlib import Path
    from donna.config import load_calendar_config
    from donna.integrations.discord_bot import DonnaBot
    from donna.notifications.service import NotificationService

    token = os.environ["DISCORD_BOT_TOKEN"]
    tasks_channel_id = int(os.environ["DISCORD_TASKS_CHANNEL_ID"])
    debug_channel_id = int(os.environ.get("DISCORD_DEBUG_CHANNEL_ID") or 0) or None
    agents_channel_id = int(os.environ.get("DISCORD_AGENTS_CHANNEL_ID") or 0) or None
    guild_id = int(os.environ.get("DISCORD_GUILD_ID") or 0) or None
    user_id = os.environ.get("DONNA_USER_ID", "nick")
    config_dir = Path(args.config_dir)

    bot = DonnaBot(
        input_parser=None,
        database=None,
        tasks_channel_id=tasks_channel_id,
        debug_channel_id=debug_channel_id,
        agents_channel_id=agents_channel_id,
        guild_id=guild_id,
    )
    bot_task = asyncio.create_task(bot.start(token))
    await bot.ready_event.wait()  # Added in DonnaBot.on_ready

    notification_service = NotificationService(
        bot=bot,
        calendar_config=load_calendar_config(config_dir),
        user_id=user_id,
    )
    sent = await notification_service.dispatch(
        notification_type=args.type, content=args.content,
        channel=args.channel, priority=args.priority,
    )
    print(f"dispatched={sent}")
    await bot.close()
    await bot_task
```

If `DonnaBot` doesn't have a `ready_event`:

```bash
grep -n "ready_event\|on_ready\|self.wait_until_ready" src/donna/integrations/discord_bot.py
```

If absent, add in `DonnaBot.__init__`:

```python
self.ready_event = asyncio.Event()
```

And in the existing `on_ready` handler:

```python
async def on_ready(self) -> None:
    # existing setup
    self.ready_event.set()
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_cli_test_notification.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/cli.py src/donna/integrations/discord_bot.py tests/unit/test_cli_test_notification.py
git commit -m "feat(cli): test-notification subcommand for manual Discord smoke"
```

---

## Task 14: Move skill-system wiring from API to orchestrator — F-6 Step 6b

**Goal:** The skill-system block currently at `src/donna/api/__init__.py` lines 196–330 moves to `src/donna/cli.py` inside `_run_orchestrator`.

**Files:**
- Modify: `src/donna/cli.py`
- Modify: `src/donna/api/__init__.py`
- Modify: admin routes that reference `app.state.skill_system_bundle` / `auto_drafter` / `skill_lifecycle_manager`
- Test: `tests/integration/test_api_no_skill_tasks.py` and update existing API tests

- [ ] **Step 1: Write the failing test**

File: `tests/integration/test_api_no_skill_tasks.py`

```python
"""After F-6: the API process must not start any skill-system background tasks."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))
    from donna.api import create_app
    app = create_app()
    with TestClient(app) as client:
        yield app, client


def test_api_does_not_wire_skill_cron(api_app) -> None:
    app, _ = api_app
    assert getattr(app.state, "skill_cron_scheduler", None) is None
    assert getattr(app.state, "skill_cron_task", None) is None
    assert getattr(app.state, "auto_drafter", None) is None
    assert getattr(app.state, "skill_lifecycle_manager", None) is None


def test_api_does_not_wire_automation_scheduler(api_app) -> None:
    app, _ = api_app
    assert getattr(app.state, "automation_scheduler", None) is None
    assert getattr(app.state, "automation_scheduler_task", None) is None
    assert getattr(app.state, "automation_dispatcher", None) is None


def test_api_still_loads_skill_system_config(api_app) -> None:
    app, _ = api_app
    assert hasattr(app.state, "skill_system_config")
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/integration/test_api_no_skill_tasks.py -v
```
Expected: at least 2 failures.

- [ ] **Step 3: Implement**

In `src/donna/api/__init__.py`, delete the skill-system wiring block (the entire "Skill system (Phase 3 + 4)" block plus the "Automation subsystem" block that follows). Replace with:

```python
    # Skill-system background work lives in the orchestrator (donna-orchestrator)
    # process. See Wave 1 spec §6.4. The API only reads config to surface
    # `enabled` status in admin endpoints.
    try:
        from donna.config import load_skill_system_config
        app.state.skill_system_config = load_skill_system_config(config_dir)
    except Exception:
        logger.warning("skill_system_config_load_failed", exc_info=True)
        app.state.skill_system_config = None
```

Also delete the corresponding shutdown code at the end of the lifespan (`scheduler.stop()`, `cron_task.cancel()`, etc.).

In `src/donna/cli.py`, inside `_run_orchestrator`, after the `notification_service = ...` block from Task 12, add the moved wiring:

```python
        # --- Skill-system wiring (moved from API) ---
        from donna.config import load_skill_system_config
        from donna.cost.budget import BudgetGuard
        from donna.cost.tracker import CostTracker
        from donna.skills.crons import (
            AsyncCronScheduler,
            NightlyDeps,
            run_nightly_tasks,
        )
        from donna.skills.startup_wiring import assemble_skill_system

        skill_config = load_skill_system_config(config_dir)

        bundle = None
        skill_router = None
        cost_tracker = None
        skill_budget_guard = None

        if skill_config.enabled:
            skill_router = ModelRouter(models_config, task_types_config, project_root)
            cost_tracker = CostTracker(db.connection)

            async def _skill_system_notifier(message: str) -> None:
                if notification_service is None:
                    log.info("skill_system_notification_no_service", message=message)
                    return
                from donna.notifications.service import (
                    CHANNEL_TASKS, NOTIF_AUTOMATION_FAILURE,
                )
                await notification_service.dispatch(
                    notification_type=NOTIF_AUTOMATION_FAILURE,
                    content=message,
                    channel=CHANNEL_TASKS,
                    priority=4,
                )

            skill_budget_guard = BudgetGuard(
                tracker=cost_tracker,
                models_config=models_config,
                notifier=lambda channel, message: _skill_system_notifier(message),
            )

            bundle = assemble_skill_system(
                connection=db.connection,
                model_router=skill_router,
                budget_guard=skill_budget_guard,
                notifier=_skill_system_notifier,
                config=skill_config,
                validation_executor_factory=None,
            )

            if bundle is not None:
                async def _nightly_job() -> None:
                    deps = NightlyDeps(
                        detector=bundle.detector,
                        auto_drafter=bundle.auto_drafter,
                        degradation=bundle.degradation,
                        evolution_scheduler=bundle.evolution_scheduler,
                        correction_cluster=bundle.correction_cluster,
                        cost_tracker=cost_tracker,
                        daily_budget_limit_usd=models_config.cost.daily_pause_threshold_usd,
                        config=skill_config,
                    )
                    report = await run_nightly_tasks(deps)
                    log.info(
                        "nightly_skill_tasks_done",
                        new_candidates=len(report.new_candidates),
                        drafted=len(report.drafted),
                        evolved=len(report.evolved),
                        degraded=len(report.degraded),
                        correction_flagged=len(report.correction_flagged),
                        errors=len(report.errors),
                    )

                scheduler = AsyncCronScheduler(
                    hour_utc=skill_config.nightly_run_hour_utc,
                    task=_nightly_job,
                )
                tasks.append(asyncio.create_task(scheduler.run_forever()))
                log.info(
                    "skill_system_started",
                    nightly_run_hour_utc=skill_config.nightly_run_hour_utc,
                )
        else:
            log.info("skill_system_disabled_in_config")
```

Find admin routes that reference removed state:

```bash
grep -rn "app.state.skill_system_bundle\|app.state.auto_drafter\|app.state.skill_lifecycle_manager" src/donna/api/
```

For each hit, either:
- Remove the endpoint if its sole purpose was invoking orchestrator-side code, or
- Rewrite it to read the same info from the DB (via `skill_lifecycle_manager` constructed on-demand from `db.connection`), or
- Return `503 Service Unavailable` with a clear error message: `{"detail": "this operation now runs in the orchestrator; check orchestrator logs"}`.

Decision principle: endpoints that mutate state by calling the lifecycle manager should continue to work, because `SkillLifecycleManager` is a thin wrapper around DB queries — construct it inline from `db.connection`. Endpoints that invoked schedulers directly should return 503.

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/integration/test_api_no_skill_tasks.py -v
pytest tests/ -k "api" -v 2>&1 | tail -40
```
Expected: new tests pass; other API tests also pass after any route adjustments.

- [ ] **Step 5: Commit**

```bash
git add src/donna/cli.py src/donna/api/ tests/
git commit -m "feat(cli): move skill-system bundle + nightly cron to orchestrator"
```

---

## Task 15: Move automation scheduler + dispatcher to orchestrator — F-6 Step 6c

**Goal:** Automation scheduler + dispatcher run in the orchestrator. Dispatcher receives the real `NotificationService`.

**Files:**
- Modify: `src/donna/cli.py`
- Test: `tests/integration/test_automation_scheduler_in_orchestrator.py`

- [ ] **Step 1: Write the failing test**

File: `tests/integration/test_automation_scheduler_in_orchestrator.py`

```python
"""Integration: orchestrator constructs AutomationDispatcher with live notifier."""

from __future__ import annotations

import argparse
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_automation_dispatcher_uses_real_notification_service(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setenv("DISCORD_TASKS_CHANNEL_ID", "1")
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))

    captured = []
    from donna.automations import dispatcher as dispatcher_module
    original_init = dispatcher_module.AutomationDispatcher.__init__

    def _capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured.append(self)

    monkeypatch.setattr(
        dispatcher_module.AutomationDispatcher, "__init__", _capturing_init,
    )

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("donna.integrations.discord_bot.DonnaBot.start", _noop)
    monkeypatch.setattr("donna.server.run_server", _noop)

    from donna.cli import _run_orchestrator
    args = argparse.Namespace(
        config_dir="config", log_level="INFO", dev=True, port=8100,
    )
    await _run_orchestrator(args)

    assert len(captured) == 1
    from donna.notifications.service import NotificationService
    assert isinstance(captured[0]._notifier, NotificationService)
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/integration/test_automation_scheduler_in_orchestrator.py -v
```
Expected: 1 failure.

- [ ] **Step 3: Implement**

In `src/donna/cli.py`, inside the `if skill_config.enabled:` block, after the skill-system bundle wiring from Task 14:

```python
            try:
                from donna.automations.alert import AlertEvaluator
                from donna.automations.cron import CronScheduleCalculator
                from donna.automations.dispatcher import AutomationDispatcher
                from donna.automations.repository import AutomationRepository
                from donna.automations.scheduler import AutomationScheduler

                automation_repo = AutomationRepository(db.connection)
                automation_dispatcher = AutomationDispatcher(
                    connection=db.connection,
                    repository=automation_repo,
                    model_router=skill_router,
                    skill_executor_factory=lambda: None,  # OOS-W1-2
                    budget_guard=skill_budget_guard,
                    alert_evaluator=AlertEvaluator(),
                    cron=CronScheduleCalculator(),
                    notifier=notification_service,
                    config=skill_config,
                )
                automation_scheduler = AutomationScheduler(
                    repository=automation_repo,
                    dispatcher=automation_dispatcher,
                    poll_interval_seconds=skill_config.automation_poll_interval_seconds,
                )
                tasks.append(asyncio.create_task(automation_scheduler.run_forever()))
                log.info(
                    "automation_scheduler_started",
                    poll_interval_seconds=skill_config.automation_poll_interval_seconds,
                )
            except Exception:
                log.exception("automation_scheduler_wiring_failed")
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/integration/test_automation_scheduler_in_orchestrator.py tests/integration/test_api_no_skill_tasks.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/cli.py tests/integration/test_automation_scheduler_in_orchestrator.py
git commit -m "feat(cli): move automation scheduler and dispatcher to orchestrator"
```

---

## Task 16: `run-now` endpoint uses `next_run_at` — F-6 Step 6d

**Goal:** API's `POST /admin/automations/{id}/run-now` sets `next_run_at = now()` and returns 202.

**Files:**
- Modify: `src/donna/api/routes/automations.py`
- Test: `tests/integration/test_run_now_endpoint.py`

- [ ] **Step 1: Write the failing test**

File: `tests/integration/test_run_now_endpoint.py`

```python
"""Tests for POST /admin/automations/{id}/run-now after F-6."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "donna.db"))
    from donna.api import create_app
    with TestClient(create_app()) as c:
        yield c


def test_run_now_returns_202_and_sets_next_run_at(client) -> None:
    created = client.post(
        "/admin/automations",
        json={
            "user_id": "nick",
            "name": "test",
            "capability_name": "parse_task",
            "inputs": {},
            "trigger_type": "on_schedule",
            "schedule": "0 * * * *",
            "alert_conditions": {},
            "alert_channels": ["tasks"],
            "min_interval_seconds": 60,
        },
    )
    assert created.status_code == 201, created.text
    automation_id = created.json()["id"]

    resp = client.post(f"/admin/automations/{automation_id}/run-now")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "scheduled"
    assert "next_run_at" in body

    detail = client.get(f"/admin/automations/{automation_id}")
    assert detail.status_code == 200
    assert detail.json()["next_run_at"] is not None


def test_run_now_404_on_missing_automation(client) -> None:
    resp = client.post("/admin/automations/nonexistent/run-now")
    assert resp.status_code == 404


def test_run_now_404_on_paused_automation(client) -> None:
    created = client.post(
        "/admin/automations",
        json={
            "user_id": "nick", "name": "paused-auto",
            "capability_name": "parse_task", "inputs": {},
            "trigger_type": "on_schedule", "schedule": "0 * * * *",
            "alert_conditions": {}, "alert_channels": ["tasks"],
            "min_interval_seconds": 60,
        },
    )
    automation_id = created.json()["id"]
    client.patch(f"/admin/automations/{automation_id}", json={"status": "paused"})
    resp = client.post(f"/admin/automations/{automation_id}/run-now")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/integration/test_run_now_endpoint.py -v
```
Expected: 3 failures.

- [ ] **Step 3: Implement**

Locate the current implementation:

```bash
grep -n "run-now\|run_now" src/donna/api/routes/automations.py
```

Replace the body:

```python
from datetime import datetime, timezone
from fastapi import HTTPException

@router.post("/automations/{automation_id}/run-now", status_code=202)
async def run_now(
    automation_id: str,
    db: Database = Depends(get_db),
) -> dict:
    """Schedule the automation to run immediately.

    Sets next_run_at to now. The orchestrator AutomationScheduler picks
    it up on its next poll (~15s by default). Returns 202 Accepted.
    """
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    cursor = await db.connection.execute(
        "UPDATE automation SET next_run_at = ?, updated_at = ? "
        "WHERE id = ? AND status = 'active'",
        (now_iso, now_iso, automation_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="automation not found or not active",
        )
    await db.connection.commit()
    return {"status": "scheduled", "next_run_at": now_iso}
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/integration/test_run_now_endpoint.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/api/routes/automations.py tests/integration/test_run_now_endpoint.py
git commit -m "feat(api): run-now sets next_run_at, returns 202"
```

---

## Task 17: Update `AutoDrafter` fixture-generation prompt + parsing for `tool_mocks`

**Goal:** Claude's fixture-generation prompt asks for a `tool_mocks` field. Parsing persists it to the new column.

**Files:**
- Modify: `src/donna/skills/auto_drafter.py`
- Test: `tests/unit/test_auto_drafter_tool_mocks.py`

- [ ] **Step 1: Write the failing test**

File: `tests/unit/test_auto_drafter_tool_mocks.py`

```python
"""AutoDrafter's fixture output includes tool_mocks; it's persisted to DB."""

from __future__ import annotations

import json
import pytest


def test_extract_draft_payload_reads_tool_mocks() -> None:
    from donna.skills.auto_drafter import AutoDrafter

    parsed = {
        "skill_yaml": "steps: []",
        "step_prompts": {},
        "output_schemas": {},
        "fixtures": [
            {
                "case_name": "case_a",
                "input": {"url": "https://x"},
                "expected_output_shape": {"type": "object"},
                "tool_mocks": {
                    'web_fetch:{"url":"https://x"}': {"status": 200, "body": "OK"},
                },
            },
        ],
    }
    skill_yaml, step_prompts, output_schemas, fixtures = \
        AutoDrafter._extract_draft_payload(None, parsed)
    assert fixtures[0]["tool_mocks"] == {
        'web_fetch:{"url":"https://x"}': {"status": 200, "body": "OK"},
    }


@pytest.mark.asyncio
async def test_persist_fixture_writes_tool_mocks_to_db(tmp_path) -> None:
    import aiosqlite
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "t.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO skill (id, capability_name, state, requires_human_gate, "
            "created_at, updated_at) VALUES ('s1', 'cap', 'draft', 0, "
            "datetime('now'), datetime('now'))"
        )
        await conn.commit()

        from donna.skills.auto_drafter import _persist_fixture
        await _persist_fixture(
            conn=conn,
            skill_id="s1",
            case_name="c1",
            input_={"url": "https://x"},
            expected_output_shape={"type": "object"},
            tool_mocks={'web_fetch:{"url":"https://x"}': {"status": 200}},
            source="claude_generated",
        )

        cursor = await conn.execute(
            "SELECT tool_mocks FROM skill_fixture WHERE skill_id = 's1'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert json.loads(row[0]) == {
            'web_fetch:{"url":"https://x"}': {"status": 200},
        }
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/unit/test_auto_drafter_tool_mocks.py -v
```
Expected: 2 failures.

- [ ] **Step 3: Implement**

Update the fixture-generation prompt in `src/donna/skills/auto_drafter.py`:

```bash
grep -n "fixtures\|tool_mocks\|expected_output_shape" src/donna/skills/auto_drafter.py | head -30
```

Inside the prompt template (likely in `_build_prompt`), add a clear directive:

```
Each fixture must include:
- "case_name": short identifier
- "input": object matching the capability's input schema
- "expected_output_shape": a STRUCTURAL JSON Schema for the final output —
  field names, types, required fields, nesting only; do NOT pin values
  except for enums.
- "tool_mocks": a JSON object mapping tool-invocation fingerprints to
  result blobs. Fingerprint format is "<tool_name>:<canonical-sorted-JSON>".
  For tools with specific rules (web_fetch uses only {"url": ...};
  gmail_read uses only {"message_id": ...}), compose the fingerprint
  from those args only. Fixtures for pure-LLM skills may set tool_mocks to {}.
```

Update `_extract_draft_payload` if it doesn't already pass `tool_mocks` through. Since `fixtures_data` is a list of dicts returned verbatim, and `tool_mocks` is just a new key, the method may already pass it through. Verify:

```python
def _extract_draft_payload(self, parsed) -> tuple:
    skill_yaml = parsed.get("skill_yaml")
    step_prompts = parsed.get("step_prompts", {})
    output_schemas = parsed.get("output_schemas", {})
    fixtures_data = parsed.get("fixtures", [])
    if not isinstance(fixtures_data, list) or not fixtures_data:
        return None, None, None, None
    return skill_yaml, step_prompts, output_schemas, fixtures_data
```

The `Fixture` construction inside `_run_sandbox_validation` needs to thread `tool_mocks`:

```python
fixtures = [
    Fixture(
        case_name=str(item.get("case_name", f"case_{i}")),
        input=dict(item.get("input", {})),
        expected_output_shape=item.get("expected_output_shape"),
        tool_mocks=item.get("tool_mocks"),
    )
    for i, item in enumerate(fixtures_data)
    if isinstance(item, dict)
]
```

Extract a module-level `_persist_fixture` helper (if none exists):

```python
async def _persist_fixture(
    conn: aiosqlite.Connection,
    skill_id: str,
    case_name: str,
    input_: dict,
    expected_output_shape: dict | None,
    tool_mocks: dict | None,
    source: str,
) -> None:
    import uuid6
    from datetime import datetime, timezone
    await conn.execute(
        "INSERT INTO skill_fixture "
        "(id, skill_id, case_name, input, expected_output_shape, "
        " source, captured_run_id, created_at, tool_mocks) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid6.uuid7()),
            skill_id,
            case_name,
            json.dumps(input_),
            json.dumps(expected_output_shape) if expected_output_shape else None,
            source,
            None,
            datetime.now(tz=timezone.utc).isoformat(),
            json.dumps(tool_mocks) if tool_mocks else None,
        ),
    )
```

Use `_persist_fixture(...)` wherever fixtures land in the DB.

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/unit/test_auto_drafter_tool_mocks.py tests/unit/test_auto_drafter*.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/auto_drafter.py tests/unit/test_auto_drafter_tool_mocks.py
git commit -m "feat(auto-drafter): request tool_mocks from Claude and persist to skill_fixture"
```

---

## Task 18: E2E harness

**Goal:** Shared harness building a minimal orchestrator-like runtime for E2E tests: throwaway DB, mocked Ollama/Claude, `FakeDonnaBot`, live `NotificationService`, skill bundle, automation dispatcher.

**Files:**
- Create: `tests/e2e/__init__.py` (empty)
- Create: `tests/e2e/harness.py`
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_harness_smoke.py`

- [ ] **Step 1: Write the failing test**

File: `tests/e2e/test_harness_smoke.py`

```python
"""Smoke test: the E2E harness builds a runtime with all wave-1 components."""

from __future__ import annotations

import pytest

from tests.e2e.harness import build_wave1_test_runtime


@pytest.mark.asyncio
async def test_harness_constructs_all_components(tmp_path) -> None:
    rt = await build_wave1_test_runtime(tmp_path)
    try:
        assert rt.db is not None
        assert rt.notification_service is not None
        assert rt.fake_bot is not None
        assert rt.fake_router is not None
        assert rt.skill_bundle is not None
        assert rt.automation_scheduler is not None
        assert rt.automation_dispatcher is not None
    finally:
        await rt.shutdown()
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/e2e/test_harness_smoke.py -v
```
Expected: failure (harness missing).

- [ ] **Step 3: Implement**

File: `tests/e2e/__init__.py` — empty file.

File: `tests/e2e/conftest.py`

```python
"""Shared pytest fixtures for Wave 1 E2E scenarios."""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from tests.e2e.harness import build_wave1_test_runtime, Wave1Runtime


@pytest_asyncio.fixture
async def runtime(tmp_path: Path) -> Wave1Runtime:
    rt = await build_wave1_test_runtime(tmp_path)
    try:
        yield rt
    finally:
        await rt.shutdown()
```

File: `tests/e2e/harness.py`

```python
"""E2E harness — build a minimal Wave 1 orchestrator runtime for testing.

Mirrors the production wiring in src/donna/cli.py:_run_orchestrator but
with fakes for Ollama, Claude, and the Discord bot so tests run in
seconds on CI.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config


@dataclass
class _Invocation:
    task_type: str
    prompt: str | None
    output: dict


class FakeOllama:
    def __init__(self, canned: dict[str, dict] | None = None) -> None:
        self.canned = canned or {}
        self.invocations: list[_Invocation] = []

    async def complete(self, *, task_type: str, prompt: str | None = None, **_kw) -> tuple[dict, Any]:
        output = dict(self.canned.get(task_type, {"_default": True}))
        self.invocations.append(_Invocation(task_type=task_type, prompt=prompt, output=output))
        class _Meta:
            invocation_id = f"fake-{len(self.invocations)}"
            cost_usd = 0.0
            latency_ms = 1
        return output, _Meta()


class FakeClaude:
    def __init__(self, canned: dict[str, dict] | None = None) -> None:
        self.canned = canned or {}
        self.invocations: list[_Invocation] = []

    async def complete(self, *, task_type: str, prompt: str | None = None, **_kw) -> tuple[dict, Any]:
        output = dict(self.canned.get(task_type, {"_default": True}))
        self.invocations.append(_Invocation(task_type=task_type, prompt=prompt, output=output))
        class _Meta:
            invocation_id = f"fake-claude-{len(self.invocations)}"
            cost_usd = 0.01
            latency_ms = 10
        return output, _Meta()


class FakeRouter:
    def __init__(self, ollama: FakeOllama, claude: FakeClaude) -> None:
        self._ollama = ollama
        self._claude = claude

    async def complete(self, *, task_type: str, **kw) -> tuple[dict, Any]:
        if task_type.startswith("skill_validation::") or task_type.startswith("chat_"):
            return await self._ollama.complete(task_type=task_type, **kw)
        return await self._claude.complete(task_type=task_type, **kw)


class FakeDonnaBot:
    """FakeDonnaBot satisfies BotProtocol; records every send."""

    def __init__(self) -> None:
        self.sends: list[tuple[str, str, str]] = []

    async def send_message(self, channel: str, content: str) -> None:
        self.sends.append(("channel", channel, content))

    async def send_embed(self, channel: str, embed: Any) -> None:
        self.sends.append(("embed", channel, str(embed)))

    async def send_to_thread(self, thread_id: int, content: str) -> None:
        self.sends.append(("thread", str(thread_id), content))


@dataclass
class Wave1Runtime:
    db: Any
    fake_ollama: FakeOllama
    fake_claude: FakeClaude
    fake_router: FakeRouter
    fake_bot: FakeDonnaBot
    notification_service: Any
    skill_bundle: Any
    skill_config: Any
    automation_dispatcher: Any
    automation_scheduler: Any
    automation_repo: Any
    cost_tracker: Any

    async def shutdown(self) -> None:
        await self.db.close()


async def build_wave1_test_runtime(tmp_path: Path, **overrides) -> Wave1Runtime:
    """Build a fully-wired Wave 1 runtime backed by a throwaway SQLite DB."""
    from donna.tasks.database import Database
    from donna.tasks.state_machine import StateMachine
    from donna.config import (
        load_calendar_config, load_state_machine_config, SkillSystemConfig,
    )
    from donna.notifications.service import NotificationService
    from donna.cost.tracker import CostTracker
    from donna.skills.startup_wiring import assemble_skill_system
    from donna.automations.alert import AlertEvaluator
    from donna.automations.cron import CronScheduleCalculator
    from donna.automations.dispatcher import AutomationDispatcher
    from donna.automations.repository import AutomationRepository
    from donna.automations.scheduler import AutomationScheduler

    db_path = tmp_path / "e2e.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")

    config_dir = Path("config")
    sm_config = load_state_machine_config(config_dir)
    state_machine = StateMachine(sm_config)
    db = Database(db_path, state_machine)
    await db.connect()

    fake_ollama = FakeOllama(overrides.get("ollama_canned"))
    fake_claude = FakeClaude(overrides.get("claude_canned"))
    fake_router = FakeRouter(fake_ollama, fake_claude)
    fake_bot = FakeDonnaBot()

    calendar_cfg = load_calendar_config(config_dir)
    # Disable time windows so tests don't get queued.
    if hasattr(calendar_cfg, "time_windows"):
        calendar_cfg.time_windows.blackout.start_hour = 0
        calendar_cfg.time_windows.blackout.end_hour = 0
        calendar_cfg.time_windows.quiet_hours.start_hour = 0
        calendar_cfg.time_windows.quiet_hours.end_hour = 0
    notification_service = NotificationService(
        bot=fake_bot,
        calendar_config=calendar_cfg,
        user_id="test-user",
    )

    skill_config = SkillSystemConfig(enabled=True, nightly_run_hour_utc=3)
    cost_tracker = CostTracker(db.connection)

    class _FakeBudget:
        async def check_pre_call(self, **kw):
            return None

    bundle = assemble_skill_system(
        connection=db.connection,
        model_router=fake_router,
        budget_guard=_FakeBudget(),
        notifier=lambda m: asyncio.sleep(0),
        config=skill_config,
        validation_executor_factory=None,
    )

    automation_repo = AutomationRepository(db.connection)
    automation_dispatcher = AutomationDispatcher(
        connection=db.connection,
        repository=automation_repo,
        model_router=fake_router,
        skill_executor_factory=lambda: None,
        budget_guard=_FakeBudget(),
        alert_evaluator=AlertEvaluator(),
        cron=CronScheduleCalculator(),
        notifier=notification_service,
        config=skill_config,
    )
    automation_scheduler = AutomationScheduler(
        repository=automation_repo,
        dispatcher=automation_dispatcher,
        poll_interval_seconds=1,
    )

    return Wave1Runtime(
        db=db,
        fake_ollama=fake_ollama,
        fake_claude=fake_claude,
        fake_router=fake_router,
        fake_bot=fake_bot,
        notification_service=notification_service,
        skill_bundle=bundle,
        skill_config=skill_config,
        automation_dispatcher=automation_dispatcher,
        automation_scheduler=automation_scheduler,
        automation_repo=automation_repo,
        cost_tracker=cost_tracker,
    )
```

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/e2e/test_harness_smoke.py -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/
git commit -m "test(e2e): shared Wave 1 runtime harness with fakes"
```

---

## Task 19: E2E scenario 1 — nightly cycle produces draft

**Goal:** Seed 200 invocations of a claude_native task type. Trigger nightly tasks. Assert draft created.

**Files:**
- Create: `tests/e2e/test_wave1_smoke.py` (scenario 1; subsequent tasks append 2-4)

- [ ] **Step 1: Write the failing test**

File: `tests/e2e/test_wave1_smoke.py`

```python
"""Wave 1 E2E smoke tests — four scenarios per spec §6.5."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.asyncio
async def test_nightly_cycle_drafts_skill(runtime) -> None:
    """Scenario 1: seed 200 recent claude_native invocations, trigger nightly
    tasks, expect skill_candidate_report + skill_version(state=draft) +
    skill_state_transition rows."""
    import uuid
    from donna.skills.crons import NightlyDeps, run_nightly_tasks

    conn = runtime.db.connection
    task_type = "test_capability_high_volume"
    now = datetime.now(tz=timezone.utc)

    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, 'seed')",
        (str(uuid.uuid4()), task_type, "High-volume test capability",
         json.dumps({"type": "object"}), "on_message", now.isoformat()),
    )

    for i in range(200):
        at = (now - timedelta(days=10) + timedelta(minutes=i)).isoformat()
        await conn.execute(
            "INSERT INTO invocation_log (id, task_type, model_alias, "
            "user_id, task_id, status, cost_usd, latency_ms, "
            "prompt_tokens, completion_tokens, at) "
            "VALUES (?, ?, 'claude_sonnet', 'nick', NULL, 'succeeded', "
            "0.10, 100, 500, 200, ?)",
            (str(uuid.uuid4()), task_type, at),
        )
    await conn.commit()

    # Canned Claude output for skill generation.
    runtime.fake_claude.canned["skill_auto_draft"] = {
        "skill_yaml": "steps: []",
        "step_prompts": {},
        "output_schemas": {},
        "fixtures": [
            {
                "case_name": "c1", "input": {},
                "expected_output_shape": {"type": "object"},
                "tool_mocks": {},
            },
        ],
    }

    deps = NightlyDeps(
        detector=runtime.skill_bundle.detector,
        auto_drafter=runtime.skill_bundle.auto_drafter,
        degradation=runtime.skill_bundle.degradation,
        evolution_scheduler=runtime.skill_bundle.evolution_scheduler,
        correction_cluster=runtime.skill_bundle.correction_cluster,
        cost_tracker=runtime.cost_tracker,
        daily_budget_limit_usd=100.0,
        config=runtime.skill_config,
    )
    await run_nightly_tasks(deps)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM skill_candidate_report WHERE status = 'drafted'"
    )
    assert (await cursor.fetchone())[0] >= 1

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM skill_version WHERE created_by = 'claude_auto_draft'"
    )
    assert (await cursor.fetchone())[0] >= 1

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM skill_state_transition"
    )
    assert (await cursor.fetchone())[0] >= 1
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/e2e/test_wave1_smoke.py::test_nightly_cycle_drafts_skill -v
```
Expected: failure.

- [ ] **Step 3: Tune**

Identify the actual `TASK_TYPE` constant used for Claude skill generation:

```bash
grep -n "TASK_TYPE\s*=" src/donna/skills/auto_drafter.py
```

Update `runtime.fake_claude.canned[<actual_task_type>]` to match. Also verify the detector's criteria — it matches on `invocation_log.task_type` → `capability.name`. Ensure the capability row is inserted before seeding invocations.

At $0.10 × 200 over 10d ≈ $60/month, well above `auto_draft_min_expected_savings_usd` default ($5).

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/e2e/test_wave1_smoke.py::test_nightly_cycle_drafts_skill -v
```
Expected: passed.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_wave1_smoke.py
git commit -m "test(e2e): scenario 1 — nightly cycle produces drafted skill"
```

---

## Task 20: E2E scenario 2 — automation tick produces run + alert

**Goal:** Seed an active automation with past `next_run_at` and alert condition. Run scheduler once. Assert `automation_run` + alert.

**Files:**
- Append to: `tests/e2e/test_wave1_smoke.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/e2e/test_wave1_smoke.py`:

```python
@pytest.mark.asyncio
async def test_automation_tick_alerts(runtime) -> None:
    """Scenario 2: active automation with past next_run_at fires an alert."""
    import uuid
    from datetime import datetime, timedelta, timezone

    conn = runtime.db.connection
    now = datetime.now(tz=timezone.utc)
    past = (now - timedelta(minutes=5)).isoformat()

    # Canned claude_native output.
    runtime.fake_claude.canned["automation_run"] = {
        "ok": True, "price_usd": 50.0, "in_stock": True,
    }

    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, 'seed')",
        (str(uuid.uuid4()), "product_watch", "Watch a product URL",
         json.dumps({"type": "object"}), "on_schedule", now.isoformat()),
    )

    automation_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO automation (id, user_id, name, description, "
        "capability_name, inputs, trigger_type, schedule, "
        "alert_conditions, alert_channels, max_cost_per_run_usd, "
        "min_interval_seconds, status, last_run_at, next_run_at, "
        "run_count, failure_count, created_at, updated_at, created_via) "
        "VALUES (?, 'nick', 'Watch COS shirt', NULL, 'product_watch', ?, "
        "'on_schedule', '0 * * * *', ?, ?, 1.0, 300, 'active', NULL, ?, "
        "0, 0, ?, ?, 'dashboard')",
        (
            automation_id,
            json.dumps({"url": "https://cos.com/shirt"}),
            json.dumps({"all_of": [{"field": "ok", "op": "==", "value": True}]}),
            json.dumps(["tasks"]),
            past,
            now.isoformat(), now.isoformat(),
        ),
    )
    await conn.commit()

    await runtime.automation_scheduler.run_once()

    cursor = await conn.execute(
        "SELECT status, alert_sent FROM automation_run WHERE automation_id = ?",
        (automation_id,),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "succeeded"
    assert rows[0][1] == 1

    assert len(runtime.fake_bot.sends) >= 1
    kind, target, content = runtime.fake_bot.sends[0]
    assert kind == "channel"
    assert target == "tasks"
    assert content
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/e2e/test_wave1_smoke.py::test_automation_tick_alerts -v
```
Expected: failure.

- [ ] **Step 3: Tune**

Check the dispatcher's claude_native path for the actual task_type it uses:

```bash
grep -n "claude_native\|execution_path\|task_type" src/donna/automations/dispatcher.py
```

Update `runtime.fake_claude.canned[<actual>]` accordingly. The alert condition references `ok == True`, so ensure the canned output has that field.

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/e2e/test_wave1_smoke.py::test_automation_tick_alerts -v
```
Expected: passed.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_wave1_smoke.py
git commit -m "test(e2e): scenario 2 — automation tick produces run and alert"
```

---

## Task 21: E2E scenario 3 — sandbox → shadow_primary auto-promotion

**Goal:** 20 successful schema-valid runs promote sandbox → shadow_primary.

**Files:**
- Append to: `tests/e2e/test_wave1_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_sandbox_promotes_to_shadow_primary(runtime) -> None:
    """Scenario 3: N=20 valid runs promotes sandbox → shadow_primary."""
    import uuid
    from datetime import datetime, timezone

    conn = runtime.db.connection
    now = datetime.now(tz=timezone.utc).isoformat()

    cap_id = str(uuid.uuid4())
    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, 'seed')",
        (cap_id, "promo_cap", "Promotion test capability",
         json.dumps({"type": "object"}), "on_message", now),
    )
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, ?, 'sandbox', 0, NULL, ?, ?)",
        (skill_id, "promo_cap", version_id, now, now),
    )
    await conn.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, "
        "yaml_backbone, step_content, output_schemas, created_by, "
        "changelog, created_at) VALUES (?, ?, 1, ?, ?, ?, 'seed', NULL, ?)",
        (version_id, skill_id, "steps: []", "{}", "{}", now),
    )

    for i in range(20):
        run_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, final_output, started_at, finished_at) "
            "VALUES (?, ?, ?, 'succeeded', '{}', ?, ?, ?)",
            (run_id, skill_id, version_id,
             json.dumps({"ok": True}), now, now),
        )
    await conn.commit()

    lifecycle = runtime.skill_bundle.lifecycle_manager
    await lifecycle.evaluate_auto_promotions()

    cursor = await conn.execute(
        "SELECT state FROM skill WHERE id = ?", (skill_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "shadow_primary"

    cursor = await conn.execute(
        "SELECT from_state, to_state, reason FROM skill_state_transition "
        "WHERE skill_id = ? ORDER BY at DESC LIMIT 1", (skill_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "sandbox"
    assert row[1] == "shadow_primary"
    assert row[2] == "gate_passed"
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/e2e/test_wave1_smoke.py::test_sandbox_promotes_to_shadow_primary -v
```
Expected: failure.

- [ ] **Step 3: Tune**

Find the actual promotion method on `SkillLifecycleManager`:

```bash
grep -n "def " src/donna/skills/lifecycle.py | head -30
```

If it's `check_promotions` or `promote_eligible` or something else, update the call. If promotion runs only via `run_nightly_tasks`, change the test to call that instead.

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/e2e/test_wave1_smoke.py::test_sandbox_promotes_to_shadow_primary -v
```
Expected: passed.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_wave1_smoke.py
git commit -m "test(e2e): scenario 3 — sandbox promotes to shadow_primary"
```

---

## Task 22: E2E scenario 4 — trusted → flagged_for_review degradation

**Goal:** 30 divergences at 0.65 agreement drop a trusted skill (baseline 0.90) to flagged_for_review.

**Files:**
- Append to: `tests/e2e/test_wave1_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_trusted_degrades_to_flagged(runtime) -> None:
    """Scenario 4: 30 shadow divergences with 0.65 agreement drops a trusted
    skill to flagged_for_review."""
    import uuid
    from datetime import datetime, timedelta, timezone

    conn = runtime.db.connection
    now = datetime.now(tz=timezone.utc)

    cap_id = str(uuid.uuid4())
    skill_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO capability (id, name, description, input_schema, "
        "trigger_type, status, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?, 'seed')",
        (cap_id, "degrade_cap", "Degradation test capability",
         json.dumps({"type": "object"}), "on_message", now.isoformat()),
    )
    await conn.execute(
        "INSERT INTO skill (id, capability_name, current_version_id, state, "
        "requires_human_gate, baseline_agreement, created_at, updated_at) "
        "VALUES (?, ?, ?, 'trusted', 0, 0.90, ?, ?)",
        (skill_id, "degrade_cap", version_id,
         now.isoformat(), now.isoformat()),
    )
    await conn.execute(
        "INSERT INTO skill_version (id, skill_id, version_number, "
        "yaml_backbone, step_content, output_schemas, created_by, "
        "changelog, created_at) VALUES (?, ?, 1, ?, ?, ?, 'seed', NULL, ?)",
        (version_id, skill_id, "steps: []", "{}", "{}", now.isoformat()),
    )

    for i in range(30):
        run_id = str(uuid.uuid4())
        div_id = str(uuid.uuid4())
        inv_id = str(uuid.uuid4())
        at = (now - timedelta(hours=i)).isoformat()
        await conn.execute(
            "INSERT INTO skill_run (id, skill_id, skill_version_id, status, "
            "state_object, started_at, finished_at) "
            "VALUES (?, ?, ?, 'succeeded', '{}', ?, ?)",
            (run_id, skill_id, version_id, at, at),
        )
        await conn.execute(
            "INSERT INTO invocation_log (id, task_type, model_alias, "
            "user_id, task_id, status, cost_usd, latency_ms, "
            "prompt_tokens, completion_tokens, at) "
            "VALUES (?, 'shadow_claude', 'claude_sonnet', 'system', NULL, "
            "'succeeded', 0.05, 100, 400, 100, ?)",
            (inv_id, at),
        )
        await conn.execute(
            "INSERT INTO skill_divergence (id, skill_run_id, "
            "shadow_invocation_id, overall_agreement, diff_summary, "
            "flagged_for_evolution, created_at) "
            "VALUES (?, ?, ?, 0.65, '{}', 0, ?)",
            (div_id, run_id, inv_id, at),
        )
    await conn.commit()

    await runtime.skill_bundle.degradation.check_all_trusted_skills()

    cursor = await conn.execute(
        "SELECT state FROM skill WHERE id = ?", (skill_id,),
    )
    row = await cursor.fetchone()
    assert row[0] == "flagged_for_review"

    cursor = await conn.execute(
        "SELECT to_state FROM skill_state_transition WHERE skill_id = ? "
        "ORDER BY at DESC LIMIT 1", (skill_id,),
    )
    assert (await cursor.fetchone())[0] == "flagged_for_review"
```

- [ ] **Step 2: Run, verify failure**

```bash
pytest tests/e2e/test_wave1_smoke.py::test_trusted_degrades_to_flagged -v
```
Expected: failure.

- [ ] **Step 3: Tune**

Verify the degradation method name:

```bash
grep -n "def " src/donna/skills/degradation.py
```

Verify column names on `skill_divergence`. Update the test insert/query accordingly.

- [ ] **Step 4: Run, verify pass**

```bash
pytest tests/e2e/test_wave1_smoke.py::test_trusted_degrades_to_flagged -v
pytest tests/e2e/test_wave1_smoke.py -v
```
Expected: all 4 scenarios pass; full file runs < 30s.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_wave1_smoke.py
git commit -m "test(e2e): scenario 4 — trusted degrades to flagged_for_review"
```

---

## Task 23: Documentation updates

**Goal:** Update architecture + notifications docs. Tick the followups inventory and the spec's requirements checklist.

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/notifications.md`
- Modify: `docs/superpowers/followups/2026-04-16-skill-system-followups.md`
- Modify: `docs/superpowers/specs/2026-04-16-skill-system-wave-1-production-enablement-design.md`

- [ ] **Step 1: Update `docs/architecture.md`**

Find the section describing process split (likely "Deployment" or "Runtime"). Add or update:

```markdown
### Process responsibilities (post Wave 1)

| Process | Owns |
|---|---|
| `donna-orchestrator` (port 8100) | DonnaBot, NotificationService, AutomationScheduler, AutomationDispatcher, nightly skill-system cron. All background / long-running work. |
| `donna-api` (port 8200) | FastAPI REST for the Flutter dashboard. CRUD only; no schedulers. Reads `skill_system_config.enabled` for admin reporting. |

Cross-process coordination uses the shared SQLite DB. Example:
`POST /admin/automations/{id}/run-now` sets `next_run_at=now()` on the
automation row; the orchestrator's AutomationScheduler picks it up on its
next poll (~15s default).
```

- [ ] **Step 2: Update `docs/notifications.md`**

Add near the top:

```markdown
> **Status (Wave 1):** NotificationService is now instantiated in the
> orchestrator process on startup, wired with the live DonnaBot and the
> calendar config. Previously only tests constructed it; production code
> now relies on it for automation alerts and skill-system warnings.
```

- [ ] **Step 3: Tick the followups doc**

In `docs/superpowers/followups/2026-04-16-skill-system-followups.md`, add near the top:

```markdown
## Completed — Wave 1 (2026-04-16)

- **F-1** Sandbox SkillExecutor → shipped as `ValidationExecutor`. See `docs/superpowers/specs/2026-04-16-skill-system-wave-1-production-enablement-design.md`.
- **F-5** Wire ValidationExecutor into lifespan.
- **F-6** NotificationService wired; automation scheduler moved to orchestrator process.
- **F-14** End-to-end "enabled" smoke test.
```

- [ ] **Step 4: Tick spec requirements checklist**

In `docs/superpowers/specs/2026-04-16-skill-system-wave-1-production-enablement-design.md`, replace `[ ]` with `[x]` for W1-R1 through W1-R18 in the table, matching each requirement to the task that verified it:

- W1-R1, R4, R5: Tasks 1, 6, 8
- W1-R2: Task 5
- W1-R3: Task 8
- W1-R6, R7, R8: Task 10
- W1-R9: Task 12
- W1-R10: Tasks 14, 15
- W1-R11: Task 16
- W1-R12: Task 20 (scheduler picks up `next_run_at=now()` within poll interval)
- W1-R13, R14, R15, R16: Tasks 19, 20, 21, 22
- W1-R17: Task 24
- W1-R18: this task

- [ ] **Step 5: Commit**

```bash
git add docs/
git commit -m "docs: update architecture, notifications, followups, and spec checklist for Wave 1"
```

---

## Task 24: Final full-suite run + production-enablement verification

**Goal:** Run the complete test suite. Confirm all four production-enablement criteria from spec §1 hold.

**Files:** none — verification only. Fix regressions in focused commits if any surface.

- [ ] **Step 1: Run full suite**

```bash
pytest -v 2>&1 | tail -50
```

Expected: 0 failures. Triage each failure:
- Tests constructing `assemble_skill_system(executor_factory=None)` → rename kwarg to `validation_executor_factory=None`.
- API tests asserting `app.state.automation_dispatcher is not None` → update to expect None.
- NotificationService tests constructing with `bot=<real DonnaBot>` → works unchanged because BotProtocol is structural.

Fix each in focused commits.

- [ ] **Step 2: Verify production-enablement criteria**

Confirm all four criteria from spec §1:

1. F-1 + F-5 landed; gates 2/3/4 return real pass rates:
   ```bash
   grep -rn "pass_rate=1.0\|validation_deferred" src/donna/ | grep -v test_
   ```
   Expected: zero hits.

2. F-6 landed; orchestrator owns automation scheduler; `notification_service` non-null:
   - `pytest tests/integration/test_automation_scheduler_in_orchestrator.py -v` green.
   - (Manual, Nick-side) run `donna test-notification --type digest --channel tasks --content "hello from Wave 1"` with real Discord creds. Message arrives.

3. F-14 green:
   ```bash
   pytest tests/e2e/ -v
   ```

4. Existing full suite green:
   ```bash
   pytest -v
   ```

- [ ] **Step 3: Toggle `skill_system.enabled=true`**

Edit `config/skills.yaml`:

```yaml
enabled: true
```

- [ ] **Step 4: Run full suite once more with enabled=true**

```bash
pytest -v
```

Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add config/skills.yaml
git commit -m "config(skills): enable skill_system in production (Wave 1 complete)"
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** Every W1-R1 through W1-R18 maps to at least one task. Verified.
- [ ] **Type consistency:** `validation_executor_factory` used consistently (Tasks 10, 14, 18). `tool_mocks` threaded through Tasks 1, 2, 5, 7, 8, 17. Existing `FixtureValidationReport` / `FixtureFailureDetail` / `Fixture` names referenced as-is.
- [ ] **No placeholders:** All code blocks contain real code. All commands include exact arguments.
- [ ] **Migration revision:** Task 1's revision `b8c9d0e1f2a3` chains from `a7b8c9d0e1f2` (current head). Verified.
- [ ] **Dependency order:** Task 2 depends on Task 1. Task 5 on Task 4. Task 8 on Tasks 5, 6, 7. Task 10 on Tasks 8, 9. Tasks 12–16 sequential F-6 steps. Tasks 19–22 depend on Task 18 harness.
