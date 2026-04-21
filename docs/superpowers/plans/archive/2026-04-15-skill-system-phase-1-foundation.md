# Skill System Phase 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foundation for the skill system — capability registry, minimal single-step skill executor, challenger refactor, and read-only dashboard views — without changing any user-visible behavior.

**Architecture:** Extend `donna_tasks.db` with four new tables (`capability`, `skill`, `skill_version`, `skill_state_transition`). Introduce `CapabilityRegistry` with semantic search via `sentence-transformers` and in-memory cosine similarity (no external vector store). Minimal `SkillExecutor` runs single-step `llm` skills in parallel to the existing Claude path — seed skills land in `sandbox` state, so the skill's output is logged but not used; Claude remains primary. `ChallengerAgent` gains a new `match_and_extract` method that queries the registry, extracts inputs, and generates clarifying questions for missing fields. The dispatcher routes to skill execution or claude-native based on skill state. Dashboard gains read-only views for capabilities and skills.

**Tech Stack:** Python 3.12 async, SQLAlchemy 2.x + Alembic (migration), `aiosqlite` (access), `sentence-transformers` (new dependency, `all-MiniLM-L6-v2`, ~80MB CPU-friendly), `numpy` (cosine similarity), `jsonschema` (output validation), `pytest` + `pytest-asyncio`, `structlog`, existing Flask dashboard routes.

**Spec alignment note:** The spec's Phase 1 handoff contract says seed skills land in `shadow_primary` state. Shadow sampling infrastructure does not exist until Phase 3, so this plan lands seed skills in `sandbox` state instead — the skill runs alongside Claude, its output is logged via `skill_run`, and Claude's output is still returned to the user. This preserves the "no user-visible behavior change" invariant for Phase 1. When Phase 3 implements shadow sampling, a targeted migration promotes the seed skills to `shadow_primary`. This deviation is intentional and should be recorded in the spec's Drift Log (§8) after this plan is accepted.

---

## File Structure

### New files

```
alembic/versions/
  add_skill_system_phase_1.py                -- Migration: capability, skill, skill_version, skill_state_transition

src/donna/capabilities/
  __init__.py                                -- Exports
  models.py                                  -- Capability, Skill, SkillVersion dataclasses + row mappers
  embeddings.py                              -- Lazy-loaded sentence-transformers helper
  registry.py                                -- CapabilityRegistry (CRUD + semantic search + post-creation audit)
  matcher.py                                 -- CapabilityMatcher (confidence-scoring wrapper)

src/donna/skills/
  __init__.py                                -- Exports
  state.py                                   -- StateObject typed dict wrapper
  validation.py                              -- JSON schema validation helper
  executor.py                                -- SkillExecutor (single-step llm only for Phase 1)
  loader.py                                  -- Load seed skill YAML files into skill + skill_version rows

skills/parse_task/
  skill.yaml                                 -- Seed skill backbone
  steps/
    extract.md                               -- Prompt content
  schemas/
    extract_v1.json                          -- Output schema

skills/dedup_check/
  skill.yaml
  steps/
    compare.md
  schemas/
    compare_v1.json

skills/classify_priority/
  skill.yaml
  steps/
    classify.md
  schemas/
    classify_v1.json

tests/unit/test_capabilities_registry.py
tests/unit/test_capabilities_matcher.py
tests/unit/test_capabilities_embeddings.py
tests/unit/test_skills_state.py
tests/unit/test_skills_validation.py
tests/unit/test_skills_executor.py
tests/unit/test_skills_loader.py
tests/unit/test_challenger_match_and_extract.py
tests/unit/test_dispatcher_skill_routing.py
tests/integration/test_skill_system_phase_1_e2e.py
```

### Modified files

```
pyproject.toml                               -- Add sentence-transformers, numpy, jsonschema deps
src/donna/tasks/db_models.py                 -- Add Capability, Skill, SkillVersion, SkillStateTransition ORM classes
src/donna/agents/challenger_agent.py         -- Add match_and_extract method (old methods preserved)
src/donna/orchestrator/dispatcher.py         -- Add skill-routing path guarded by feature check
src/donna/api/routes/                        -- Add capabilities.py, skills.py read-only routes
src/donna/config.py                          -- Add SkillSystemConfig pydantic model
config/donna_models.yaml                     -- No changes yet; Phase 3 will touch this
```

---

## Task 1: Add `sentence-transformers`, `numpy`, `jsonschema` dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies to `pyproject.toml`**

Locate the `[project]` or `[tool.poetry.dependencies]` section (depending on tooling — check the file) and add:

```toml
sentence-transformers = "^3.0.0"
numpy = "^1.26.0"
jsonschema = "^4.20.0"
```

If `numpy` is already a transitive dep, declare it anyway so it's pinned.

- [ ] **Step 2: Install the new dependencies**

Run: `pip install -e .` from the repo root (or `poetry install` if poetry is the tooling — check `pyproject.toml` for `[tool.poetry]` section).

Expected: installs the three packages plus their transitive deps. First install of `sentence-transformers` downloads torch which can take a few minutes. This only happens once per environment.

- [ ] **Step 3: Verify the imports work**

Run:
```bash
python -c "import sentence_transformers; import numpy; import jsonschema; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore(deps): add sentence-transformers, numpy, jsonschema for skill system"
```

---

## Task 2: Alembic migration — add skill system tables

**Files:**
- Create: `alembic/versions/add_skill_system_phase_1.py`

- [ ] **Step 1: Generate the migration file**

Check `alembic/versions/` for the current head revision ID: `ls alembic/versions/` and look at the most recent migration to see the `down_revision` pattern.

Create `alembic/versions/add_skill_system_phase_1.py`:

```python
"""add skill system phase 1 tables

Revision ID: 7b2a4c8d1e3f
Revises: <CURRENT_HEAD_REVISION>
Create Date: 2026-04-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Fill these in based on the current head:
revision = "7b2a4c8d1e3f"
down_revision = "<CURRENT_HEAD_REVISION>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capability",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("default_output_shape", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("name", name="uq_capability_name"),
    )
    with op.batch_alter_table("capability", schema=None) as batch_op:
        batch_op.create_index("ix_capability_status", ["status"])
        batch_op.create_index("ix_capability_trigger_type", ["trigger_type"])

    op.create_table(
        "skill",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("capability_name", sa.String(length=200), nullable=False),
        sa.Column("current_version_id", sa.String(length=36), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("requires_human_gate", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("baseline_agreement", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("capability_name", name="uq_skill_capability_name"),
        sa.ForeignKeyConstraint(["capability_name"], ["capability.name"], name="fk_skill_capability_name"),
    )
    with op.batch_alter_table("skill", schema=None) as batch_op:
        batch_op.create_index("ix_skill_state", ["state"])

    op.create_table(
        "skill_version",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("yaml_backbone", sa.Text(), nullable=False),
        sa.Column("step_content", sa.JSON(), nullable=False),
        sa.Column("output_schemas", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=20), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"], name="fk_skill_version_skill_id"),
    )
    with op.batch_alter_table("skill_version", schema=None) as batch_op:
        batch_op.create_index("ix_skill_version_skill_id", ["skill_id"])

    op.create_table(
        "skill_state_transition",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.Column("from_state", sa.String(length=30), nullable=False),
        sa.Column("to_state", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column("actor", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"], name="fk_skill_state_transition_skill_id"),
    )
    with op.batch_alter_table("skill_state_transition", schema=None) as batch_op:
        batch_op.create_index("ix_skill_state_transition_skill_id", ["skill_id"])


def downgrade() -> None:
    with op.batch_alter_table("skill_state_transition", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_state_transition_skill_id")
    op.drop_table("skill_state_transition")

    with op.batch_alter_table("skill_version", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_version_skill_id")
    op.drop_table("skill_version")

    with op.batch_alter_table("skill", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_state")
    op.drop_table("skill")

    with op.batch_alter_table("capability", schema=None) as batch_op:
        batch_op.drop_index("ix_capability_trigger_type")
        batch_op.drop_index("ix_capability_status")
    op.drop_table("capability")
```

Replace `<CURRENT_HEAD_REVISION>` with the actual head revision ID from your Alembic versions directory.

- [ ] **Step 2: Run the migration against a temp database**

Run:
```bash
DONNA_DB_PATH=/tmp/donna_test.db alembic upgrade head
```

Expected: migration applies cleanly. No errors.

- [ ] **Step 3: Verify tables exist**

Run:
```bash
sqlite3 /tmp/donna_test.db ".tables"
```

Expected output includes: `capability skill skill_state_transition skill_version` alongside the existing tables.

- [ ] **Step 4: Run downgrade to verify reversibility**

Run:
```bash
DONNA_DB_PATH=/tmp/donna_test.db alembic downgrade -1
sqlite3 /tmp/donna_test.db ".tables"
```

Expected: the four new tables are gone, existing tables unchanged.

- [ ] **Step 5: Re-upgrade so the DB is back to head**

```bash
DONNA_DB_PATH=/tmp/donna_test.db alembic upgrade head
rm /tmp/donna_test.db
```

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/add_skill_system_phase_1.py
git commit -m "feat(db): add skill system phase 1 tables

Adds capability, skill, skill_version, and skill_state_transition
tables per spec 2026-04-15 §5.1–5.3 and §5.8."
```

---

## Task 3: SQLAlchemy ORM models for the new tables

**Files:**
- Modify: `src/donna/tasks/db_models.py`

- [ ] **Step 1: Write failing test for ORM model import and construction**

Create `tests/unit/test_skills_db_models.py`:

```python
import pytest
from datetime import datetime, timezone

from donna.tasks.db_models import (
    Capability,
    Skill,
    SkillVersion,
    SkillStateTransition,
    TriggerType,
    SkillState,
)


def test_capability_construction():
    cap = Capability(
        id="11111111-1111-1111-1111-111111111111",
        name="product_watch",
        description="Monitor a product page",
        input_schema={"type": "object", "properties": {}},
        trigger_type=TriggerType.ON_SCHEDULE,
        status="active",
        embedding=None,
        created_at=datetime.now(timezone.utc),
        created_by="seed",
    )
    assert cap.name == "product_watch"
    assert cap.trigger_type == TriggerType.ON_SCHEDULE


def test_skill_construction():
    skill = Skill(
        id="22222222-2222-2222-2222-222222222222",
        capability_name="product_watch",
        current_version_id=None,
        state=SkillState.DRAFT,
        requires_human_gate=False,
        baseline_agreement=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert skill.state == SkillState.DRAFT


def test_skill_state_values():
    assert SkillState.CLAUDE_NATIVE.value == "claude_native"
    assert SkillState.SKILL_CANDIDATE.value == "skill_candidate"
    assert SkillState.DRAFT.value == "draft"
    assert SkillState.SANDBOX.value == "sandbox"
    assert SkillState.SHADOW_PRIMARY.value == "shadow_primary"
    assert SkillState.TRUSTED.value == "trusted"
    assert SkillState.FLAGGED_FOR_REVIEW.value == "flagged_for_review"
    assert SkillState.DEGRADED.value == "degraded"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_skills_db_models.py -v`
Expected: `ImportError: cannot import name 'Capability' from 'donna.tasks.db_models'`

- [ ] **Step 3: Add the ORM classes**

Open `src/donna/tasks/db_models.py`. Before the final `__all__` if there is one (or at the end of the file), add:

```python
class TriggerType(str, enum.Enum):
    ON_MESSAGE = "on_message"
    ON_SCHEDULE = "on_schedule"
    ON_MANUAL = "on_manual"


class SkillState(str, enum.Enum):
    CLAUDE_NATIVE = "claude_native"
    SKILL_CANDIDATE = "skill_candidate"
    DRAFT = "draft"
    SANDBOX = "sandbox"
    SHADOW_PRIMARY = "shadow_primary"
    TRUSTED = "trusted"
    FLAGGED_FOR_REVIEW = "flagged_for_review"
    DEGRADED = "degraded"


class Capability(Base):
    __tablename__ = "capability"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    trigger_type: Mapped[TriggerType] = mapped_column(
        Enum(TriggerType), nullable=False, index=True
    )
    default_output_shape: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", index=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Skill(Base):
    __tablename__ = "skill"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    capability_name: Mapped[str] = mapped_column(
        String(200),
        ForeignKey("capability.name"),
        nullable=False,
        unique=True,
    )
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[SkillState] = mapped_column(
        Enum(SkillState), nullable=False, index=True
    )
    requires_human_gate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    baseline_agreement: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkillVersion(Base):
    __tablename__ = "skill_version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill.id"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    yaml_backbone: Mapped[str] = mapped_column(Text, nullable=False)
    step_content: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_schemas: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(20), nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkillStateTransition(Base):
    __tablename__ = "skill_state_transition"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skill.id"), nullable=False, index=True
    )
    from_state: Mapped[str] = mapped_column(String(30), nullable=False)
    to_state: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    actor: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Ensure the imports at the top of `db_models.py` include: `Boolean`, `Float`, `LargeBinary`, `Text` from `sqlalchemy`, and `Enum` is already there.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_skills_db_models.py -v`
Expected: all three tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/tasks/db_models.py tests/unit/test_skills_db_models.py
git commit -m "feat(db-models): add Capability, Skill, SkillVersion, SkillStateTransition ORM"
```

---

## Task 4: Capability dataclass and row mapper

**Files:**
- Create: `src/donna/capabilities/__init__.py`
- Create: `src/donna/capabilities/models.py`
- Create: `tests/unit/test_capabilities_models.py`

- [ ] **Step 1: Write failing test for dataclass and mapper**

Create `tests/unit/test_capabilities_models.py`:

```python
from datetime import datetime, timezone

import pytest

from donna.capabilities.models import CapabilityRow, row_to_capability


def test_capability_row_basic():
    row = CapabilityRow(
        id="abc",
        name="product_watch",
        description="desc",
        input_schema={"type": "object"},
        trigger_type="on_schedule",
        default_output_shape=None,
        status="active",
        embedding=None,
        created_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
        created_by="seed",
        notes=None,
    )
    assert row.name == "product_watch"
    assert row.status == "active"


def test_row_to_capability_parses_json_fields():
    raw = (
        "abc",
        "product_watch",
        "desc",
        '{"type": "object"}',
        "on_schedule",
        None,
        "active",
        None,
        "2026-04-15T00:00:00+00:00",
        "seed",
        None,
    )
    cap = row_to_capability(raw)
    assert cap.input_schema == {"type": "object"}
    assert cap.trigger_type == "on_schedule"
    assert cap.created_at.year == 2026
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_capabilities_models.py -v`
Expected: `ModuleNotFoundError: No module named 'donna.capabilities'`

