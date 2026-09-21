"""ungrounded figures

Every answer's dollar amounts and weights are checked against the numbers its
tools returned (agents.grounding). The ones found nowhere are stored with the
message, so the warning a reader saw survives a reload.

Revision ID: c5d8e2f3a1b7
Revises: 8e4f0b61d2a9
Create Date: 2026-09-21 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5d8e2f3a1b7"
down_revision: Union[str, Sequence[str], None] = "8e4f0b61d2a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column("ungrounded", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("message", "ungrounded")
