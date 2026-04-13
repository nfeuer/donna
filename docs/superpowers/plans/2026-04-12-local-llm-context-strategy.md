# Local LLM Context Window Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the silent 2048-token Ollama window truncation and add router-level prompt budgeting with cloud escalation, estimation-accuracy tracking, and a distinct log signal for context-overflow escalations.

**Architecture:** Three layers: (1) config adds `num_ctx` + `output_reserve` to `OllamaConfig` and per-alias override to `ModelConfig`; (2) `ModelRouter` estimates prompt tokens before dispatching to local aliases and escalates to `fallback` on overflow (raises `ContextOverflowError` when no fallback); (3) observability gets two new `invocation_log` columns, a structured `context_overflow_escalation` warn event, and LLM Gateway dashboard extensions surfacing estimation accuracy and overflow-escalation counts.

**Tech Stack:** Python 3.12 + asyncio, Pydantic v2 for config, Alembic + SQLAlchemy Core for the migration, `aiosqlite` for the DB layer, `structlog` for logging, FastAPI for the admin API, React + TypeScript for the dashboard UI.

**Spec:** `docs/superpowers/specs/2026-04-12-local-llm-context-strategy-design.md`

---

## File Structure

### Backend — modify

- `src/donna/config.py` — add `default_num_ctx` / `default_output_reserve` to `OllamaConfig`; add optional `num_ctx` override to `ModelConfig`.
- `config/donna_models.yaml` — populate the new fields with starting values.
- `src/donna/models/providers/__init__.py` — extend the `ModelProvider` protocol with an optional `num_ctx` kwarg.
- `src/donna/models/providers/ollama.py` — accept `num_ctx` kwarg, send it as `options.num_ctx`.
- `src/donna/models/providers/anthropic.py` — accept `num_ctx` kwarg as a no-op so the protocol is satisfied.
- `src/donna/models/router.py` — estimate tokens, budget check, escalate on overflow, raise `ContextOverflowError` when no fallback, expose the estimate and overflow flag on the return path.
- `src/donna/logging/invocation_logger.py` — new fields on `InvocationMetadata`, updated `INSERT`.
- `src/donna/api/routes/admin_invocations.py` — surface new columns on the list + detail endpoints and add an `overflow_escalated` filter param.
- `src/donna/api/routes/admin_dashboard.py` — extend the `/dashboard/llm-gateway` endpoint with estimation-accuracy aggregates and an overflow-escalations counter.

### Backend — create

- `src/donna/models/tokens.py` — pure-Python token-estimation helper (`estimate_tokens(text) -> int`).
- `alembic/versions/add_context_budget_columns.py` — Alembic migration adding `estimated_tokens_in` and `overflow_escalated` to `invocation_log`.
- `tests/unit/test_token_estimation.py`
- `tests/unit/test_ollama_num_ctx.py`
- `tests/unit/test_router_context_budget.py`
- `tests/unit/test_invocation_logger_context_fields.py`

### Frontend — modify

- `donna-ui/src/api/llmGateway.ts` — extend `LLMGatewayData` type with `estimation_accuracy` + `overflow_escalations` fields.
- `donna-ui/src/api/admin.ts` (or wherever `listInvocations` lives — grep-confirm before editing) — add `overflow_escalated` filter param and surface new columns in the row type.
- `donna-ui/src/pages/LLMGateway/index.tsx` — new summary tile for overflow escalations and estimation-accuracy MAE; pass the new field through.
- `donna-ui/src/pages/LLMGateway/` — wherever the invocation list is rendered, add the `est / actual` column with the traffic-light and the two new filter toggles.

### Docs — modify

- `docs/model-layer.md` — document the new config surface, the budgeting flow, the overflow-escalation signal, and the YAGNI deferrals.

---

## Conventions

- **Every task ends with a commit.** Small, frequent commits per `CLAUDE.md`.
- **TDD throughout.** Write the failing test first, watch it fail with a meaningful message, then implement.
- **Run full `pytest` after each task** that touches backend code. Fast feedback keeps regressions from compounding.
- **Type hints on every new signature.** `mypy` / `pyright` runs in CI.
- **Structured logging only.** Never `print()`. `logger = structlog.get_logger()` at module top.
- **Async everywhere.** The codebase is `asyncio` top to bottom.
- **Config over code.** No hardcoded `num_ctx` or `output_reserve` — always read from `ModelsConfig`.
- **Commit message style:** `feat(model-layer): ...`, `fix(model-layer): ...`, `test(model-layer): ...`, `docs(model-layer): ...` (follows repo convention).

---

## Task 1: Token estimation helper

**Why first:** Everything downstream depends on it. Pure function, no I/O, fast to TDD.

**Files:**
- Create: `src/donna/models/tokens.py`
- Test: `tests/unit/test_token_estimation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_token_estimation.py`:

```python
"""Unit tests for the char-based token estimation helper."""

from donna.models.tokens import estimate_tokens


def test_estimate_tokens_empty_string() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_short_string() -> None:
    # "hello world" = 11 chars → 11 // 4 = 2
    assert estimate_tokens("hello world") == 2


def test_estimate_tokens_rounds_down() -> None:
    # 7 chars → 7 // 4 = 1
    assert estimate_tokens("abcdefg") == 1


def test_estimate_tokens_longer_string() -> None:
    # 400 chars → 100
    assert estimate_tokens("x" * 400) == 100


def test_estimate_tokens_non_ascii() -> None:
    # Heuristic is char-based, not byte-based; 4 chars = 1 token regardless.
    assert estimate_tokens("日本語です") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_token_estimation.py -v`
Expected: `ModuleNotFoundError: No module named 'donna.models.tokens'`

- [ ] **Step 3: Write minimal implementation**

Create `src/donna/models/tokens.py`:

```python
"""Prompt token estimation for router-level budget checks.

Uses a character-based heuristic (len // 4) that matches the ballpark for
English prompts. We compare against the actual `tokens_in` returned by
Ollama and surface drift on the LLM Gateway dashboard; if drift grows past
the red threshold, upgrade to Ollama's /api/tokenize endpoint.
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Rough token-count estimate for a prompt.

    Uses the len(text) // 4 heuristic — zero dependencies, constant-time,
    and close enough for budget checks on modest-sized prompts. Accuracy
    is tracked on the LLM Gateway dashboard via the estimated_tokens_in
    column on invocation_log.
    """
    return len(text) // 4
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_token_estimation.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/donna/models/tokens.py tests/unit/test_token_estimation.py
git commit -m "feat(model-layer): add char-based prompt token estimator"
```

---

## Task 2: Config model — OllamaConfig + ModelConfig fields

**Files:**
- Modify: `src/donna/config.py` (`OllamaConfig` class around line 53, `ModelConfig` class around line 17)
- Test: `tests/unit/test_context_config.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_context_config.py`:

