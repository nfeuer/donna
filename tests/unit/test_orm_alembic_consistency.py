"""Slice 24 — ORM / Alembic schema-consistency regression.

Catches the drift category that broke slice-21 columns
(``originating_entity_type``, ``human_review`` etc.) and slice-22's
``tool_request`` table off the SQLAlchemy ORM:
``Base.metadata.create_all`` (the path tests use) silently dropped
columns that Alembic added, so any test fixture that did not run
``alembic upgrade head`` raised ``OperationalError`` on the first
write. The chat-mode E2E was the canary; the assertion here is the
guard.

We diff column-sets — types are intentionally not compared because
SQLite reports ``BOOLEAN`` for ``Boolean()`` and ``INTEGER`` for the
slice-22 ``priority`` server-default in either source, but Alembic and
the ORM disagree on display strings. Slice 24's mandate is *survives*
``Base.metadata.create_all`` matching ``alembic upgrade head``;
column-presence is the contract that buys us that.

Realises docs/superpowers/specs/manual-escalation.md §10.9 (multi-user
readiness — ``user_id`` discipline) and §11 (acceptance — failure-mode
regression coverage). Surfaces in followups.md when columns drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from donna.tasks.db_models import Base

# Tables Donna writes to via raw SQL (not via the ORM) and therefore
# need to exist after ``Base.metadata.create_all``. Add a row here
# whenever a new Alembic migration lands a table.
_TABLES_REQUIRED_IN_ORM = {
    "escalation_request",
    "tool_request",
    "daily_budget_extension",
    "dashboard_setting",
    "invocation_log",
    "tasks",
}


@pytest.fixture
def alembic_engine(tmp_path: Path) -> sa.Engine:
    """Run ``alembic upgrade head`` against a temp SQLite file."""
    db_path = tmp_path / "alembic.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")
    return engine


@pytest.fixture
def orm_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _columns(engine: sa.Engine, table: str) -> set[str]:
    insp = sa.inspect(engine)
    return {c["name"] for c in insp.get_columns(table)}


class TestORMMatchesAlembicSchema:
    """One assertion per table: ORM column-set == Alembic column-set.

    A failure here means a migration added/removed a column without
    updating ``src/donna/tasks/db_models.py``. Either update the ORM
    or — if the column is genuinely Alembic-only — explicitly drop it
    from ``_TABLES_REQUIRED_IN_ORM`` with a comment.
    """

    @pytest.mark.parametrize("table", sorted(_TABLES_REQUIRED_IN_ORM))
    def test_table_columns_match(
        self,
        alembic_engine: sa.Engine,
        orm_engine: sa.Engine,
        table: str,
    ) -> None:
        alembic_cols = _columns(alembic_engine, table)
        orm_cols = _columns(orm_engine, table)
        missing_in_orm = alembic_cols - orm_cols
        extra_in_orm = orm_cols - alembic_cols
        assert not missing_in_orm and not extra_in_orm, (
            f"ORM/Alembic schema drift on {table!r}: "
            f"missing in ORM={sorted(missing_in_orm)}, "
            f"extra in ORM={sorted(extra_in_orm)}"
        )

    def test_every_required_table_present_in_alembic(
        self,
        alembic_engine: sa.Engine,
    ) -> None:
        present = set(sa.inspect(alembic_engine).get_table_names())
        missing = _TABLES_REQUIRED_IN_ORM - present
        assert not missing, f"alembic head missing tables: {sorted(missing)}"

    def test_every_required_table_present_in_orm(
        self,
        orm_engine: sa.Engine,
    ) -> None:
        present = set(sa.inspect(orm_engine).get_table_names())
        missing = _TABLES_REQUIRED_IN_ORM - present
        assert not missing, f"ORM missing tables: {sorted(missing)}"

    def test_parent_escalation_id_index_present(
        self,
        alembic_engine: sa.Engine,
        orm_engine: sa.Engine,
    ) -> None:
        """Slice 25 — recursive-CTE chain walk needs the parent index.

        Both ``alembic upgrade head`` (via revision
        ``f0a1b2c3d4e5_re_escalation_parent_index``) and the ORM
        (via ``mapped_column(..., index=True)``) must produce the
        ``ix_escalation_request_parent_escalation_id`` index. Drift
        here would silently regress
        :meth:`EscalationRepository.find_chain_depth` to a full-scan.
        """
        index_name = "ix_escalation_request_parent_escalation_id"
        alembic_indexes = {
            ix["name"]
            for ix in sa.inspect(alembic_engine).get_indexes("escalation_request")
        }
        orm_indexes = {
            ix["name"]
            for ix in sa.inspect(orm_engine).get_indexes("escalation_request")
        }
        assert index_name in alembic_indexes, (
            f"alembic head missing {index_name} on escalation_request: "
            f"{sorted(alembic_indexes)}"
        )
        assert index_name in orm_indexes, (
            f"ORM missing {index_name} on escalation_request: "
            f"{sorted(orm_indexes)}"
        )
