"""add pattern_fingerprint column + index to skill_candidate_report

The ``status`` column is a plain ``VARCHAR(20)`` with no CHECK constraint
(see ``add_lifecycle_tables_phase_3.py``), so widening the accepted set to
include ``'claude_native_registered'`` requires no DDL — application code
simply writes the new value. Wave 3 only needs the fingerprint column to
dedupe reports keyed on the pattern Claude recognised.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-04-17 00:00:01.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pattern_fingerprint", sa.Text(), nullable=True))
        batch_op.create_index(
            "ix_skill_candidate_report_pattern_fingerprint",
            ["pattern_fingerprint"],
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_candidate_report_pattern_fingerprint")
        batch_op.drop_column("pattern_fingerprint")
