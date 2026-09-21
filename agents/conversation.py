"""Earlier turns of a thread, and which of them a model is shown.

Until this module a thread was a record, not context: every question reached the
model alone. That broke the day the specialists learned to end an answer with an
offer — "want me to show that by account?" — because the natural reply, "yes",
means nothing on its own.

Three decisions live here rather than in the prompts, because each is a
guardrail and rule 7 puts guardrails in code.

**A specialist sees only its own earlier answers.** Forge has no business
reading someone's balances to answer a question about their squat, and a figure
in its context is a figure it can repeat. Rule 11 scopes tools per agent; this
scopes history the same way.

**A refused turn is never shown to any model.** The pre-flight check exists so
that a question it refuses reaches no model at all. Replaying that question as
context on the next turn would undo it one turn late.

**The window is small and counted in code.** At most `MAX_EXCHANGES` earlier
exchanges and `MAX_CHARS` characters of them, measured on the host on
2026-09-21: a turn on its own peaks near 3,300 of the 8,192 tokens — 1,500
before the question for the prompt and tool schemas, then the tool result, an
answer, and up to 1,400 tokens of reasoning — and answers heavy with figures run
about 2.3 characters to a token. 4,000 characters is about 1,750 tokens, which
leaves room for real data being larger than the fixture. Exchanges are whole or
absent: an answer cut in half would be a list with its end missing, read as
complete.

Earlier *tool results* are carried too, but never shown to the model — they are
the bulk of a turn. They are what the grounding check accepts as the source of a
figure repeated from an earlier answer. The earlier answer itself is not a
source: a figure it invented would otherwise pass the next turn's check by
being repeated.
"""

from collections.abc import Sequence
from dataclasses import dataclass

#: Who can have answered an exchange that a model may be shown.
SPECIALISTS = ("tally", "forge")

#: How many earlier exchanges a specialist is shown, at most.
MAX_EXCHANGES = 3

#: And how many characters of them, questions and answers together.
MAX_CHARS = 4000


@dataclass(frozen=True)
class Exchange:
    """One earlier question and what came back."""

    question: str
    #: Empty when the turn failed before an answer was stored.
    answer: str
    #: The specialist that answered, "unsupported" for a decline, or None.
    agent: str | None
    refused: bool = False
    #: The turn's tool results, as the model saw them. Grounding only.
    results: tuple[str, ...] = ()

    @property
    def answered(self) -> bool:
        """A specialist answered it, and it was not refused."""
        return bool(self.answer) and not self.refused and self.agent in SPECIALISTS


def window(history: Sequence[Exchange], agent: str) -> list[Exchange]:
    """The earlier exchanges `agent` is shown, oldest first.

    Its own answered exchanges, newest first until either cap would be passed;
    anything older than the first one that does not fit is dropped with it, so
    what the model reads is always the most recent run of the conversation.
    """
    chosen: list[Exchange] = []
    used = 0
    for exchange in reversed(history):
        if exchange.agent != agent or not exchange.answered:
            continue
        size = len(exchange.question) + len(exchange.answer)
        if len(chosen) == MAX_EXCHANGES or used + size > MAX_CHARS:
            break
        chosen.append(exchange)
        used += size
    return chosen[::-1]


def previous(history: Sequence[Exchange]) -> Exchange | None:
    """The exchange a new message may be replying to: the last one, if a
    specialist answered it. After a refusal, a decline or a failure there is
    nothing to continue, and the new message is routed on its own."""
    if history and history[-1].answered:
        return history[-1]
    return None


def last_line(answer: str) -> str:
    """The end of an answer, where the prompts put the offer a reply accepts."""
    lines = [line.strip() for line in answer.strip().splitlines() if line.strip()]
    return lines[-1][-300:] if lines else ""
