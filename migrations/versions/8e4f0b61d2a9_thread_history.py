"""thread history

Phase 8 stores conversations, and a stored conversation has to be redrawn the
way it looked: which tools an answer called, whether it was a refusal rather
than an answer, and how sure the router was about who answered. Plus pinning,
which is what exempts a thread from the one-year retention window.

All additive and nullable (or defaulted), so existing rows need nothing.

Revision ID: 8e4f0b61d2a9
Revises: 3c1e9a7b2f40
Create Date: 2026-09-21 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8e4f0b61d2a9"
down_revision: Union[str, Sequence[str], None] = "3c1e9a7b2f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("thread", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "message",
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "message",
        sa.Column("refused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "message", sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("message", "confidence")
    op.drop_column("message", "refused")
    op.drop_column("message", "tool_calls")
    op.drop_column("thread", "pinned_at")
