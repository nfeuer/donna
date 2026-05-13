"""merge reply handler and immich nullable heads

Revision ID: 9c6cfd1afb9c
Revises: a1b2c3d4e5f0, e3f4a5b6c7d8
Create Date: 2026-05-12 21:56:41.911550
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c6cfd1afb9c'
down_revision: Union[str, None] = ('a1b2c3d4e5f0', 'e3f4a5b6c7d8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
