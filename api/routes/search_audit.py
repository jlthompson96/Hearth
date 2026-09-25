"""The local search egress audit."""

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from db.session import readonly_connection
from history import audit

router = APIRouter(prefix="/api/search-audit", tags=["search audit"])


class SearchAuditEntryOut(BaseModel):
    id: int
    query: str
    allowed: bool
    violation: str | None
    result_count: int | None
    thread_id: uuid.UUID | None
    created_at: dt.datetime


class SearchAuditListing(BaseModel):
    entries: list[SearchAuditEntryOut]


@router.get("", response_model=SearchAuditListing, summary="Search egress audit")
def list_entries(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[dt.datetime | None, Query(description="Older than this time")] = None,
) -> SearchAuditListing:
    with readonly_connection() as conn:
        found = audit.entries(conn, limit=limit, before=before)
    return SearchAuditListing(
        entries=[SearchAuditEntryOut.model_validate(entry) for entry in found]
    )