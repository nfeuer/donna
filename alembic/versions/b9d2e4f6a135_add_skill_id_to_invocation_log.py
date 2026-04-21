"""add skill_id column to invocation_log

Revision ID: b9d2e4f6a135
Revises: a6b7c8d9e0f1
Create Date: 2026-04-21 00:00:00.000000

Wave 3 (F-12): enables per-skill cost aggregation in the skill-system
Grafana dashboard without parsing the `skill_step::<capability>::<step>`
task_type string. Nullable — non-skill invocations (parse_task, dedup_check)
continue to write skill_id=NULL.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9d2e4f6a135"
down_revision: Union[str, None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("skill_id", sa.String(length=36), nullable=True))
        batch_op.create_index(
            "ix_invocation_log_skill_id", ["skill_id"], unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.drop_index("ix_invocation_log_skill_id")
        batch_op.drop_column("skill_id")
