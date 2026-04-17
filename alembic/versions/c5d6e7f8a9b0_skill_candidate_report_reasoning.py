"""add skill_candidate_report.reasoning for claude_native_registered rows

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-04-17 00:00:05.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reasoning", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.drop_column("reasoning")
