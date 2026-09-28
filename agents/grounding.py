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

A tool's figure grounds only a figure of its own kind: a dollar amount by a
dollar amount, a weight by a weight, a percentage by a percentage. Every number
in a result used to count, and a result is full of numbers that are not
figures — the day, month and year of every line, a rep count, a number of
recordings — so "$31" passed because a line was dated the 31st. The tools write
every figure with its unit (`tools.bindings`), which is what makes this safe.

A question grounds by any number in it, because people type amounts loosely —
"over 40000" is fairly answered "$40,000". Only its dates are set aside first.
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
#: 2026-01-31, 2026-01 and 2026-Q1: the ways a tool or a person writes a date.
_DATE = re.compile(r"\b\d{4}-(?:\d{2}-\d{2}|\d{2}|Q[1-4])\b")

_KINDS = (_MONEY, _WEIGHT, _PERCENT)


def _value(text: str) -> Decimal | None:
    try:
        return abs(Decimal(text.replace(",", "")))
    except InvalidOperation:
        return None


def _values(pattern: re.Pattern[str], texts: Iterable[str], group: int) -> set[Decimal]:
    return {
        v
        for text in texts
        for match in pattern.finditer(_DATE.sub(" ", text))
        if (v := _value(match.group(group))) is not None
    }


def ungrounded(answer: str, results: Iterable[str], questions: Iterable[str] = ()) -> list[str]:
    """The figures in `answer` that no tool result states as a figure of the
    same kind, and no question contains as a number — in the order they appear,
    each once."""
    results, questions = list(results), list(questions)
    typed = _values(_NUMBER, questions, 0)
    flagged: list[str] = []
    for kind in _KINDS:
        known = _values(kind, results, 1) | typed
        for match in kind.finditer(answer):
            figure = match.group(0).strip()
            if _value(match.group(1)) not in known and figure not in flagged:
                flagged.append(figure)
    return flagged