```python
"""Unit tests for the new context-window config fields."""

from donna.config import ModelConfig, ModelsConfig, OllamaConfig


def test_ollama_config_defaults_for_num_ctx() -> None:
    cfg = OllamaConfig()
    assert cfg.default_num_ctx == 8192
    assert cfg.default_output_reserve == 1024


def test_ollama_config_accepts_overrides() -> None:
    cfg = OllamaConfig(default_num_ctx=4096, default_output_reserve=512)
    assert cfg.default_num_ctx == 4096
    assert cfg.default_output_reserve == 512


def test_model_config_num_ctx_defaults_to_none() -> None:
    mc = ModelConfig(provider="ollama", model="qwen2.5:32b-instruct-q6_K")
    assert mc.num_ctx is None


def test_model_config_accepts_num_ctx_override() -> None:
    mc = ModelConfig(
        provider="ollama", model="qwen2.5:32b-instruct-q6_K", num_ctx=16384
    )
    assert mc.num_ctx == 16384


def test_models_config_roundtrip_with_new_fields() -> None:
    data = {
        "models": {
            "local_parser": {
                "provider": "ollama",
                "model": "qwen2.5:32b-instruct-q6_K",
                "num_ctx": 16384,
            }
        },
        "routing": {},
        "ollama": {
            "base_url": "http://localhost:11434",
            "timeout_s": 120,
            "keepalive": "5m",
            "default_num_ctx": 8192,
            "default_output_reserve": 1024,
        },
    }
    cfg = ModelsConfig(**data)
    assert cfg.ollama.default_num_ctx == 8192
    assert cfg.models["local_parser"].num_ctx == 16384
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_context_config.py -v`
Expected: `AttributeError` or pydantic validation errors — `default_num_ctx` / `num_ctx` don't exist yet.

- [ ] **Step 3: Modify `OllamaConfig` and `ModelConfig`**

Open `src/donna/config.py`. Replace the `ModelConfig` class:

```python
class ModelConfig(BaseModel):
    """A single model alias definition."""

    provider: str
    model: str
    estimated_cost_per_1k_tokens: float | None = None
    num_ctx: int | None = None
```

Replace the `OllamaConfig` class:

```python
class OllamaConfig(BaseModel):
    """Connection settings for the local Ollama LLM server."""

    base_url: str = "http://localhost:11434"
    timeout_s: int = 120
    keepalive: str = "5m"
    default_num_ctx: int = 8192
    default_output_reserve: int = 1024
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_context_config.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the full suite to catch regressions**

Run: `pytest -x -q`
Expected: All existing tests still pass. If any fail, a caller is constructing `OllamaConfig` or `ModelConfig` positionally — inspect and fix before moving on.

- [ ] **Step 6: Commit**

```bash
git add src/donna/config.py tests/unit/test_context_config.py
git commit -m "feat(model-layer): add num_ctx and output_reserve to model config"
```

---

## Task 3: Populate `config/donna_models.yaml` with the new fields

**Files:**
- Modify: `config/donna_models.yaml`

- [ ] **Step 1: Edit the YAML**

Replace the current `ollama:` block:

```yaml
# Ollama local LLM connection settings (RTX 3090)
ollama:
  base_url: http://localhost:11434
  timeout_s: 120
  keepalive: 5m
  default_num_ctx: 8192
  default_output_reserve: 1024
```

Add `num_ctx` under the existing `local_parser` alias:

```yaml
  local_parser:
    provider: ollama
    model: qwen2.5:32b-instruct-q6_K
    estimated_cost_per_1k_tokens: 0.0001  # hardware amortization
    num_ctx: 8192
