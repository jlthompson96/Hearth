"""Routing a turn to a specialist.

Behind a `Router` protocol on purpose. The classifier below is a constrained-
JSON call because LM Studio parses tool calls out of model text against a chat
template, and at this size that is too unreliable to put underneath every turn.
On better hardware an LLM tool-calling router could replace it by implementing
the same two-method protocol, and the graph would not change.

## Destinations carry descriptions, and that is the experiment

Phase 4 measured this model choosing between the bare labels `tally`, `forge`
and `errand` and getting a net-worth question wrong 30 times out of 30. Phase 5
measured the same model choosing between three *tools* — same size, same
endpoint — and getting it right 18 times out of 18. The difference the plan
guessed at is that a tool carries a name and a sentence saying what it is for,
where a router choice was a label and nothing else.

So each destination here carries a description, and the prompt is built from
them. If that guess was right, this is a cheaper fix than the keyword-plus-
embedding fallback Phase 6 allows for. `tests/test_router.py` measures it
against the labelled set rather than assuming either way.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from agents.loop import load_prompt
from llm import structured_model


class Destination(StrEnum):
    """Where a turn can go.

    `unsupported` is a destination rather than a failure mode. Without it the
    classifier has to put "what's the weather" somewhere, and it will — into
    whichever specialist sounds closest, which is how a finance agent ends up
    answering questions about the weather with a tool call.

    `errand` joins at Phase 9. It is deliberately absent rather than stubbed:
    a destination the router can pick but nothing can serve is worse than one
    that does not exist yet.
    """

    tally = "tally"
    forge = "forge"
    unsupported = "unsupported"


#: What each destination is for, in the words the classifier sees. These live
#: beside the enum rather than in the prompt file because they describe the
#: things themselves; the prompt that frames them is in prompts/steward.md and
#: stays diffable for Phase 7.
DESCRIPTIONS: dict[Destination, str] = {
    Destination.tally: (
        "REPORTING already-recorded money: their net worth, their account "
        "balances, their savings, their holdings and allocation, how those "
        "figures have moved over time. Only questions answerable by reading "
        "their own records"
    ),
    Destination.forge: (
        "REPORTING already-recorded training: lifts and how much was lifted, "
        "their workouts, sets and reps, personal bests, and body measurements "
        "they have logged such as body mass. Only questions answerable by "
        "reading their own records"
    ),
    Destination.unsupported: (
        "anything that is not reading back their own records: advice about what "
        "they should do or buy, whether a decision is wise, prices or costs of "
        "things in the world, general knowledge, news, weather, other people. "
        "A question can be about money or training and still belong here when "
        "answering it would need information their records do not contain"
    ),
}


class Decision(BaseModel):
    """The classifier's output. Constrained so the model cannot return prose."""

    destination: Destination
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class Routed:
    """A decision plus how it was reached, so the UI can attribute an answer and
    an eval can tell a confident route from a coin flip."""

    destination: Destination
    confidence: float
    router: str


class Router(Protocol):
    """Swap an implementation in without touching the graph."""

    @property
    def name(self) -> str: ...

    def route(self, question: str) -> Routed: ...


def _destination_lines() -> str:
    return "\n".join(f"- {d.value}: {DESCRIPTIONS[d]}" for d in Destination)


def system_prompt() -> str:
    return load_prompt("steward").format(destinations=_destination_lines())


class ConstrainedJSONRouter:
    """The default. One constrained-JSON call, no tool calling."""

    name = "constrained-json"

    def __init__(self, *, temperature: float = 0.0) -> None:
        self._temperature = temperature

    def route(self, question: str) -> Routed:
        model = structured_model(Decision, temperature=self._temperature)
        decision = model.invoke(
            [("system", system_prompt()), ("human", question)],
        )
        return Routed(
            destination=decision.destination,
            confidence=decision.confidence,
            router=self.name,
        )
