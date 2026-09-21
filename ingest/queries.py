"""Read-only views of what has been imported, for the Data & imports screen.

Plain selects, run through the read-only role by their callers. Nothing here is
a tool: the model never sees import history, only the snapshots it produced.
"""

import datetime as dt
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from db.models import ImportBatch


@dataclass(frozen=True)
class BatchView:
    id: uuid.UUID
    filename: str
    source_label: str
    normalizer: str
    as_of: dt.date
    rows: int
    status: str
    imported_at: dt.datetime


def batches(conn: sa.Connection) -> list[BatchView]:
    rows = conn.execute(
        sa.select(
            ImportBatch.id,
            ImportBatch.original_filename,
            ImportBatch.source_label,
            ImportBatch.normalizer,
            ImportBatch.as_of,
            ImportBatch.row_count,
            ImportBatch.status,
            ImportBatch.imported_at,
        ).order_by(ImportBatch.imported_at.desc())
    ).all()
    return [BatchView(*row) for row in rows]


def imported_hashes(conn: sa.Connection) -> dict[str, tuple[uuid.UUID, dt.date]]:
    """File hash -> (batch, as_of). How the file list knows a file is already
    in, even after it has been renamed."""
    rows = conn.execute(sa.select(ImportBatch.file_sha256, ImportBatch.id, ImportBatch.as_of))
    return {sha: (batch_id, as_of) for sha, batch_id, as_of in rows}
