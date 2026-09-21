"""The Model log screen: runs, most recent first, and one run in full.

Read through the read-only role, like every other read. The log holds what the
model was sent — earlier turns, tool results, real figures — so it is served to
this machine's own browser and to nothing else; there is no export and no
"open in" another tool. Langfuse is where that button would go, and it waits on
Docker (docs/plan.md).
"""

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

import preferences
from api.refusals import REFUSALS
from db.session import readonly_connection
from history import model_log

router = APIRouter(prefix="/api/model-log", tags=["model log"])


class StepOut(BaseModel):
    kind: str
    caller: str
    tool: str | None
    failed: bool


class RunSummaryOut(BaseModel):
    question_id: uuid.UUID
    thread_id: uuid.UUID
    thread_title: str | None
    question: str
    started_at: dt.datetime
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    failed: bool
    chain: list[StepOut]


class RunListing(BaseModel):
    #: Shown on the screen, so the rule is visible where it applies.
    retention_days: int
    runs: list[RunSummaryOut]


class EntryOut(BaseModel):
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


class RunDetailOut(BaseModel):
    summary: RunSummaryOut
    answer: str | None
    answered_by: str | None
    entries: list[EntryOut]


def _summary(run: model_log.RunSummary) -> RunSummaryOut:
    return RunSummaryOut(
        question_id=run.question_id,
        thread_id=run.thread_id,
        thread_title=run.thread_title,
        question=run.question,
        started_at=run.started_at,
        duration_ms=run.duration_ms,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        failed=run.failed,
        chain=[
            StepOut(kind=s.kind, caller=s.caller, tool=s.tool, failed=s.failed) for s in run.chain
        ],
    )


@router.get("", response_model=RunListing, summary="Runs, most recent first")
def list_runs(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[dt.datetime | None, Query(description="Older than this start time")] = None,
) -> RunListing:
    with readonly_connection() as conn:
        found = model_log.runs(conn, limit=limit, before=before)
        kept = preferences.days(conn, "model_log_retention_days")
    return RunListing(retention_days=kept.days, runs=[_summary(r) for r in found])


@router.get(
    "/{question_id}",
    response_model=RunDetailOut,
    summary="One run: every model call and tool run, verbatim",
    responses={404: REFUSALS[404]},
)
def read_run(question_id: uuid.UUID) -> RunDetailOut:
    with readonly_connection() as conn:
        detail = model_log.run(conn, question_id)
    return RunDetailOut(
        summary=_summary(detail.summary),
        answer=detail.answer,
        answered_by=detail.answered_by,
        entries=[
            EntryOut(
                id=e.id,
                seq=e.seq,
                kind=e.kind,
                caller=e.caller,
                model=e.model,
                request=e.request,
                response=e.response,
                error=e.error,
                input_tokens=e.input_tokens,
                output_tokens=e.output_tokens,
                duration_ms=e.duration_ms,
                started_at=e.started_at,
            )
            for e in detail.entries
        ],
    )
