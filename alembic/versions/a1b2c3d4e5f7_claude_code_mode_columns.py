"""slice 21: claude_code_mode_columns — human_review, target_paths, originating_entity, base_sha, merged_at

Adds the columns the claude_code manual-handoff path needs:

- ``human_review`` / ``merged_at`` — terminal flags written when the
  iteration cap is reached or after the user has merged the validated
  branch into ``main``. Both are tracking-only (Donna does not auto-merge
  per spec §15 / §5.3).
- ``target_paths`` — JSON snapshot of the rendered scope globs at
  gate-fire time, so the dashboard renders the exact scope even after
  config edits mid-flight (spec §10.7 row 2).
- ``originating_entity_type`` / ``originating_entity_id`` — explicit
  pointer to the row that triggered the escalation (e.g.
  ``('skill_candidate_report', candidate.id)`` for ``skill_auto_draft``).
  ``escalation_request.task_id`` is always NULL for these task types
  (auto_drafter.py:171, evolution.py:114), so this pair is the only way
  the diff validator can substitute ``{name}`` into target_paths globs.
- ``base_sha`` — pinned ``main`` SHA at gate-fire time, baked into the
  worktree command so subsequent merges of *other* validated branches
  don't move the floor under an in-progress build.

Realizes docs/superpowers/specs/manual-escalation.md §5.3 (claude_code
mode) and §8 (data model — adds columns referenced inline in §5.3 but not
listed in §8 originally; §8 is updated in the same PR per the slice 21
drift checklist).

Revision ID: a1b2c3d4e5f7
Revises: d8e9f0a1b2c3
Create Date: 2026-05-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("escalation_request", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "human_review",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(sa.Column("target_paths", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("originating_entity_type", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("originating_entity_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("base_sha", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("merged_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("escalation_request", schema=None) as batch_op:
        batch_op.drop_column("merged_at")
        batch_op.drop_column("base_sha")
        batch_op.drop_column("originating_entity_id")
        batch_op.drop_column("originating_entity_type")
        batch_op.drop_column("target_paths")
        batch_op.drop_column("human_review")
