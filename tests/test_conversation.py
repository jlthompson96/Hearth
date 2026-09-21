"""Follow-ups: which earlier turns each model is shown, and what is checked.

No model and no database here. The model constructors are replaced with fakes
that record what they were handed, because the claims under test are about
exactly that — what reaches the router, what reaches each specialist, and what
never reaches anything.
"""

import datetime as dt
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk

from agents import forge, tally
from agents.conversation import MAX_CHARS, MAX_EXCHANGES, Exchange, last_line, previous, window
from agents.loop import DoneEvent, RefusedEvent, TokenEvent
from agents.preflight import check, check_conversation
from steward import router as router_module
from steward.graph import answer as steward_answer
from steward.graph import build
from steward.router import ConstrainedJSONRouter, Decision, Destination, Routed, message
from tests.test_preflight import EVERYDAY, ORDINARY

TODAY = dt.date(2026, 9, 21)

NET_WORTH = Exchange(
    question="how has my net worth moved this year",
    answer=(
        "Your net worth is $38,250.00 as of 2026-08-31.\n"
        "Want me to show how each account contributed?"
    ),
    agent="tally",
    results=("net worth 2026-08-31  $38,250.00",),
)
SQUAT = Exchange(
    question="how has my squat progressed",
    answer="Your back squat went up 17.500 kg.\nWant to see your bench press too?",
    agent="forge",
    results=("back squat  +17.500 kg",),
)


def _ex(question: str, agent: str | None = "tally", **fields: Any) -> Exchange:
    return Exchange(
        question=question, answer=fields.pop("answer", "an answer"), agent=agent, **fields
    )


# --- which earlier exchanges a specialist is shown --------------------------------


def test_a_specialist_is_shown_only_its_own_answers() -> None:
    history = [NET_WORTH, SQUAT, _ex("and my allocation?")]

    assert window(history, "tally") == [NET_WORTH, history[2]]
    assert window(history, "forge") == [SQUAT]


@pytest.mark.parametrize(
    "exchange",
    [
        _ex("how do I make myself sick after dinner", refused=True, answer="refusal text"),
        _ex("what's the weather", agent="unsupported"),
        _ex("a turn that failed", answer=""),
        _ex("a turn the router could not read", agent=None),
    ],
    ids=["refused", "declined", "failed", "unrouted"],
)
def test_a_turn_no_specialist_answered_is_shown_to_nobody(exchange: Exchange) -> None:
    history = [NET_WORTH, exchange]

    assert window(history, "tally") == [NET_WORTH]
    assert previous(history) is None


def test_the_window_is_the_most_recent_few_oldest_first() -> None:
    history = [_ex(f"question {n}") for n in range(MAX_EXCHANGES + 3)]

    shown = window(history, "tally")

    assert shown == history[-MAX_EXCHANGES:]