- [ ] **Step 3: Create the `capabilities` package init**

Create `src/donna/capabilities/__init__.py`:

```python
"""Capability registry — user-facing task patterns Donna can handle.

See docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md
"""

from donna.capabilities.models import CapabilityRow, row_to_capability

__all__ = ["CapabilityRow", "row_to_capability"]
```

- [ ] **Step 4: Create the models module**

Create `src/donna/capabilities/models.py`:

```python
"""Lightweight dataclasses and row mappers for the capability registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

CAPABILITY_COLUMNS = (
    "id",
    "name",
    "description",
    "input_schema",
    "trigger_type",
    "default_output_shape",
    "status",
    "embedding",
    "created_at",
    "created_by",
    "notes",
)

SELECT_CAPABILITY = ", ".join(CAPABILITY_COLUMNS)


@dataclass(slots=True)
class CapabilityRow:
    id: str
    name: str
    description: str
    input_schema: dict
    trigger_type: str
    default_output_shape: dict | None
    status: str
    embedding: bytes | None
    created_at: datetime
    created_by: str
    notes: str | None


def row_to_capability(row: tuple) -> CapabilityRow:
    return CapabilityRow(
        id=row[0],
        name=row[1],
        description=row[2],
        input_schema=_parse_json(row[3]),
        trigger_type=row[4],
        default_output_shape=_parse_json(row[5]) if row[5] is not None else None,
        status=row[6],
        embedding=row[7],
        created_at=_parse_dt(row[8]),
        created_by=row[9],
        notes=row[10],
    )


def _parse_json(value: str | dict | None) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/unit/test_capabilities_models.py -v`
Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/capabilities/ tests/unit/test_capabilities_models.py
git commit -m "feat(capabilities): add CapabilityRow dataclass and row mapper"
```

---

## Task 5: Embedding helper with lazy model loading

**Files:**
- Create: `src/donna/capabilities/embeddings.py`
- Create: `tests/unit/test_capabilities_embeddings.py`

- [ ] **Step 1: Write failing test for embedding helper**

Create `tests/unit/test_capabilities_embeddings.py`:

```python
import numpy as np
import pytest

from donna.capabilities.embeddings import (
    embed_text,
    embedding_to_bytes,
    bytes_to_embedding,
    cosine_similarity,
    EMBEDDING_DIM,
)


@pytest.mark.slow
def test_embed_text_returns_expected_shape():
    vec = embed_text("check the price of a product")
    assert isinstance(vec, np.ndarray)
    assert vec.shape == (EMBEDDING_DIM,)
    assert vec.dtype == np.float32


@pytest.mark.slow
def test_roundtrip_bytes_conversion():
    vec = embed_text("hello world")
    blob = embedding_to_bytes(vec)
    restored = bytes_to_embedding(blob)
    assert np.allclose(vec, restored)


def test_cosine_similarity_identical_vectors():
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(-1.0)
```

The `@pytest.mark.slow` marker lets us skip model-loading tests in fast runs: `pytest -m "not slow"`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_capabilities_embeddings.py -v`
Expected: all tests fail with `ModuleNotFoundError: No module named 'donna.capabilities.embeddings'`

- [ ] **Step 3: Create the embeddings module**

Create `src/donna/capabilities/embeddings.py`:

```python
"""Embedding helper for capability semantic search.

Uses sentence-transformers' all-MiniLM-L6-v2 (384-dim, ~80MB, CPU-friendly).
The model is lazy-loaded on first use to avoid import-time cost.
"""

from __future__ import annotations

import threading

import numpy as np

EMBEDDING_DIM = 384
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_model = None
_model_lock = threading.Lock()


def _get_model():
    """Lazy-load the sentence-transformers model. Thread-safe."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embed_text(text: str) -> np.ndarray:
    """Return a 384-dim float32 embedding for the input text.

    The returned vector is not normalized; cosine_similarity handles
    normalization at comparison time.
    """
    model = _get_model()
    vec = model.encode(text, convert_to_numpy=True, show_progress_bar=False)
    return vec.astype(np.float32)


def embedding_to_bytes(vec: np.ndarray) -> bytes:
    """Serialize a float32 numpy array to bytes for SQLite storage."""
    assert vec.dtype == np.float32
    assert vec.shape == (EMBEDDING_DIM,)
    return vec.tobytes()


def bytes_to_embedding(blob: bytes) -> np.ndarray:
    """Deserialize bytes back to a float32 numpy array."""
    return np.frombuffer(blob, dtype=np.float32).copy()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors.

    Returns a value in [-1.0, 1.0]. Safe against zero-norm vectors
    (returns 0.0 in that case to avoid NaN).
    """
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
```

- [ ] **Step 4: Register the `slow` marker in pyproject.toml**

Add to `pyproject.toml` under `[tool.pytest.ini_options]` (or wherever pytest config lives):

```toml
markers = [
    "slow: tests that load large ML models or do full-system runs",
    "unit: unit tests",
    "integration: integration tests",
]
```

Keep existing markers; add `slow` to the list.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit/test_capabilities_embeddings.py -v`
Expected: fast tests (cosine_similarity) pass instantly. Slow tests take 30-60s on first run (downloads model to `~/.cache/torch/sentence_transformers/`), then pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/capabilities/embeddings.py tests/unit/test_capabilities_embeddings.py pyproject.toml
git commit -m "feat(capabilities): add embedding helper with all-MiniLM-L6-v2"
```

---

## Task 6: CapabilityRegistry — CRUD methods

**Files:**
- Create: `src/donna/capabilities/registry.py`
- Create: `tests/unit/test_capabilities_registry.py`

- [ ] **Step 1: Write failing test for CRUD**

Create `tests/unit/test_capabilities_registry.py`:

```python
import pytest
import aiosqlite
from pathlib import Path

from donna.capabilities.registry import CapabilityRegistry, CapabilityInput


@pytest.fixture
async def registry(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    # Create the capability table with the same schema as the migration.
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            input_schema TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            default_output_shape TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            embedding BLOB,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            notes TEXT
        );
        CREATE INDEX ix_capability_status ON capability(status);
        CREATE INDEX ix_capability_trigger_type ON capability(trigger_type);
    """)
    await conn.commit()
    reg = CapabilityRegistry(conn)
    yield reg
    await conn.close()


async def test_register_and_get_by_name(registry):
    cap = await registry.register(CapabilityInput(
        name="product_watch",
        description="Monitor a product URL for price or availability changes",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        trigger_type="on_schedule",
    ), created_by="seed")
    assert cap.name == "product_watch"
    assert cap.status == "active"

    fetched = await registry.get_by_name("product_watch")
    assert fetched is not None
    assert fetched.name == "product_watch"
    assert fetched.input_schema["properties"]["url"]["type"] == "string"


async def test_get_by_name_returns_none_for_missing(registry):
    assert await registry.get_by_name("nope") is None


async def test_list_all(registry):
    for name in ["a", "b", "c"]:
        await registry.register(CapabilityInput(
            name=name,
            description=f"cap {name}",
            input_schema={},
            trigger_type="on_message",
        ), created_by="seed")
    caps = await registry.list_all()
    assert len(caps) == 3
    assert {c.name for c in caps} == {"a", "b", "c"}


async def test_register_duplicate_name_raises(registry):
    await registry.register(CapabilityInput(
        name="dup",
        description="first",
        input_schema={},
        trigger_type="on_message",
    ), created_by="seed")
    with pytest.raises(ValueError, match="already exists"):
        await registry.register(CapabilityInput(
            name="dup",
            description="second",
            input_schema={},
            trigger_type="on_message",
        ), created_by="seed")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_capabilities_registry.py -v`
Expected: `ImportError: cannot import name 'CapabilityRegistry' from 'donna.capabilities.registry'`

- [ ] **Step 3: Create the registry module (CRUD only for now)**

Create `src/donna/capabilities/registry.py`:

```python
"""CapabilityRegistry — CRUD and retrieval for the capability table.

See docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md §6.1
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import structlog
import uuid6

from donna.capabilities.models import (
    SELECT_CAPABILITY,
    CapabilityRow,
    row_to_capability,
)

logger = structlog.get_logger()


@dataclass(slots=True)
class CapabilityInput:
    """Input payload for registering a new capability."""

    name: str
    description: str
    input_schema: dict
    trigger_type: str  # on_message | on_schedule | on_manual
    default_output_shape: dict | None = None
    notes: str | None = None


class CapabilityRegistry:
    """CRUD and retrieval for user-facing capabilities."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def register(
        self,
        payload: CapabilityInput,
        created_by: str,
        status: str = "active",
    ) -> CapabilityRow:
        """Insert a new capability row.

        Raises ValueError if a capability with the same name already exists.
        """
        existing = await self.get_by_name(payload.name)
        if existing is not None:
            raise ValueError(f"Capability '{payload.name}' already exists")

        cap_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc)

        await self._conn.execute(
            f"""
            INSERT INTO capability ({SELECT_CAPABILITY})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cap_id,
                payload.name,
                payload.description,
                json.dumps(payload.input_schema),
                payload.trigger_type,
                json.dumps(payload.default_output_shape) if payload.default_output_shape else None,
                status,
                None,  # embedding added in Task 7
                now.isoformat(),
                created_by,
                payload.notes,
            ),
        )
        await self._conn.commit()

        logger.info(
            "capability_registered",
            capability_id=cap_id,
            name=payload.name,
            status=status,
            created_by=created_by,
        )

        result = await self.get_by_name(payload.name)
        assert result is not None
        return result

    async def get_by_name(self, name: str) -> CapabilityRow | None:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_CAPABILITY} FROM capability WHERE name = ?",
            (name,),
        )
        row = await cursor.fetchone()
        return row_to_capability(row) if row else None

    async def list_all(self, status: str | None = None, limit: int = 500) -> list[CapabilityRow]:
        if status is None:
            cursor = await self._conn.execute(
                f"SELECT {SELECT_CAPABILITY} FROM capability ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        else:
            cursor = await self._conn.execute(
                f"SELECT {SELECT_CAPABILITY} FROM capability WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        rows = await cursor.fetchall()
        return [row_to_capability(r) for r in rows]

    async def update_status(self, name: str, status: str) -> None:
        """Change a capability's status (e.g., pending_review → active)."""
        await self._conn.execute(
            "UPDATE capability SET status = ? WHERE name = ?",
            (status, name),
        )
        await self._conn.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_capabilities_registry.py -v`
Expected: all four tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/capabilities/registry.py tests/unit/test_capabilities_registry.py
git commit -m "feat(capabilities): add CapabilityRegistry CRUD methods"
```

---

## Task 7: CapabilityRegistry — semantic search with embedding generation

**Files:**
- Modify: `src/donna/capabilities/registry.py`
- Modify: `tests/unit/test_capabilities_registry.py`

- [ ] **Step 1: Write failing test for semantic search**

Append to `tests/unit/test_capabilities_registry.py`:

```python
@pytest.mark.slow
async def test_semantic_search_returns_ranked_matches(registry):
    await registry.register(CapabilityInput(
        name="product_watch",
        description="Monitor a product URL for price or availability changes",
        input_schema={},
        trigger_type="on_schedule",
    ), created_by="seed")

    await registry.register(CapabilityInput(
        name="news_check",
        description="Fetch recent news articles about a topic",
        input_schema={},
        trigger_type="on_schedule",
    ), created_by="seed")

    await registry.register(CapabilityInput(
        name="parse_task",
        description="Extract structured task fields from a natural language message",
        input_schema={},
        trigger_type="on_message",
    ), created_by="seed")

    results = await registry.semantic_search("watch this shirt for a price drop", k=3)
    assert len(results) == 3
    # product_watch should rank first
    assert results[0][0].name == "product_watch"
    # Confidence should be a float in [-1, 1]
    assert -1.0 <= results[0][1] <= 1.0


@pytest.mark.slow
async def test_register_stores_embedding(registry):
    cap = await registry.register(CapabilityInput(
        name="product_watch",
        description="Monitor a product URL for price changes",
        input_schema={},
        trigger_type="on_schedule",
    ), created_by="seed")
    assert cap.embedding is not None
    assert len(cap.embedding) == 384 * 4  # 384 float32 values
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_capabilities_registry.py -v -m slow`
Expected: `AttributeError: 'CapabilityRegistry' object has no attribute 'semantic_search'`

- [ ] **Step 3: Extend the registry with embedding generation and semantic search**

Modify `src/donna/capabilities/registry.py`:

Change the imports to include:

```python
from donna.capabilities.embeddings import (
    bytes_to_embedding,
    cosine_similarity,
    embed_text,
    embedding_to_bytes,
)
```

Inside `register()`, after computing `cap_id` and `now`, compute the embedding before the INSERT:

```python
        cap_id = str(uuid6.uuid7())
        now = datetime.now(timezone.utc)

        embedding_text = _embedding_text(payload.name, payload.description, payload.input_schema)
        embedding_blob = embedding_to_bytes(embed_text(embedding_text))
```

Replace the INSERT's `None,  # embedding added in Task 7` with `embedding_blob`.

Add a new helper at module level:

```python
def _embedding_text(name: str, description: str, input_schema: dict) -> str:
    """Build the text that gets embedded for semantic search.

    Combines name, description, and a flattened rendering of input schema
    field names so that capabilities with similar inputs cluster together.
    """
    field_names = list(input_schema.get("properties", {}).keys())
    field_part = " ".join(field_names) if field_names else ""
    return f"{name}. {description}. Inputs: {field_part}".strip()
```

Add the semantic search method to the class:

```python
    async def semantic_search(
        self, query: str, k: int = 5, status: str = "active"
    ) -> list[tuple[CapabilityRow, float]]:
        """Return top-k capabilities ranked by cosine similarity to query.

        Capabilities without embeddings are skipped. Results sorted by
        similarity descending; lower-ranked matches may have negative scores
        for unrelated queries.
        """
        query_vec = embed_text(query)
        caps = await self.list_all(status=status, limit=1000)

        scored: list[tuple[CapabilityRow, float]] = []
        for cap in caps:
            if cap.embedding is None:
                continue
            cap_vec = bytes_to_embedding(cap.embedding)
            score = cosine_similarity(query_vec, cap_vec)
            scored.append((cap, score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_capabilities_registry.py -v`
Expected: fast tests still pass; slow tests now also pass. First run is slow (model load); subsequent runs are fast.

- [ ] **Step 5: Commit**

```bash
git add src/donna/capabilities/registry.py tests/unit/test_capabilities_registry.py
git commit -m "feat(capabilities): add semantic_search with embedding generation"
```

---

## Task 8: Post-creation audit for similar capabilities

