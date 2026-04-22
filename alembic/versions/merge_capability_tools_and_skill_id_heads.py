"""merge capability_tools_json and skill_id_to_invocation_log heads

Revision ID: d1e2f3a4b5c6
Revises: b7c8d9e0f1a2, b9d2e4f6a135
Create Date: 2026-04-22 00:00:00.000000
"""

from typing import Sequence, Union

revision: str = "d1e2f3a4b5c6"
down_revision: Union[tuple, str, None] = ("b7c8d9e0f1a2", "b9d2e4f6a135")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
