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
from typing import Any, Protocol

from openai import ContentFilterFinishReasonError, LengthFinishReasonError
from pydantic import BaseModel, Field

from agents.conversation import Exchange, last_line
from agents.loop import load_prompt
from llm import structured_reply
from modellog import Clock, LogEntry, request_body, response_body, tokens


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
    #: Every call it took, for the Model log: one, or two when the first reply
    #: was unreadable.
    log: tuple[LogEntry, ...] = ()


class Router(Protocol):
    """Swap an implementation in without touching the graph.

    `previous` is the exchange the new message may be replying to, or None. A
    router that ignores it still routes every self-contained question right; it
    only fails the "yes" that accepts an offer.
    """

    @property
    def name(self) -> str: ...

    def route(self, question: str, previous: Exchange | None = None) -> Routed: ...


def _destination_lines() -> str:
    return "\n".join(f"- {d.value}: {DESCRIPTIONS[d]}" for d in Destination)


def system_prompt() -> str:
    return load_prompt("steward").format(destinations=_destination_lines())


def message(question: str, previous: Exchange | None) -> str:
    """What the classifier is asked to route.

    One message, with the earlier exchange quoted inside it, rather than the
    exchange replayed as chat turns: a classifier shown an assistant turn of
    prose is being invited to continue the prose. Only the answer's last line is
    quoted — the prompts end every answer with the offer a "yes" accepts, and
    the figures above it are nothing a routing decision needs.
    """
    if previous is None:
        return question
    return (
        f"Route this new message: {question}\n"
        "\n"
        "What came before it, only to tell what its words refer to:\n"
        f"They asked: {previous.question}\n"
        f"The {previous.agent} specialist answered, ending: {last_line(previous.answer)}"
    )


class RoutingError(RuntimeError):
    """The router's reply was unreadable on every attempt. The turn ends with a
    plain message rather than a traceback, and no specialist runs. The failed
    calls are carried for the Model log, which is where "why" gets answered."""

    def __init__(self, message: str, log: tuple[LogEntry, ...] = ()) -> None:
        super().__init__(message)
        self.log = log


#: Off, measured on the host on 2026-09-21 against nvidia/nemotron-3-nano-4b.
#: With reasoning on, the router now and then reasoned for twenty tokens and
#: returned nothing — a crashed turn in the app on "what are my current
#: positions", and 3 of 18 calls in one probe. With it off: 40/40 on the
#: labelled cases, no empty replies, 0.41s a call against 1.29s, and "what do I
#: own right now" routed to Tally 5 times in 5, where reasoning on had declined
#: it 5 times in 5. The specialists keep reasoning: off, they stopped calling
#: tools and answered from nothing.
REASONING_EFFORT = "none"

#: One retry. A reply that is empty or not the schema is nondeterministic, so a
#: second attempt usually lands; a third would only delay the message that says
#: it did not.
ATTEMPTS = 2

#: What an unreadable reply looks like on the way back. A reply that does not
#: parse — empty, or not the schema — comes back as `parsing_error` beside the
#: raw message; these are the ones raised instead: the model stopping on length
#: or a content filter, or a ValueError from the client. A connection error is
#: not here: it is not the reply's fault, and the caller should see it as what
#: it is.
_UNREADABLE = (ValueError, LengthFinishReasonError, ContentFilterFinishReasonError)


class ConstrainedJSONRouter:
    """The default. One constrained-JSON call, no tool calling, retried once."""

    name = "constrained-json"

    def __init__(self, *, temperature: float = 0.0) -> None:
        self._temperature = temperature

    def route(self, question: str, previous: Exchange | None = None) -> Routed:
        model = structured_reply(
            Decision, temperature=self._temperature, reasoning_effort=REASONING_EFFORT
        )
        asked = [("system", system_prompt()), ("human", message(question, previous))]
        body = request_body(model, asked)
        log: list[LogEntry] = []
        failure: Exception | None = None
        for _ in range(ATTEMPTS):
            clock = Clock()
            reply: dict[str, Any] = {}
            try:
                reply = model.invoke(asked)
                failure = reply.get("parsing_error")
            except _UNREADABLE as error:
                failure = error
            decision = reply.get("parsed")
            spent_in, spent_out = tokens(reply.get("raw"))
            log.append(
                LogEntry(
                    kind="route",
                    caller="steward",
                    request=body,
                    response=response_body(reply.get("raw")),
                    started_at=clock.started_at,
                    duration_ms=clock.ms,
                    model=body.get("model"),
                    error=None if decision is not None else f"unreadable reply: {failure}",
                    input_tokens=spent_in,
                    output_tokens=spent_out,
                )
            )
            if decision is None:
                continue
            return Routed(
                destination=decision.destination,
                confidence=decision.confidence,
                router=self.name,
                log=tuple(log),
            )
        raise RoutingError(
            f"the router's reply was unreadable {ATTEMPTS} times ({type(failure).__name__})",
            log=tuple(log),
        ) from failure
