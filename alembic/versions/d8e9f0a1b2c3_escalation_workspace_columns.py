"""slice 19: escalation_workspace_columns — prompt_body, summary, mode, result, validation_result

Adds the columns the dashboard escalation workspace needs to render and
store submissions. The spec §5.2/§5.3 references ``escalation_request.prompt_body``
explicitly; slice 17 omitted it (storing only ``prompt_path``). Slice 19
backfills the missing columns so the dashboard can render the full prompt
without filesystem access and accept submissions through the same row.

Realizes docs/superpowers/specs/manual-escalation.md §6.3(b) (escalation
workspace) and §8 (data model — adds the columns referenced inline in
§5.2/§5.3 but not enumerated in §8 originally).

Revision ID: d8e9f0a1b2c3
Revises: e2f3a4b5c6d7
Create Date: 2026-05-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("escalation_request", schema=None) as batch_op:
        batch_op.add_column(sa.Column("prompt_body", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("mode", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("result", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("validation_result", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("escalation_request", schema=None) as batch_op:
        batch_op.drop_column("validation_result")
        batch_op.drop_column("result")
        batch_op.drop_column("mode")
        batch_op.drop_column("summary")
        batch_op.drop_column("prompt_body")
