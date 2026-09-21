"""import as_of and cash lines

Two things Phase 2 needs that Phase 1 could not have known.

`import_batch.as_of`: the date an export describes, stated at import. The raw
rows are the recovery path for a normalizer bug found months later, and
re-normalizing them needs the date they were for. Without it here, the only
place that date survives is the snapshots the bug may have written wrong.

`holding_snapshot.quantity` becomes nullable: a cash line in a positions export
reports a value and no share count. Price was already nullable for the same
reason.

`as_of` is added NOT NULL with no default, which fails on a table that already
has rows. That is deliberate: no batch exists before Phase 2, and one that did
would have no honest date to backfill.

Revision ID: 3c1e9a7b2f40
Revises: d68e1bfcda46
Create Date: 2026-09-21 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3c1e9a7b2f40"
down_revision: Union[str, Sequence[str], None] = "d68e1bfcda46"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("import_batch", sa.Column("as_of", sa.Date(), nullable=False))
    op.alter_column(
        "holding_snapshot",
        "quantity",
        existing_type=sa.Numeric(precision=24, scale=8),
        nullable=True,
    )


def downgrade() -> None:
    # Fails if a cash line has been imported, rather than inventing a quantity
    # for it. A downgrade that makes data up is worse than one that stops.
    op.alter_column(
        "holding_snapshot",
        "quantity",
        existing_type=sa.Numeric(precision=24, scale=8),
        nullable=False,
    )
    op.drop_column("import_batch", "as_of")
