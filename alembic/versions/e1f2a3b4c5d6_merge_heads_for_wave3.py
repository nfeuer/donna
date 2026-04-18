"""merge c2d3e4f5a6b7 + d0e1f2a3b4c5 heads before Wave 3

Revision ID: e1f2a3b4c5d6
Revises: c2d3e4f5a6b7, d0e1f2a3b4c5
Create Date: 2026-04-17 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

revision: str = "e1f2a3b4c5d6"
down_revision: Union[tuple, str, None] = ("c2d3e4f5a6b7", "d0e1f2a3b4c5")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
