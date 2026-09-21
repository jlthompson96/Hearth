"""Tally — the finance specialist.

A prompt and a tool list over the shared loop in `agents.loop`. The loop used to
live here; it moved when Forge arrived and would otherwise have been a second
copy of it.

The pre-flight check runs here too, though the questions it exists for are
Forge's. It guards every entry point rather than a route: the Steward checks
before routing, and each specialist checks again for callers that name it
directly. A guardrail that holds only on the path someone expected is the gap
Phase 8's testing found.
"""

import datetime as dt
from collections.abc import Iterator, Sequence

from agents import preflight
from agents.conversation import Exchange, window
from agents.loop import Detail, Event, RefusedEvent, detail_prompt, load_prompt, run
from tools.bindings import TALLY_TOOLS


def system_prompt(today: dt.date, detail: Detail = "normal") -> str:
    return load_prompt("tally").format(today=today.isoformat(), detail=detail_prompt(detail))


def answer(
    question: str,
    *,
    today: dt.date,
    detail: Detail = "normal",
    history: Sequence[Exchange] = (),
) -> Iterator[Event]:
    """Answer `question`, yielding events as they happen.

    `today` is required and never defaults to `date.today()`. The agent turns
    "this year" into dates, so the day it believes it is changes the answer —
    which makes it something a caller states rather than something the code
    assumes, and something an eval can pin.

    `history` is the thread so far. Tally chooses from it what it is shown —
    its own answers only — and checks those questions and this one together.
    """
    earlier = window(history, "tally")
    refusal = preflight.check_conversation([e.question for e in earlier], question)
    if refusal is not None:
        yield RefusedEvent(refusal.signal, refusal.message)
        return

    yield from run(
        caller="tally",
        system=system_prompt(today, detail),
        question=question,
        tools=TALLY_TOOLS,
        history=earlier,
    )