```

- [ ] **Step 2: Verify the config still parses**

Run: `python -c "from pathlib import Path; from donna.config import load_models_config; print(load_models_config(Path('config')).ollama)"`
Expected: Something like `base_url='http://localhost:11434' timeout_s=120 keepalive='5m' default_num_ctx=8192 default_output_reserve=1024`.

- [ ] **Step 3: Commit**

```bash
git add config/donna_models.yaml
git commit -m "feat(model-layer): wire num_ctx defaults into donna_models.yaml"
```

---

## Task 4: Alembic migration — new `invocation_log` columns

**Files:**
- Create: `alembic/versions/add_context_budget_columns.py`

- [ ] **Step 1: Find the current head revision**

Run: `alembic heads`
Expected: a single revision ID printed — e.g. `e7a3b4c5d692 (head)` from the LLM gateway columns migration. Note this ID as `<HEAD_ID>`. If multiple heads are printed, stop and ask — this plan assumes a linear history.

- [ ] **Step 2: Write the migration**

Create `alembic/versions/add_context_budget_columns.py`:

```python
"""add context-budget columns to invocation_log

Revision ID: f1b8c2d4e703
Revises: <HEAD_ID>
Create Date: 2026-04-12 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1b8c2d4e703"
down_revision: Union[str, None] = "<HEAD_ID>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("estimated_tokens_in", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "overflow_escalated",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.drop_column("overflow_escalated")
        batch_op.drop_column("estimated_tokens_in")
```

Replace `<HEAD_ID>` on both the `Revises:` docstring line and the `down_revision` assignment with the ID from Step 1.

- [ ] **Step 3: Run the migration against a disposable database**

Run: `DATABASE_URL=sqlite:///./_tmp_migration_check.db alembic upgrade head`
Expected: Alembic runs the new revision with no errors. Verify with:
`sqlite3 ./_tmp_migration_check.db ".schema invocation_log"` — both `estimated_tokens_in` and `overflow_escalated` must appear.

- [ ] **Step 4: Confirm downgrade works**

Run: `DATABASE_URL=sqlite:///./_tmp_migration_check.db alembic downgrade -1`
Expected: Downgrade completes. Re-run the `.schema` check and confirm both columns are gone. Then delete the scratch DB: `rm ./_tmp_migration_check.db`.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/add_context_budget_columns.py
git commit -m "feat(model-layer): migration for context-budget columns on invocation_log"
```

---

## Task 5: Extend `InvocationMetadata` and the logger insert

**Files:**
- Modify: `src/donna/logging/invocation_logger.py`
- Test: `tests/unit/test_invocation_logger_context_fields.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_invocation_logger_context_fields.py`:

```python
"""Tests that the invocation logger writes the new context-budget fields."""

from __future__ import annotations

import aiosqlite
import pytest

from donna.logging.invocation_logger import InvocationLogger, InvocationMetadata


@pytest.mark.asyncio
async def test_log_writes_estimated_tokens_and_overflow_flag(tmp_path) -> None:
    db_path = tmp_path / "inv.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """CREATE TABLE invocation_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                task_type TEXT,
                task_id TEXT,
                model_alias TEXT,
                model_actual TEXT,
                input_hash TEXT,
                latency_ms INTEGER,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd REAL,
                output TEXT,
                quality_score REAL,
                is_shadow INTEGER,
                eval_session_id TEXT,
                spot_check_queued INTEGER,
                user_id TEXT,
                queue_wait_ms INTEGER,
                interrupted INTEGER,
                chain_id TEXT,
                caller TEXT,
                estimated_tokens_in INTEGER,
                overflow_escalated INTEGER NOT NULL DEFAULT 0
            )"""
        )
        await conn.commit()

        logger = InvocationLogger(conn)
        inv_id = await logger.log(
            InvocationMetadata(
                task_type="generate_nudge",
                model_alias="local_parser",
                model_actual="ollama/qwen2.5:32b-instruct-q6_K",
                input_hash="abc",
                latency_ms=100,
                tokens_in=1500,
                tokens_out=50,
                cost_usd=0.0,
                user_id="nick",
                estimated_tokens_in=1480,
                overflow_escalated=False,
            )
        )

        cursor = await conn.execute(
            "SELECT estimated_tokens_in, overflow_escalated FROM invocation_log WHERE id = ?",
            (inv_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 1480
        assert bool(row[1]) is False


@pytest.mark.asyncio
async def test_log_defaults_for_missing_context_fields(tmp_path) -> None:
    db_path = tmp_path / "inv2.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """CREATE TABLE invocation_log (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                task_type TEXT,
                task_id TEXT,
                model_alias TEXT,
                model_actual TEXT,
                input_hash TEXT,
                latency_ms INTEGER,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_usd REAL,
                output TEXT,
                quality_score REAL,
                is_shadow INTEGER,
                eval_session_id TEXT,
                spot_check_queued INTEGER,
                user_id TEXT,
                queue_wait_ms INTEGER,
                interrupted INTEGER,
                chain_id TEXT,
                caller TEXT,
                estimated_tokens_in INTEGER,
                overflow_escalated INTEGER NOT NULL DEFAULT 0
            )"""
        )
        await conn.commit()

        logger = InvocationLogger(conn)
        inv_id = await logger.log(
            InvocationMetadata(
                task_type="parse_task",
                model_alias="parser",
                model_actual="anthropic/claude-sonnet-4-20250514",
                input_hash="def",
                latency_ms=200,
                tokens_in=900,
                tokens_out=120,
                cost_usd=0.004,
                user_id="nick",
            )
        )

        cursor = await conn.execute(
            "SELECT estimated_tokens_in, overflow_escalated FROM invocation_log WHERE id = ?",
            (inv_id,),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] is None
        assert bool(row[1]) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_invocation_logger_context_fields.py -v`
Expected: Both tests fail — either `TypeError: __init__() got an unexpected keyword argument 'estimated_tokens_in'` or an `OperationalError` on the INSERT because the new columns are not bound.

- [ ] **Step 3: Add fields to `InvocationMetadata`**

In `src/donna/logging/invocation_logger.py`, replace the `InvocationMetadata` dataclass:

```python
@dataclasses.dataclass(frozen=True)
class InvocationMetadata:
    """Data captured from every LLM invocation."""

    task_type: str
    model_alias: str
    model_actual: str
    input_hash: str
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    user_id: str
    task_id: str | None = None
    output: dict[str, Any] | None = None
    quality_score: float | None = None
    is_shadow: bool = False
    eval_session_id: str | None = None
    spot_check_queued: bool = False
    queue_wait_ms: int | None = None
    interrupted: bool = False
    chain_id: str | None = None
    caller: str | None = None
    estimated_tokens_in: int | None = None
    overflow_escalated: bool = False
```

- [ ] **Step 4: Update the `INSERT` in `log()`**

In the same file, replace the `execute` call in `log()`:

```python
        await self._conn.execute(
            """INSERT INTO invocation_log
            (id, timestamp, task_type, task_id, model_alias, model_actual,
             input_hash, latency_ms, tokens_in, tokens_out, cost_usd,
             output, quality_score, is_shadow, eval_session_id,
             spot_check_queued, user_id,
             queue_wait_ms, interrupted, chain_id, caller,
             estimated_tokens_in, overflow_escalated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invocation_id,
                now,
                metadata.task_type,
                metadata.task_id,
                metadata.model_alias,
                metadata.model_actual,
                metadata.input_hash,
                metadata.latency_ms,
                metadata.tokens_in,
                metadata.tokens_out,
                metadata.cost_usd,
                json.dumps(metadata.output) if metadata.output is not None else None,
                metadata.quality_score,
                metadata.is_shadow,
                metadata.eval_session_id,
                metadata.spot_check_queued,
                metadata.user_id,
                metadata.queue_wait_ms,
                metadata.interrupted,
                metadata.chain_id,
                metadata.caller,
                metadata.estimated_tokens_in,
                metadata.overflow_escalated,
            ),
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_invocation_logger_context_fields.py -v`
Expected: 2 passed.

- [ ] **Step 6: Run the full suite**

Run: `pytest -x -q`
Expected: All existing tests pass — existing `InvocationMetadata(...)` call sites don't pass the new kwargs, so defaults kick in.

- [ ] **Step 7: Commit**

```bash
git add src/donna/logging/invocation_logger.py tests/unit/test_invocation_logger_context_fields.py
git commit -m "feat(model-layer): log estimated_tokens_in and overflow_escalated"
```

---

## Task 6: Provider protocol + Ollama `num_ctx` support

**Files:**
- Modify: `src/donna/models/providers/__init__.py` (Protocol)
- Modify: `src/donna/models/providers/ollama.py`
- Modify: `src/donna/models/providers/anthropic.py` (no-op kwarg sink)
- Test: `tests/unit/test_ollama_num_ctx.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_ollama_num_ctx.py`:

```python
"""Tests that OllamaProvider forwards num_ctx into the request payload."""

from __future__ import annotations

from typing import Any

import pytest

from donna.models.providers.ollama import OllamaProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status = 200

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return {
            "message": {"content": '{"ok": true}'},
            "model": "qwen2.5:32b-instruct-q6_K",
            "prompt_eval_count": 10,
            "eval_count": 5,
        }


class _FakeSession:
    def __init__(self) -> None:
        self.last_post_json: dict[str, Any] | None = None
        self.closed = False

    def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        self.last_post_json = json
        return _FakeResponse(json)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_num_ctx_is_sent_in_options() -> None:
    provider = OllamaProvider()
    fake = _FakeSession()
    provider._session = fake  # type: ignore[assignment]

    await provider.complete(
        prompt="hello",
        model="qwen2.5:32b-instruct-q6_K",
        max_tokens=512,
        num_ctx=8192,
    )

    assert fake.last_post_json is not None
    assert fake.last_post_json["options"]["num_ctx"] == 8192
    assert fake.last_post_json["options"]["num_predict"] == 512


