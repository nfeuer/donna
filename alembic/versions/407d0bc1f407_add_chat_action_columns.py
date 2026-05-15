"""add chat action columns

Revision ID: 407d0bc1f407
Revises: d2e3f4a5b6c7
Create Date: 2026-05-15 18:53:15.021928
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '407d0bc1f407'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_sessions",
        sa.Column("pending_action", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("action_name", sa.String(100), nullable=True),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("action_result", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "action_result")
    op.drop_column("conversation_messages", "action_name")
    op.drop_column("conversation_sessions", "pending_action")
