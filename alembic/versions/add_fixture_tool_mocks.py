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
        fp_key = f"{tool}:{canonical}"
        mocks[fp_key] = result
    return mocks