@pytest.mark.asyncio
async def test_num_ctx_defaults_when_not_provided() -> None:
    provider = OllamaProvider()
    fake = _FakeSession()
    provider._session = fake  # type: ignore[assignment]

    await provider.complete(prompt="hello", model="qwen2.5:32b-instruct-q6_K")

    assert fake.last_post_json is not None
    # When the caller does not pass num_ctx, we must still send one so
    # Ollama does not fall back to its 2048 default. Default matches the
    # OllamaConfig.default_num_ctx starting value.
    assert fake.last_post_json["options"]["num_ctx"] == 8192
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ollama_num_ctx.py -v`
Expected: `TypeError: complete() got an unexpected keyword argument 'num_ctx'` for the first test, and a `KeyError` on `options.num_ctx` for the second.

- [ ] **Step 3: Update the provider protocol**

In `src/donna/models/providers/__init__.py`, replace the `ModelProvider` protocol's `complete` signature:

```python
    async def complete(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 1024,
        num_ctx: int | None = None,
    ) -> tuple[dict[str, Any], CompletionMetadata]: ...
```

- [ ] **Step 4: Update `OllamaProvider.complete`**

In `src/donna/models/providers/ollama.py`, replace `complete()`:

```python
    async def complete(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 1024,
        json_mode: bool = True,
        num_ctx: int | None = None,
    ) -> tuple[dict[str, Any], CompletionMetadata]:
        """Send a prompt and return parsed output with metadata.

        Args:
            prompt: The fully-rendered prompt text.
            model: Ollama model tag (e.g. "qwen2.5:32b-instruct-q6_K").
            max_tokens: Maximum output tokens.
            json_mode: When True (default), requests JSON format from Ollama
                and parses the response as JSON. When False, returns plain text
                wrapped in {"text": <response>}.
            num_ctx: Total context window (prompt + output). Defaults to 8192
                when not provided so we never fall back to Ollama's 2048
                default.

        Returns:
            Tuple of (parsed dict, CompletionMetadata).
        """
        session = self._get_session()
        start = time.monotonic()

        effective_num_ctx = num_ctx if num_ctx is not None else 8192

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "num_ctx": effective_num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"

        async with session.post(
            f"{self._base_url}/api/chat", json=payload
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        elapsed_ms = int((time.monotonic() - start) * 1000)

        raw_text = data["message"]["content"]
        if json_mode:
            parsed = parse_json_response(raw_text)
        else:
            parsed = {"text": raw_text}

        tokens_in = data.get("prompt_eval_count", 0)
        tokens_out = data.get("eval_count", 0)
        total_tokens = tokens_in + tokens_out
        cost = total_tokens * self._estimated_cost_per_1k / 1000

        metadata = CompletionMetadata(
            latency_ms=elapsed_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            model_actual=f"ollama/{data.get('model', model)}",
        )

        logger.info(
            "ollama_completion",
            model=model,
            latency_ms=elapsed_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=metadata.cost_usd,
            num_ctx=effective_num_ctx,
        )

        return parsed, metadata
```

- [ ] **Step 5: Add `num_ctx` sink to `AnthropicProvider.complete`**

Open `src/donna/models/providers/anthropic.py`, find the `complete()` method, and add `num_ctx: int | None = None` as a keyword-only argument at the end of the signature. Do not reference it in the body — Anthropic ignores it. This keeps the `ModelProvider` protocol uniform so the router can pass the kwarg unconditionally.

- [ ] **Step 6: Run the new test**

Run: `pytest tests/unit/test_ollama_num_ctx.py -v`
Expected: 2 passed.

- [ ] **Step 7: Run the full suite**

Run: `pytest -x -q`
Expected: All passes. Any failure likely means an existing test constructs a provider with positional args — inspect and fix minimally (no refactor).

- [ ] **Step 8: Commit**

```bash
git add src/donna/models/providers/__init__.py src/donna/models/providers/ollama.py src/donna/models/providers/anthropic.py tests/unit/test_ollama_num_ctx.py
git commit -m "feat(model-layer): forward num_ctx from router into Ollama requests"
```

---

## Task 7: Router budget check and `ContextOverflowError`

This is the heart of the change. It has more steps than earlier tasks because the test fixtures must be built up explicitly — do not skip ahead.

**Files:**
- Modify: `src/donna/models/router.py`
- Test: `tests/unit/test_router_context_budget.py` (new)

- [ ] **Step 1: Read the existing router once more for context**

Run: `sed -n '130,180p' src/donna/models/router.py`
(This is a literal read for the implementer, not a code change. Keep it loaded in your head: the dispatch path is `_resolve_route` → `resilient_call(provider.complete, prompt, model_id, ...)`.)

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_router_context_budget.py`:

```python
"""Router-level context-budget checks and cloud escalation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from donna.config import (
    ModelConfig,
    ModelsConfig,
    OllamaConfig,
    RoutingEntry,
    TaskTypeEntry,
    TaskTypesConfig,
)
from donna.models.router import ContextOverflowError, ModelRouter
from donna.models.types import CompletionMetadata


class _RecordingProvider:
    """Minimal provider stub that records every call."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        prompt: str,
        model: str,
        max_tokens: int = 1024,
        num_ctx: int | None = None,
    ) -> tuple[dict[str, Any], CompletionMetadata]:
        self.calls.append(
            {"prompt": prompt, "model": model, "num_ctx": num_ctx}
        )
        return (
            {"ok": True, "provider": self.name},
            CompletionMetadata(
                latency_ms=1,
                tokens_in=len(prompt) // 4,
                tokens_out=5,
                cost_usd=0.0,
                model_actual=f"{self.name}/{model}",
            ),
        )


def _build_router(
    *,
    num_ctx: int = 100,
    output_reserve: int = 20,
    with_fallback: bool = True,
) -> tuple[ModelRouter, _RecordingProvider, _RecordingProvider]:
    ollama = _RecordingProvider("ollama")
    anthropic = _RecordingProvider("anthropic")

    models_config = ModelsConfig(
        models={
            "local_parser": ModelConfig(
                provider="ollama",
                model="qwen2.5:32b-instruct-q6_K",
                num_ctx=num_ctx,
            ),
            "parser": ModelConfig(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
            ),
        },
        routing={
            "generate_nudge": RoutingEntry(
                model="local_parser",
                fallback="parser" if with_fallback else None,
            ),
        },
        ollama=OllamaConfig(
            default_num_ctx=num_ctx,
            default_output_reserve=output_reserve,
        ),
    )
    task_types_config = TaskTypesConfig(
        task_types={
            "generate_nudge": TaskTypeEntry(
                description="",
                model="local_parser",
                prompt_template="unused.md",
                output_schema="unused.json",
            ),
        }
    )

    router = ModelRouter.__new__(ModelRouter)
    router._models_config = models_config
    router._task_types_config = task_types_config
    router._project_root = Path(".")
    router._budget_guard = None
    router._on_shadow_complete = None
    router._providers = {"ollama": ollama, "anthropic": anthropic}
    router._prompt_cache = {}
    router._schema_cache = {}
    from donna.resilience.retry import CircuitBreaker
    router._circuit_breaker = CircuitBreaker()

    return router, ollama, anthropic


@pytest.mark.asyncio
async def test_small_prompt_dispatches_to_local() -> None:
    router, ollama, anthropic = _build_router()
    # num_ctx=100, reserve=20 → budget=80 tokens → 80*4=320 chars fit
    prompt = "x" * 200  # 200 chars ≈ 50 tokens
    _, meta = await router.complete(prompt=prompt, task_type="generate_nudge")
    assert len(ollama.calls) == 1
    assert len(anthropic.calls) == 0
    assert meta.model_actual.startswith("ollama/")


@pytest.mark.asyncio
async def test_large_prompt_escalates_to_fallback() -> None:
    router, ollama, anthropic = _build_router()
    # budget=80 tokens → anything > 80 tokens (>320 chars) overflows
    prompt = "x" * 2000  # 2000 chars ≈ 500 tokens
    _, meta = await router.complete(prompt=prompt, task_type="generate_nudge")
    assert len(ollama.calls) == 0
    assert len(anthropic.calls) == 1
    assert meta.model_actual.startswith("anthropic/")


@pytest.mark.asyncio
async def test_large_prompt_no_fallback_raises_context_overflow_error() -> None:
    router, ollama, anthropic = _build_router(with_fallback=False)
    prompt = "x" * 2000
    with pytest.raises(ContextOverflowError):
        await router.complete(prompt=prompt, task_type="generate_nudge")
    assert len(ollama.calls) == 0
    assert len(anthropic.calls) == 0


@pytest.mark.asyncio
async def test_local_dispatch_forwards_num_ctx_to_provider() -> None:
    router, ollama, _ = _build_router(num_ctx=4096)
    await router.complete(prompt="small", task_type="generate_nudge")
    assert ollama.calls[0]["num_ctx"] == 4096
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/unit/test_router_context_budget.py -v`
Expected: `ImportError: cannot import name 'ContextOverflowError' from 'donna.models.router'` (or similar). The test file will not import.

- [ ] **Step 4: Add `ContextOverflowError` to the router**

In `src/donna/models/router.py`, below the existing `RoutingError` class (around line 34), add:

```python
class ContextOverflowError(Exception):
    """Raised when a prompt exceeds the local-model context budget and no
    fallback is configured. Loud-fail by design: a silently truncated
    prompt produces silent garbage, which is worse."""
```

- [ ] **Step 5: Implement the budget check in `complete()`**

In `src/donna/models/router.py`, replace the entire `complete()` method body (current lines ~130–180) with the version below. This adds: token estimation before dispatch, overflow detection with escalation via `fallback`, and passing `num_ctx` into the provider call.

```python
    async def complete(
        self,
        prompt: str,
        task_type: str,
        task_id: str | None = None,
        user_id: str = "system",
    ) -> tuple[dict[str, Any], CompletionMetadata]:
        """Route a completion call through the configured provider.

        Raises:
            RoutingError: If the task type or model cannot be resolved.
            ContextOverflowError: If the prompt exceeds the local budget and
                no fallback is configured.
            BudgetPausedError: If daily spend exceeds the pause threshold.
        """
        from donna.models.tokens import estimate_tokens  # local import: circular-safe

        if self._budget_guard is not None:
            await self._budget_guard.check_pre_call(user_id)

        provider, model_id, alias = self._resolve_route(task_type)
        model_config = self._models_config.models[alias]

        estimated_in: int | None = None
        overflow_escalated = False
        num_ctx_to_send: int | None = None

        if model_config.provider == "ollama":
            num_ctx_to_send = (
                model_config.num_ctx
                if model_config.num_ctx is not None
                else self._models_config.ollama.default_num_ctx
            )
            output_reserve = self._models_config.ollama.default_output_reserve
            budget = num_ctx_to_send - output_reserve
            estimated_in = estimate_tokens(prompt)

            if estimated_in > budget:
                routing_entry = self._models_config.routing.get(task_type)
                fallback_alias = routing_entry.fallback if routing_entry else None

                if fallback_alias is None:
                    logger.error(
                        "context_overflow_no_fallback",
                        task_type=task_type,
                        from_alias=alias,
                        estimated_tokens=estimated_in,
                        budget=budget,
                        user_id=user_id,
                    )
                    raise ContextOverflowError(
                        f"Prompt for task_type={task_type!r} estimated at "
                        f"{estimated_in} tokens exceeds budget {budget} "
                        f"(alias={alias!r}, num_ctx={num_ctx_to_send}, "
                        f"reserve={output_reserve}); no fallback configured."
                    )

                logger.warning(
                    "context_overflow_escalation",
                    task_type=task_type,
                    from_alias=alias,
                    to_alias=fallback_alias,
                    estimated_tokens=estimated_in,
                    budget=budget,
                    user_id=user_id,
                )

                fallback_config = self._models_config.models.get(fallback_alias)
                if fallback_config is None:
                    raise RoutingError(
                        f"Fallback alias {fallback_alias!r} (for task type "
                        f"{task_type!r}) not found in config"
                    )
                fallback_provider = self._providers.get(fallback_config.provider)
                if fallback_provider is None:
                    raise RoutingError(
                        f"Fallback provider {fallback_config.provider!r} "
                        f"not available (alias {fallback_alias!r})"
                    )

                provider = fallback_provider
                model_id = fallback_config.model
                alias = fallback_alias
                model_config = fallback_config
                num_ctx_to_send = None  # fallback is not Ollama
                overflow_escalated = True

        logger.info(
            "model_router_dispatch",
            task_type=task_type,
            model_alias=alias,
            model_id=model_id,
            task_id=task_id,
            estimated_tokens_in=estimated_in,
            overflow_escalated=overflow_escalated,
        )

        result, metadata = await resilient_call(
            provider.complete,
            prompt,
            model_id,
            category=TaskCategory.STANDARD,
            circuit_breaker=self._circuit_breaker,
            num_ctx=num_ctx_to_send,
        )

        # Shadow mode: fire secondary model in parallel if configured.
        routing = self._models_config.routing.get(task_type)
        if routing and routing.shadow and self._on_shadow_complete:
            asyncio.create_task(
                self._run_shadow(prompt, task_type, routing.shadow)
            )

        return result, metadata
```

- [ ] **Step 6: Run the router tests**

Run: `pytest tests/unit/test_router_context_budget.py -v`
Expected: 4 passed. If any fail, read the failure carefully — the most common miss is `num_ctx=None` being passed to `provider.complete` for Anthropic without the sink kwarg from Task 6. Do not alter the test to pass; fix the code.

- [ ] **Step 7: Run the full suite**

Run: `pytest -x -q`
Expected: All passes. The router's existing tests must still pass — the non-Ollama path is unchanged except for the two extra kwargs in the `logger.info` dispatch event.

- [ ] **Step 8: Commit**

```bash
git add src/donna/models/router.py tests/unit/test_router_context_budget.py
git commit -m "feat(model-layer): budget local prompts and escalate on context overflow"
```

---

## Task 8: Return the budget metadata from the router

The router now knows the estimate and the overflow flag, but it discards both before returning. Callers (input_parser, dedup, anything else constructing `InvocationMetadata`) need this data so it reaches `invocation_log`.

**Files:**
- Modify: `src/donna/models/types.py` — add two fields to `CompletionMetadata`.
- Modify: `src/donna/models/router.py` — populate them from the return of `provider.complete`.
- Modify: `src/donna/models/providers/ollama.py` and `anthropic.py` — both must preserve the new fields as defaults.
- Modify: `src/donna/orchestrator/input_parser.py` and `src/donna/tasks/dedup.py` — pass the metadata through to `InvocationMetadata`.
- Test: extend `tests/unit/test_router_context_budget.py` with a metadata-propagation assertion.

- [ ] **Step 1: Extend `CompletionMetadata`**

In `src/donna/models/types.py`, replace the dataclass:

```python
@dataclasses.dataclass(frozen=True)
class CompletionMetadata:
    """Metadata returned alongside every LLM completion."""

    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model_actual: str
    is_shadow: bool = False
    estimated_tokens_in: int | None = None
    overflow_escalated: bool = False
```

- [ ] **Step 2: Populate the fields in `ModelRouter.complete`**

Back in `src/donna/models/router.py`, replace the `return result, metadata` at the bottom of `complete()` with:

```python
        enriched_metadata = CompletionMetadata(
            latency_ms=metadata.latency_ms,
            tokens_in=metadata.tokens_in,
            tokens_out=metadata.tokens_out,
            cost_usd=metadata.cost_usd,
            model_actual=metadata.model_actual,
            is_shadow=metadata.is_shadow,
            estimated_tokens_in=estimated_in,
            overflow_escalated=overflow_escalated,
        )

        return result, enriched_metadata
```

- [ ] **Step 3: Pass the fields through at the two call-site loggers**

In `src/donna/orchestrator/input_parser.py`, find the existing `InvocationMetadata(...)` construction (around line 138) and add:

```python
                estimated_tokens_in=metadata.estimated_tokens_in,
                overflow_escalated=metadata.overflow_escalated,
```

Do the same in `src/donna/tasks/dedup.py` around line 233. Do not touch any other code in those files.

- [ ] **Step 4: Add the metadata propagation test**

Append to `tests/unit/test_router_context_budget.py`:

```python
@pytest.mark.asyncio
async def test_metadata_carries_estimated_and_overflow_flag() -> None:
    router, _, _ = _build_router()
    _, meta = await router.complete(prompt="x" * 200, task_type="generate_nudge")
    assert meta.estimated_tokens_in == 50  # 200 // 4
    assert meta.overflow_escalated is False


@pytest.mark.asyncio
async def test_metadata_marks_overflow_escalation() -> None:
    router, _, _ = _build_router()
    _, meta = await router.complete(prompt="x" * 2000, task_type="generate_nudge")
    assert meta.estimated_tokens_in == 500  # 2000 // 4
    assert meta.overflow_escalated is True
```

- [ ] **Step 5: Run the new tests**

Run: `pytest tests/unit/test_router_context_budget.py -v`
Expected: 6 passed (4 from Task 7 + 2 new).

- [ ] **Step 6: Run the full suite**

Run: `pytest -x -q`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/donna/models/types.py src/donna/models/router.py src/donna/orchestrator/input_parser.py src/donna/tasks/dedup.py tests/unit/test_router_context_budget.py
git commit -m "feat(model-layer): carry budget metadata through CompletionMetadata"
```

---

## Task 9: Surface the new fields on the invocation list API

**Files:**
- Modify: `src/donna/api/routes/admin_invocations.py`
- Test: integration check via the existing FastAPI test client (no new test file if one doesn't exist — see step 1)

- [ ] **Step 1: Locate the existing invocations API tests**

Run: `find tests -name "*admin_invocations*" -o -name "*invocations*" | head`
If a test file exists, extend it. If not, create `tests/integration/test_admin_invocations_context.py` using the pattern from other files in `tests/integration/` (start by reading one to confirm how `app.state.db` is set up — do not guess).

- [ ] **Step 2: Write the failing test**

Add a test that:
1. Sets up the in-memory DB with the new columns (mirror the CREATE TABLE from Task 5's test).
2. Inserts one row with `estimated_tokens_in=1480, overflow_escalated=0` and one with `estimated_tokens_in=8200, overflow_escalated=1`.
3. Hits `GET /admin/invocations?overflow_escalated=true` and asserts exactly one row comes back (the escalated one).
4. Hits `GET /admin/invocations` with no filter and asserts both rows carry `estimated_tokens_in` and `overflow_escalated` in the response.

Use the existing test harness pattern rather than inventing one. If you find you have to mock FastAPI app state in a way the rest of the suite does not, stop and ask.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/integration/test_admin_invocations_context.py -v`
Expected: Either a KeyError on the new response fields or a 422 on the new query param.

- [ ] **Step 4: Update `admin_invocations.py`**

Open `src/donna/api/routes/admin_invocations.py`. Add `overflow_escalated: bool | None = Query(default=None)` to the `list_invocations` signature, add the matching `WHERE` clause:

```python
    if overflow_escalated is not None:
        where_clauses.append("overflow_escalated = ?")
        params.append(overflow_escalated)
```

Add `estimated_tokens_in, overflow_escalated` to both SELECT columns (list and detail). Update both row-to-dict mappings to include the two new fields. Cast `overflow_escalated` via `bool(...)` as the existing `is_shadow` field does.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_admin_invocations_context.py -v`
Expected: Pass.

- [ ] **Step 6: Run the full suite**

Run: `pytest -x -q`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/donna/api/routes/admin_invocations.py tests/integration/test_admin_invocations_context.py
git commit -m "feat(api): expose context-budget fields on invocations endpoint"
```

---

## Task 10: Dashboard aggregate — estimation accuracy + overflow count

**Files:**
- Modify: `src/donna/api/routes/admin_dashboard.py` (the `get_llm_gateway_analytics` function around line 459)
- Test: create `tests/integration/test_llm_gateway_context_metrics.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_llm_gateway_context_metrics.py`. It should:
1. Set up the same in-memory `invocation_log` schema as Task 5/9.
2. Insert ~6 rows: 4 local (model_actual starts with `ollama/`) with varying estimate-vs-actual drift, 2 Anthropic, and 1 with `overflow_escalated=1`.
3. Hit `GET /admin/dashboard/llm-gateway?days=30`.
4. Assert the response contains a top-level `context_budget` block with:
   - `overflow_escalations_7d` (int, counts only rows where `overflow_escalated = 1` in the last 7 days regardless of range)
   - `overflow_escalations_range` (int, respects `days`)
   - `estimation_mae_pct` (float, computed only over rows where `model_actual LIKE 'ollama/%' AND estimated_tokens_in IS NOT NULL AND tokens_in > 0`)
   - `estimation_sample_count` (int, how many rows the MAE was computed over — caller can tell "0.0%" from "no data")

Follow the existing test-harness pattern (look at nearby integration tests for DB setup idiom).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_llm_gateway_context_metrics.py -v`
Expected: `KeyError: 'context_budget'` in the response.

- [ ] **Step 3: Extend the dashboard endpoint**

In `src/donna/api/routes/admin_dashboard.py`, inside `get_llm_gateway_analytics`, after the `by_caller` block and before the `return`, add:

```python
    # --- Context-budget metrics ---
    seven_days_ago = _days_ago(7)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM invocation_log "
        "WHERE timestamp >= ? AND overflow_escalated = 1",
        (seven_days_ago,),
    )
    overflow_7d = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM invocation_log "
        "WHERE timestamp >= ? AND overflow_escalated = 1",
        (since,),
    )
    overflow_range = (await cursor.fetchone())[0]

    cursor = await conn.execute(
        """SELECT estimated_tokens_in, tokens_in
           FROM invocation_log
           WHERE timestamp >= ?
               AND model_actual LIKE 'ollama/%'
               AND estimated_tokens_in IS NOT NULL
               AND tokens_in > 0""",
        (since,),
    )
    accuracy_rows = await cursor.fetchall()

    if accuracy_rows:
        errors = [
            abs(est - actual) / actual
            for est, actual in accuracy_rows
            if est is not None and actual > 0
        ]
        estimation_mae_pct = round(sum(errors) / len(errors) * 100, 2) if errors else 0.0
        sample_count = len(errors)
    else:
        estimation_mae_pct = 0.0
        sample_count = 0

    context_budget = {
        "overflow_escalations_7d": overflow_7d,
        "overflow_escalations_range": overflow_range,
        "estimation_mae_pct": estimation_mae_pct,
        "estimation_sample_count": sample_count,
    }