**Files:**
- Modify: `src/donna/capabilities/registry.py`
- Modify: `tests/unit/test_capabilities_registry.py`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_capabilities_registry.py`:

```python
@pytest.mark.slow
async def test_register_flags_similar_capability_for_review(registry):
    await registry.register(CapabilityInput(
        name="product_price_watch",
        description="Monitor a product URL for price drops and availability",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        trigger_type="on_schedule",
    ), created_by="seed")

    # Register a near-duplicate with same intent but different name.
    flagged = await registry.register(CapabilityInput(
        name="watch_product_price",
        description="Watch a product URL for price changes and stock availability",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        trigger_type="on_schedule",
    ), created_by="claude")

    assert flagged.status == "pending_review"

    # And the flagged capability should not appear in default semantic_search.
    results = await registry.semantic_search("monitor product price", k=5)
    names = [cap.name for cap, _ in results]
    assert "watch_product_price" not in names
    assert "product_price_watch" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_capabilities_registry.py::test_register_flags_similar_capability_for_review -v`
Expected: fail — the registered capability has `status="active"` not `"pending_review"`.

- [ ] **Step 3: Implement the post-creation audit**

Modify `register()` in `src/donna/capabilities/registry.py`. Before the INSERT, after computing the embedding, add:

```python
        # Post-creation audit: if the new capability is semantically close to
        # an existing one, flag it for human review instead of activating.
        audit_status = await self._audit_for_duplicates(embedding_blob, status)
```

Replace the status field in the INSERT with `audit_status`.

Add the audit method to the class:

```python
    SIMILARITY_THRESHOLD = 0.80

    async def _audit_for_duplicates(
        self, new_embedding_blob: bytes, requested_status: str
    ) -> str:
        """Compare a new capability's embedding against existing ones.

        If cosine similarity to any existing active capability exceeds
        SIMILARITY_THRESHOLD, return 'pending_review'. Otherwise return
        the requested status.
        """
        if requested_status != "active":
            return requested_status

        new_vec = bytes_to_embedding(new_embedding_blob)
        existing = await self.list_all(status="active", limit=1000)

        for cap in existing:
            if cap.embedding is None:
                continue
            cap_vec = bytes_to_embedding(cap.embedding)
            sim = cosine_similarity(new_vec, cap_vec)
            if sim >= self.SIMILARITY_THRESHOLD:
                logger.warning(
                    "capability_post_creation_audit_flagged",
                    similar_to=cap.name,
                    similarity=sim,
                    threshold=self.SIMILARITY_THRESHOLD,
                )
                return "pending_review"

        return "active"
```

Ensure `semantic_search()` filters by `status` (it already does via the `status` parameter defaulting to `"active"`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_capabilities_registry.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/capabilities/registry.py tests/unit/test_capabilities_registry.py
git commit -m "feat(capabilities): add post-creation audit to flag similar capabilities"
```

---

## Task 9: CapabilityMatcher with confidence thresholds

**Files:**
- Create: `src/donna/capabilities/matcher.py`
- Create: `tests/unit/test_capabilities_matcher.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_capabilities_matcher.py`:

```python
from unittest.mock import AsyncMock

import pytest

from donna.capabilities.matcher import (
    CapabilityMatcher,
    MatchConfidence,
    MatchResult,
)
from donna.capabilities.models import CapabilityRow
from datetime import datetime, timezone


def _cap(name: str) -> CapabilityRow:
    return CapabilityRow(
        id="id-" + name,
        name=name,
        description="desc " + name,
        input_schema={},
        trigger_type="on_message",
        default_output_shape=None,
        status="active",
        embedding=None,
        created_at=datetime.now(timezone.utc),
        created_by="seed",
        notes=None,
    )


async def test_high_confidence_match():
    registry = AsyncMock()
    registry.semantic_search.return_value = [(_cap("product_watch"), 0.92)]
    matcher = CapabilityMatcher(registry)

    result = await matcher.match("monitor this shirt for sales")
    assert result.confidence == MatchConfidence.HIGH
    assert result.best_match is not None
    assert result.best_match.name == "product_watch"


async def test_medium_confidence_match():
    registry = AsyncMock()
    registry.semantic_search.return_value = [
        (_cap("news_check"), 0.55),
        (_cap("product_watch"), 0.40),
    ]
    matcher = CapabilityMatcher(registry)

    result = await matcher.match("keep tabs on current events")
    assert result.confidence == MatchConfidence.MEDIUM
    assert result.best_match.name == "news_check"


async def test_low_confidence_match():
    registry = AsyncMock()
    registry.semantic_search.return_value = [(_cap("irrelevant"), 0.2)]
    matcher = CapabilityMatcher(registry)

    result = await matcher.match("do something completely novel")
    assert result.confidence == MatchConfidence.LOW
    assert result.best_match is None


async def test_no_matches_returned():
    registry = AsyncMock()
    registry.semantic_search.return_value = []
    matcher = CapabilityMatcher(registry)

    result = await matcher.match("anything")
    assert result.confidence == MatchConfidence.LOW
    assert result.best_match is None


async def test_match_result_exposes_candidates():
    registry = AsyncMock()
    registry.semantic_search.return_value = [
        (_cap("a"), 0.8),
        (_cap("b"), 0.6),
    ]
    matcher = CapabilityMatcher(registry)

    result = await matcher.match("query")
    assert len(result.candidates) == 2
    assert result.candidates[0][1] == 0.8
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_capabilities_matcher.py -v`
Expected: `ModuleNotFoundError: No module named 'donna.capabilities.matcher'`

- [ ] **Step 3: Create the matcher module**

Create `src/donna/capabilities/matcher.py`:

```python
"""CapabilityMatcher — confidence-scoring wrapper over CapabilityRegistry.

Used by the challenger to decide whether an inbound message matches
an existing capability with high enough confidence to proceed.

Thresholds (HIGH ≥ 0.75, MEDIUM ≥ 0.40, LOW < 0.40) are starter values
and should be tuned based on production match-confidence distributions.
See spec §6.7.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

import structlog

from donna.capabilities.models import CapabilityRow
from donna.capabilities.registry import CapabilityRegistry

logger = structlog.get_logger()

# Tunable thresholds. Should move to config in Phase 3.
HIGH_CONFIDENCE_THRESHOLD = 0.75
MEDIUM_CONFIDENCE_THRESHOLD = 0.40


class MatchConfidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(slots=True)
class MatchResult:
    confidence: MatchConfidence
    best_match: CapabilityRow | None
    best_score: float
    candidates: list[tuple[CapabilityRow, float]] = field(default_factory=list)


class CapabilityMatcher:
    """Wraps CapabilityRegistry.semantic_search with confidence scoring."""

    def __init__(self, registry: CapabilityRegistry, k: int = 5) -> None:
        self._registry = registry
        self._k = k

    async def match(self, query: str) -> MatchResult:
        candidates = await self._registry.semantic_search(query, k=self._k)

        if not candidates:
            return MatchResult(
                confidence=MatchConfidence.LOW,
                best_match=None,
                best_score=0.0,
                candidates=[],
            )

        best_cap, best_score = candidates[0]
        confidence = self._classify_confidence(best_score)

        logger.info(
            "capability_match",
            query_preview=query[:80],
            best_match=best_cap.name,
            best_score=best_score,
            confidence=confidence.value,
            candidate_count=len(candidates),
        )

        return MatchResult(
            confidence=confidence,
            best_match=best_cap if confidence != MatchConfidence.LOW else None,
            best_score=best_score,
            candidates=candidates,
        )

    @staticmethod
    def _classify_confidence(score: float) -> MatchConfidence:
        if score >= HIGH_CONFIDENCE_THRESHOLD:
            return MatchConfidence.HIGH
        if score >= MEDIUM_CONFIDENCE_THRESHOLD:
            return MatchConfidence.MEDIUM
        return MatchConfidence.LOW
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_capabilities_matcher.py -v`
Expected: all five tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/capabilities/matcher.py tests/unit/test_capabilities_matcher.py
git commit -m "feat(capabilities): add CapabilityMatcher with confidence thresholds"
```

---

## Task 10: StateObject and schema validation helpers

**Files:**
- Create: `src/donna/skills/__init__.py`
- Create: `src/donna/skills/state.py`
- Create: `src/donna/skills/validation.py`
- Create: `tests/unit/test_skills_state.py`
- Create: `tests/unit/test_skills_validation.py`

- [ ] **Step 1: Write failing test for StateObject**

Create `tests/unit/test_skills_state.py`:

```python
import pytest

from donna.skills.state import StateObject


def test_state_object_set_and_get():
    state = StateObject()
    state["step1"] = {"value": 42}
    assert state["step1"] == {"value": 42}


def test_state_object_contains():
    state = StateObject()
    state["foo"] = "bar"
    assert "foo" in state
    assert "baz" not in state


def test_state_object_serialize_and_restore():
    state = StateObject()
    state["step1"] = {"a": 1}
    state["step2"] = {"b": "hello"}

    data = state.to_dict()
    assert data == {"step1": {"a": 1}, "step2": {"b": "hello"}}

    restored = StateObject.from_dict(data)
    assert restored["step1"] == {"a": 1}
    assert restored["step2"] == {"b": "hello"}


def test_state_object_iter_step_names():
    state = StateObject()
    state["a"] = 1
    state["b"] = 2
    assert sorted(state.step_names()) == ["a", "b"]


def test_state_object_rejects_non_dict_step_output():
    state = StateObject()
    with pytest.raises(TypeError, match="must be a dict"):
        state["step"] = "not a dict"
```

- [ ] **Step 2: Write failing test for schema validation**

Create `tests/unit/test_skills_validation.py`:

```python
import pytest

from donna.skills.validation import SchemaValidationError, validate_output


def test_validate_output_passes_valid_input():
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }
    validate_output({"value": 42}, schema)  # Should not raise


def test_validate_output_rejects_missing_required():
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }
    with pytest.raises(SchemaValidationError):
        validate_output({}, schema)


def test_validate_output_rejects_wrong_type():
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }
    with pytest.raises(SchemaValidationError):
        validate_output({"value": "not an int"}, schema)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_skills_state.py tests/unit/test_skills_validation.py -v`
Expected: `ModuleNotFoundError: No module named 'donna.skills'`

- [ ] **Step 4: Create the skills package**

Create `src/donna/skills/__init__.py`:

```python
"""Skill system runtime — executor, state, validation, triage (later).

See docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md
"""

from donna.skills.state import StateObject
from donna.skills.validation import SchemaValidationError, validate_output

__all__ = ["StateObject", "SchemaValidationError", "validate_output"]
```

- [ ] **Step 5: Create `state.py`**

Create `src/donna/skills/state.py`:

```python
"""StateObject — typed container for cross-step data in a skill run.

Each skill step writes its structured output to the state object under
its step name. Later steps read from it via Jinja template rendering
when their prompt is constructed.
"""

from __future__ import annotations

from typing import Any


class StateObject:
    """Typed dict wrapper for skill run state.

    Keys are step names; values are the structured outputs of those steps
    (which must be JSON-serializable dicts).
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise TypeError(
                f"StateObject values must be a dict, got {type(value).__name__}"
            )
        self._data[key] = value

    def __getitem__(self, key: str) -> dict[str, Any]:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def step_names(self) -> list[str]:
        return list(self._data.keys())

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Return a shallow copy for JSON serialization."""
        return dict(self._data)

    @classmethod
    def from_dict(cls, data: dict[str, dict[str, Any]]) -> StateObject:
        state = cls()
        for k, v in data.items():
            state[k] = v
        return state
```

- [ ] **Step 6: Create `validation.py`**

Create `src/donna/skills/validation.py`:

```python
"""JSON schema validation for skill step outputs."""

from __future__ import annotations

from typing import Any

import jsonschema


class SchemaValidationError(Exception):
    """Raised when a skill step's output does not match its declared schema."""


def validate_output(output: Any, schema: dict) -> None:
    """Validate `output` against `schema`. Raises SchemaValidationError on mismatch."""
    try:
        jsonschema.validate(instance=output, schema=schema)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(
            f"Output failed schema validation at {'.'.join(str(p) for p in exc.path)}: {exc.message}"
        ) from exc
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/unit/test_skills_state.py tests/unit/test_skills_validation.py -v`
Expected: all 8 tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/donna/skills/__init__.py src/donna/skills/state.py src/donna/skills/validation.py \
        tests/unit/test_skills_state.py tests/unit/test_skills_validation.py
git commit -m "feat(skills): add StateObject and JSON schema validation helper"
```

---

## Task 11: Skill dataclasses and row mappers

**Files:**
- Create: `src/donna/skills/models.py`
- Create: `tests/unit/test_skills_models.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_skills_models.py`:

