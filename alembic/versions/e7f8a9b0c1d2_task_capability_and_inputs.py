"""add tasks.capability_name + tasks.inputs_json for Wave 3 intent dispatcher
"""
from __future__ import annotations

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("capability_name", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("inputs_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("inputs_json")
        batch_op.drop_column("capability_name")
