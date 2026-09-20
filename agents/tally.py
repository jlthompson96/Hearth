"""Tally — the finance specialist.

A prompt and a tool list over the shared loop in `agents.loop`. The loop used to
live here; it moved when Forge arrived and would otherwise have been a second
copy of it.
"""

import datetime as dt
from collections.abc import Iterator

from agents.loop import Event, load_prompt, run
from tools.bindings import TALLY_TOOLS


def system_prompt(today: dt.date) -> str:
    return load_prompt("tally").format(today=today.isoformat())


def answer(question: str, *, today: dt.date) -> Iterator[Event]:
    """Answer `question`, yielding events as they happen.

    `today` is required and never defaults to `date.today()`. The agent turns
    "this year" into dates, so the day it believes it is changes the answer —
    which makes it something a caller states rather than something the code
    assumes, and something an eval can pin.
    """
    return run(system=system_prompt(today), question=question, tools=TALLY_TOOLS)
