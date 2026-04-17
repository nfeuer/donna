"""merge auth and skill system heads

Revision ID: c2d3e4f5a6b7
Revises: a1c9d3e5f701, b8c9d0e1f2a3
Create Date: 2026-04-17 00:00:00.000000
"""

from typing import Sequence, Union

revision: str = "c2d3e4f5a6b7"
down_revision: Union[tuple, str, None] = ("a1c9d3e5f701", "b8c9d0e1f2a3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