```python
from datetime import datetime, timezone

from donna.skills.models import SkillRow, SkillVersionRow, row_to_skill, row_to_skill_version


def test_skill_row_basic():
    s = SkillRow(
        id="s1",
        capability_name="product_watch",
        current_version_id="v1",
        state="sandbox",
        requires_human_gate=False,
        baseline_agreement=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert s.state == "sandbox"


def test_row_to_skill_version_parses_json():
    raw = (
        "v1",
        "s1",
        1,
        "yaml: content",
        '{"step_a": "markdown"}',
        '{"step_a": {"type": "object"}}',
        "claude",
        "initial version",
        "2026-04-15T00:00:00+00:00",
    )
    version = row_to_skill_version(raw)
    assert version.version_number == 1
    assert version.step_content == {"step_a": "markdown"}
    assert version.output_schemas == {"step_a": {"type": "object"}}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_skills_models.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create `src/donna/skills/models.py`**

```python
"""Skill and SkillVersion dataclasses + row mappers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

SKILL_COLUMNS = (
    "id",
    "capability_name",
    "current_version_id",
    "state",
    "requires_human_gate",
    "baseline_agreement",
    "created_at",
    "updated_at",
)
SELECT_SKILL = ", ".join(SKILL_COLUMNS)

SKILL_VERSION_COLUMNS = (
    "id",
    "skill_id",
    "version_number",
    "yaml_backbone",
    "step_content",
    "output_schemas",
    "created_by",
    "changelog",
    "created_at",
)
SELECT_SKILL_VERSION = ", ".join(SKILL_VERSION_COLUMNS)


@dataclass(slots=True)
class SkillRow:
    id: str
    capability_name: str
    current_version_id: str | None
    state: str
    requires_human_gate: bool
    baseline_agreement: float | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class SkillVersionRow:
    id: str
    skill_id: str
    version_number: int
    yaml_backbone: str
    step_content: dict
    output_schemas: dict
    created_by: str
    changelog: str | None
    created_at: datetime


def row_to_skill(row: tuple) -> SkillRow:
    return SkillRow(
        id=row[0],
        capability_name=row[1],
        current_version_id=row[2],
        state=row[3],
        requires_human_gate=bool(row[4]),
        baseline_agreement=row[5],
        created_at=_parse_dt(row[6]),
        updated_at=_parse_dt(row[7]),
    )


def row_to_skill_version(row: tuple) -> SkillVersionRow:
    return SkillVersionRow(
        id=row[0],
        skill_id=row[1],
        version_number=row[2],
        yaml_backbone=row[3],
        step_content=_parse_json(row[4]),
        output_schemas=_parse_json(row[5]),
        created_by=row[6],
        changelog=row[7],
        created_at=_parse_dt(row[8]),
    )


def _parse_json(value: str | dict | None) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_skills_models.py -v`
Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/models.py tests/unit/test_skills_models.py
git commit -m "feat(skills): add SkillRow and SkillVersionRow dataclasses"
```

---

## Task 12: Seed skill files — parse_task, dedup_check, classify_priority

**Files:**
- Create: `skills/parse_task/skill.yaml`, `steps/extract.md`, `schemas/extract_v1.json`
- Create: `skills/dedup_check/skill.yaml`, `steps/compare.md`, `schemas/compare_v1.json`
- Create: `skills/classify_priority/skill.yaml`, `steps/classify.md`, `schemas/classify_v1.json`

- [ ] **Step 1: Create `skills/parse_task/skill.yaml`**

```yaml
capability_name: parse_task
version: 1
description: |
  Extract structured task fields from a natural language message.
  This is the primary entry point for turning Discord messages into
  task rows.

inputs:
  schema:
    type: object
    properties:
      raw_text:
        type: string
      user_id:
        type: string
    required: [raw_text]

steps:
  - name: extract
    kind: llm
    prompt: steps/extract.md
    output_schema: schemas/extract_v1.json

final_output: "{{ state.extract }}"
```

- [ ] **Step 2: Create `skills/parse_task/steps/extract.md`**

```markdown
You are Donna's task parser. Extract structured task fields from the user's
message. Be sharp and concise. Do not invent information that isn't present.

User message:
{{ inputs.raw_text }}

Return a JSON object with the following fields:
- title: a short summary of the task (required)
- description: any additional detail from the message, or empty string
- domain: one of "work", "personal", "household", or "unknown"
- priority: integer 1-5 (5 = highest); infer from urgency cues, default 2
- estimated_duration_minutes: integer; best guess, default 30
- deadline: ISO 8601 datetime if explicit, otherwise null
- confidence: your confidence in this parse, 0.0-1.0
```

- [ ] **Step 3: Create `skills/parse_task/schemas/extract_v1.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "title": {"type": "string", "minLength": 1},
    "description": {"type": "string"},
    "domain": {"type": "string", "enum": ["work", "personal", "household", "unknown"]},
    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
    "estimated_duration_minutes": {"type": "integer", "minimum": 1},
    "deadline": {"type": ["string", "null"]},
    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "escalate": {
      "type": "object",
      "properties": {"reason": {"type": "string"}}
    }
  },
  "required": ["title", "domain", "priority", "estimated_duration_minutes", "confidence"],
  "additionalProperties": false
}
```

- [ ] **Step 4: Create `skills/dedup_check/skill.yaml`**

```yaml
capability_name: dedup_check
version: 1
description: |
  Determine whether two task candidates represent the same work item.
  Used to avoid creating duplicates when parsing forwarded messages or
  inputs received through multiple channels.

inputs:
  schema:
    type: object
    properties:
      task_a:
        type: object
      task_b:
        type: object
    required: [task_a, task_b]

steps:
  - name: compare
    kind: llm
    prompt: steps/compare.md
    output_schema: schemas/compare_v1.json

final_output: "{{ state.compare }}"
```

- [ ] **Step 5: Create `skills/dedup_check/steps/compare.md`**

```markdown
You are Donna's deduplicator. Decide whether the two task candidates below
represent the same work item, are related, or are distinct.

Task A:
{{ inputs.task_a | tojson }}

Task B:
{{ inputs.task_b | tojson }}

Return a JSON object with:
- relationship: one of "same", "related", "different"
- reason: one short sentence explaining your decision
- confidence: 0.0-1.0
```

- [ ] **Step 6: Create `skills/dedup_check/schemas/compare_v1.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "relationship": {"type": "string", "enum": ["same", "related", "different"]},
    "reason": {"type": "string"},
    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "escalate": {
      "type": "object",
      "properties": {"reason": {"type": "string"}}
    }
  },
  "required": ["relationship", "reason", "confidence"],
  "additionalProperties": false
}
```

- [ ] **Step 7: Create `skills/classify_priority/skill.yaml`**

```yaml
capability_name: classify_priority
version: 1
description: |
  Assign a priority (1-5) to a task based on its content, deadline,
  and any context from related tasks.

inputs:
  schema:
    type: object
    properties:
      title:
        type: string
      description:
        type: string
      deadline:
        type: ["string", "null"]
    required: [title]

steps:
  - name: classify
    kind: llm
    prompt: steps/classify.md
    output_schema: schemas/classify_v1.json

final_output: "{{ state.classify }}"
```

- [ ] **Step 8: Create `skills/classify_priority/steps/classify.md`**

```markdown
You are Donna's priority classifier. Assign a priority level from 1 (low)
to 5 (urgent) based on the task below.

Title: {{ inputs.title }}
Description: {{ inputs.description | default("(none)") }}
Deadline: {{ inputs.deadline | default("(none)") }}

Consider:
- Explicit urgency cues ("urgent", "ASAP", "today")
- Deadline proximity
- Domain (work tasks default higher than personal unless marked otherwise)

Return a JSON object with:
- priority: integer 1-5
- rationale: one short sentence
- confidence: 0.0-1.0
```

- [ ] **Step 9: Create `skills/classify_priority/schemas/classify_v1.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
    "rationale": {"type": "string"},
    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "escalate": {
      "type": "object",
      "properties": {"reason": {"type": "string"}}
    }
  },
  "required": ["priority", "rationale", "confidence"],
  "additionalProperties": false
}
```

- [ ] **Step 10: Commit**

```bash
git add skills/
git commit -m "feat(skills): add seed skill files for parse_task, dedup_check, classify_priority"
```

---

## Task 13: Skill loader — parse filesystem YAML into skill + skill_version rows

**Files:**
- Create: `src/donna/skills/loader.py`
- Create: `tests/unit/test_skills_loader.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_skills_loader.py`:

```python
import json
from pathlib import Path

import aiosqlite
import pytest

from donna.skills.loader import load_skill_from_directory, SkillLoadError


@pytest.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT UNIQUE, description TEXT,
            input_schema TEXT, trigger_type TEXT, default_output_shape TEXT,
            status TEXT NOT NULL DEFAULT 'active', embedding BLOB,
            created_at TEXT, created_by TEXT, notes TEXT
        );
        CREATE TABLE skill (
            id TEXT PRIMARY KEY, capability_name TEXT UNIQUE,
            current_version_id TEXT, state TEXT, requires_human_gate INTEGER,
            baseline_agreement REAL, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE skill_version (
            id TEXT PRIMARY KEY, skill_id TEXT, version_number INTEGER,
            yaml_backbone TEXT, step_content TEXT, output_schemas TEXT,
            created_by TEXT, changelog TEXT, created_at TEXT
        );
    """)
    # Seed the capability that the skill references.
    await conn.execute("""
        INSERT INTO capability (id, name, description, input_schema, trigger_type, status, created_at, created_by)
        VALUES ('c1', 'parse_task', 'parse task', '{}', 'on_message', 'active', '2026-04-15T00:00:00+00:00', 'seed')
    """)
    await conn.commit()
    yield conn
    await conn.close()


async def test_load_skill_from_directory(db, tmp_path: Path):
    skill_dir = tmp_path / "skills" / "parse_task"
    skill_dir.mkdir(parents=True)

    (skill_dir / "skill.yaml").write_text("""
capability_name: parse_task
version: 1
description: |
  Extract task fields.
inputs:
  schema:
    type: object
    properties: {}
steps:
  - name: extract
    kind: llm
    prompt: steps/extract.md
    output_schema: schemas/extract_v1.json
final_output: "{{ state.extract }}"
""")

    (skill_dir / "steps").mkdir()
    (skill_dir / "steps" / "extract.md").write_text("Extract the task fields.")

    (skill_dir / "schemas").mkdir()
    (skill_dir / "schemas" / "extract_v1.json").write_text(
        '{"type": "object", "properties": {"title": {"type": "string"}}}'
    )

    skill_id = await load_skill_from_directory(skill_dir, db, initial_state="sandbox")

    cursor = await db.execute("SELECT capability_name, state FROM skill WHERE id = ?", (skill_id,))
    row = await cursor.fetchone()
    assert row == ("parse_task", "sandbox")

    cursor = await db.execute(
        "SELECT version_number, step_content, output_schemas FROM skill_version WHERE skill_id = ?",
        (skill_id,),
    )
    vrow = await cursor.fetchone()
    assert vrow[0] == 1
    assert json.loads(vrow[1]) == {"extract": "Extract the task fields."}
    assert json.loads(vrow[2]) == {"extract": {"type": "object", "properties": {"title": {"type": "string"}}}}


