"""Forge — the fitness specialist.

The same shape as Tally: `preflight.check` runs on the question before any
model call, and a question that trips it never reaches inference. The check was
written for Forge; since Phase 8 the Steward runs it before routing as well, so
this is the second line for callers that name Forge directly.

That ordering is the whole design. The refusal is not something the model is
asked to produce and might not — it is a `return` before the model exists in
this turn, which is what rule 7 means by a guardrail living in code. Phase 11's
exit criterion is that path being tested, and it is testable precisely because
no sampling is involved in reaching it.
"""

import datetime as dt
from collections.abc import Iterator, Sequence

from agents import preflight
from agents.conversation import Exchange, window
from agents.loop import Detail, Event, RefusedEvent, detail_prompt, load_prompt, run
from tools.bindings import FORGE_TOOLS


def system_prompt(today: dt.date, detail: Detail = "normal") -> str:
    return load_prompt("forge").format(today=today.isoformat(), detail=detail_prompt(detail))


def answer(
    question: str,
    *,
    today: dt.date,
    detail: Detail = "normal",
    history: Sequence[Exchange] = (),
) -> Iterator[Event]:
    """Answer `question`, yielding events as they happen.

    Refuses before inference when the pre-flight check fires — on this question,
    or on it read together with the earlier ones Forge is shown, since a request
    can arrive in halves. `today` is required for the same reason it is in
    Tally: "this year" means something different depending on the day, and that
    is a caller's assumption to state.
    """
    earlier = window(history, "forge")
    refusal = preflight.check_conversation([e.question for e in earlier], question)
    if refusal is not None:
        yield RefusedEvent(refusal.signal, refusal.message)
        return

    yield from run(
        caller="forge",
        system=system_prompt(today, detail),
        question=question,
        tools=FORGE_TOOLS,
        history=earlier,
    )
