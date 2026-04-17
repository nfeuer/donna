"""merge chat + context-budget heads

Revision ID: 42bdc9502b1b
Revises: f8b2d4e6a913, f1b8c2d4e703
Create Date: 2026-04-15 09:03:00.270508
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '42bdc9502b1b'
down_revision: Union[str, None] = ('f8b2d4e6a913', 'f1b8c2d4e703')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