async def test_load_skill_missing_capability_raises(db, tmp_path: Path):
    skill_dir = tmp_path / "skills" / "nonexistent"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text("""
capability_name: nonexistent
version: 1
description: x
inputs:
  schema: {type: object}
steps: []
final_output: "{}"
""")
    (skill_dir / "steps").mkdir()
    (skill_dir / "schemas").mkdir()

    with pytest.raises(SkillLoadError, match="capability 'nonexistent' not found"):
        await load_skill_from_directory(skill_dir, db, initial_state="sandbox")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_skills_loader.py -v`
Expected: `ModuleNotFoundError: No module named 'donna.skills.loader'`

- [ ] **Step 3: Create the loader**

Create `src/donna/skills/loader.py`:

```python
"""Skill loader — parse filesystem YAML into skill + skill_version rows.

The filesystem format is authoritative for Phase 1 seed skills only.
Subsequent phases generate skills directly in the DB. The loader's job
is one-shot import: read the files, validate against the referenced
capability, and create the skill + skill_version rows.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import structlog
import uuid6
import yaml

from donna.skills.models import SELECT_SKILL, SELECT_SKILL_VERSION

logger = structlog.get_logger()


class SkillLoadError(Exception):
    """Raised when a skill file cannot be loaded."""


async def load_skill_from_directory(
    skill_dir: Path,
    conn: aiosqlite.Connection,
    initial_state: str = "sandbox",
) -> str:
    """Load a skill from a filesystem directory and insert it into the DB.

    The directory must contain:
      skill.yaml
      steps/<step_name>.md  (one per step declared in skill.yaml)
      schemas/<step_name>_v<N>.json  (one per llm step)

    Returns the newly-created skill.id.
    Raises SkillLoadError if the capability is not in the registry,
    if required files are missing, or if YAML parsing fails.
    """
    skill_yaml_path = skill_dir / "skill.yaml"
    if not skill_yaml_path.exists():
        raise SkillLoadError(f"skill.yaml not found in {skill_dir}")

    try:
        with open(skill_yaml_path) as f:
            skill_data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"Failed to parse {skill_yaml_path}: {exc}") from exc

    capability_name = skill_data.get("capability_name")
    if not capability_name:
        raise SkillLoadError(f"{skill_yaml_path} missing capability_name field")

    # Verify capability exists.
    cursor = await conn.execute(
        "SELECT name FROM capability WHERE name = ?", (capability_name,)
    )
    if not await cursor.fetchone():
        raise SkillLoadError(
            f"capability '{capability_name}' not found in registry; "
            f"seed the capability before loading this skill"
        )

    # Read step content and schemas for each llm step.
    step_content: dict[str, str] = {}
    output_schemas: dict[str, dict] = {}
    for step in skill_data.get("steps", []):
        if step.get("kind") != "llm":
            continue
        name = step["name"]
        prompt_path = skill_dir / step["prompt"]
        schema_path = skill_dir / step["output_schema"]

        if not prompt_path.exists():
            raise SkillLoadError(f"prompt file not found: {prompt_path}")
        if not schema_path.exists():
            raise SkillLoadError(f"schema file not found: {schema_path}")

        step_content[name] = prompt_path.read_text()
        with open(schema_path) as f:
            output_schemas[name] = json.load(f)

    # Insert skill row.
    skill_id = str(uuid6.uuid7())
    version_id = str(uuid6.uuid7())
    now = datetime.now(timezone.utc).isoformat()

    await conn.execute(
        f"""
        INSERT INTO skill ({SELECT_SKILL})
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            skill_id,
            capability_name,
            version_id,
            initial_state,
            0,  # requires_human_gate
            None,  # baseline_agreement
            now,
            now,
        ),
    )

    # Insert skill_version row.
    yaml_backbone = skill_yaml_path.read_text()
    await conn.execute(
        f"""
        INSERT INTO skill_version ({SELECT_SKILL_VERSION})
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            skill_id,
            skill_data.get("version", 1),
            yaml_backbone,
            json.dumps(step_content),
            json.dumps(output_schemas),
            "human",
            "Initial seed version",
            now,
        ),
    )

    await conn.commit()

    logger.info(
        "skill_loaded",
        skill_id=skill_id,
        capability_name=capability_name,
        version_number=skill_data.get("version", 1),
        initial_state=initial_state,
    )

    return skill_id
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_skills_loader.py -v`
Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/loader.py tests/unit/test_skills_loader.py
git commit -m "feat(skills): add filesystem skill loader for seed skills"
```

---

## Task 14: Minimal SkillExecutor — single-step llm skills only

**Files:**
- Create: `src/donna/skills/executor.py`
- Create: `tests/unit/test_skills_executor.py`

- [ ] **Step 1: Write failing test for executor**

Create `tests/unit/test_skills_executor.py`:

```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from donna.skills.executor import SkillExecutor, SkillRunResult
from donna.skills.models import SkillRow, SkillVersionRow


def make_skill_version(
    step_content: dict,
    output_schemas: dict,
) -> SkillVersionRow:
    return SkillVersionRow(
        id="v1",
        skill_id="s1",
        version_number=1,
        yaml_backbone="",
        step_content=step_content,
        output_schemas=output_schemas,
        created_by="seed",
        changelog=None,
        created_at=datetime.now(timezone.utc),
    )


def make_skill() -> SkillRow:
    return SkillRow(
        id="s1",
        capability_name="parse_task",
        current_version_id="v1",
        state="sandbox",
        requires_human_gate=False,
        baseline_agreement=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


async def test_executor_runs_single_step_skill():
    version = make_skill_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["title", "confidence"],
        }},
    )

    model_router = AsyncMock()
    model_router.complete.return_value = (
        {"title": "Draft Q2 review", "confidence": 0.9},
        AsyncMock(invocation_id="inv-1", latency_ms=100, tokens_in=50, tokens_out=20, cost_usd=0.0),
    )

    executor = SkillExecutor(model_router)
    result = await executor.execute(
        skill=make_skill(),
        version=version,
        inputs={"raw_text": "draft the Q2 review by Friday"},
        user_id="nick",
    )

    assert result.status == "succeeded"
    assert result.final_output == {"title": "Draft Q2 review", "confidence": 0.9}
    assert "extract" in result.state
    assert result.state["extract"]["title"] == "Draft Q2 review"


async def test_executor_handles_escalate_signal():
    version = make_skill_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "escalate": {"type": "object"},
            },
        }},
    )

    model_router = AsyncMock()
    model_router.complete.return_value = (
        {"escalate": {"reason": "insufficient info"}},
        AsyncMock(invocation_id="inv-2", latency_ms=80, tokens_in=50, tokens_out=10, cost_usd=0.0),
    )

    executor = SkillExecutor(model_router)
    result = await executor.execute(
        skill=make_skill(),
        version=version,
        inputs={"raw_text": "???"},
        user_id="nick",
    )

    assert result.status == "escalated"
    assert result.escalation_reason == "insufficient info"


async def test_executor_fails_on_schema_validation_error():
    version = make_skill_version(
        step_content={"extract": "Extract: {{ inputs.raw_text }}"},
        output_schemas={"extract": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }},
    )

    model_router = AsyncMock()
    # Model returns an output missing the required field.
    model_router.complete.return_value = (
        {"not_title": "x"},
        AsyncMock(invocation_id="inv-3", latency_ms=50, tokens_in=20, tokens_out=5, cost_usd=0.0),
    )

    executor = SkillExecutor(model_router)
    result = await executor.execute(
        skill=make_skill(),
        version=version,
        inputs={"raw_text": "foo"},
        user_id="nick",
    )

    assert result.status == "failed"
    assert "title" in result.error
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_skills_executor.py -v`
Expected: `ImportError: cannot import name 'SkillExecutor'`

- [ ] **Step 3: Create `src/donna/skills/executor.py`**

```python
"""SkillExecutor — minimal single-step implementation for Phase 1.

Phase 1 supports only `llm`-kind steps with no tool dispatch and no DSL.
Phase 2 expands this to multi-step skills with tool invocations,
flow control primitives (for_each, retry, escalate), and triage.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import jinja2
import structlog
import yaml

from donna.skills.models import SkillRow, SkillVersionRow
from donna.skills.state import StateObject
from donna.skills.validation import SchemaValidationError, validate_output

logger = structlog.get_logger()


@dataclass(slots=True)
class SkillRunResult:
    status: str  # succeeded | failed | escalated
    final_output: Any = None
    state: dict[str, Any] = field(default_factory=dict)
    escalation_reason: str | None = None
    error: str | None = None
    invocation_ids: list[str] = field(default_factory=list)
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0


class SkillExecutor:
    """Executes a skill version against inputs.

    Phase 1 contract: single-step llm skills only. If a skill has
    multiple steps, only the first step is executed and a warning is
    logged. This is a deliberate guardrail so Phase 1 doesn't
    silently misexecute complex skills that will be supported in Phase 2.
    """

    def __init__(self, model_router: Any) -> None:
        self._router = model_router
        self._jinja_env = jinja2.Environment(
            autoescape=False,
            undefined=jinja2.StrictUndefined,
        )

    async def execute(
        self,
        skill: SkillRow,
        version: SkillVersionRow,
        inputs: dict,
        user_id: str,
    ) -> SkillRunResult:
        state = StateObject()
        start_time = time.monotonic()

        # Parse the YAML backbone to get the step list in order.
        try:
            backbone = yaml.safe_load(version.yaml_backbone) if version.yaml_backbone else {}
        except yaml.YAMLError as exc:
            return SkillRunResult(status="failed", error=f"yaml_parse: {exc}")

        steps = backbone.get("steps", [])
        if not steps:
            # Degenerate case: no steps declared. Treat as empty run.
            return SkillRunResult(status="succeeded", final_output={}, state={})

        if len(steps) > 1:
            logger.warning(
                "skill_executor_phase_1_multistep_skipped",
                skill_id=skill.id,
                step_count=len(steps),
                note="Phase 1 runs first step only; Phase 2 adds multi-step support",
            )

        step = steps[0]
        step_name = step["name"]
        step_kind = step.get("kind", "llm")

        if step_kind != "llm":
            return SkillRunResult(
                status="failed",
                error=f"Phase 1 only supports llm steps; got kind={step_kind}",
            )

        prompt_template = version.step_content.get(step_name, "")
        schema = version.output_schemas.get(step_name, {})

        # Render the prompt with inputs and state.
        try:
            template = self._jinja_env.from_string(prompt_template)
            rendered = template.render(inputs=inputs, state=state.to_dict())
        except jinja2.UndefinedError as exc:
            return SkillRunResult(
                status="failed",
                error=f"prompt_render: undefined variable: {exc}",
            )

        # Call the model router.
        try:
            output, meta = await self._router.complete(
                prompt=rendered,
                schema=schema,
                model_alias="local_parser",
                task_type=f"skill_step::{skill.capability_name}::{step_name}",
                user_id=user_id,
            )
        except Exception as exc:
            logger.exception(
                "skill_executor_model_call_failed",
                skill_id=skill.id,
                step_name=step_name,
            )
            return SkillRunResult(
                status="failed",
                error=f"model_call: {exc}",
            )

        total_latency_ms = int((time.monotonic() - start_time) * 1000)

        # Check for escalate signal.
        if isinstance(output, dict) and "escalate" in output:
            esc = output["escalate"]
            reason = esc.get("reason", "unspecified") if isinstance(esc, dict) else str(esc)
            logger.info(
                "skill_step_escalated",
                skill_id=skill.id,
                step_name=step_name,
                reason=reason,
            )
            return SkillRunResult(
                status="escalated",
                state=state.to_dict(),
                escalation_reason=reason,
                invocation_ids=[meta.invocation_id],
                total_latency_ms=total_latency_ms,
                total_cost_usd=meta.cost_usd,
            )

        # Validate output against schema.
        try:
            validate_output(output, schema)
        except SchemaValidationError as exc:
            logger.warning(
                "skill_step_schema_invalid",
                skill_id=skill.id,
                step_name=step_name,
                error=str(exc),
            )
            return SkillRunResult(
                status="failed",
                state=state.to_dict(),
                error=f"schema_validation: {exc}",
                invocation_ids=[meta.invocation_id],
                total_latency_ms=total_latency_ms,
                total_cost_usd=meta.cost_usd,
            )

        state[step_name] = output

        logger.info(
            "skill_step_completed",
            skill_id=skill.id,
            step_name=step_name,
            latency_ms=meta.latency_ms,
        )

        return SkillRunResult(
            status="succeeded",
            final_output=output,
            state=state.to_dict(),
            invocation_ids=[meta.invocation_id],
            total_latency_ms=total_latency_ms,
            total_cost_usd=meta.cost_usd,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_skills_executor.py -v`
Expected: all three tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/skills/executor.py tests/unit/test_skills_executor.py
git commit -m "feat(skills): add minimal single-step SkillExecutor"
```

---

## Task 15: ChallengerAgent — add `match_and_extract` method

**Files:**
- Modify: `src/donna/agents/challenger_agent.py`
- Create: `tests/unit/test_challenger_match_and_extract.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_challenger_match_and_extract.py`:

```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from donna.capabilities.matcher import MatchConfidence, MatchResult
from donna.capabilities.models import CapabilityRow
from donna.agents.challenger_agent import ChallengerAgent, ChallengerMatchResult


def _cap(name: str, schema: dict) -> CapabilityRow:
    return CapabilityRow(
        id="id",
        name=name,
        description="desc",
        input_schema=schema,
        trigger_type="on_message",
        default_output_shape=None,
        status="active",
        embedding=None,
        created_at=datetime.now(timezone.utc),
        created_by="seed",
        notes=None,
    )


async def test_high_confidence_match_with_complete_inputs():
    cap = _cap("parse_task", {
        "type": "object",
        "properties": {"raw_text": {"type": "string"}, "user_id": {"type": "string"}},
        "required": ["raw_text", "user_id"],
    })

    matcher = AsyncMock()
    matcher.match.return_value = MatchResult(
        confidence=MatchConfidence.HIGH,
        best_match=cap,
        best_score=0.9,
        candidates=[(cap, 0.9)],
    )
    extractor = AsyncMock()
    extractor.extract.return_value = {"raw_text": "draft the review", "user_id": "nick"}

    challenger = ChallengerAgent(matcher=matcher, input_extractor=extractor)
    result = await challenger.match_and_extract(
        user_message="draft the review",
        user_id="nick",
    )

    assert result.status == "ready"
    assert result.capability is not None
    assert result.capability.name == "parse_task"
    assert result.extracted_inputs == {"raw_text": "draft the review", "user_id": "nick"}
    assert result.missing_fields == []


async def test_high_confidence_match_with_missing_inputs():
    cap = _cap("product_watch", {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "target_size": {"type": "string"},
            "price_threshold_usd": {"type": "number"},
        },
        "required": ["url", "target_size", "price_threshold_usd"],
    })

    matcher = AsyncMock()
    matcher.match.return_value = MatchResult(
        confidence=MatchConfidence.HIGH,
        best_match=cap,
        best_score=0.85,
        candidates=[(cap, 0.85)],
    )
    extractor = AsyncMock()
    extractor.extract.return_value = {"url": "https://cos.com/shirt"}

    challenger = ChallengerAgent(matcher=matcher, input_extractor=extractor)
    result = await challenger.match_and_extract(
        user_message="watch this shirt",
        user_id="nick",
    )

    assert result.status == "needs_input"
    assert result.capability.name == "product_watch"
    assert sorted(result.missing_fields) == ["price_threshold_usd", "target_size"]
    assert result.clarifying_question is not None
    assert "target_size" in result.clarifying_question or "price" in result.clarifying_question.lower()


async def test_low_confidence_match_escalates():
    matcher = AsyncMock()
    matcher.match.return_value = MatchResult(
        confidence=MatchConfidence.LOW,
        best_match=None,
        best_score=0.2,
        candidates=[],
    )
    extractor = AsyncMock()

    challenger = ChallengerAgent(matcher=matcher, input_extractor=extractor)
    result = await challenger.match_and_extract(
        user_message="do something completely novel",
        user_id="nick",
    )

    assert result.status == "escalate_to_claude"
    assert result.capability is None
    extractor.extract.assert_not_called()
```

- [ ] **Step 2: Read the current challenger_agent.py and plan the additive refactor**

Open `src/donna/agents/challenger_agent.py`. The existing class has `execute()` and supporting properties; the plan adds `match_and_extract()` as a new public method without removing existing code. The old method remains functional so existing callers are not broken during the transition.

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/unit/test_challenger_match_and_extract.py -v`
Expected: `ImportError: cannot import name 'ChallengerMatchResult'`

- [ ] **Step 4: Add the new types and method**

At the top of `src/donna/agents/challenger_agent.py`, add imports:

```python
from dataclasses import dataclass, field
from typing import Any

from donna.capabilities.matcher import CapabilityMatcher, MatchConfidence
from donna.capabilities.models import CapabilityRow
```

Near the top of the file (before the class), add:

```python
@dataclass(slots=True)
class ChallengerMatchResult:
    """Result of ChallengerAgent.match_and_extract.

    status values:
      - ready: matched with confidence ≥ HIGH and all required inputs extracted
      - needs_input: matched with confidence ≥ HIGH but missing required fields;
        clarifying_question is populated for the user.
      - escalate_to_claude: match confidence is LOW; caller should route to Claude
        novelty judgment (Phase 1 stub: return claude_native).
      - ambiguous: match confidence is MEDIUM; caller should ask one disambiguation
        question. (Phase 1 treats this the same as needs_input with a
        disambiguation-style question; full treatment in Phase 3.)
    """
    status: str
    capability: CapabilityRow | None
    extracted_inputs: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    clarifying_question: str | None = None
    match_score: float = 0.0


class InputExtractor:
    """Protocol for input extraction; a concrete implementation lives in
    capabilities/ and uses the local LLM. For Phase 1 testing we inject
    an AsyncMock that returns a pre-shaped dict."""

    async def extract(
        self, user_message: str, schema: dict, user_id: str
    ) -> dict[str, Any]:
        raise NotImplementedError
```

Inside the `ChallengerAgent` class, add the constructor extension and the new method. If the existing class has no constructor, add one; if it does, extend it to accept optional `matcher` and `input_extractor` parameters that default to None (so existing callers that construct without them still work):

```python
    def __init__(
        self,
        *args,
        matcher: CapabilityMatcher | None = None,
        input_extractor: InputExtractor | None = None,
        **kwargs,
    ) -> None:
        # If the existing __init__ takes args, call super or preserve behavior.
        if hasattr(super(), "__init__"):
            try:
                super().__init__(*args, **kwargs)
            except TypeError:
                pass
        self._matcher = matcher
        self._input_extractor = input_extractor

    async def match_and_extract(
        self,
        user_message: str,
        user_id: str,
    ) -> ChallengerMatchResult:
        """Match a user message against the capability registry and
        extract structured inputs against the matched capability's schema.

        See spec §6.7.
        """
        assert self._matcher is not None, "CapabilityMatcher not configured"

        match = await self._matcher.match(user_message)

        if match.confidence == MatchConfidence.LOW:
            return ChallengerMatchResult(
                status="escalate_to_claude",
                capability=None,
                match_score=match.best_score,
            )

        cap = match.best_match
        assert cap is not None, "best_match must be set when confidence is not LOW"

        # Extract inputs. Phase 1 relies on the injected extractor.
        assert self._input_extractor is not None, "InputExtractor not configured"
        extracted = await self._input_extractor.extract(
            user_message=user_message,
            schema=cap.input_schema,
            user_id=user_id,
        )

        required = cap.input_schema.get("required", [])
        missing = [f for f in required if f not in extracted or extracted[f] in (None, "")]

        if missing:
            question = self._build_clarifying_question(cap, missing)
            status = "needs_input" if match.confidence == MatchConfidence.HIGH else "ambiguous"
            return ChallengerMatchResult(
                status=status,
                capability=cap,
                extracted_inputs=extracted,
                missing_fields=missing,
                clarifying_question=question,
                match_score=match.best_score,
            )

        return ChallengerMatchResult(
            status="ready",
            capability=cap,
            extracted_inputs=extracted,
            missing_fields=[],
            match_score=match.best_score,
        )

    def _build_clarifying_question(
        self, cap: CapabilityRow, missing: list[str]
    ) -> str:
        """Phase 1: simple templated question. Phase 3 can evolve this to
        use a local LLM call for persona-adjusted phrasing."""
        props = cap.input_schema.get("properties", {})
        field_descriptions = []
        for field in missing:
            desc = props.get(field, {}).get("description", field)
            field_descriptions.append(f"- {field}: {desc}")

        return (
            f"I need a bit more to act on this as a {cap.name}:\n"
            + "\n".join(field_descriptions)
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/unit/test_challenger_match_and_extract.py -v`
Expected: all three tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/agents/challenger_agent.py tests/unit/test_challenger_match_and_extract.py
git commit -m "feat(challenger): add match_and_extract method for skill-based routing"
```

---

## Task 15b: Concrete `LocalLLMInputExtractor`

**Files:**
- Create: `src/donna/capabilities/input_extractor.py`
- Create: `tests/unit/test_capabilities_input_extractor.py`

**Rationale:** Task 15 defines the `InputExtractor` protocol but Challenger tests use an `AsyncMock`. This task provides the real implementation that uses the local LLM via `ModelRouter.complete()` to extract inputs against a capability's JSON schema. Without it, R5 is verified only by mock, not by real code.

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_capabilities_input_extractor.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.capabilities.input_extractor import LocalLLMInputExtractor


async def test_extractor_returns_llm_output():
    router = AsyncMock()
    router.complete.return_value = (
        {"raw_text": "draft the review", "user_id": "nick"},
        MagicMock(invocation_id="inv-1"),
    )
    extractor = LocalLLMInputExtractor(router)

    result = await extractor.extract(
        user_message="draft the review",
        schema={
            "type": "object",
            "properties": {
                "raw_text": {"type": "string"},
                "user_id": {"type": "string"},
            },
            "required": ["raw_text", "user_id"],
        },
        user_id="nick",
    )
    assert result == {"raw_text": "draft the review", "user_id": "nick"}


async def test_extractor_returns_empty_dict_on_llm_failure():
    router = AsyncMock()
    router.complete.side_effect = Exception("model_unavailable")
    extractor = LocalLLMInputExtractor(router)

    result = await extractor.extract(
        user_message="anything",
        schema={"type": "object", "properties": {}, "required": []},
        user_id="nick",
    )
    assert result == {}


async def test_extractor_prompt_includes_schema_field_names():
    router = AsyncMock()
    router.complete.return_value = ({"url": "x"}, MagicMock(invocation_id="i"))
    extractor = LocalLLMInputExtractor(router)

    await extractor.extract(
        user_message="msg",
        schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "product URL"},
                "price_threshold_usd": {"type": "number", "description": "alert below"},
            },
            "required": ["url"],
        },
        user_id="nick",
    )

    # Inspect the prompt the router was called with.
    call_args = router.complete.call_args
    prompt = call_args.kwargs.get("prompt") or call_args.args[0]
    assert "url" in prompt
    assert "price_threshold_usd" in prompt
    assert "product URL" in prompt
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_capabilities_input_extractor.py -v`
Expected: `ModuleNotFoundError: No module named 'donna.capabilities.input_extractor'`

- [ ] **Step 3: Create the extractor**

Create `src/donna/capabilities/input_extractor.py`:

```python
"""LocalLLMInputExtractor — extracts structured inputs from free text
against a capability's JSON schema, using the local LLM via ModelRouter.

Part of the challenger flow: once a capability match is made, this
extractor populates the capability's input fields from the user's message.
Fields the LLM cannot confidently fill are left as null, which the
challenger then surfaces as missing fields for clarifying questions.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger()


class LocalLLMInputExtractor:
    """Extracts structured inputs using the local LLM with JSON-mode output."""

    def __init__(self, model_router: Any) -> None:
        self._router = model_router

    async def extract(
        self,
        user_message: str,
        schema: dict,
        user_id: str,
    ) -> dict[str, Any]:
        """Extract structured inputs from user_message against schema.

        Returns a dict matching the schema's properties. Fields the LLM
        cannot populate are set to null or omitted. On LLM failure returns
        an empty dict (caller treats all required fields as missing).
        """
        prompt = self._build_prompt(user_message, schema)

        try:
            output, _meta = await self._router.complete(
                prompt=prompt,
                schema=schema,
                model_alias="local_parser",
                task_type="capability_input_extraction",
                user_id=user_id,
            )
            if not isinstance(output, dict):
                logger.warning("input_extractor_unexpected_output_type", type=type(output).__name__)
                return {}
            return output
        except Exception as exc:
            logger.warning(
                "input_extractor_failed",
                error=str(exc),
                user_id=user_id,
            )
            return {}

    @staticmethod
    def _build_prompt(user_message: str, schema: dict) -> str:
        props = schema.get("properties", {})
        field_lines = []
        for field_name, field_def in props.items():
            desc = field_def.get("description", "")
            ftype = field_def.get("type", "any")
            field_lines.append(f"- {field_name} ({ftype}): {desc}".rstrip(": "))

        required = schema.get("required", [])
        required_str = ", ".join(required) if required else "(none)"
        field_block = "\n".join(field_lines) if field_lines else "(no fields declared)"

        return (
            "You are Donna's input extractor. Extract structured fields from "
            "the user message below against the schema. If a field cannot be "
            "determined from the message, leave it null or omit it — do not "
            "invent information.\n\n"
            f"User message:\n{user_message}\n\n"
            f"Fields to extract:\n{field_block}\n\n"
            f"Required fields: {required_str}\n\n"
            "Return a JSON object containing the extracted fields."
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_capabilities_input_extractor.py -v`
Expected: all three tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/capabilities/input_extractor.py tests/unit/test_capabilities_input_extractor.py
git commit -m "feat(capabilities): add LocalLLMInputExtractor for challenger flow"
```

---

## Task 16: Dispatcher — add skill-routing path behind feature flag

**Files:**
- Modify: `src/donna/orchestrator/dispatcher.py`
- Create: `tests/unit/test_dispatcher_skill_routing.py`

**Guiding principle:** Phase 1 MUST NOT change user-visible behavior. The new skill-routing path runs *in parallel* to the existing path. The existing path is still primary; the skill path's output is logged but not returned.

- [ ] **Step 1: Write failing test for the new parallel-skill path**

Create `tests/unit/test_dispatcher_skill_routing.py`:

```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from donna.capabilities.models import CapabilityRow
from donna.agents.challenger_agent import ChallengerMatchResult
from donna.orchestrator.dispatcher import AgentDispatcher


async def test_dispatcher_runs_skill_alongside_existing_flow():
    """When a task matches a capability with a sandbox skill, the dispatcher
    runs the skill in parallel to the existing claude-native flow. The user
    still gets the claude-native result; the skill run is logged."""

    cap = CapabilityRow(
        id="c1", name="parse_task", description="x", input_schema={"type": "object"},
        trigger_type="on_message", default_output_shape=None, status="active",
        embedding=None, created_at=datetime.now(timezone.utc), created_by="seed", notes=None,
    )

    challenger = AsyncMock()
    challenger.match_and_extract.return_value = ChallengerMatchResult(
        status="ready",
        capability=cap,
        extracted_inputs={"raw_text": "draft the review"},
    )

    # Mock the skill database to return a fake skill + version for parse_task.
    skill_database = AsyncMock()
    skill_row = MagicMock(id="s1", capability_name="parse_task", current_version_id="v1", state="sandbox")
    version_row = MagicMock(id="v1", version_number=1)
    skill_database.get_by_capability.return_value = skill_row
    skill_database.get_version.return_value = version_row

    skill_executor = AsyncMock()
    skill_executor.execute.return_value = MagicMock(status="succeeded", final_output={"title": "x"})

    # Existing claude path is mocked as a function that returns a placeholder.
    legacy_agent_path = AsyncMock()
    legacy_agent_path.return_value = {"task_id": "t1", "path": "claude_native"}

    dispatcher = AgentDispatcher(
        challenger=challenger,
        skill_executor=skill_executor,
        skill_database=skill_database,
        legacy_execution=legacy_agent_path,
        skill_routing_enabled=True,
    )

    task = MagicMock()
    task.raw_text = "draft the review"
    task.user_id = "nick"

    result = await dispatcher.dispatch(task)

    # Legacy result is still returned to the user.
    assert result == {"task_id": "t1", "path": "claude_native"}
    # Skill database was queried and skill was executed.
    skill_database.get_by_capability.assert_awaited_once_with("parse_task")
    skill_executor.execute.assert_awaited_once()
    executed_kwargs = skill_executor.execute.call_args.kwargs
    assert executed_kwargs["skill"] is skill_row
    assert executed_kwargs["version"] is version_row


async def test_dispatcher_skips_skill_when_no_match():
    challenger = AsyncMock()
    challenger.match_and_extract.return_value = ChallengerMatchResult(
        status="escalate_to_claude",
        capability=None,
    )
    skill_database = AsyncMock()
    skill_executor = AsyncMock()
    legacy_agent_path = AsyncMock()
    legacy_agent_path.return_value = {"task_id": "t1"}

    dispatcher = AgentDispatcher(
        challenger=challenger,
        skill_executor=skill_executor,
        skill_database=skill_database,
        legacy_execution=legacy_agent_path,
        skill_routing_enabled=True,
    )

    task = MagicMock()
    task.raw_text = "novel request"
    task.user_id = "nick"

    result = await dispatcher.dispatch(task)

    assert result == {"task_id": "t1"}
    skill_database.get_by_capability.assert_not_called()
    skill_executor.execute.assert_not_called()


async def test_dispatcher_ignores_skill_when_flag_disabled():
    challenger = AsyncMock()
    skill_executor = AsyncMock()
    skill_database = AsyncMock()
    legacy_agent_path = AsyncMock()
    legacy_agent_path.return_value = {"task_id": "t1"}

    dispatcher = AgentDispatcher(
        challenger=challenger,
        skill_executor=skill_executor,
        skill_database=skill_database,
        legacy_execution=legacy_agent_path,
        skill_routing_enabled=False,
    )

    task = MagicMock()
    result = await dispatcher.dispatch(task)

    assert result == {"task_id": "t1"}
    challenger.match_and_extract.assert_not_called()
    skill_executor.execute.assert_not_called()
    skill_database.get_by_capability.assert_not_called()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_dispatcher_skill_routing.py -v`
Expected: existing dispatcher's constructor does not accept these kwargs.

- [ ] **Step 3: Refactor `AgentDispatcher` to support the new parameters**

The existing class retains its current dispatch flow. Add new optional parameters and a new dispatch path that runs before/after the existing one. In `src/donna/orchestrator/dispatcher.py`, add:

```python
    def __init__(
        self,
        *,
        challenger=None,
        skill_executor=None,
        skill_database=None,
        legacy_execution=None,
        skill_routing_enabled: bool = False,
        # Existing params below — preserve them for backward compat.
        **legacy_kwargs,
    ) -> None:
        self._challenger = challenger
        self._skill_executor = skill_executor
        self._skill_database = skill_database
        self._legacy_execution = legacy_execution
        self._skill_routing_enabled = skill_routing_enabled
        # Store legacy kwargs for the existing flow if the class had args.
        self._legacy_kwargs = legacy_kwargs

    async def dispatch(self, task) -> dict:
        """Dispatch a task through the legacy path, optionally running the
        skill path in parallel for logging (Phase 1 shadow behavior).
        """
        skill_run_result = None

        if self._skill_routing_enabled and self._challenger is not None:
            try:
                match = await self._challenger.match_and_extract(
                    user_message=getattr(task, "raw_text", ""),
                    user_id=getattr(task, "user_id", "unknown"),
                )
                if match.status == "ready" and self._skill_executor is not None and self._skill_database is not None:
                    # Look up the skill and its current version from the DB,
                    # then pass both to the executor. This is the real integration
                    # path used at runtime; the unit tests inject mocks.
                    skill_row = await self._skill_database.get_by_capability(match.capability.name)
                    if skill_row is not None and skill_row.current_version_id is not None:
                        version_row = await self._skill_database.get_version(skill_row.current_version_id)
                        if version_row is not None:
                            skill_run_result = await self._skill_executor.execute(
                                skill=skill_row,
                                version=version_row,
                                inputs=match.extracted_inputs,
                                user_id=getattr(task, "user_id", "unknown"),
                            )
                    logger.info(
                        "dispatcher_skill_shadow_run",
                        capability=match.capability.name if match.capability else None,
                        skill_status=getattr(skill_run_result, "status", None),
                    )
            except Exception:
                logger.exception("dispatcher_skill_shadow_failed")

        # Legacy path is always primary in Phase 1.
        if self._legacy_execution is not None:
            return await self._legacy_execution(task)

        # Fall through to any existing dispatch logic the class had before.
        raise RuntimeError("No legacy execution configured")
```

Make sure `logger = structlog.get_logger()` is defined at module scope if it isn't already.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_dispatcher_skill_routing.py -v`
Expected: all three tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/donna/orchestrator/dispatcher.py tests/unit/test_dispatcher_skill_routing.py
git commit -m "feat(dispatcher): add feature-flagged skill-routing path"
```

---

## Task 17: Skill system config block and seed migration

**Files:**
- Modify: `src/donna/config.py`
- Create: `alembic/versions/seed_skill_system_phase_1.py`

- [ ] **Step 1: Add the config block**

Open `src/donna/config.py`. Add a new pydantic model alongside the existing ones:

```python
class SkillSystemConfig(BaseModel):
    """Phase 1 skill system runtime configuration."""

    enabled: bool = False  # Phase 1 default: off; flip to true via env or config
    match_confidence_high: float = 0.75
    match_confidence_medium: float = 0.40
    similarity_audit_threshold: float = 0.80
    seed_skills_initial_state: str = "sandbox"
```

If there's a top-level `Config` or `DonnaConfig` model, add `skill_system: SkillSystemConfig = Field(default_factory=SkillSystemConfig)` to it.

- [ ] **Step 2: Create the seed migration**

Create `alembic/versions/seed_skill_system_phase_1.py`:

```python
"""seed skill system phase 1 - three capabilities and skills

