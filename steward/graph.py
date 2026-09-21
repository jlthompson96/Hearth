"""The Steward's graph: route, dispatch, and stop.

LangGraph arrives here and not earlier because this is the first thing in Hearth
with more than one path through it. A single agent answering a question is a
loop in a function (`agents/loop.py`); a turn that can be sent one of several
ways, can come back, and must provably stop is a graph.

## The cap is an edge, not a sentence

Rule 7 asks for guardrails in code. `MAX_HOPS` is enforced by
`_after_route`, a conditional edge evaluated before any specialist runs. It
cannot be talked out of the way a prompt instruction can, and it binds whatever
the router decides — including a router that has been argued into handing the
same turn back forever.

## About the handoff

The graph supports a specialist handing a turn back to be re-routed, which is
what makes a delegation loop possible at all and therefore what the cap is for.
Today no specialist sets it: Tally and Forge answer or say they cannot, and
neither has a reliable way to know it was the wrong one. The mechanism is here
because the cap has to bind something real to be worth testing, and
`tests/test_steward.py` drives it with a component that always hands off, which
proves the edge holds regardless of what any future specialist does.

Shipping a heuristic that guessed at "this looks like the other agent's
question" would have been the alternative, and it would have been a worse thing
to have: wrong sometimes, unfalsifiable, and an actual source of loops rather
than a guard against them.
"""

import datetime as dt
from collections.abc import Iterator, Sequence
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from agents import forge, preflight, tally
from agents.conversation import Exchange, previous
from agents.loop import (
    Detail,
    DoneEvent,
    Event,
    LogEvent,
    RefusedEvent,
    RoutedEvent,
    TokenEvent,
)
from steward.router import ConstrainedJSONRouter, Destination, Router, RoutingError

#: Phase 6: an adversarial delegation-loop prompt must terminate within 6 hops.
#: A hop is one routing decision. The ordinary turn uses exactly one.
MAX_HOPS = 6

#: Said when the cap binds. It is deliberately not an apology — the cap firing
#: is the system working, and the person is owed a plain account of it.
HALTED = (
    "I could not settle on which specialist should answer that, and I stopped "
    "rather than keep passing it around. Try asking about your finances or your "
    "training more directly."
)

#: Said when the router's reply could not be read, twice. It is the system's
#: fault, not the question's, and the person is told so plainly.
UNROUTED = (
    "I couldn't work out which specialist should answer that — the model's reply "
    "came back empty twice. Nothing is wrong with the question; asking it again "
    "usually works."
)

#: Said when the router chooses `unsupported`. Hearth reads back what you have
#: recorded; it is not a search engine and not an adviser.
DECLINED = (
    "That is outside what I can answer. I can report what you have recorded — "
    "your accounts and net worth, or your lifts and body measurements — but not "
    "advice, prices, or anything that would need information your own records "
    "do not contain."
)


class StewardState(TypedDict, total=False):
    question: str
    today: dt.date
    detail: Detail
    #: The thread so far. The router is shown the last exchange; each
    #: specialist chooses its own window from the rest.
    history: Sequence[Exchange]
    hops: int
    destination: Destination | None
    confidence: float
    router: str
    handoff: bool


def _emit(event: Event) -> None:
    """Push an event to whoever is streaming this run."""
    get_stream_writer()(event)


def build(router: Router | None = None, specialists: dict[str, Any] | None = None) -> Any:
    """Compile the graph.

    Both dependencies are injectable so the cap can be tested against a router
    that always hands the turn back — which is the only way to show the edge
    binds without shipping a specialist that loops on purpose.
    """
    chosen: Router = router or ConstrainedJSONRouter()
    agents = specialists or {
        Destination.tally.value: tally.answer,
        Destination.forge.value: forge.answer,
    }

    def route(state: StewardState) -> StewardState:
        try:
            decision = chosen.route(state["question"], previous(state.get("history", ())))
        except RoutingError as failure:
            for entry in failure.log:
                _emit(LogEvent(entry))
            _emit(TokenEvent(UNROUTED))
            _emit(DoneEvent(f"routing failed: {failure}"))
            return {"destination": None, "hops": state.get("hops", 0) + 1}
        for entry in decision.log:
            _emit(LogEvent(entry))
        _emit(
            RoutedEvent(
                destination=decision.destination.value,
                confidence=decision.confidence,
                router=decision.router,
            )
        )
        return {
            "destination": decision.destination,
            "confidence": decision.confidence,
            "router": decision.router,
            "hops": state.get("hops", 0) + 1,
            "handoff": False,
        }

    def specialist(state: StewardState) -> StewardState:
        destination = state["destination"]
        assert destination is not None
        answer = agents[destination.value]

        handoff = False
        for event in answer(
            state["question"],
            today=state["today"],
            detail=state.get("detail", "normal"),
            history=state.get("history", ()),
        ):
            if getattr(event, "handoff", False):
                handoff = True
                continue
            _emit(event)
        return {"handoff": handoff}

    def decline(state: StewardState) -> StewardState:
        _emit(TokenEvent(DECLINED))
        _emit(DoneEvent("unsupported"))
        return {}

    def halt(state: StewardState) -> StewardState:
        _emit(TokenEvent(HALTED))
        _emit(DoneEvent(f"stopped after {MAX_HOPS} routing hops"))
        return {}

    def _after_route(state: StewardState) -> str:
        # Checked before any specialist runs, so an over-budget turn costs no
        # further model calls.
        if state.get("hops", 0) > MAX_HOPS:
            return "halt"
        if state.get("destination") is None:
            # Routing failed and has already said so. Nothing else runs.
            return "stop"
        if state["destination"] is Destination.unsupported:
            return "decline"
        return "specialist"

    def _after_specialist(state: StewardState) -> str:
        return "route" if state.get("handoff") else END

    graph = StateGraph(StewardState)
    graph.add_node("route", route)
    graph.add_node("specialist", specialist)
    graph.add_node("decline", decline)
    graph.add_node("halt", halt)

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        _after_route,
        {"specialist": "specialist", "decline": "decline", "halt": "halt", "stop": END},
    )
    graph.add_conditional_edges("specialist", _after_specialist, {"route": "route", END: END})
    graph.add_edge("decline", END)
    graph.add_edge("halt", END)

    return graph.compile()


def answer(
    question: str,
    *,
    today: dt.date,
    detail: Detail = "normal",
    history: Sequence[Exchange] = (),
    graph: Any | None = None,
) -> Iterator[Event]:
    """Route `question` and stream whatever the chosen specialist produces.

    `today` is required here for the same reason it is in the specialists: the
    day changes what "this year" means, and that is a caller's assumption to
    state rather than the code's to invent.
    """
    # The pre-flight check runs here, before the router, because every turn
    # from the UI enters here and the router is a model call too. It used to
    # run only inside Forge, and the router sent "how do I make myself sick
    # after dinner" to `unsupported`, where the check never saw it. Now nothing
    # that trips it reaches any model — not the router, not a specialist. The
    # router reads the previous question too, so the check reads it with this
    # one; the specialist checks its own window again when it runs.
    last = previous(history)
    refusal = preflight.check_conversation([last.question] if last else [], question)
    if refusal is not None:
        yield RefusedEvent(refusal.signal, refusal.message)
        return

    compiled = graph or build()
    state: StewardState = {
        "question": question,
        "today": today,
        "detail": detail,
        "history": history,
        "hops": 0,
    }

    # `custom` carries exactly what the nodes wrote and nothing else — no
    # framework bookkeeping reaches the SSE stream.
    yield from compiled.stream(state, stream_mode="custom")