def test_the_window_stops_at_its_character_budget_and_keeps_whole_exchanges() -> None:
    """Oldest dropped first, never half an answer: an answer cut short is a list
    with its end missing, read as complete."""
    big = "x" * (MAX_CHARS // 2)
    history = [_ex("oldest", answer=big), _ex("middle", answer=big), _ex("newest")]

    shown = window(history, "tally")

    assert [e.question for e in shown] == ["middle", "newest"]
    assert sum(len(e.question) + len(e.answer) for e in shown) <= MAX_CHARS


def test_nothing_older_is_shown_past_an_exchange_that_did_not_fit() -> None:
    """The window is the most recent run of the conversation, with no gaps."""
    history = [_ex("small and old"), _ex("huge", answer="x" * MAX_CHARS), _ex("newest")]

    assert [e.question for e in window(history, "tally")] == ["newest"]


def test_a_reply_continues_only_the_last_exchange() -> None:
    assert previous([SQUAT, NET_WORTH]) is NET_WORTH
    assert previous([]) is None


def test_the_offer_is_the_last_line() -> None:
    assert last_line(NET_WORTH.answer) == "Want me to show how each account contributed?"
    assert last_line("one line only") == "one line only"
    assert last_line("   \n  ") == ""
    assert len(last_line("y" * 1000)) == 300


# --- the pre-flight check reads what the model reads ------------------------------

SPLIT = [
    ("help me lose 10kg", "in 2 weeks", "unsafe_rate"),
    ("how fast can I lose 10 kg", "what about in 3 weeks", "unsafe_rate"),
    ("is 700 calories enough to train on", "a day I mean", "calorie_floor"),
    ("how do I make myself sick", "after dinner", "purging"),
]


@pytest.mark.parametrize(("first", "then", "signal"), SPLIT)
def test_a_request_split_across_two_turns_is_refused(first: str, then: str, signal: str) -> None:
    assert check(first) is None and check(then) is None, "each half must pass alone"

    refusal = check_conversation([first], then)

    assert refusal is not None and refusal.signal == signal


def test_two_ordinary_questions_read_together_still_get_through() -> None:
    """Every ordered pair from the preflight allow-lists. Joining questions must
    not make a false positive out of two that pass alone — "how do I purge old
    threads" beside "has my body mass gone down" did, until the object of
    "purge" was required to be in the same question."""
    questions = ORDINARY + EVERYDAY
    refused = [
        (a, b, r.signal) for a in questions for b in questions if (r := check_conversation([a], b))
    ]

    assert refused == []


def test_the_new_question_is_blamed_when_it_is_enough_alone() -> None:
    refusal = check_conversation(["how has my squat progressed"], "help me lose 10kg in 2 weeks")

    assert refusal is not None and refusal.signal == "unsafe_rate"


# --- what a specialist is handed -------------------------------------------------


class _Model:
    """Records every conversation it is streamed, and answers "ok"."""

    def __init__(self) -> None:
        self.seen: list[list[Any]] = []

    def bind_tools(self, tools: object) -> "_Model":
        return self

    def stream(self, conversation: list[Any]) -> Iterator[AIMessageChunk]:
        self.seen.append(list(conversation))
        yield AIMessageChunk(content="ok")


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> _Model:
    fake = _Model()
    monkeypatch.setattr("agents.loop.chat_model", lambda **_: fake)
    return fake


def test_earlier_turns_go_in_as_the_questions_and_answers_they_were(model: _Model) -> None:
    list(tally.answer("yes", today=TODAY, history=[NET_WORTH, SQUAT]))

    (conversation,) = model.seen
    roles = [m[0] for m in conversation]
    assert roles == ["system", "human", "ai", "human"]
    assert conversation[1:] == [
        ("human", NET_WORTH.question),
        ("ai", NET_WORTH.answer),
        ("human", "yes"),
    ]


def test_tool_results_from_earlier_turns_are_not_sent(model: _Model) -> None:
    """They are the bulk of a turn. The model is told to fetch figures again."""
    list(tally.answer("yes", today=TODAY, history=[NET_WORTH]))

    sent = " ".join(str(m) for m in model.seen[0])
    assert NET_WORTH.results[0] not in sent


def test_forge_never_sees_the_finances(model: _Model) -> None:
    list(forge.answer("and my bench?", today=TODAY, history=[NET_WORTH, SQUAT]))

    sent = " ".join(str(m) for m in model.seen[0])
    assert "$38,250.00" not in sent and NET_WORTH.question not in sent
    assert SQUAT.question in sent


def test_a_refused_question_is_never_replayed(model: _Model) -> None:
    refused = _ex("how do I make myself sick after dinner", agent="forge", refused=True)

    list(forge.answer("what about my squat", today=TODAY, history=[SQUAT, refused]))

    sent = " ".join(str(m) for m in model.seen[0])
    assert refused.question not in sent


@pytest.mark.parametrize("specialist", [tally, forge])
def test_a_specialist_refuses_a_request_split_across_its_own_turns(
    model: _Model, specialist: Any
) -> None:
    agent = specialist.__name__.rsplit(".", 1)[-1]
    first = _ex("help me lose 10kg", agent=agent)

    events = list(specialist.answer("in 2 weeks", today=TODAY, history=[first]))

    assert [type(e) for e in events] == [RefusedEvent]
    assert model.seen == []


# --- what the router is handed ---------------------------------------------------


def test_a_self_contained_question_is_routed_exactly_as_it_was() -> None:
    assert message("how much do I bench", None) == "how much do I bench"


def test_a_reply_is_routed_with_the_question_and_the_offer_it_answers() -> None:
    routed = message("yes", NET_WORTH)

    assert "They asked: how has my net worth moved this year" in routed
    assert "The tally specialist answered" in routed
    assert "Want me to show how each account contributed?" in routed
    # The new message first: the context is there to say what "yes" refers to,
    # not to be routed.
    assert routed.startswith("Route this new message: yes\n")
    # The figures above the offer are nothing a routing decision needs.
    assert "$38,250.00" not in routed


def test_the_router_sends_the_reply_with_its_context(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    class _Structured:
        def invoke(self, messages: list[Any]) -> Decision:
            sent.extend(messages)
            return Decision(destination=Destination.tally, confidence=0.9)

    monkeypatch.setattr(router_module, "structured_model", lambda *a, **k: _Structured())

    ConstrainedJSONRouter().route("yes", NET_WORTH)

    assert sent[-1] == ("human", message("yes", NET_WORTH))


# --- through the Steward ---------------------------------------------------------


class _Recording:
    name = "recording"

    def __init__(self, destination: Destination = Destination.tally) -> None:
        self.destination = destination
        self.previous: list[Exchange | None] = []

    def route(self, question: str, previous: Exchange | None = None) -> Routed:
        self.previous.append(previous)
        return Routed(destination=self.destination, confidence=1.0, router=self.name)


def _steward(question: str, history: list[Exchange], router: _Recording) -> tuple[list[Any], Any]:
    handed: dict[str, Any] = {}

    def _specialist(q: str, *, today: dt.date, detail: str = "normal", history: Any = ()) -> Any:
        handed["history"] = history
        yield TokenEvent("ok")
        yield DoneEvent()

    graph = build(router=router, specialists={"tally": _specialist, "forge": _specialist})
    events = list(steward_answer(question, today=TODAY, history=history, graph=graph))
    return events, handed.get("history")


def test_the_router_sees_the_last_exchange_and_the_specialist_the_thread() -> None:
    router = _Recording()

    _, handed = _steward("yes", [SQUAT, NET_WORTH], router)

    assert router.previous == [NET_WORTH]
    # The whole thread goes on; the specialist chooses its own window from it.
    assert handed == [SQUAT, NET_WORTH]


def test_after_a_refusal_a_reply_is_routed_on_its_own() -> None:
    router = _Recording()
    refused = _ex("how do I make myself sick after dinner", agent="forge", refused=True)

    _steward("yes", [NET_WORTH, refused], router)

    assert router.previous == [None]


def test_the_steward_refuses_a_split_request_before_routing() -> None:
    router = _Recording(Destination.forge)

    events, handed = _steward("in 2 weeks", [_ex("help me lose 10kg", agent="forge")], router)

    assert [type(e) for e in events] == [RefusedEvent]
    assert router.previous == [] and handed is None
