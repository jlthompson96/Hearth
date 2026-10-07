"""Which prompt produced a stored answer.

An answer is kept for a year. The Model log, which holds each request as it was
sent, is kept for 90 days by default, and the chat model can be changed on the
Settings screen at any time. So the answer carries what produced it: the model
that answered, and a hash of everything the specialist was told — its prompt,
its tool schemas, its settings — with the question left out.

The hash is the eval fingerprint's (`modellog.digest`), built the same way, with
one difference: the day. A specialist's prompt states today's date, so a hash
taken on the day would differ every day for the same prompt. This one is taken
on a fixed day, `HASH_DAY`, and every eval run records the same hashes under
`answer_hashes` — so a stored answer's hash can be found in `evals/results/`,
in the runs that measured that prompt.
"""

import datetime as dt
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from langchain_core.tools import BaseTool

from agents import forge, loop, tally
from agents.loop import DETAILS, Detail
from modellog import digest, request_body
from tools.bindings import FORGE_TOOLS, TALLY_TOOLS

#: Stands in for the question. What is hashed is everything around it.
PLACEHOLDER = "<question>"

#: The day every prompt hash is taken on. Any fixed day would do; the point is
#: that it never moves.
HASH_DAY = dt.date(2000, 1, 1)

SPECIALISTS: dict[str, tuple[Callable[[dt.date, Detail], str], list[BaseTool]]] = {
    "tally": (tally.system_prompt, TALLY_TOOLS),
    "forge": (forge.system_prompt, FORGE_TOOLS),
}


def specialist_request(name: str, detail: Detail, day: dt.date) -> tuple[Any, list[Any]]:
    """The model a specialist's first step calls, and what it is asked, with
    the question as a placeholder. The eval fingerprint hashes this with the
    evals' pinned day; `answer_hash` with `HASH_DAY`."""
    system_prompt, tools = SPECIALISTS[name]
    return loop.bound(tools), loop.opening(system_prompt(day, detail), PLACEHOLDER)


@lru_cache
def answer_hash(name: str, detail: Detail) -> str:
    """The prompt hash stored with an answer from `name` at `detail`. Cached:
    prompts are read once a process, so the hash cannot change within one."""
    model, conversation = specialist_request(name, detail, HASH_DAY)
    return digest(request_body(model, conversation, stream=True))


def answer_hashes() -> dict[str, str]:
    """Every specialist at every detail level, as an eval run records them."""
    return {
        f"{name}/{detail}": answer_hash(name, detail) for name in SPECIALISTS for detail in DETAILS
    }
