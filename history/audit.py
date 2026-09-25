"""Read-only access to the search egress audit."""

import datetime as dt
import uuid
from dataclasses import dataclass

import sqlalchemy as sa

from db.models import SearchAudit


@dataclass(frozen=True)
class AuditEntry:
    id: int
    query: str
    allowed: bool
    violation: str | None
    result_count: int | None
    thread_id: uuid.UUID | None
    created_at: dt.datetime


def entries(
    conn: sa.Connection,
    *,
    limit: int = 50,
    before: dt.datetime | None = None,
) -> list[AuditEntry]:
    """Return audit entries newest first, with timestamp pagination."""
    query = sa.select(SearchAudit).order_by(SearchAudit.created_at.desc(), SearchAudit.id.desc())
    if before is not None:
        query = query.where(SearchAudit.created_at < before)
    rows = conn.execute(query.limit(limit)).scalars().all()
    return [
        AuditEntry(
            id=row.id,
            query=row.query,
            allowed=row.allowed,
            violation=row.violation,
            result_count=row.result_count,
            thread_id=row.thread_id,
            created_at=row.created_at,
        )
        for row in rows
    ]
