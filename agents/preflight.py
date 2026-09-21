"""The pre-flight check on every question, before any model sees it.

This is an input filter in code, not a paragraph in a prompt (CLAUDE.md, rule
7). The distinction is the whole point: a prompt is a request, and an 8B model
at Q4 honours requests unreliably and can be talked out of them. A function that
returns before the model is ever called cannot be argued with.

It runs on the user's text *before* inference — before the Steward's router,
before a specialist, before a title is made. Nothing that trips it reaches a
model, so there is no generated response to police afterwards.

It was written for Forge and first ran only inside Forge. Phase 8's testing
found the hole in that: the UI never names Forge, and the Steward routed "how do
I make myself sick after dinner" to `unsupported`, where the check never saw it.
It now runs at every entry point — the Steward and both specialists — and so it
sees money questions as well as training ones. That is why the patterns below
are tighter than they were: "purge my old accounts", "stop eating out" and "rose
fast for 3 days" all tripped it, and a filter that refuses a finance question
with an eating-disorder message is one that gets switched off.

## What it is for

Forge has a food-and-body-shaped surface: it knows body mass over time and how
much someone lifted. Those are useful things to see. They are also exactly the
numbers that, pointed the wrong way, turn a training log into an instrument for
starving yourself — a tool that will happily compute the deficit to reach an
unsafe weight, or how much cardio "cancels" a meal.

Two more signals, on the owner's instruction (2026-09-21): **self-harm** and
**hate speech** are not accepted, whatever they are asked alongside. Each has
its own reply. Someone in crisis is pointed to people who can help, and not
offered their squat progression; someone being hateful is declined in one
line, without a lecture.

## What it is not for

It is not a general moderation system and it does not moralise. Wanting to lose
weight is ordinary and passes. Tracking body mass is the feature. The check
targets a narrow set of signals and lets everything else through, because a
filter that fires on "I want to drop a few kilos", "this workout is killing me"
or "my Chinese stocks fell" would be useless within a week and switched off by
the person it was meant to protect.

False positives have a real cost here. So do false negatives. The patterns below
lean toward specificity, and the numeric checks carry explicit thresholds rather
than vibes.
"""

import re
from collections.abc import Sequence
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


#: Whitespace within one line. `check_conversation` joins questions with a line
#: break, and a pattern built on this stops at it.
_SP = r"[^\S\n]+"

#: A meal, or eating. What turns "throwing up" from a hard set into something
#: else, and "purge" from housekeeping into something else.
_EATING = r"(?:meals?|eating|eat|ate|food|dinner|lunch|breakfast|snacks?|binge\w*)"

#: Named food, for the forms that trade exercise against it.
_FOOD = (
    r"(?:meals?|dinner|lunch|breakfast|cake|pizza|chocolate|dessert|snacks?|binge|food|"
    r"burgers?|fries|ice\s+cream|cookies?|donuts?|beers?|wine|drinks)"
)

