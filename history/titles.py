"""A thread's title: one short model call after the first turn.

Constrained JSON, the same mechanism the router uses, with reasoning switched
off. Measured on the host before this was written: left on, the model reasons
for about 300 tokens ahead of a twelve-token title, and `max_tokens` counts
those, so the plan's "cap output ~10 tokens" truncated the thinking and got
nothing back. With `reasoning_effort="none"`: 24/24 schema-valid across eight
questions, identical titles run to run, about 0.2s each.

Titled from the question alone. The answer is where the figures are, and a title
sits in a list where a balance has no business being — the prompt asks for no
figures, and not sending them is what makes that hold.

Whatever comes back is cleaned and capped here, in code (rule 7), and any
failure falls back to the question's first words. A title is never worth
failing a turn over.
"""

from collections.abc import Callable
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.loop import load_prompt
from llm import structured_reply
from modellog import Clock, LogEntry, request_body, response_body, tokens

MAX_WORDS = 6
MAX_CHARS = 60
#: The JSON wrapper, `{"title": "..."}`, plus six words. Reasoning is off, so
#: this bounds the answer and nothing else.
MAX_TOKENS = 24

#: A turn the pre-flight check stopped is never sent to the model to be titled.
REFUSED = "Declined request"


class _Title(BaseModel):
    title: str = Field(description=f"At most {MAX_WORDS} words")


class _TitleModel(Protocol):
    # Positional-only, so a Runnable and a two-line test double both fit.
    def invoke(self, input: list[BaseMessage], /) -> Any: ...


def _model() -> _TitleModel:
    return structured_reply(_Title, max_tokens=MAX_TOKENS, reasoning_effort="none")


def for_question(
    question: str,
    *,
    model: _TitleModel | None = None,
    log: Callable[[LogEntry], None] | None = None,
) -> str:
    """The title, or the question's first words if the call fails. `log`
    receives the call's Model log entry, failed or not."""
    # The question is fenced, and cannot close its own fence.
    fenced = question.replace("<question>", "").replace("</question>", "")
    messages = [
        SystemMessage(load_prompt("title")),
        HumanMessage(f"<question>\n{fenced}\n</question>"),
    ]
    chosen = model or _model()
    clock = Clock()
    reply: dict[str, Any] = {}
    error: str | None = None
    try:
        reply = chosen.invoke(messages)
        if reply.get("parsed") is None:
            error = f"unreadable reply: {reply.get('parsing_error')}"
    except Exception as failure:  # noqa: BLE001 - any failure means the fallback, never a failed turn
        error = f"{type(failure).__name__}: {failure}"
    if log is not None:
        body = request_body(chosen, messages)
        spent_in, spent_out = tokens(reply.get("raw"))
        log(
            LogEntry(
                kind="title",
                caller="titles",
                request=body,
                response=response_body(reply.get("raw")),
                started_at=clock.started_at,
                duration_ms=clock.ms,
                model=body.get("model"),
                error=error,
                input_tokens=spent_in,
                output_tokens=spent_out,
            )
        )
    parsed = reply.get("parsed")
    return clean(str(parsed.title) if parsed is not None else "") or fallback(question)


def fallback(question: str) -> str:
    return clean(question) or "Untitled"


def clean(raw: str) -> str | None:
    """One line, no wrapping quotes or trailing punctuation, at most six words
    and sixty characters, first letter capitalised. None if nothing is left."""
    text = " ".join(raw.split())
    if text.lower().startswith("title:"):
        text = text[len("title:") :]
    text = " ".join(text.strip(" \"'`.,;:!?").split()[:MAX_WORDS])
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit(" ", 1)[0] if " " in text[:MAX_CHARS] else text[:MAX_CHARS]
    text = text.strip(" \"'`.,;:!?")
    return text[:1].upper() + text[1:] if text else None
