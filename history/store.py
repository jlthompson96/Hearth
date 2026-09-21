"""Storing threads, reading them back, finding them, and forgetting them.

Search is Postgres full-text over `to_tsvector('english', content)` — the exact
expression the GIN index was built on in Phase 1, so the index is used. English
stemming is the point: the word you remember typing is "squat", the one you
typed was "squats". Embeddings would find what is *similar*; you are looking for
a thread you wrote, where exact terms win, and full-text costs no VRAM.

Retention is a year from a thread's last message, pinned threads exempt. It is
enforced by `sweep`, which runs when the API starts and whenever a thread is
started, so it holds even if the backend runs for a year without a restart.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

from db.models import Message, Thread
from ingest.errors import NotFound

#: The decision, made on 2026-09-21: a year after the last message, unless
#: pinned. "Forever by default" was ruled out in the plan.
RETENTION = dt.timedelta(days=365)

#: Marks around each match in a search snippet. Control characters, so no text
#: anyone typed can collide with them; split out in Python, never sent on.
_START, _STOP = chr(2), chr(3)
_HEADLINE_OPTIONS = f"StartSel={_START}, StopSel={_STOP}, MinWords=6, MaxWords=18"


@dataclass(frozen=True)
class SnippetPart:
    text: str
    match: bool


@dataclass(frozen=True)
class ThreadSummary:
    id: uuid.UUID
    title: str | None
    updated_at: dt.datetime
    pinned: bool
    #: Only for a search: the best-matching message, matches marked.
    snippet: tuple[SnippetPart, ...] | None


@dataclass(frozen=True)
class StoredMessage:
    id: uuid.UUID
    role: str
    agent: str | None
    content: str
    tool_calls: list[dict[str, object]] | None
    refused: bool
    confidence: Decimal | None
    created_at: dt.datetime


@dataclass(frozen=True)
class ThreadDetail:
    id: uuid.UUID
    title: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    pinned: bool
    messages: tuple[StoredMessage, ...]


def open_thread(
    conn: sa.Connection, thread_id: uuid.UUID | None, *, now: dt.datetime
) -> tuple[uuid.UUID, bool]:
    """(thread, whether it was just created). Starting a thread sweeps first."""
    if thread_id is None:
        sweep(conn, now=now)
        created = conn.execute(sa.insert(Thread).returning(Thread.id)).scalar_one()
        return created, True
    if conn.execute(sa.select(Thread.id).where(Thread.id == thread_id)).first() is None:
        raise NotFound(f"no thread {thread_id}")
    return thread_id, False


def add_message(
    conn: sa.Connection,
    thread_id: uuid.UUID,
    *,
    role: str,
    content: str,
    agent: str | None = None,
    tool_calls: list[dict[str, object]] | None = None,
    refused: bool = False,
    confidence: Decimal | None = None,
) -> uuid.UUID:
    # clock_timestamp, not now(): now() is the transaction's start, and two
    # messages written in one transaction would tie and read back in any order.
    stamp = sa.func.clock_timestamp()
    message_id = conn.execute(
        sa.insert(Message)
        .values(
            thread_id=thread_id,
            role=role,
            content=content,
            agent=agent,
            tool_calls=tool_calls,
            refused=refused,
            confidence=confidence,
            created_at=stamp,
        )
        .returning(Message.id)
    ).scalar_one()
    conn.execute(sa.update(Thread).where(Thread.id == thread_id).values(updated_at=stamp))
    return message_id


def needs_title(conn: sa.Connection, thread_id: uuid.UUID) -> bool:
    title = conn.execute(sa.select(Thread.title).where(Thread.id == thread_id)).scalar_one()
    return title is None


def set_title(conn: sa.Connection, thread_id: uuid.UUID, title: str) -> None:
    conn.execute(sa.update(Thread).where(Thread.id == thread_id).values(title=title))


def set_pinned(
    conn: sa.Connection, thread_id: uuid.UUID, pinned: bool, *, now: dt.datetime
) -> None:
    """Unpinning puts a thread back under retention — one older than a year
    goes at the next sweep."""
    changed = conn.execute(
        sa.update(Thread).where(Thread.id == thread_id).values(pinned_at=now if pinned else None)
    )
    if changed.rowcount == 0:
        raise NotFound(f"no thread {thread_id}")


def delete_thread(conn: sa.Connection, thread_id: uuid.UUID) -> None:
    removed = conn.execute(sa.delete(Thread).where(Thread.id == thread_id))
    if removed.rowcount == 0:
        raise NotFound(f"no thread {thread_id}")


def sweep(conn: sa.Connection, *, now: dt.datetime) -> int:
    """Delete every unpinned thread whose last message is older than
    `RETENTION`. Messages go with it (ON DELETE CASCADE)."""
    removed = conn.execute(
        sa.delete(Thread).where(Thread.pinned_at.is_(None), Thread.updated_at < now - RETENTION)
    )
    return removed.rowcount


def list_threads(
    conn: sa.Connection, *, query: str | None = None, limit: int = 50
) -> list[ThreadSummary]:
    """Pinned first, then most recently active. With a query, only threads
    with a matching message, each carrying its best-matching snippet."""
    if query is None or not query.strip():
        rows = conn.execute(
            sa.select(Thread.id, Thread.title, Thread.updated_at, Thread.pinned_at)
            .order_by(Thread.pinned_at.is_(None), Thread.updated_at.desc())
            .limit(limit)
        ).all()
        return [ThreadSummary(i, t, u, p is not None, None) for i, t, u, p in rows]

    # websearch_to_tsquery reads what a person types into a search box —
    # quotes, "or", a minus sign — and never raises on malformed input. A query
    # of nothing but stopwords becomes an empty tsquery, which matches nothing.
    rows = conn.execute(
        sa.text(
            """
            with q as (select websearch_to_tsquery('english', :query) as tsq),
            hits as (
                select distinct on (m.thread_id)
                       m.thread_id, m.content
                from message m cross join q
                where to_tsvector('english', m.content) @@ q.tsq
                order by m.thread_id,
                         ts_rank(to_tsvector('english', m.content), q.tsq) desc,
                         m.created_at desc
            )
            select t.id, t.title, t.updated_at, t.pinned_at,
                   ts_headline('english', hits.content, q.tsq, :options) as headline
            from hits
            join thread t on t.id = hits.thread_id
            cross join q
            order by t.pinned_at is null, t.updated_at desc
            limit :limit
            """
        ),
        {"query": query, "options": _HEADLINE_OPTIONS, "limit": limit},
    ).all()
    return [ThreadSummary(i, t, u, p is not None, _parts(h)) for i, t, u, p, h in rows]


def get_thread(conn: sa.Connection, thread_id: uuid.UUID) -> ThreadDetail:
    thread = conn.execute(
        sa.select(
            Thread.id, Thread.title, Thread.created_at, Thread.updated_at, Thread.pinned_at
        ).where(Thread.id == thread_id)
    ).first()
    if thread is None:
        raise NotFound(f"no thread {thread_id}")
    messages = conn.execute(
        sa.select(
            Message.id,
            Message.role,
            Message.agent,
            Message.content,
            Message.tool_calls,
            Message.refused,
            Message.confidence,
            Message.created_at,
        )
        .where(Message.thread_id == thread_id)
        .order_by(Message.created_at, Message.id)
    ).all()
    return ThreadDetail(
        id=thread.id,
        title=thread.title,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        pinned=thread.pinned_at is not None,
        messages=tuple(StoredMessage(*m) for m in messages),
    )


def _parts(headline: str) -> tuple[SnippetPart, ...]:
    """Split `ts_headline`'s marked text into plain and matched runs."""
    first, *rest = headline.split(_START)
    parts = [SnippetPart(first, False)] if first else []
    for piece in rest:
        matched, _, after = piece.partition(_STOP)
        parts.append(SnippetPart(matched, True))
        if after:
            parts.append(SnippetPart(after, False))
    return tuple(parts)