```

Then add `"context_budget": context_budget,` to the returned dict (right after `"days": days,`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_llm_gateway_context_metrics.py -v`
Expected: Pass.

- [ ] **Step 5: Run the full suite**

Run: `pytest -x -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/donna/api/routes/admin_dashboard.py tests/integration/test_llm_gateway_context_metrics.py
git commit -m "feat(dashboard): add context-budget aggregates to llm-gateway endpoint"
```

---

## Task 11: Frontend — `LLMGatewayData` type + summary tile

**Files:**
- Modify: `donna-ui/src/api/llmGateway.ts`
- Modify: `donna-ui/src/pages/LLMGateway/index.tsx`

- [ ] **Step 1: Extend the data type**

In `donna-ui/src/api/llmGateway.ts`, add to `LLMGatewayData`:

```typescript
export interface LLMGatewayContextBudget {
  overflow_escalations_7d: number;
  overflow_escalations_range: number;
  estimation_mae_pct: number;
  estimation_sample_count: number;
}

export interface LLMGatewayData {
  summary: {
    total_calls: number;
    internal_calls: number;
    external_calls: number;
    total_interrupted: number;
    avg_latency_ms: number;
    unique_callers: number;
  };
  time_series: LLMGatewayTimeSeriesEntry[];
  by_caller: LLMGatewayCallerEntry[];
  days: number;
  context_budget: LLMGatewayContextBudget;
}
```

