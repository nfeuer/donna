"""add tasks.time_intent_json

Revision ID: 933f74c55f24
Revises: c8e1f2a3b4d5
Create Date: 2026-06-06 00:42:06.505402
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '933f74c55f24'
down_revision: Union[str, None] = 'c8e1f2a3b4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("time_intent_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "time_intent_json")