#: Each form has to survive two questions: "could someone training normally
#: write this?" and — since the check runs on every question — "could someone
#: asking about money write this?"
#:
#: - Vomiting counts next to eating: "after dinner", not "after leg day",
#:   because lifters say it about hard sets.
#: - "Purge" counts next to eating, as binge-and-purge, about the body, or said
#:   of oneself with no object. "I've been purging" is a disclosure; "purge my
#:   old accounts" is housekeeping; the object is the difference — so the object
#:   must be in the same question. Read across a follow-up, "how do I purge old
#:   threads" and "has my body mass gone down" would otherwise be one sentence.
#: - Laxatives count alone: no money question mentions them. Diuretics count
#:   only beside a weight goal, because they are also prescribed.
_PURGING = re.compile(
    r"\b(?:throw(?:ing)?\s+up|vomit(?:ing)?|make\s+myself\s+sick)"
    rf"(?:\s+\w+){{0,4}}?\s+(?:after|before)\s+(?:\w+\s+){{0,2}}{_EATING}\b"
    rf"|\b(?:after|post)[-\s]?{_EATING}(?:\s+\w+){{0,3}}?\s+(?:throw(?:ing)?\s+up|vomit\w*|purg\w*)"
    r"|\bbinge[-\s]+(?:and[-\s]+)?purg\w*"
    rf"|\bpurg(?:e|es|ed|ing)\b(?:{_SP}\w+){{0,4}}?{_SP}(?:(?:after|before){_SP}(?:\w+{_SP}){{0,2}})?{_EATING}\b"
    rf"|\bpurg(?:e|es|ed|ing)\b(?:{_SP}\w+){{0,4}}?{_SP}(?:weight|lean|gains|muscle|body|fat|calories)\b"
    r"|\b(?:i|i'?m|i'?ve|i\s+have|i\s+am|been|keep|kept|started|stop(?:ped)?|quit|to)\s+"
    r"(?:\w+\s+){0,2}?purg(?:e|es|ed|ing)\b"
    r"(?!\s+(?:my|the|old|all|these|those|this|that|some|a|an|every|any|them|it|unused|"
    r"duplicate|stale|out)\b)"
    r"|\blaxatives?\b"
    r"|\b(?:diuretics?|water\s+pills?)\b(?:\s+\w+){0,6}?\s+"
    r"(?:weight|cut|cutting|lean|lose|losing|slim|thin|weigh[-\s]?ins?|fat)\b"
    r"|\b(?:weight|cut|cutting|lean|lose|losing|slim|thin|weigh[-\s]?ins?)\b"
    r"(?:\s+\w+){0,6}?\s+(?:diuretics?|water\s+pills?)\b",
    re.IGNORECASE,
)

#: Exercise as payment for food: a named food on one side, exercise on the
#: other. "Burn off some stress after that meeting" names no food, and "cancel
#: out the fees" names fees.
_COMPENSATORY = re.compile(
    rf"\bburn\s+off(?:\s+\w+){{0,3}}?\s+(?:(?:that|the|this|my)\s+)?(?:{_FOOD}|calories\s+i\s+ate|what\s+i\s+ate)\b"
    rf"|\b(?:work|run|cardio)(?:\s+\w+){{0,2}}?\s+off(?:\s+\w+){{0,3}}?\s+(?:{_FOOD}|what\s+i\s+ate)\b"
    rf"|\bcancel\s+out(?:\s+\w+){{0,3}}?\s+(?:{_FOOD}|calories|eating|what\s+i\s+ate)\b",
    re.IGNORECASE,
)

#: "Earn my dinner" is the same trade, but "earn my food budget" is a wage, so
#: it counts only when the question also talks about exercise.
_EARN_FOOD = re.compile(
    rf"\bearn(?:\s+\w+){{0,2}}?\s+(?:my\s+)?(?:{_FOOD}|calories)\b"
    r"(?!\s+(?:budget|money|allowance|bills?|costs?|spending|expenses?))",
    re.IGNORECASE,
)
_EXERCISE = re.compile(
    r"\b(?:run|runs|running|ran|miles?|km|kilomet(?:re|er)s?|cardio|workouts?|exercise|steps|"
    r"burpees|laps|reps|gym|treadmill|bike|biking|cycling|swim|swimming|walk|walking)\b",
    re.IGNORECASE,
)

#: Not eating, stated as a plan rather than as a passing complaint.
#: "Stop eating" counts unless what follows makes it moderation ("stop eating
#: out", "stop eating junk food"); "fast for N days" needs someone to be doing
#: the fasting, so "rose fast for 3 days" is a portfolio, not a diet; "eat
#: nothing but chicken and rice" is a meal plan.
_STARVATION = re.compile(
    r"\bstarv(?:e|es|ing)\s+myself\b"
    r"|\bstop\s+eating\b(?!\s+(?:out|at|so\s+much|as\s+much|junk|sugar|sweets|candy|snacks?|"
    r"fast\s+food|takeout|take-?away|meat|red\s+meat|dairy|gluten|carbs?|bread|pasta|late|"
    r"before|after|processed|fried|chips|like\s+that|badly|unhealthily|unhealthy))"
    r"|\bskip(?:ping)?\s+(?:all\s+)?(?:my\s+)?meals\s+(?:for|until)\b"
    r"|(?:\b(?:to|will|i'?ll|i|we|gonna|water|dry|extended|a)\s+fast|\bfasting|\bnot\s+eat(?:ing)?)"
    r"\s+for\s+\d+\s*(?:days?|weeks?)\b"
    r"|\beat(?:ing)?\s+nothing\b(?!\s+but)",
    re.IGNORECASE,
)