- [ ] **Step 2: Render the summary tile**

In `donna-ui/src/pages/LLMGateway/index.tsx`, find the block where `Stat` components (for total calls, avg latency, etc.) are rendered. Add two new `Stat` tiles from the same component:

```tsx
<Stat
  label="Overflow escalations (7d)"
  value={data.context_budget.overflow_escalations_7d.toString()}
  tone={data.context_budget.overflow_escalations_7d > 0 ? "warn" : "neutral"}
/>
<Stat
  label="Estimation MAE"
  value={
    data.context_budget.estimation_sample_count > 0
      ? `${data.context_budget.estimation_mae_pct.toFixed(1)}%`
      : "—"
  }
  hint={`${data.context_budget.estimation_sample_count} samples`}
/>
```

If `Stat` does not accept `tone` or `hint`, use whichever props it does accept — grep the primitive's source first. The acceptance criteria is that the tiles render, not that the exact prop names match.

- [ ] **Step 3: Build the UI**

Run: `cd donna-ui && npm run build`
Expected: Clean build, no type errors. If TypeScript complains about `context_budget` being missing from existing fixture data in any test mock, add the field to those fixtures with all-zero values.

- [ ] **Step 4: Spin up the dev server and eyeball the page**

Run: `cd donna-ui && npm run dev`
Visit the LLM Gateway page. Confirm the two new tiles render alongside the existing summary tiles. With an empty DB, overflow count should be `0` and MAE should display as `—`.

