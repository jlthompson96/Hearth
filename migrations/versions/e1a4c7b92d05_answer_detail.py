"""answer detail

The "Less / Normal / More" control under an answer re-asks the question at a
different detail level. The level an answer was given at is stored with it, so a
rerun still reads as one after a reload. Nullable: answers from before this
have no level, which is not the same as "normal".

Revision ID: e1a4c7b92d05
Revises: c5d8e2f3a1b7
Create Date: 2026-09-22 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1a4c7b92d05"
down_revision: Union[str, Sequence[str], None] = "c5d8e2f3a1b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("message", sa.Column("detail", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "detail")