Revision ID: 8c3b5d9e2f4a
Revises: 7b2a4c8d1e3f
Create Date: 2026-04-15
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = "8c3b5d9e2f4a"
down_revision = "7b2a4c8d1e3f"
branch_labels = None
depends_on = None

SEED_CAPABILITIES = [
    {
        "name": "parse_task",
        "description": "Extract structured task fields from a natural language message",
        "input_schema": {
            "type": "object",
            "properties": {
                "raw_text": {"type": "string", "description": "The user's raw message"},
                "user_id": {"type": "string", "description": "The user ID"},
            },
            "required": ["raw_text", "user_id"],
        },
        "trigger_type": "on_message",
    },
    {
        "name": "dedup_check",
        "description": "Determine whether two task candidates represent the same work item",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_a": {"type": "object"},
                "task_b": {"type": "object"},
            },
            "required": ["task_a", "task_b"],
        },
        "trigger_type": "on_message",
    },
    {
        "name": "classify_priority",
        "description": "Assign a priority level (1-5) to a task based on content and deadline",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "deadline": {"type": ["string", "null"]},
            },
            "required": ["title"],
        },
        "trigger_type": "on_message",
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    for cap in SEED_CAPABILITIES:
        conn.execute(
            sa.text("""
                INSERT INTO capability
                  (id, name, description, input_schema, trigger_type, status, created_at, created_by)
                VALUES
                  (:id, :name, :description, :input_schema, :trigger_type, 'active', :created_at, 'seed')
            """),
            {
                "id": f"seed-{cap['name']}",
                "name": cap["name"],
                "description": cap["description"],
                "input_schema": json.dumps(cap["input_schema"]),
                "trigger_type": cap["trigger_type"],
                "created_at": now,
            },
        )

    # Note: embedding generation is deferred to application startup
    # rather than migration time because it requires importing the
    # sentence-transformers model. A startup hook in the application
    # entry point loops over capabilities with embedding=NULL and fills them in.

    # Note: loading skill YAML into skill + skill_version rows is also
    # deferred to application startup (via load_seed_skills_on_startup)
    # because it needs the filesystem, which migrations shouldn't touch.