- [ ] **Step 5: Commit**

```bash
git add donna-ui/src/api/llmGateway.ts donna-ui/src/pages/LLMGateway/index.tsx
git commit -m "feat(ui): surface context-budget tiles on LLM Gateway page"
```

---

## Task 12: Frontend — invocation list column + filter toggles

**Why:** The spec explicitly calls for an `est / actual` traffic-light column on the invocation list, plus two filter toggles ("Overflow escalations only" and "High estimation error"). Task 11 covered only the summary tiles — this task closes the gap.

**Files:**
- Modify: wherever `fetchInvocations` / `listInvocations` is defined in `donna-ui/src/api/` — grep for `admin/invocations` first to find the exact file.
- Modify: the invocations table view — grep for `invocations` under `donna-ui/src/pages/` to locate it (likely under `DevPrimitives`, `LLMGateway`, or a dedicated page; confirm before editing).

- [ ] **Step 1: Locate the invocations table**

Run: `grep -r "admin/invocations" donna-ui/src --include="*.ts" --include="*.tsx" -l`
Open each returned file. Identify the API function and the component that renders the list.

- [ ] **Step 2: Extend the invocation row type**

In the API file where the invocation shape is declared, add the new fields:

```typescript
export interface Invocation {
  // ... existing fields ...
  estimated_tokens_in: number | null;
  overflow_escalated: boolean;
}
```

And add an optional filter param to the fetch function:

```typescript
export async function fetchInvocations(params: {
  // ... existing params ...
  overflow_escalated?: boolean;
}): Promise<InvocationListResponse> {
  const { data } = await client.get("/admin/invocations", { params });
  return data;
}
```

- [ ] **Step 3: Add the `est / actual` column**

In the table component, add a new column to the `ColumnDef[]` array. Use the same cell-rendering style as existing columns (inspect a neighbor for idiom). The cell logic:

