"""Thread history: the list, a search over it, one thread read back, pinning
and deleting.

Reads go through the read-only role. Writes — pinning, deleting — use
`db.writer`; storing turns is the chat route's job, as they stream.
"""

import datetime as dt
import uuid
from decimal import Decimal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

import preferences
from api.refusals import REFUSALS
from db.session import readonly_connection
from db.writer import writer_connection
from history import store

router = APIRouter(prefix="/api/threads", tags=["threads"])


class SnippetPart(BaseModel):
    text: str
    match: bool


class ThreadSummary(BaseModel):
    id: uuid.UUID
    title: str | None
    updated_at: dt.datetime
    pinned: bool
    snippet: list[SnippetPart] | None


class ThreadListing(BaseModel):
    #: Shown under the list, so the rule is visible where it applies.
    retention_days: int
    threads: list[ThreadSummary]


class ToolCall(BaseModel):
    name: str
    args: dict[str, object]


class StoredMessage(BaseModel):
    id: uuid.UUID
    role: str
    agent: str | None
    content: str
    tool_calls: list[ToolCall] | None
    refused: bool
    confidence: Decimal | None
    #: Figures the answer stated that no tool returned. Shown as a warning.
    ungrounded: list[str] | None
    #: brief, normal or detailed; None for answers from before the control.
    detail: str | None
    created_at: dt.datetime


class ThreadDetail(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    pinned: bool
    messages: list[StoredMessage]


class ThreadChange(BaseModel):
    pinned: bool


@router.get("", response_model=ThreadListing, summary="Threads, or those matching a search")
def list_threads(
    q: str | None = Query(default=None, max_length=200, description="Words to search for"),
) -> ThreadListing:
    with readonly_connection() as conn:
        found = store.list_threads(conn, query=q)
        kept = preferences.days(conn, "thread_retention_days")
    return ThreadListing(
        retention_days=kept.days,
        threads=[
            ThreadSummary(
                id=t.id,
                title=t.title,
                updated_at=t.updated_at,
                pinned=t.pinned,
                snippet=[SnippetPart(text=p.text, match=p.match) for p in t.snippet]
                if t.snippet is not None
                else None,
            )
            for t in found
        ],
    )


@router.get("/{thread_id}", response_model=ThreadDetail, responses=REFUSALS, summary="One thread")
def get_thread(thread_id: uuid.UUID) -> ThreadDetail:
    with readonly_connection() as conn:
        detail = store.get_thread(conn, thread_id)
    return ThreadDetail(
        id=detail.id,
        title=detail.title,
        created_at=detail.created_at,
        updated_at=detail.updated_at,
        pinned=detail.pinned,
        messages=[
            StoredMessage(
                id=m.id,
                role=m.role,
                agent=m.agent,
                content=m.content,
                tool_calls=[ToolCall.model_validate(c) for c in m.tool_calls]
                if m.tool_calls
                else None,
                refused=m.refused,
                confidence=m.confidence,
                ungrounded=m.ungrounded,
                detail=m.detail,
                created_at=m.created_at,
            )
            for m in detail.messages
        ],
    )


@router.patch("/{thread_id}", status_code=204, responses=REFUSALS, summary="Pin or unpin a thread")
def change_thread(thread_id: uuid.UUID, change: ThreadChange) -> Response:
    with writer_connection() as conn:
        store.set_pinned(conn, thread_id, change.pinned, now=dt.datetime.now(dt.UTC))
    return Response(status_code=204)


@router.delete("/{thread_id}", status_code=204, responses=REFUSALS, summary="Delete a thread now")
def delete_thread(thread_id: uuid.UUID) -> Response:
    with writer_connection() as conn:
        store.delete_thread(conn, thread_id)
    return Response(status_code=204)
