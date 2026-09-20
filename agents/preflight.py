"""The pre-flight check on what reaches Forge.

This is an input filter in code, not a paragraph in a prompt (CLAUDE.md, rule
7). The distinction is the whole point: a prompt is a request, and an 8B model
at Q4 honours requests unreliably and can be talked out of them. A function that
returns before the model is ever called cannot be argued with.

It runs on the user's text *before* inference. Nothing that trips it reaches the
model, so there is no generated response to police afterwards.

## What it is for

Forge has a food-and-body-shaped surface: it knows body mass over time and how
much someone lifted. Those are useful things to see. They are also exactly the
numbers that, pointed the wrong way, turn a training log into an instrument for
starving yourself — a tool that will happily compute the deficit to reach an
unsafe weight, or how much cardio "cancels" a meal.

## What it is not for

It is not a general safety filter and it does not moralise. Wanting to lose
weight is ordinary and passes. Tracking body mass is the feature. The check
targets a narrow set of signals — purging, compensatory exercise, starvation-
level intake, and targets at a rate or a floor that is not survivable — and
lets everything else through, because a filter that fires on "I want to drop a
few kilos" would be useless within a week and switched off by the person it was
meant to protect.

False positives have a real cost here. So do false negatives. The patterns below
lean toward specificity, and the numeric checks carry explicit thresholds rather
than vibes.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

#: Below this, a stated daily intake target is not a diet, it is starvation.
#: Well under the lowest figure clinical guidance puts on a supervised
#: very-low-calorie diet, so ordinary dieting talk does not reach it.
CALORIE_FLOOR = 1000

#: Sustained loss faster than roughly a kilo a week is not achievable from fat
#: alone. A request to plan one is a request to plan something else.
MAX_KG_PER_WEEK = Decimal("1.5")

KG_PER_LB = Decimal("0.453592")


@dataclass(frozen=True)
class Refusal:
    """Why the turn stopped, and what the user is told.

    `signal` is for the test suite and the audit trail; `message` is the only
    part the person sees.
    """

    signal: str
    message: str


#: Deliberate, unambiguous phrasings. Each has to survive the question "could
#: someone training normally write this?" — which is why "throw up" is paired
#: with an eating context rather than matched alone, since lifters say it about
#: hard sets.
_PURGING = re.compile(
    r"\b("
    r"purge|purging|"
    # Plurals matter: the trailing \b on the group means "laxative" alone does
    # not match "laxatives", which is how anyone would actually write it.
    r"laxatives?|diuretics?|"
    r"(?:throw(?:ing)?\s+up|vomit(?:ing)?|make\s+myself\s+sick)"
    r"(?:\s+\w+){0,4}\s+(?:after|food|meal|eating|dinner|lunch|breakfast)|"
    r"(?:after|post)[-\s]?(?:meal|eating|dinner|binge)(?:\s+\w+){0,3}\s+"
    r"(?:throw(?:ing)?\s+up|vomit)"
    r")\b",
    re.IGNORECASE,
)

#: Exercise as payment for food. The giveaway is a named food or meal on one
#: side and a quantity of exercise on the other.
_COMPENSATORY = re.compile(
    r"\b("
    r"burn\s+off(?:\s+\w+){0,3}\s+(?:meal|dinner|lunch|breakfast|cake|pizza|"
    r"chocolate|calories\s+i\s+ate|what\s+i\s+ate|that)|"
    r"(?:work|run|cardio)(?:\s+\w+){0,2}\s+off(?:\s+\w+){0,3}\s+"
    r"(?:meal|dinner|binge|what\s+i\s+ate)|"
    r"cancel\s+out(?:\s+\w+){0,3}\s+(?:meal|calories|eating|what\s+i\s+ate)|"
    r"earn(?:\s+\w+){0,2}\s+(?:my\s+)?(?:dinner|food|meal|calories)"
    r")\b",
    re.IGNORECASE,
)

#: Not eating, stated as a plan rather than as a passing complaint.
_STARVATION = re.compile(
    r"\b("
    r"starve\s+myself|stop\s+eating(?:\s+(?:entirely|completely|altogether))?|"
    r"skip(?:ping)?\s+(?:all\s+)?meals\s+(?:for|until)|"
    r"(?:fast|fasting|not\s+eat(?:ing)?)\s+for\s+\d+\s*(?:day|days|week)|"
    r"eat\s+nothing"
    r")\b",
    re.IGNORECASE,
)

#: "800 calories a day", "under 900 kcal" — a stated daily ceiling.
_CALORIE_TARGET = re.compile(
    r"(\d{2,4})\s*(?:k?cal(?:orie)?s?)\b(?:\s*(?:a|per|each)\s*day|\s*daily)?",
    re.IGNORECASE,
)
_DAILY_CONTEXT = re.compile(
    r"\b(a\s*day|per\s*day|daily|each\s*day|only|just|maximum|max|under|limit)\b",
    re.IGNORECASE,
)

#: "lose 10kg in 3 weeks".
_RATE = re.compile(
    r"lose\s+(\d+(?:\.\d+)?)\s*(kg|kilos?|kilograms?|lbs?|pounds?)"
    r"(?:\s+\w+){0,3}?\s+in\s+(\d+)\s*(day|days|week|weeks|month|months)",
    re.IGNORECASE,
)

#: The message is one message on purpose. Varying it by which pattern fired
#: would invite reading the filter's mind and steering around it, and the person
#: on the other side does not need a diagnosis from a training log.
_MESSAGE = (
    "I'm not going to help with that one.\n\n"
    "Hearth can show what you've lifted and how your body mass has moved, and "
    "I'm glad to do either. What I won't do is put numbers behind restricting, "
    "compensating for food, or losing weight at a rate that isn't survivable — "
    "that's the point where a training log stops being useful and starts being "
    "a way to hurt yourself.\n\n"
    "If food or your body has been hard lately, talking to someone who knows "
    "this territory is worth more than anything I can calculate. In the US, "
    "ANAD's helpline is 1-888-375-7767, and 988 reaches the Suicide & Crisis "
    "Lifeline any time.\n\n"
    "If you'd like, ask me about your squat or bench progression instead."
)


def _rate_kg_per_week(amount: Decimal, unit: str, count: int, period: str) -> Decimal | None:
    """Python does this arithmetic, not the model (rule 1) — and not the filter's
    caller either, so the threshold is applied the same way every time."""
    if count <= 0:
        return None
    kilos = amount if unit.lower().startswith("k") else amount * KG_PER_LB
    weeks = {
        "day": Decimal(count) / 7,
        "days": Decimal(count) / 7,
        "week": Decimal(count),
        "weeks": Decimal(count),
        "month": Decimal(count) * Decimal("4.345"),
        "months": Decimal(count) * Decimal("4.345"),
    }[period.lower()]
    return kilos / weeks if weeks > 0 else None


def check(text: str) -> Refusal | None:
    """`None` when the question may proceed to the model.

    Ordered cheapest-first, and every branch returns the same message — the
    `signal` is what differs, because the tests and any future audit need to
    know which rule fired even though the person does not.
    """
    if _PURGING.search(text):
        return Refusal("purging", _MESSAGE)
    if _COMPENSATORY.search(text):
        return Refusal("compensatory_exercise", _MESSAGE)
    if _STARVATION.search(text):
        return Refusal("starvation", _MESSAGE)

    # A number alone means nothing: "I burned 800 calories" is a training note.
    # It is a stated daily ceiling that matters.
    if _DAILY_CONTEXT.search(text):
        for match in _CALORIE_TARGET.finditer(text):
            if int(match.group(1)) < CALORIE_FLOOR:
                return Refusal("calorie_floor", _MESSAGE)

    for match in _RATE.finditer(text):
        rate = _rate_kg_per_week(
            Decimal(match.group(1)), match.group(2), int(match.group(3)), match.group(4)
        )
        if rate is not None and rate > MAX_KG_PER_WEEK:
            return Refusal("unsafe_rate", _MESSAGE)

    return None