#: "800 calories a day", "under 900 kcal", "a 900 calorie diet" — an intake.
_CALORIE_TARGET = re.compile(r"(\d{2,4})\s*-?\s*(?:k?cal(?:orie)?s?)\b", re.IGNORECASE)
_DAILY_CONTEXT = re.compile(
    r"\b(a\s*day|per\s*day|daily|each\s*day|only|just|maximum|max|under|limit|diet)\b",
    re.IGNORECASE,
)

#: What makes a calorie figure a change rather than an intake: "cut 500
#: calories", "burn about 600", "a 500 calorie deficit". Deliberately not "to"
#: — "cut to 800 calories a day" is an intake — and not "less than", which is
#: a ceiling.
_DELTA_BEFORE = re.compile(
    r"\b(?:cut(?:ting)?|reduc(?:e|ing)|drop(?:ping)?|trim(?:ming)?|shav(?:e|ing)|minus|"
    r"burn(?:s|ed|t|ing)?)\s+(?:(?:by|about|around|roughly|an?\s+extra|another|off)\s+)?$",
    re.IGNORECASE,
)
_DELTA_AFTER = re.compile(
    r"^\s*(?:(?:a|per|each)\s+day\s+|daily\s+)?(?:deficit|less|fewer|below|under\s+maintenance|"
    r"surplus|more|extra|over)\b",
    re.IGNORECASE,
)

#: "lose 10kg in 3 weeks".
_RATE = re.compile(
    r"lose\s+(\d+(?:\.\d+)?)\s*(kg|kilos?|kilograms?|lbs?|pounds?)"
    r"(?:\s+\w+){0,3}?\s+in\s+(\d+)\s*(day|days|week|weeks|month|months)",
    re.IGNORECASE,
)

#: "Pounds" is money as well as weight. Beside anything financial, a rate in
#: pounds is a portfolio's, not a body's. Kilograms and "lbs" are never money.
_MONEY_CONTEXT = re.compile(
    r"[$£€]|\b(?:accounts?|portfolio|stocks?|shares|brokerage|savings|net\s+worth|invest\w*|"
    r"market|trading|funds?|balance|gbp|sterling|isa|pension)\b",
    re.IGNORECASE,
)

#: Self-harm: intent, a method, or the wish to be gone, said of oneself. Gym and
#: money idiom is the hard part — "this workout is killing me", "I hurt myself
#: deadlifting", "financial suicide", a "suicide grip" on the bench — so hurting
#: counts only with intent ("want to hurt myself") or as a habit ("been cutting
#: myself"), not as an accident. "Leg day makes me want to die" is refused, and
#: that is a cost accepted knowingly: a caring reply to an idiom is cheap, and
#: a missed one is not.
_SELF_HARM = re.compile(
    r"\bkill(?:ing)?\s+myself\b"
    r"(?!\s+(?:at|in|on|with|over|for)\s+(?:the\s+)?(?:gym|work|training|workouts?|cardio|"
    r"these|this|leg|squats?|deadlifts?|bench))"
    r"|\b(?:end(?:ing)?|tak(?:e|ing))\s+my\s+(?:own\s+)?life\b"
    r"|\bend\s+it\s+all\b"
    r"|\bself[-\s]?harm\w*"
    r"|\b(?:want|wanna|trying|urges?|need|planning|thinking\s+about|thoughts\s+of|feel\s+like)"
    r"\s+(?:to\s+)?(?:hurt|harm|cut|burn|hurting|harming|cutting|burning)\s+myself\b"
    r"|\b(?:been|keep|kept|started|start|stop|stopped)\s+(?:cutting|burning|hurting|harming)\s+myself\b"
    r"|\bhurt\s+myself\s+on\s+purpose\b"
    r"|\b(?:want|wanna)\s+(?:to\s+)?die\b"
    r"|\bwish\s+i\s+(?:was|were)\s+(?:dead|gone|never\s+born)\b"
    r"|\bbetter\s+off\s+(?:dead|without\s+me)\b"
    r"|\b(?:don'?t|do\s+not)\s+want\s+to\s+(?:be\s+alive|live|exist)\b"
    r"|\bnot\s+worth\s+living\b"
    r"|\b(?:painless|easiest|quickest|fastest|best)\s+(?:way|ways|method|methods)\s+to\s+(?:die|kill\s+myself)\b"
    r"|\bhow\s+many\s+(?:pills|tablets|sleeping\s+pills)\b",
    re.IGNORECASE,
)

