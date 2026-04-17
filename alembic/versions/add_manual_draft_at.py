"""add skill_candidate_report.manual_draft_at column for manual draft trigger

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-04-17 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.add_column(sa.Column("manual_draft_at", sa.Text(), nullable=True))
        batch_op.create_index(
            "ix_skill_candidate_report_manual_draft_at",
            ["manual_draft_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_candidate_report_manual_draft_at")
        batch_op.drop_column("manual_draft_at")
