"""The agent loop both specialists run.

Extracted when Forge arrived and would otherwise have been a second copy of
Tally's sixty lines. What differs between specialists is a prompt file and a
tool list; the loop that drives them is the same, and two copies of it would
have drifted by Phase 6.

An explicit loop rather than a prebuilt agent, for two reasons. The iteration
cap is a countable thing in code, which is what rule 7 asks for — a cap that
lives in a framework's defaults is a cap nobody can point at. And every message
that enters the context window is appended here, in the open, because the window
is 8,192 tokens and a few hundred are gone to tool schemas before the user types.

The loop streams each step. In the ordinary two-step shape — call a tool, read
the result, answer — the first step carries no text, so nothing provisional
reaches the UI. A model that narrated before calling a tool would briefly stream
that narration; `TokenEvent.provisional` marks text emitted in a step that turned
out to be a tool call, so a consumer can drop it rather than leaving it on screen
as though it were the answer.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool

from llm import chat_model

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

#: Rule 7: the cap is a number in code, not a sentence in a prompt. Two steps is
#: the ordinary shape (call a tool, answer from it). Four leaves room for a
#: second lookup — a question needing a third is one this agent should give up
#: on rather than grind at, on a model this size and a window this small.
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
class RefusedEvent:
    """The turn stopped before inference. Distinct from a DoneEvent so the UI
    and the evals can both tell a refusal from an answer, and so Phase 7 can
    assert that the refusal path was taken rather than that some text came
    back that happened to read like one."""

    signal: str
    message: str


@dataclass(frozen=True)
class RoutedEvent:
    """Which specialist the Steward chose, and how sure it was.

    Emitted before the specialist runs so the UI can attribute an answer while
    it is still streaming, rather than labelling it after the fact. `router`
    names the implementation that decided, because routing will not always be
    one mechanism — a keyword rule and a model call should not be
    indistinguishable in a transcript.
    """

    destination: str
    confidence: float
    router: str


@dataclass(frozen=True)
class DoneEvent:
    reason: str = "complete"


Event = TokenEvent | ToolEvent | ToolResultEvent | RefusedEvent | RoutedEvent | DoneEvent


@lru_cache
def load_prompt(name: str) -> str:
    """Read once. Prompts are version-controlled files, never inline string
    literals, because Phase 7 measures the delta when one changes and a prompt
    you cannot diff is a prompt you cannot measure."""
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def run(*, system: str, question: str, tools: list[BaseTool]) -> Iterator[Event]:
    """Drive one turn to an answer, yielding events as they happen."""
    model = chat_model().bind_tools(tools)
    by_name = {t.name: t for t in tools}

    conversation: list[tuple[str, str] | BaseMessage] = [
        ("system", system),
        ("human", question),
    ]

    for _step in range(MAX_STEPS):
        gathered: AIMessageChunk | None = None
        emitted: list[str] = []

        for chunk in model.stream(conversation):
            assert isinstance(chunk, AIMessageChunk)
            gathered = chunk if gathered is None else gathered + chunk
            if chunk.text:
                emitted.append(chunk.text)
                yield TokenEvent(chunk.text)

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

            tool = by_name.get(name)
            if tool is None:
                # Only the bound tools can be called. A name that is not one of
                # them is the model inventing a capability, and it is told so
                # rather than the turn dying on a KeyError.
                result = f"No tool named {name!r}. Available: {', '.join(sorted(by_name))}."
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
