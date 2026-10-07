"""answer provenance

Each stored answer names the model that gave it and a hash of what the
specialist was told (agents/provenance.py). The Model log holds the same facts
for 90 days by default, and the chat model can change at runtime; an answer is
kept for a year. Nullable: answers from before this carry neither, and a
decline, a refusal or a search has no specialist prompt to hash.

Revision ID: a7d3e5b1c902
Revises: f2c8a91d4e67
Create Date: 2026-10-07 11:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3e5b1c902"
down_revision: Union[str, Sequence[str], None] = "f2c8a91d4e67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("message", sa.Column("model", sa.Text(), nullable=True))
    op.add_column("message", sa.Column("prompt_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "prompt_hash")
    op.drop_column("message", "model")