#: "Suicide" is checked in code rather than in the pattern: the word has a
#: bench-press grip, a conditioning drill and a figure of speech attached to it,
#: and which one it is depends on the words either side.
_SUICIDE = re.compile(r"\b(\w+\s+)?(suicid\w*)(\s+\w+)?", re.IGNORECASE)
_SUICIDE_IDIOM_BEFORE = {"financial", "career", "political", "social", "commercial"}
_SUICIDE_IDIOM_AFTER = {
    "grip",
    "grips",
    "sprint",
    "sprints",
    "drill",
    "drills",
    "run",
    "runs",
    "squeeze",
}

#: Hate speech: violence toward, or dehumanising statements about, a group of
#: people — by what they are, not what they did. A group named beside money or
#: food is a market or a restaurant ("my Chinese stocks", "the Mexican place").
_GROUPS = (
    r"(?:jews|jewish\s+people|muslims|christians|catholics|hindus|sikhs|blacks|black\s+people|"
    r"whites|white\s+people|asians|mexicans|immigrants|refugees|migrants|gays|gay\s+people|"
    r"lesbians|trans\s+people|transgender\s+people|women|men|disabled\s+people|arabs|hispanics|"
    r"latinos|africans|indians|chinese|koreans|japanese|gypsies|roma|foreigners|homosexuals)"
)
_NOT_PEOPLE = (
    r"(?!\s+(?:stocks?|shares|companies|markets?|food|restaurants?|funds?|economy|bonds?|"
    r"takeout|cuisine|place))"
)
_HATE = re.compile(
    r"\b(?:kill|murder|exterminate|eradicate|gas|lynch|wipe\s+out|purge|eliminate|get\s+rid\s+of|"
    rf"shoot|hang|deport)\s+(?:all\s+)?(?:the\s+|those\s+|these\s+|every\s+)?{_GROUPS}\b{_NOT_PEOPLE}"
    rf"|\b{_GROUPS}\s+(?:are|is)\s+(?:all\s+|just\s+|nothing\s+but\s+)?"
    r"(?:animals|vermin|subhuman|sub-human|cockroaches|rats|parasites|savages|apes|monkeys|"
    r"a\s+disease|a\s+plague|a\s+cancer|filth|scum|inferior|not\s+human|less\s+than\s+human|"
    r"genetically\s+inferior|evil)\b"
    rf"|\b{_GROUPS}\s+(?:should|must|need\s+to|deserve\s+to|ought\s+to)\s+(?:all\s+)?"
    r"(?:die|be\s+killed|be\s+exterminated|be\s+gassed|be\s+wiped\s+out|not\s+exist|"
    r"be\s+eliminated|burn|be\s+deported)\b"
    rf"|\bi\s+hate\s+(?:all\s+)?(?:the\s+)?{_GROUPS}\b{_NOT_PEOPLE}",
    re.IGNORECASE,
)

#: Tokens for the slur check: runs of letters and the characters people swap in
#: for them. Split words ("z.q.x") are rejoined before the lookup as well.
_TOKEN = re.compile(r"[\w@$!|]+")

