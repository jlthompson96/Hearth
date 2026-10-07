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


def ungrounded(
    answer: str, results: Iterable[str], questions: Iterable[str] = (), *, strict: bool = False
) -> list[str]:
    """The figures in `answer` that no tool result states as a figure of the
    same kind, and no question contains as a number — in the order they appear,
    each once. `strict` adds the figures written without their sign
    (`unitless`)."""
    results, questions = list(results), list(questions)
    typed = _values(_NUMBER, questions, 0)
    flagged: list[str] = []
    for kind in _KINDS:
        known = _values(kind, results, 1) | typed
        for match in kind.finditer(answer):
            figure = match.group(0).strip()
            if _value(match.group(1)) not in known and figure not in flagged:
                flagged.append(figure)
    if strict:
        flagged += [f for f in unitless(answer, results, questions) if f not in flagged]
    return flagged


# --- figures written without the sign of their kind --------------------------------
#
# The sign is what told the check a number was a figure: `$`, a weight unit, `%`.
# An answer that wrote "38,250 dollars" or "about 38 thousand" walked past it,
# and a rounded figure is exactly what rule 1 exists to catch. These are found
# by `unitless`, and flagged only with `strict=True`: added 2026-10-07 without a
# measured run behind them, they are reported by the evals first, and enforced
# once a run shows what they catch (docs/reviews/genai-review-2026-10-07-plan.md,
# PR 7).

#: "38,250 dollars", "38250 USD": money, by the word.
_DOLLAR_WORDS = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?(?:dollars?|usd)\b", re.IGNORECASE)
#: "38 thousand", "38.2k", "$1.2 million": money, scaled. Not "m" for million:
#: in a training answer it is metres or minutes.
_SCALED = re.compile(r"(?<![\w.])\$?(\d+(?:\.\d+)?)\s?(k|thousand|million)\b", re.IGNORECASE)
_SCALE = {"k": Decimal(1000), "thousand": Decimal(1000), "million": Decimal(1_000_000)}
#: "38,250" with no sign at all: any number with a thousands separator. Years
#: and day counts have none, and dates are taken out first.
_BARE = re.compile(
    r"(?<![\$\d.,\w-])(\d{1,3}(?:,\d{3})+(?:\.\d+)?)"
    r"(?![\d%]|\s?(?:kg|kgs|kilos?|kilograms?|lbs?|pounds?|dollars?|usd|k|thousand|million)\b)",
    re.IGNORECASE,
)


def unitless(answer: str, results: Iterable[str], questions: Iterable[str] = ()) -> list[str]:
    """Figures in `answer` written without `$`, a weight unit or `%` that no
    tool returned and no question carried — in the order found, each once.

    A worded or scaled amount is money, and is grounded by a dollar figure in a
    result. A bare number is grounded by any number in a result: a quantity the
    tools write without commas ("quantity 1200") is still the quantity.
    """
    results, questions = list(results), list(questions)
    typed = _values(_NUMBER, questions, 0)
    money = _values(_MONEY, results, 1) | typed
    anything = _values(_NUMBER, results, 0) | typed
    text = _DATE.sub(" ", answer)
    found: list[str] = []

    def note(figure: str) -> None:
        if figure not in found:
            found.append(figure)

    for match in _DOLLAR_WORDS.finditer(text):
        if _value(match.group(1)) not in money:
            note(match.group(0).strip())
    for match in _SCALED.finditer(text):
        base = _value(match.group(1))
        if base is not None and base * _SCALE[match.group(2).lower()] not in money:
            note(match.group(0).strip())
    for match in _BARE.finditer(text):
        if _value(match.group(1)) not in anything:
            note(match.group(0).strip())
    return found
