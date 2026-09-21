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

from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.loop import load_prompt
from llm import structured_model

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
    return structured_model(_Title, max_tokens=MAX_TOKENS, reasoning_effort="none")


def for_question(question: str, *, model: _TitleModel | None = None) -> str:
    # The question is fenced, and cannot close its own fence.
    fenced = question.replace("<question>", "").replace("</question>", "")
    try:
        answer = (model or _model()).invoke(
            [
                SystemMessage(load_prompt("title")),
                HumanMessage(f"<question>\n{fenced}\n</question>"),
            ]
        )
        raw = str(answer.title)
    except Exception:  # noqa: BLE001 - any failure means the fallback, never a failed turn
        raw = ""
    return clean(raw) or fallback(question)


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