_SELF_HARM_MESSAGE = (
    "I can't help with that here, and I don't want to just leave it there.\n\n"
    "If you're thinking about hurting yourself or ending your life, please reach "
    "out now. In the US, call or text 988 for the Suicide & Crisis Lifeline, or "
    "text HOME to 741741 for the Crisis Text Line. If you're in immediate danger, "
    "call 911.\n\n"
    "You don't need the right words. Saying you're having a hard time is enough."
)

_HATE_MESSAGE = (
    "I won't respond to that.\n\n"
    "I answer questions about your own finances and training, and I'm glad to "
    "help with either."
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


def _suicide(text: str) -> bool:
    for match in _SUICIDE.finditer(text):
        before = (match.group(1) or "").strip().lower()
        after = (match.group(3) or "").strip().lower()
        word = match.group(2).lower()
        if before in _SUICIDE_IDIOM_BEFORE or after in _SUICIDE_IDIOM_AFTER:
            continue
        # "Suicides" on its own is the sprint drill; the plural is not how
        # anyone describes their own state.
        if word == "suicides":
            continue
        return True
    return False


def _slur(text: str) -> bool:
    """Look every token up by digest, singular as well, and the whole text with
    separators removed so "z.q.x" cannot step around it."""
    from agents.hate_terms import DIGESTS, digest, normalize

    tokens = [normalize(t) for t in _TOKEN.findall(text)]
    tokens.append(normalize("".join(text.split())))
    for token in tokens:
        if not token:
            continue
        forms = {token, token.removesuffix("s"), token.removesuffix("es")}
        if any(digest(form) in DIGESTS for form in forms if form):
            return True
    return False


def check(text: str) -> Refusal | None:
    """`None` when the question may proceed to the model.

    Ordered cheapest-first, and every branch returns the same message — the
    `signal` is what differs, because the tests and any future audit need to
    know which rule fired even though the person does not.
    """
    # Self-harm first: of everything here it is the one where the reply matters
    # most, and it has a reply of its own.
    if _SELF_HARM.search(text) or _suicide(text):
        return Refusal("self_harm", _SELF_HARM_MESSAGE)
    if _HATE.search(text) or _slur(text):
        return Refusal("hate", _HATE_MESSAGE)

    if _PURGING.search(text):
        return Refusal("purging", _MESSAGE)
    if _COMPENSATORY.search(text) or (_EARN_FOOD.search(text) and _EXERCISE.search(text)):
        return Refusal("compensatory_exercise", _MESSAGE)
    if _STARVATION.search(text):
        return Refusal("starvation", _MESSAGE)

    # A number alone means nothing: "I burned 800 calories" is a training note,
    # and "cut 500 calories a day" is a deficit. It is a stated intake below the
    # floor that matters.
    if _DAILY_CONTEXT.search(text):
        for match in _CALORIE_TARGET.finditer(text):
            before, after = text[: match.start()], text[match.end() :]
            if _DELTA_BEFORE.search(before) or _DELTA_AFTER.match(after):
                continue
            if int(match.group(1)) < CALORIE_FLOOR:
                return Refusal("calorie_floor", _MESSAGE)

    for match in _RATE.finditer(text):
        if match.group(2).lower().startswith("p") and _MONEY_CONTEXT.search(text):
            continue
        rate = _rate_kg_per_week(
            Decimal(match.group(1)), match.group(2), int(match.group(3)), match.group(4)
        )
        if rate is not None and rate > MAX_KG_PER_WEEK:
            return Refusal("unsafe_rate", _MESSAGE)

    return None


def check_conversation(earlier: Sequence[str], question: str) -> Refusal | None:
    """`check` over every question a model is about to read, together.

    Once a follow-up carries earlier turns, the model reads more than one
    question, and a request the check would refuse can arrive in halves — "help
    me lose 10kg", then "in 2 weeks" — each of which passes alone. Joined in
    order, as the model reads them, they are the question that was asked.

    The new question is checked alone first, so a refusal is attributed to it
    whenever it is enough by itself.
    """
    return check(question) or check("\n".join([*earlier, question]))