def downgrade() -> None:
    conn = op.get_bind()
    for cap in SEED_CAPABILITIES:
        conn.execute(
            sa.text("DELETE FROM skill WHERE capability_name = :name"),
            {"name": cap["name"]},
        )
        conn.execute(
            sa.text("DELETE FROM capability WHERE name = :name"),
            {"name": cap["name"]},
        )
```

- [ ] **Step 3: Apply the migration**

Run:
```bash
DONNA_DB_PATH=/tmp/donna_test.db alembic upgrade head
sqlite3 /tmp/donna_test.db "SELECT name, status FROM capability;"
```

Expected: three rows — `parse_task|active`, `dedup_check|active`, `classify_priority|active`.

- [ ] **Step 4: Clean up**

```bash
rm /tmp/donna_test.db
```

- [ ] **Step 5: Commit**

```bash
git add src/donna/config.py alembic/versions/seed_skill_system_phase_1.py
git commit -m "feat(config): add SkillSystemConfig and seed three capabilities"
```

---

## Task 18: Startup hook — generate embeddings and load seed skills

**Files:**
- Modify: `src/donna/server.py` (or wherever application startup lives — see file for the pattern)
- Create: `src/donna/skills/startup.py`
- Create: `tests/integration/test_skill_startup.py`

- [ ] **Step 1: Write failing integration test**

Create `tests/integration/test_skill_startup.py`:

```python
import json
from pathlib import Path

import aiosqlite
import pytest

from donna.skills.startup import initialize_skill_system


