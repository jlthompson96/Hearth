"""drop unused columns

`thread.archived_at` and `message.token_count` came with the initial schema and
nothing has ever written either: retention deletes a thread rather than
archiving it, and token counts are kept per model call in `model_log`, where
LM Studio reports them. A column that is always null reads as meaningful in an
export, so they go. The downgrade puts both back, empty, as they always were.

Revision ID: f2c8a91d4e67
Revises: b7d2e9f41c63
Create Date: 2026-10-07 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c8a91d4e67"
down_revision: Union[str, Sequence[str], None] = "b7d2e9f41c63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("thread", "archived_at")
    op.drop_column("message", "token_count")


def downgrade() -> None:
    op.add_column("message", sa.Column("token_count", sa.Integer(), nullable=True))
    op.add_column(
        "thread", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
