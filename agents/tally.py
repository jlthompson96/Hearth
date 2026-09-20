"""Tally — the finance specialist.

An explicit loop rather than a prebuilt agent, for two reasons. The iteration
cap is a countable thing in code, which is what rule 7 asks for — a cap that
lives in a framework's defaults is a cap nobody can point at. And every message
that enters the context window is appended here, in the open, because the
window is 8,192 tokens and roughly 300 of them are gone to tool schemas before
the user types anything.

The loop streams each step. In the ordinary two-step shape — the model calls a
tool, the tool answers, the model writes the reply — the first step carries no
text, so nothing provisional reaches the UI. A model that narrated before
calling a tool would briefly stream that narration; `TokenEvent.provisional`
marks text emitted in a step that turned out to be a tool call, so a consumer
can drop it rather than leaving it on screen as though it were the answer.
"""

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage

from llm import chat_model
from tools.bindings import TALLY_TOOLS

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "tally.md"

#: Rule 7: the cap is a number in code, not a sentence in a prompt. Two steps is
#: the ordinary shape (call a tool, answer from it). Four leaves room for a
#: second lookup — a question that needs a third is a question this agent should
#: give up on rather than grind at, on a model this size and a window this small.
MAX_STEPS = 4


@dataclass(frozen=True)
class TokenEvent:
    text: str
    provisional: bool = False


@dataclass(frozen=True)
class ToolEvent:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResultEvent:
    name: str
    result: str


@dataclass(frozen=True)
class DoneEvent:
    reason: str = "complete"


Event = TokenEvent | ToolEvent | ToolResultEvent | DoneEvent


@lru_cache
def _prompt_template() -> str:
    """Read once. The prompt is a version-controlled file, never an inline
    string literal, because Phase 7 measures the delta when it changes and a
    prompt you cannot diff is a prompt you cannot measure."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def system_prompt(today: dt.date) -> str:
    return _prompt_template().format(today=today.isoformat())


def _tools_by_name() -> dict[str, Any]:
    return {t.name: t for t in TALLY_TOOLS}


def answer(question: str, *, today: dt.date) -> Iterator[Event]:
    """Answer `question`, yielding events as they happen.

    `today` is required and never defaults to `date.today()`. The agent turns
    "this year" into dates, so the day it believes it is changes the answer —
    which makes it something a caller states rather than something the code
    assumes, and something an eval can pin.
    """
    model = chat_model().bind_tools(TALLY_TOOLS)
    tools = _tools_by_name()

    conversation: list[tuple[str, str] | BaseMessage] = [
        ("system", system_prompt(today)),
        ("human", question),
    ]

    for _step in range(MAX_STEPS):
        gathered: AIMessageChunk | None = None
        emitted: list[str] = []

        for chunk in model.stream(conversation):
            assert isinstance(chunk, AIMessageChunk)
            gathered = chunk if gathered is None else gathered + chunk
            text = chunk.text
            if text:
                emitted.append(text)
                yield TokenEvent(text)

        if gathered is None:
            yield DoneEvent("the model returned nothing")
            return

        calls = gathered.tool_calls or []
        if not calls:
            yield DoneEvent()
            return

        # Text streamed during a step that turned out to be a tool call is not
        # the answer. Say so rather than leaving it on screen.
        if emitted:
            yield TokenEvent("".join(emitted), provisional=True)

        conversation.append(gathered)
        for call in calls:
            name = call["name"]
            args = dict(call.get("args") or {})
            yield ToolEvent(name, args)

            tool = tools.get(name)
            if tool is None:
                # Only the bound tools can be called. A name that is not one of
                # them is the model inventing a capability, and it is told so
                # rather than the turn dying on a KeyError.
                result = f"No tool named {name!r}. Available: {', '.join(sorted(tools))}."
            else:
                try:
                    result = str(tool.invoke(args))
                except Exception as error:  # noqa: BLE001 - surfaced to the model, not swallowed
                    result = f"{type(error).__name__}: {error}"

            yield ToolResultEvent(name, result)
            conversation.append(
                ToolMessage(content=result, tool_call_id=call.get("id") or name, name=name)
            )

    # Falling out of the loop is the cap doing its job. The conditional is the
    # guardrail; this message is only how it explains itself.
    yield DoneEvent(f"stopped after {MAX_STEPS} steps without a final answer")
