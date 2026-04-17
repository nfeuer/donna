"""add UNIQUE(user_id, name) to automation

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-04-17 00:00:03.000000

Wave 3 Task 9 — AutomationCreationPath relies on the repository raising
``AlreadyExistsError`` (wrapping ``aiosqlite.IntegrityError``) when a user
approves the same draft twice. That only triggers if the underlying schema
enforces uniqueness on (user_id, name).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.create_index(
            "uq_automation_user_id_name",
            ["user_id", "name"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.drop_index("uq_automation_user_id_name")
