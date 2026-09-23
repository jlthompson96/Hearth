"""Figures in an answer that no tool produced.

Rule 1 — the model never does arithmetic — is asked of the model in its prompt
and measured by the evals against a fixture. Neither checks the answer to the
question someone actually asked. This does: every dollar amount and weight an
answer states is looked for among the numbers its turn's tools returned, and
among the numbers in the question itself, which an answer may fairly quote
back. A figure found in neither was computed, rounded or invented by the model.

It flags; it cannot block. By the time an answer is whole it has streamed, so
the flag is shown under it and stored with it, and whoever reads the answer
knows which figure has no source.

Figures are compared by value, not as strings. "$38,250" for a tool's
"$38,250.00" is the same figure; "fell $50.00" for a tool's "-$50.00" is the
same figure told as a fall. "About $38,000" is a different figure, and is
flagged.

Percentages count as figures, and for the same reason. Asked what share of a
net worth sits in one account, the model answered "100%" — a number no tool
returned, worked out from a total it had misread. A share is arithmetic on two
figures, which is exactly what rule 1 forbids, so a percentage the tools did
not state is flagged like any other invented number.
"""

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

_MINUS = chr(0x2212)

_MONEY = re.compile(r"[-" + _MINUS + r"]?\$\s?(\d[\d,]*(?:\.\d+)?)")
_WEIGHT = re.compile(
    r"(\d+(?:\.\d+)?)\s?(?:kg|kgs|kilos?|kilograms?|lbs?|pounds?)\b", re.IGNORECASE
)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _value(text: str) -> Decimal | None:
    try:
        return abs(Decimal(text.replace(",", "")))
    except InvalidOperation:
        return None


def ungrounded(answer: str, sources: Iterable[str]) -> list[str]:
    """The figures in `answer` whose value appears in none of `sources`, in the
    order they appear, each once."""
    known = {
        v for source in sources for n in _NUMBER.findall(source) if (v := _value(n)) is not None
    }
    flagged: list[str] = []
    for pattern in (_MONEY, _WEIGHT, _PERCENT):
        for match in pattern.finditer(answer):
            figure = match.group(0).strip()
            if _value(match.group(1)) not in known and figure not in flagged:
                flagged.append(figure)
    return flagged