@pytest.fixture
async def db_with_seed_caps(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = await aiosqlite.connect(str(db_path))
    await conn.executescript(open("alembic/versions/add_skill_system_phase_1.py").read().split('"""')[0] if False else """
        -- Use the inline schema for test speed.
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT UNIQUE, description TEXT,
            input_schema TEXT, trigger_type TEXT, default_output_shape TEXT,
            status TEXT NOT NULL DEFAULT 'active', embedding BLOB,
            created_at TEXT, created_by TEXT, notes TEXT
        );
        CREATE TABLE skill (
            id TEXT PRIMARY KEY, capability_name TEXT UNIQUE,
            current_version_id TEXT, state TEXT, requires_human_gate INTEGER,
            baseline_agreement REAL, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE skill_version (
            id TEXT PRIMARY KEY, skill_id TEXT, version_number INTEGER,
            yaml_backbone TEXT, step_content TEXT, output_schemas TEXT,
            created_by TEXT, changelog TEXT, created_at TEXT
        );
    """)
    # Insert one seed capability for the test.
    await conn.execute("""
        INSERT INTO capability (id, name, description, input_schema, trigger_type, status, created_at, created_by)
        VALUES ('seed-parse_task', 'parse_task', 'Extract structured task fields',
                '{"type": "object", "properties": {"raw_text": {"type": "string"}}}',
                'on_message', 'active', '2026-04-15T00:00:00+00:00', 'seed')
    """)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.mark.slow
async def test_initialize_skill_system_fills_embeddings_and_loads_skills(
    db_with_seed_caps, tmp_path: Path
):
    # Point skills_dir at the real project skills/ directory.
    # This test depends on Task 12's seed skill files existing.
    skills_dir = Path("skills")

    await initialize_skill_system(db_with_seed_caps, skills_dir)

    # Capability should now have an embedding.
    cursor = await db_with_seed_caps.execute(
        "SELECT embedding FROM capability WHERE name = 'parse_task'"
    )
    row = await cursor.fetchone()
    assert row[0] is not None
    assert len(row[0]) == 384 * 4

    # Skill row should exist with state=sandbox.
    cursor = await db_with_seed_caps.execute(
        "SELECT state, current_version_id FROM skill WHERE capability_name = 'parse_task'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "sandbox"
    assert row[1] is not None

    # SkillVersion should exist with the correct content.
    cursor = await db_with_seed_caps.execute(
        "SELECT step_content FROM skill_version WHERE skill_id = (SELECT id FROM skill WHERE capability_name = 'parse_task')"
    )
    vrow = await cursor.fetchone()
    content = json.loads(vrow[0])
    assert "extract" in content
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/integration/test_skill_startup.py -v`
Expected: `ModuleNotFoundError: No module named 'donna.skills.startup'`

- [ ] **Step 3: Create `src/donna/skills/startup.py`**

```python
"""Startup initialization for the skill system.

Called once at application boot. Responsibilities:
  1. Generate embeddings for any capability rows with embedding=NULL.
  2. Load seed skills from the `skills/` directory into skill + skill_version rows
     for any capability that doesn't yet have a skill.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import structlog

from donna.capabilities.embeddings import embed_text, embedding_to_bytes
from donna.capabilities.registry import _embedding_text
from donna.skills.loader import SkillLoadError, load_skill_from_directory

logger = structlog.get_logger()


async def initialize_skill_system(
    conn: aiosqlite.Connection,
    skills_dir: Path,
) -> None:
    """Initialize the skill system at startup. Idempotent."""
    await _fill_missing_embeddings(conn)
    await _load_seed_skills(conn, skills_dir)


async def _fill_missing_embeddings(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute(
        "SELECT id, name, description, input_schema FROM capability WHERE embedding IS NULL"
    )
    rows = await cursor.fetchall()

    if not rows:
        return

    import json as _json

    for row in rows:
        cap_id, name, description, input_schema_json = row
        schema = _json.loads(input_schema_json) if input_schema_json else {}
        text = _embedding_text(name, description, schema)
        vec = embed_text(text)
        blob = embedding_to_bytes(vec)

        await conn.execute(
            "UPDATE capability SET embedding = ? WHERE id = ?",
            (blob, cap_id),
        )
        logger.info("capability_embedding_generated", capability_id=cap_id, name=name)

    await conn.commit()


async def _load_seed_skills(
    conn: aiosqlite.Connection,
    skills_dir: Path,
) -> None:
    if not skills_dir.exists():
        logger.warning("seed_skills_dir_not_found", path=str(skills_dir))
        return

    cursor = await conn.execute("SELECT name FROM capability")
    capability_names = {row[0] for row in await cursor.fetchall()}

    cursor = await conn.execute("SELECT capability_name FROM skill")
    skill_names = {row[0] for row in await cursor.fetchall()}

    for skill_subdir in sorted(skills_dir.iterdir()):
        if not skill_subdir.is_dir():
            continue
        if not (skill_subdir / "skill.yaml").exists():
            continue
        name = skill_subdir.name
        if name not in capability_names:
            logger.info("skill_skipped_no_capability", skill=name)
            continue
        if name in skill_names:
            continue  # already loaded

        try:
            await load_skill_from_directory(skill_subdir, conn, initial_state="sandbox")
        except SkillLoadError as exc:
            logger.error("skill_load_failed", skill=name, error=str(exc))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/integration/test_skill_startup.py -v`
Expected: test passes. First run is slow (model load).

- [ ] **Step 5: Wire it into the application startup**

Find the application startup hook in `src/donna/server.py` (look for a function called `startup`, `on_startup`, or equivalent — it's where existing subsystems like the scheduler get initialized). Add:

```python
from donna.skills.startup import initialize_skill_system
from pathlib import Path

# Inside the startup function, after the database connection is established
# and before the dispatcher starts serving traffic:
await initialize_skill_system(db_conn, Path("skills"))
```

Guard it behind the config flag:

```python
if config.skill_system.enabled:
    await initialize_skill_system(db_conn, Path("skills"))
```

- [ ] **Step 6: Commit**

```bash
git add src/donna/skills/startup.py tests/integration/test_skill_startup.py src/donna/server.py
git commit -m "feat(skills): add startup initialization for embeddings and seed skills"
```

---

## Task 19: Read-only dashboard API routes for capabilities and skills

**Files:**
- Create: `src/donna/api/routes/capabilities.py`
- Create: `src/donna/api/routes/skills.py`
- Modify: `src/donna/api/__init__.py` (or wherever routes are registered)
- Create: `tests/unit/test_api_capabilities_routes.py`
- Create: `tests/unit/test_api_skills_routes.py`

- [ ] **Step 1: Explore existing route patterns**

Look at `src/donna/api/routes/` to find an existing route file and follow its pattern. The plan below assumes Flask blueprints; adapt if the project uses FastAPI.

- [ ] **Step 2: Write failing tests**

Create `tests/unit/test_api_capabilities_routes.py`:

```python
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_list_capabilities_returns_all(test_client, mock_registry):
    mock_registry.list_all.return_value = [
        type("C", (), {
            "name": "parse_task",
            "description": "x",
            "trigger_type": "on_message",
            "status": "active",
        })(),
    ]
    response = await test_client.get("/api/capabilities")
    assert response.status_code == 200
    data = response.json
    assert len(data["capabilities"]) == 1
    assert data["capabilities"][0]["name"] == "parse_task"


@pytest.mark.asyncio
async def test_get_capability_by_name(test_client, mock_registry):
    mock_registry.get_by_name.return_value = type("C", (), {
        "name": "product_watch",
        "description": "x",
        "input_schema": {"type": "object"},
        "trigger_type": "on_schedule",
        "status": "active",
    })()
    response = await test_client.get("/api/capabilities/product_watch")
    assert response.status_code == 200
    assert response.json["name"] == "product_watch"


@pytest.mark.asyncio
async def test_get_capability_not_found(test_client, mock_registry):
    mock_registry.get_by_name.return_value = None
    response = await test_client.get("/api/capabilities/nonexistent")
    assert response.status_code == 404
```

Create `tests/unit/test_api_skills_routes.py`:

```python
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_list_skills(test_client, mock_skill_database):
    mock_skill_database.list_all.return_value = [
        type("S", (), {
            "id": "s1", "capability_name": "parse_task", "state": "sandbox",
            "requires_human_gate": False, "baseline_agreement": None,
        })(),
    ]
    response = await test_client.get("/api/skills")
    assert response.status_code == 200
    assert len(response.json["skills"]) == 1


@pytest.mark.asyncio
async def test_get_skill_detail(test_client, mock_skill_database):
    mock_skill_database.get_by_id.return_value = type("S", (), {
        "id": "s1", "capability_name": "parse_task", "state": "sandbox",
        "current_version_id": "v1",
    })()
    mock_skill_database.get_version.return_value = type("V", (), {
        "id": "v1", "version_number": 1, "yaml_backbone": "...",
        "step_content": {"extract": "..."}, "output_schemas": {"extract": {}},
    })()
    response = await test_client.get("/api/skills/s1")
    assert response.status_code == 200
    assert response.json["id"] == "s1"
    assert response.json["current_version"]["version_number"] == 1
```

Note: The exact test client fixture and `mock_registry` fixture depend on how the existing API tests are structured. If tests/conftest.py has an app fixture, use it. Otherwise match the pattern in the most recent API route test file.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/unit/test_api_capabilities_routes.py tests/unit/test_api_skills_routes.py -v`
Expected: fail because routes don't exist.

- [ ] **Step 4: Create `src/donna/api/routes/capabilities.py`**

```python
"""Read-only API routes for the capability registry."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

capabilities_bp = Blueprint("capabilities", __name__, url_prefix="/api/capabilities")


def _capability_to_dict(cap) -> dict:
    return {
        "name": cap.name,
        "description": cap.description,
        "input_schema": cap.input_schema,
        "trigger_type": (
            cap.trigger_type.value if hasattr(cap.trigger_type, "value") else cap.trigger_type
        ),
        "status": cap.status,
        "created_at": cap.created_at.isoformat() if hasattr(cap.created_at, "isoformat") else cap.created_at,
        "created_by": cap.created_by,
    }


@capabilities_bp.route("", methods=["GET"])
async def list_capabilities():
    registry = request.app.registry  # injected at app creation
    status_filter = request.args.get("status")
    caps = await registry.list_all(status=status_filter)
    return jsonify({"capabilities": [_capability_to_dict(c) for c in caps]})


@capabilities_bp.route("/<name>", methods=["GET"])
async def get_capability(name: str):
    registry = request.app.registry
    cap = await registry.get_by_name(name)
    if cap is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_capability_to_dict(cap))
```

- [ ] **Step 5: Create `src/donna/api/routes/skills.py`**

```python
"""Read-only API routes for the skill system."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

skills_bp = Blueprint("skills", __name__, url_prefix="/api/skills")


def _skill_to_dict(skill, version=None) -> dict:
    data = {
        "id": skill.id,
        "capability_name": skill.capability_name,
        "state": skill.state,
        "requires_human_gate": skill.requires_human_gate,
        "baseline_agreement": skill.baseline_agreement,
        "current_version_id": skill.current_version_id,
    }
    if version is not None:
        data["current_version"] = {
            "id": version.id,
            "version_number": version.version_number,
            "yaml_backbone": version.yaml_backbone,
            "step_content": version.step_content,
            "output_schemas": version.output_schemas,
        }
    return data


@skills_bp.route("", methods=["GET"])
async def list_skills():
    skill_db = request.app.skill_database
    state_filter = request.args.get("state")
    skills = await skill_db.list_all(state=state_filter)
    return jsonify({"skills": [_skill_to_dict(s) for s in skills]})


@skills_bp.route("/<skill_id>", methods=["GET"])
async def get_skill(skill_id: str):
    skill_db = request.app.skill_database
    skill = await skill_db.get_by_id(skill_id)
    if skill is None:
        return jsonify({"error": "not_found"}), 404
    version = None
    if skill.current_version_id:
        version = await skill_db.get_version(skill.current_version_id)
    return jsonify(_skill_to_dict(skill, version))
```

- [ ] **Step 6: Register the blueprints**

In `src/donna/api/__init__.py` (or the app factory), add:

```python
from donna.api.routes.capabilities import capabilities_bp
from donna.api.routes.skills import skills_bp

app.register_blueprint(capabilities_bp)
app.register_blueprint(skills_bp)
```

Attach the registry and skill database to `app`:

```python
app.registry = capability_registry  # CapabilityRegistry instance
app.skill_database = skill_database  # SkillDatabase instance (see note below)
```

- [ ] **Step 7: Add a minimal `SkillDatabase` class**

Create `src/donna/skills/database.py`:

```python
"""Lightweight read-only DB access for skills (used by dashboard routes)."""

from __future__ import annotations

import aiosqlite

from donna.skills.models import (
    SELECT_SKILL,
    SELECT_SKILL_VERSION,
    SkillRow,
    SkillVersionRow,
    row_to_skill,
    row_to_skill_version,
)


class SkillDatabase:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def list_all(self, state: str | None = None, limit: int = 200) -> list[SkillRow]:
        if state:
            cursor = await self._conn.execute(
                f"SELECT {SELECT_SKILL} FROM skill WHERE state = ? ORDER BY updated_at DESC LIMIT ?",
                (state, limit),
            )
        else:
            cursor = await self._conn.execute(
                f"SELECT {SELECT_SKILL} FROM skill ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [row_to_skill(r) for r in rows]

    async def get_by_id(self, skill_id: str) -> SkillRow | None:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_SKILL} FROM skill WHERE id = ?",
            (skill_id,),
        )
        row = await cursor.fetchone()
        return row_to_skill(row) if row else None

    async def get_by_capability(self, capability_name: str) -> SkillRow | None:
        """Return the skill row for a capability, or None if none exists.

        Each capability has at most one active skill, so this returns
        the unique match. Used by the dispatcher to resolve which skill
        to execute for a matched capability.
        """
        cursor = await self._conn.execute(
            f"SELECT {SELECT_SKILL} FROM skill WHERE capability_name = ?",
            (capability_name,),
        )
        row = await cursor.fetchone()
        return row_to_skill(row) if row else None

    async def get_version(self, version_id: str) -> SkillVersionRow | None:
        cursor = await self._conn.execute(
            f"SELECT {SELECT_SKILL_VERSION} FROM skill_version WHERE id = ?",
            (version_id,),
        )
        row = await cursor.fetchone()
        return row_to_skill_version(row) if row else None
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/unit/test_api_capabilities_routes.py tests/unit/test_api_skills_routes.py -v`
Expected: tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/donna/api/routes/capabilities.py src/donna/api/routes/skills.py \
        src/donna/api/__init__.py src/donna/skills/database.py \
        tests/unit/test_api_capabilities_routes.py tests/unit/test_api_skills_routes.py
git commit -m "feat(api): add read-only capability and skill dashboard routes"
```

---

## Task 20: End-to-end integration test for Phase 1

**Files:**
- Create: `tests/integration/test_skill_system_phase_1_e2e.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_skill_system_phase_1_e2e.py`:

```python
"""Phase 1 end-to-end integration test.

Verifies the complete Phase 1 handoff contract:
  - Capability registry has three seed capabilities with embeddings.
  - Three seed skills are loaded in sandbox state.
  - Challenger matches 'parse_task'-like messages against the registry.
  - SkillExecutor runs the parse_task skill against a real local LLM call (or mock).
  - Dispatcher routes correctly in parallel with the existing flow.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from donna.capabilities.matcher import CapabilityMatcher, MatchConfidence
from donna.capabilities.registry import CapabilityRegistry
from donna.skills.startup import initialize_skill_system


@pytest.fixture
async def initialized_db(tmp_path: Path):
    db_path = tmp_path / "donna_test.db"
    conn = await aiosqlite.connect(str(db_path))

    # Apply the schema manually (copies the migration's upgrade statements).
    await conn.executescript("""
        CREATE TABLE capability (
            id TEXT PRIMARY KEY, name TEXT UNIQUE, description TEXT,
            input_schema TEXT, trigger_type TEXT, default_output_shape TEXT,
            status TEXT NOT NULL DEFAULT 'active', embedding BLOB,
            created_at TEXT, created_by TEXT, notes TEXT
        );
        CREATE TABLE skill (
            id TEXT PRIMARY KEY, capability_name TEXT UNIQUE,
            current_version_id TEXT, state TEXT, requires_human_gate INTEGER,
            baseline_agreement REAL, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE skill_version (
            id TEXT PRIMARY KEY, skill_id TEXT, version_number INTEGER,
            yaml_backbone TEXT, step_content TEXT, output_schemas TEXT,
            created_by TEXT, changelog TEXT, created_at TEXT
        );
    """)

    # Seed the three capabilities (same as the seed migration).
    import json
    seeds = [
        ("seed-parse_task", "parse_task",
         "Extract structured task fields from a natural language message",
         json.dumps({
             "type": "object",
             "properties": {
                 "raw_text": {"type": "string"},
                 "user_id": {"type": "string"},
             },
             "required": ["raw_text", "user_id"],
         }),
         "on_message"),
        ("seed-dedup_check", "dedup_check",
         "Determine whether two task candidates represent the same work item",
         json.dumps({"type": "object", "properties": {}, "required": []}),
         "on_message"),
        ("seed-classify_priority", "classify_priority",
         "Assign a priority level (1-5) to a task based on content",
         json.dumps({"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}),
         "on_message"),
    ]
    for cap_id, name, desc, schema, trigger in seeds:
        await conn.execute(
            """INSERT INTO capability (id, name, description, input_schema, trigger_type, status, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, 'active', '2026-04-15T00:00:00+00:00', 'seed')""",
            (cap_id, name, desc, schema, trigger),
        )
    await conn.commit()

    # Initialize the skill system (generates embeddings + loads skills).
    await initialize_skill_system(conn, Path("skills"))

    yield conn
    await conn.close()


@pytest.mark.slow
@pytest.mark.integration
async def test_phase_1_handoff_contract(initialized_db):
    """Verifies Phase 1 handoff contract items 1-6."""
    conn = initialized_db

    # H1: capability table has three seeds with embeddings.
    cursor = await conn.execute("SELECT name, embedding FROM capability")
    rows = await cursor.fetchall()
    assert len(rows) == 3
    for name, embedding in rows:
        assert embedding is not None, f"capability {name} missing embedding"

    # H2: CapabilityRegistry.semantic_search returns top-k with scores.
    registry = CapabilityRegistry(conn)
    results = await registry.semantic_search("extract task info from this message", k=3)
    assert len(results) == 3
    names = [cap.name for cap, _ in results]
    assert results[0][0].name == "parse_task", f"expected parse_task first, got: {names}"

    # H3: CapabilityMatcher returns HIGH confidence for a clear match.
    matcher = CapabilityMatcher(registry)
    match_result = await matcher.match("extract task info from this message")
    assert match_result.confidence == MatchConfidence.HIGH
    assert match_result.best_match.name == "parse_task"

    # H4: three skills exist in sandbox state.
    cursor = await conn.execute("SELECT capability_name, state FROM skill")
    skill_rows = await cursor.fetchall()
    assert len(skill_rows) == 3
    for name, state in skill_rows:
        assert state == "sandbox", f"skill {name} expected sandbox state, got {state}"

    # H5: every skill has a current_version_id pointing to a skill_version row.
    cursor = await conn.execute("""
        SELECT s.capability_name, v.version_number, v.step_content
        FROM skill s
        JOIN skill_version v ON s.current_version_id = v.id
    """)
    version_rows = await cursor.fetchall()
    assert len(version_rows) == 3
    for name, version_num, step_content in version_rows:
        assert version_num == 1
        assert step_content is not None
        import json as _json
        assert len(_json.loads(step_content)) >= 1
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/integration/test_skill_system_phase_1_e2e.py -v`
Expected: pass. First run is slow due to embedding model load.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_skill_system_phase_1_e2e.py
git commit -m "test(skills): add Phase 1 end-to-end handoff contract test"
```

---

## Task 21: Update the spec's Drift Log and Phase 1 handoff contract

**Files:**
- Modify: `docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md`

- [ ] **Step 1: Add the drift log entry**

Open the spec. In §8 Drift Log, add:

```markdown
#### 2026-04-15 — Phase 1, §7 Handoff Contract
- **What changed**: Seed skills land in `sandbox` state, not `shadow_primary` as
  originally written in the Phase 1 handoff contract.
- **Why**: Shadow sampling infrastructure (100% Claude comparison during
  `shadow_primary`) is a Phase 3 dependency. Landing seeds in `shadow_primary`
  would require shadow machinery that doesn't exist until Phase 3, violating
  the "task flow is never blocked by skill infrastructure" invariant from §4.2.
  `sandbox` means the skill runs alongside Claude without affecting user-visible
  output, which preserves the invariant and still generates per-skill run data.
- **Handoff contracts affected**: Phase 1 handoff (seed skill state), Phase 3
  handoff (must promote sandbox → shadow_primary for existing seed skills when
  shadow sampling lands).
- **Action required for downstream phases**: Phase 3 implementation should
  include a targeted migration that promotes the three seed skills from
  `sandbox` → `shadow_primary` as the first step after shadow sampling is
  working.
```

- [ ] **Step 2: Update the Phase 1 handoff contract bullet to match**

In §7 Phase 1 Handoff Contract, change:

> - Three seed capabilities exist: `parse_task`, `dedup_check`, `classify_priority`, each with a hand-written skill in `shadow_primary` state.

to:

> - Three seed capabilities exist: `parse_task`, `dedup_check`, `classify_priority`, each with a hand-written skill in `sandbox` state. (See Drift Log entry 2026-04-15 for the rationale.)

- [ ] **Step 3: Check off requirements in the checklist**

In §9 Requirements Checklist, mark the following as verified by noting the test or scenario name in the "Verified by" column:

- R1, R2, R3, R4, R5, R6, R9, R10, R11, R38, R40

(These are the Phase 1 requirements. Later phases verify the rest.)

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md
git commit -m "docs(spec): record Phase 1 drift - seed skills in sandbox not shadow_primary"
```

---

## Self-Review (fill in during execution)

After completing all tasks, run:

```bash
pytest tests/unit/ -v -m "not slow"
pytest tests/unit/ -v -m slow
pytest tests/integration/ -v
```

Verify:
- [ ] All unit tests pass
- [ ] All slow (model-loading) tests pass
- [ ] Phase 1 end-to-end integration test passes
- [ ] `python -c "from donna.capabilities.registry import CapabilityRegistry; from donna.skills.executor import SkillExecutor; print('ok')"` succeeds
- [ ] Running the dashboard shows capabilities and skills in the new routes
- [ ] No existing test was broken by the dispatcher refactor
- [ ] The drift log entry is in the spec

---

## Phase 1 Acceptance Scenarios (from spec §7)

Run these after all tasks complete. They map to AS-1.1 through AS-1.4 in the spec:

**AS-1.1**: With `skill_system.enabled = true`, have the test harness send a parsed task with text "draft Q2 review by Friday" through the dispatcher. Verify that the challenger matches `parse_task` with high confidence, the SkillExecutor runs the hand-written skill, a `skill_run` row is created with a valid final output, and the legacy flow also ran and its output is what the user sees.

**AS-1.2**: Send a parsed task with text "monitor https://cos.com/shirt daily for size L under $100." Verify that the challenger returns `escalate_to_claude` (no matching capability at confidence ≥ HIGH), the dispatcher skips skill execution, and the legacy flow handles the task. No new capability is registered in Phase 1 (the novelty judgment Claude call is a Phase 3 feature).

**AS-1.3**: Start the dashboard and navigate to `/api/capabilities` — verify the three seed capabilities are listed. Navigate to `/api/skills` — verify the three seed skills are listed with `state: "sandbox"`. Navigate to `/api/skills/<id>` for one of them — verify the response includes the current version YAML, step content, and output schemas.

**AS-1.4**: With `skill_system.enabled = true`, send a parsed task that matches `classify_priority` but is missing the required `title` field (edge case — simulate by passing a task with an empty title). Verify the challenger returns `needs_input` with `missing_fields=["title"]` and a clarifying question is generated. (Note: in Phase 1 the clarifying question is templated, not generated by an LLM; that evolves in Phase 3.)

---

## Notes for the Implementer

- **Read the spec first.** `docs/superpowers/specs/2026-04-15-skill-system-and-challenger-refactor-design.md` has the full design. Sections §4 (architecture), §5 (data model), §6.1, §6.2, §6.3, §6.4, §6.7, §6.10 are directly relevant to Phase 1.

- **Prefer Test-Driven Development (TDD).** Every task starts with a failing test; implement only enough code to make the test pass; commit when green. If a task doesn't show a test step, write one anyway.

- **Don't skip the commit step.** Each task commits at its end. This preserves a clean bisection path if something breaks three tasks later.

- **The `slow` pytest marker exists for tests that load the embedding model.** Run `pytest -m "not slow"` for fast iteration. CI should run `pytest -m slow` separately.

- **The dispatcher refactor in Task 16 is the most risky change.** It touches the hot path for every inbound task. The feature flag (`skill_routing_enabled`) means the default behavior is unchanged — flip the flag to `true` only after all other tasks pass.

- **If any deviation from this plan is required during implementation**, add a drift log entry to the spec (§8) as part of the task that deviated, and update the Phase 1 Handoff Contract (§7) if it affects downstream phases.

- **Task 15 uses an `InputExtractor` protocol with an AsyncMock in tests; Task 15b provides the concrete `LocalLLMInputExtractor`.** When wiring the real application at startup, pass a `LocalLLMInputExtractor(model_router)` instance into the `ChallengerAgent` constructor.