```tsx
{
  id: "estimation",
  header: "est / actual",
  cell: ({ row }) => {
    const est = row.original.estimated_tokens_in;
    const actual = row.original.tokens_in;
    if (est == null || actual === 0) return <span style={{ color: "var(--color-text-muted)" }}>—</span>;
    const errorPct = Math.abs(est - actual) / actual;
    const color =
      errorPct < 0.15 ? "var(--color-success)" :
      errorPct < 0.30 ? "var(--color-warn)" :
      "var(--color-danger)";
    return (
      <span style={{ display: "flex", alignItems: "center", gap: "var(--space-1)" }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: color }} />
        <span style={{ fontFamily: "var(--font-mono)" }}>
          {est.toLocaleString()} / {actual.toLocaleString()}
        </span>
      </span>
    );
  },
},
```

If your theme uses different CSS variable names for status colors, adapt to whatever the rest of the UI uses — grep `color-success` / `color-warn` first to confirm.

- [ ] **Step 4: Add the two filter toggles**

In the filter bar component that wraps the table, add two new toggle controls using the same primitive the other filters use (likely `Segmented`, a `Checkbox`, or a `ToggleGroup` — look at the existing filter row first). Each toggle updates a piece of state that is forwarded to `fetchInvocations`:

```tsx
const [overflowOnly, setOverflowOnly] = useState(false);
const [highErrorOnly, setHighErrorOnly] = useState(false);

// When calling the API:
const params = {
  ...otherParams,
  overflow_escalated: overflowOnly ? true : undefined,
};
```

"High estimation error" is a client-side filter for now — after the data loads, filter the rendered rows to only those where `est != null && actual > 0 && Math.abs(est - actual) / actual >= 0.25`. The backend does not need a new filter for this: the set of rows with high error is small and already loaded.

- [ ] **Step 5: Build the UI**

Run: `cd donna-ui && npm run build`
Expected: Clean build, no type errors. Fix any mocks in unit tests that need the new fields.

- [ ] **Step 6: Dev-server sanity check**

Run: `cd donna-ui && npm run dev`
Visit the invocations list. Confirm:
- The new column renders with "—" for pre-migration rows (they have null `estimated_tokens_in`).
- Toggling "Overflow only" hits `/admin/invocations?overflow_escalated=true` (check the network tab).
- Toggling "High estimation error" filters the visible rows without a second network request.

- [ ] **Step 7: Commit**

```bash
git add donna-ui/src/api donna-ui/src/pages
git commit -m "feat(ui): invocation list column and filters for context budgeting"
```

---

## Task 13: Docs — update `docs/model-layer.md`

**Files:**
- Modify: `docs/model-layer.md`

- [ ] **Step 1: Add a new section**

Append a new section after "Local Model Cost Approximation":

```markdown
## Local LLM Context Window Strategy

Ollama defaults to a 2048-token window unless `num_ctx` is explicitly set. Donna configures it on every Ollama call via two knobs in `config/donna_models.yaml`:

- `ollama.default_num_ctx` — the total window (prompt + output) for all Ollama aliases.
- `ollama.default_output_reserve` — tokens held aside for model output so the prompt budget never clips mid-generation.

Per-alias overrides live on the individual model entry: `models.<alias>.num_ctx`.

### Pre-dispatch budgeting

Before dispatching to a local alias, `ModelRouter` estimates prompt tokens (`len(prompt) // 4`) and compares against `num_ctx - output_reserve`. If the estimate exceeds the budget:

1. If the task type has a `fallback` configured, the call escalates to the cloud alias. A `context_overflow_escalation` warn event is logged, and `invocation_log.overflow_escalated` is set to `1`.
2. If no fallback exists, the router raises `ContextOverflowError`. This is deliberate — silent truncation produces silent garbage.

Every call to an Ollama alias records `invocation_log.estimated_tokens_in` alongside the actual `tokens_in` reported by Ollama. The LLM Gateway dashboard surfaces mean absolute error as a gauge for when to upgrade the estimator to exact tokenization.

### Future extensions (explicitly deferred)

The following are documented as deferred in `docs/superpowers/specs/2026-04-12-local-llm-context-strategy-design.md`:

- Per-task-type compaction strategies (rolling summary, map-reduce, RAG).
- `pgvector` "brain" on Supabase for long-history retrieval.
- Exact tokenization via Ollama `/api/tokenize`.
- Per-alias daily caps on overflow escalations.
```

- [ ] **Step 2: Commit**

```bash
git add docs/model-layer.md
git commit -m "docs(model-layer): document context window budgeting flow"
```

---

## Final Verification

- [ ] **Step 1: Run the full backend suite**

Run: `pytest -q`
Expected: All tests pass.

- [ ] **Step 2: Run lint / type check (whatever CI runs)**

Run: `ruff check src/ tests/ && pyright src/donna/models src/donna/logging src/donna/api/routes`
(Exact command depends on repo — if `ruff` or `pyright` is not the tool, use whatever is configured in `pyproject.toml` / CI.)
Expected: Clean.

- [ ] **Step 3: Apply the migration to the real development DB**

Run: `alembic upgrade head`
Expected: The new migration runs. Verify the columns exist on the dev DB.

- [ ] **Step 4: Smoke-test a local Ollama call end-to-end**

If a local Ollama server is running, trigger a `generate_nudge` task (via the CLI or a small script) and confirm:
- The request payload sent to Ollama contains `options.num_ctx: 8192` (check `ollama_completion` log line).
- The resulting `invocation_log` row has `estimated_tokens_in` populated and `overflow_escalated = 0`.

If no Ollama server is running locally, skip this step but note it in the PR description.

- [ ] **Step 5: Smoke-test the dashboard**

Run: `cd donna-ui && npm run dev`
Visit the LLM Gateway page and confirm the two new tiles render without errors.

---

## Self-Review (run before handing off)

**Spec coverage:**

- ✅ Fix `num_ctx` footgun — Task 6 sends it on every Ollama call.
- ✅ Config surface — Tasks 2 and 3.
- ✅ Router budgeting + escalation — Tasks 7 and 8.
- ✅ `ContextOverflowError` — Task 7.
- ✅ Alembic migration — Task 4.
- ✅ `invocation_log` propagation — Tasks 5 and 8.
- ✅ Structured `context_overflow_escalation` warn event — Task 7.
- ✅ Dashboard summary tile + MAE — Tasks 10 and 11.
- ✅ Invocation list `est / actual` column with traffic light — Task 12.
- ✅ Invocation filter for overflow-only — Task 9 (backend) + Task 12 (UI toggle).
- ✅ "High estimation error" filter — Task 12 (client-side).
- ✅ Docs — Task 13.

**Type consistency:** `num_ctx`, `estimated_tokens_in`, `overflow_escalated`, `ContextOverflowError` are spelled the same across every task.

**No placeholders:** Every code step has concrete code. Tasks 9, 10, and 12 use "follow existing test harness / component pattern" rather than spelling out the exact fixture — this is intentional because the idioms already exist in-repo and copying the wrong one would create churn. The implementer is directed to read a neighboring file first.
