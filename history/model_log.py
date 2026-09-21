"""Storing the Model log, and reading it back by run.

A run is one question and everything done to answer it: the routing call,
each of the specialist's model calls and tool runs, and the title call. It is
identified by the question's message id, so a run is found from the chat by the
question it answers, and goes when that question's thread goes.
"""

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from db.models import Message, ModelLog, Thread
from ingest.errors import NotFound
from modellog import LogEntry


@dataclass(frozen=True)
class Step:
    """One entry in a run's chain: what ran, and whether it failed."""

    kind: str
    caller: str
    #: The tool's name, for a tool run.
    tool: str | None
    failed: bool


@dataclass(frozen=True)
class RunSummary:
    question_id: uuid.UUID
    thread_id: uuid.UUID
    thread_title: str | None
    question: str
    started_at: dt.datetime
    #: From the first entry's start to the last one's end.
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    chain: tuple[Step, ...]

    @property
    def failed(self) -> bool:
        return any(step.failed for step in self.chain)


@dataclass(frozen=True)
class Entry:
    id: uuid.UUID
    seq: int
    kind: str
    caller: str
    model: str | None
    request: dict[str, Any]
    response: dict[str, Any] | None
    error: str | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int
    started_at: dt.datetime


@dataclass(frozen=True)
class RunDetail:
    summary: RunSummary
    #: What was stored as the answer, if anything was.
    answer: str | None
    answered_by: str | None
    entries: tuple[Entry, ...]


def add(
    conn: sa.Connection,
    thread_id: uuid.UUID,
    question_id: uuid.UUID,
    entries: Sequence[LogEntry],
) -> None:
    """Store a run's entries, numbered in the order they happened."""
    if not entries:
        return
    conn.execute(
        sa.insert(ModelLog),
        [
            {
                "thread_id": thread_id,
                "question_id": question_id,
                "seq": seq,
                "kind": e.kind,
                "caller": e.caller,
                "model": e.model,
                "request": e.request,
                "response": e.response,
                "error": e.error,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "duration_ms": e.duration_ms,
                "started_at": e.started_at,
            }
            for seq, e in enumerate(entries)
        ],
    )


def runs(
    conn: sa.Connection, *, limit: int = 50, before: dt.datetime | None = None
) -> list[RunSummary]:
    """Most recent first. `before` pages back through older runs."""
    started = sa.func.min(ModelLog.started_at)
    query = (
        sa.select(ModelLog.question_id)
        .group_by(ModelLog.question_id)
        .order_by(started.desc())
        .limit(limit)
    )
    if before is not None:
        query = query.having(started < before)
    ids = list(conn.execute(query).scalars())
    return _summaries(conn, ids)


def run(conn: sa.Connection, question_id: uuid.UUID) -> RunDetail:
    summaries = _summaries(conn, [question_id])
    if not summaries:
        raise NotFound(
            "No model log for that question. It may have expired, or the turn was "
            "refused before any model ran."
        )
    rows = conn.execute(
        sa.select(
            ModelLog.id,
            ModelLog.seq,
            ModelLog.kind,
            ModelLog.caller,
            ModelLog.model,
            ModelLog.request,
            ModelLog.response,
            ModelLog.error,
            ModelLog.input_tokens,
            ModelLog.output_tokens,
            ModelLog.duration_ms,
            ModelLog.started_at,
        )
        .where(ModelLog.question_id == question_id)
        .order_by(ModelLog.seq)
    ).all()
    asked = conn.execute(
        sa.select(Message.thread_id, Message.created_at).where(Message.id == question_id)
    ).one()
    # The answer is the message stored straight after the question — if that is
    # an answer at all. A turn that failed has none, and the next message is
    # the next question.
    following = conn.execute(
        sa.select(Message.role, Message.content, Message.agent)
        .where(Message.thread_id == asked.thread_id, Message.created_at > asked.created_at)
        .order_by(Message.created_at)
        .limit(1)
    ).first()
    answer = following if following is not None and following.role == "assistant" else None
    return RunDetail(
        summary=summaries[0],
        answer=answer.content if answer else None,
        answered_by=answer.agent if answer else None,
        entries=tuple(Entry(*row) for row in rows),
    )


def _summaries(conn: sa.Connection, ids: Sequence[uuid.UUID]) -> list[RunSummary]:
    if not ids:
        return []
    rows = conn.execute(
        sa.select(
            ModelLog.question_id,
            ModelLog.thread_id,
            Thread.title,
            Message.content,
            ModelLog.kind,
            ModelLog.caller,
            ModelLog.request["name"].astext.label("name"),
            ModelLog.error,
            ModelLog.input_tokens,
            ModelLog.output_tokens,
            ModelLog.started_at,
            ModelLog.duration_ms,
        )
        .join(Message, Message.id == ModelLog.question_id)
        .join(Thread, Thread.id == ModelLog.thread_id)
        .where(ModelLog.question_id.in_(ids))
        .order_by(ModelLog.question_id, ModelLog.seq)
    ).all()
    grouped: dict[uuid.UUID, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.question_id, []).append(row)

    out = []
    for question_id in ids:
        entries = grouped.get(question_id)
        if not entries:
            continue
        first = entries[0]
        start = min(e.started_at for e in entries)
        end = max(e.started_at + dt.timedelta(milliseconds=e.duration_ms) for e in entries)
        spent_in = [e.input_tokens for e in entries if e.input_tokens is not None]
        spent_out = [e.output_tokens for e in entries if e.output_tokens is not None]
        out.append(
            RunSummary(
                question_id=question_id,
                thread_id=first.thread_id,
                thread_title=first.title,
                question=first.content,
                started_at=start,
                duration_ms=round((end - start).total_seconds() * 1000),
                input_tokens=sum(spent_in) if spent_in else None,
                output_tokens=sum(spent_out) if spent_out else None,
                chain=tuple(
                    Step(
                        kind=e.kind,
                        caller=e.caller,
                        tool=e.name if e.kind == "tool" else None,
                        failed=e.error is not None,
                    )
                    for e in entries
                ),
            )
        )
    return out
