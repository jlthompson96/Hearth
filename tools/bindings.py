"""The `@tool` surface over the Phase 3 query functions.

Kept apart from `tools/finance.py` and `tools/fitness.py` on purpose: the data
layer keeps no LangChain dependency, its tests do not import an agent framework
to run, and the pure functions stay callable without one. Phase 3's fifty-six
tests still exercise the arithmetic; these wrappers are a thin rendering layer
over results that are already correct.

Each wrapper does three things the pure functions deliberately do not:

  - opens its own read-only connection, because a tool call arrives from a model
    rather than from a caller already holding a transaction;
  - renders the result as compact text, because prompts receive tool outputs and
    every line competes with conversation history for 8,192 tokens;
  - states the coverage caveat verbatim where one applies, rather than handing
    the model a list of dates and hoping it writes the sentence itself.

The wrappers never compute anything. Every figure below is copied out of a
dataclass that Phase 3 already rounded (CLAUDE.md, rule 1) — the f-strings here
format, they do not add.

Tool descriptions are what the model sees, and each one costs context before the
first message (rule 11). They are terse deliberately, and `schema_cost` exists so
the total can be logged rather than assumed.
"""

import datetime as dt
import json
from decimal import Decimal

import sqlalchemy as sa
from langchain_core.tools import BaseTool, tool

from db.session import readonly_connection
from tools.finance import (
    Allocation,
    BalanceHistory,
    NetWorthTrend,
    Position,
    UnknownAccountError,
    get_allocation,
    get_balance_history,
    get_net_worth_trend,
)
from tools.fitness import (
    LiftProgression,
    MetricTrend,
    UnknownExerciseError,
    UnknownMetricError,
    get_body_metric_trend,
    get_lift_progression,
)

#: Dates are rendered and parsed in one format everywhere. The model is told
#: this in the tool description and again in the prompt; at 4B it needs both.
ISO = "%Y-%m-%d"


def _money(value: Decimal) -> str:
    """US currency, the sign ahead of the dollar sign: `$38,250.00`, `-$1,800.00`.

    The model copies this string rather than composing one. Handed a bare
    `38,250.00` it added the `$` itself — formatting it should not be trusted
    with, least of all on a negative, where the sign has two places to go and
    only one of them is right.
    """
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _signed(value: Decimal | None) -> str:
    """Always signed for a change, so a fall cannot be read as a rise by a model
    skimming for digits."""
    if value is None:
        return "not enough data to state a change (fewer than two snapshots)"
    return f"{'+' if value >= 0 else '-'}{_money(abs(value))}"


def _render_history(result: BalanceHistory) -> str:
    if not result.points:
        return (
            f"No snapshots for {result.account_label} between "
            f"{result.start:{ISO}} and {result.end:{ISO}}."
        )
    lines = [
        f"{result.account_label} ({result.currency}) {result.start:{ISO}} to {result.end:{ISO}}",
        *(f"  {p.as_of:{ISO}}  {_money(p.balance)}" for p in result.points),
        f"change over the period: {_signed(result.change)}",
    ]
    return "\n".join(lines)


def _render_trend(result: NetWorthTrend, available: tuple[dt.date, dt.date] | None = None) -> str:
    if not result.points:
        # An empty period is an absence of data, and the one thing that keeps a
        # model from reporting it as "no change" is knowing what the data does
        # cover. Saying so here rather than leaving the prompt to carry it
        # alone: a fact belongs in the tool output, not in an instruction.
        answer = (
            f"NO DATA: there are no snapshots at all between "
            f"{result.start:{ISO}} and {result.end:{ISO}}. This is missing "
            f"data, not a flat balance — no change can be reported."
        )
        if available:
            answer += f" Recorded data runs from {available[0]:{ISO}} to {available[1]:{ISO}}."
        return answer
    # A partial total is marked on its own line, not only in the caveat below.
    # A walk through the months reads each line on its own, and an unmarked
    # July missing one account reads as a July that fell — which is how a
    # detailed answer came to describe "a sharp drop" that never happened.
    incomplete = set(result.coverage.incomplete_dates)
    lines = [
        f"net worth {result.start:{ISO}} to {result.end:{ISO}}",
        *(
            f"  {p.as_of:{ISO}}  {_money(p.balance)}"
            + (
                "  incomplete: an account has no figure for this date"
                if p.as_of in incomplete
                else ""
            )
            for p in result.points
        ),
        f"change over the period: {_signed(result.change)}",
    ]
    # The caveat is a finished sentence from Phase 3 and the agent is required
    # to repeat it. The instruction to do so lives in the prompt, not on this
    # line: when the two were combined the model dutifully read the instruction
    # out to the user along with the sentence. A field carries content; telling
    # the model what to do with it is the prompt's job.
    caveat = result.coverage.caveat()
    if caveat:
        lines.append(f"caveat: {caveat}")
    else:
        lines.append("coverage: complete for every date in this period")
    return "\n".join(lines)


def _render_allocation(result: Allocation) -> str:
    if result.as_of_used is None:
        return f"No holdings recorded on or before {result.as_of_requested:{ISO}}."
    lines = [f"allocation as of {result.as_of_used:{ISO}}"]
    if result.as_of_used != result.as_of_requested:
        # Answering a question about today with March's data without saying so
        # is how a figure stops being trustworthy without ever being wrong.
        lines.append(
            f"caveat: You asked about {result.as_of_requested:{ISO}}; the most "
            f"recent holdings on or before that date are from "
            f"{result.as_of_used:{ISO}}."
        )
    lines += [f"  {s.symbol}  {_money(s.market_value)}  {s.percentage}%" for s in result.slices]
    lines.append(f"total: {_money(result.total)}")
    if result.accounts:
        lines.append("by account:")
        for account in result.accounts:
            lines.append(f"  {account.label}  {_money(account.total)}")
            lines += [_position_line(p) for p in account.positions]
    return "\n".join(lines)


def _quantity(value: Decimal) -> str:
    """120, not 120.00000000; 100.5 as 100.5. Trailing zeros are storage, not
    information."""
    return f"{value.normalize():f}"


def _price(value: Decimal) -> str:
    """To the cent, and further only when the price carries more: $72.00,
    $72.1234. Trimmed, never rounded."""
    sign = "-" if value < 0 else ""
    whole, _, fraction = f"{abs(value):,.6f}".partition(".")
    return f"{sign}${whole}.{fraction.rstrip('0').ljust(2, '0')}"


def _position_line(position: Position) -> str:
    if position.quantity is None:
        return f"    {position.symbol}  {_money(position.market_value)}"
    price = f" at {_price(position.price)}" if position.price is not None else ""
    return (
        f"    {position.symbol}  quantity {_quantity(position.quantity)}{price}  "
        f"{_money(position.market_value)}"
    )


@tool
def net_worth_trend(start: str, end: str) -> str:
    """Total net worth across ALL accounts over a period, with data-coverage caveats.

    Use for: net worth, total wealth, overall financial position, "how am I doing".
    Dates are ISO yyyy-mm-dd and both are required.
    """
    with readonly_connection() as conn:
        result = get_net_worth_trend(conn, dt.date.fromisoformat(start), dt.date.fromisoformat(end))
        return _render_trend(result, None if result.points else _recorded_range(conn))


@tool
def balance_history(account_label: str, start: str, end: str) -> str:
    """Balances for ONE named account over a period.

    Use only when the user names a specific account. For an overall total use
    net_worth_trend instead. Dates are ISO yyyy-mm-dd and all three arguments
    are required.
    """
    with readonly_connection() as conn:
        try:
            return _render_history(
                get_balance_history(
                    conn, account_label, dt.date.fromisoformat(start), dt.date.fromisoformat(end)
                )
            )
        except UnknownAccountError:
            # "No movement" and "there is no such account" are different
            # answers, and the second is usually a typo. Phase 3 raises rather
            # than returning empty; the tool turns that into something the model
            # can act on instead of an exception that ends the turn.
            known = _account_labels()
            return (
                f"There is no account labelled {account_label!r}. "
                f"Known accounts: {', '.join(known) if known else 'none'}."
            )


@tool
def allocation(as_of: str) -> str:
    """Investment holdings on one date: by symbol with percentages, then each
    account's positions with quantity, price and value.

    Use for: allocation, holdings, positions, what am I invested in. Answers with
    the most recent holdings on or before the date given, and says so when that
    is an earlier date. `as_of` is ISO yyyy-mm-dd and is required.
    """
    with readonly_connection() as conn:
        return _render_allocation(get_allocation(conn, dt.date.fromisoformat(as_of)))


def _recorded_range(conn: sa.Connection) -> tuple[dt.date, dt.date] | None:
    """The span the balance data actually covers, asked only when a period came
    back empty — so the answer can be "no data, and here is where the data is"
    rather than a bare nothing the model will round to "no change"."""
    row = conn.execute(sa.text("select min(as_of), max(as_of) from balance_snapshot")).one()
    return (row[0], row[1]) if row[0] is not None else None


def _account_labels() -> list[str]:
    """Labels only. There are no account numbers in this database to leak
    (CLAUDE.md, rule 4) — a label is the whole identity of an account."""
    with readonly_connection() as conn:
        return [r.label for r in conn.execute(sa.text("select label from account order by label"))]


#: Tally's tools. Scoped per agent rather than exposing every tool to every
#: agent (rule 11): Forge's lift progression is not in this list, and the cost
#: of its schema is therefore not paid on a finance turn.
TALLY_TOOLS: list[BaseTool] = [net_worth_trend, balance_history, allocation]


def schema_cost(tools: list[BaseTool]) -> int:
    """A rough token estimate of what these schemas cost before the first message.

    Deliberately crude — four characters to a token, over the JSON the schemas
    serialise to. Rule 11 asks for the total to be logged, and a number that is
    consistently measured the same way is worth more here than a precise one,
    because what matters is whether it moved when a tool was added.
    """
    payload = json.dumps(
        [{"name": t.name, "description": t.description, "schema": t.args} for t in tools],
        default=str,
    )
    return len(payload) // 4


def _render_progression(result: LiftProgression) -> str:
    if not result.sessions:
        return (
            f"NO DATA: no loaded sets of {result.exercise} recorded between "
            f"{result.start:{ISO}} and {result.end:{ISO}}. This is missing data, "
            f"not a stalled lift."
        )
    unit = result.unit or ""
    lines = [
        f"{result.exercise} {result.start:{ISO}} to {result.end:{ISO}} "
        f"(heaviest working set per session)",
        *(
            f"  {s.performed_on:{ISO}}  {s.best_weight}{unit} x{s.reps_at_best}  "
            f"est. 1RM {s.estimated_1rm}{unit}"
            for s in result.sessions
        ),
    ]
    if result.change is not None:
        lines.append(f"change over the period: {result.change:+}{unit}")
    lines.append("note: estimated 1RM is Epley, an estimate, not a tested max")
    return "\n".join(lines)


def _render_metric(result: MetricTrend) -> str:
    if not result.points:
        return (
            f"NO DATA: no {result.metric} recorded between {result.start:{ISO}} "
            f"and {result.end:{ISO}}. This is missing data, not an unchanged value."
        )
    unit = result.unit or ""
    lines = [
        f"{result.metric} {result.start:{ISO}} to {result.end:{ISO}}",
        *(f"  {p.as_of:{ISO}}  {p.value}{unit}" for p in result.points),
    ]
    if result.change is not None:
        lines.append(f"change over the period: {result.change:+}{unit}")
    return "\n".join(lines)


@tool
def lift_progression(exercise: str, start: str, end: str) -> str:
    """Heaviest working set per session for ONE named lift, with estimated 1RM.

    Use for: strength progress on a specific exercise, "how is my squat going",
    personal bests. Bodyweight movements carry no load and will not appear.
    Dates are ISO yyyy-mm-dd; all three arguments are required.
    """
    exercise = _as_logged(exercise)
    with readonly_connection() as conn:
        try:
            return _render_progression(
                get_lift_progression(
                    conn, exercise, dt.date.fromisoformat(start), dt.date.fromisoformat(end)
                )
            )
        except UnknownExerciseError:
            return _unknown_name("lift", exercise, _exercise_names())


@tool
def body_metric_trend(metric: str, start: str, end: str) -> str:
    """One recorded body measurement over a period, e.g. metric="body_mass".

    Use for: body mass or another tracked measurement over time. Reports the
    values exactly as recorded. Dates are ISO yyyy-mm-dd; all three required.
    """
    with readonly_connection() as conn:
        try:
            return _render_metric(
                get_body_metric_trend(
                    conn, metric, dt.date.fromisoformat(start), dt.date.fromisoformat(end)
                )
            )
        except UnknownMetricError:
            return _unknown_name("measurement", metric, _metric_names())


def _unknown_name(kind: str, asked: str, known: list[str]) -> str:
    """A name that was never logged, written as the finished sentence the agent
    repeats — the mechanism `Coverage.caveat()` uses, for the same reason.

    "No lift called 'squat' has ever been logged. Logged lifts: back squat, ..."
    was the wording before, and the Model log caught the answer it produced:
    "I have no sessions logged for that period." A name nobody logged and a
    period with no sessions are different things, and the second sends someone
    looking for missing data that was never missing. Handing over a sentence to
    say leaves nothing to compose.
    """
    names = ", ".join(known[:-1]) + f" and {known[-1]}" if len(known) > 1 else "".join(known)
    have = f"the {kind}s in your log are {names}" if known else f"your log has no {kind}s at all"
    # The names go in the first sentence. Handed them in a second one, the model
    # repeated the first and stopped, and the answer never said what was logged
    # — which is the half that helps.
    return (
        f"caveat: You have no {kind} called {asked!r} logged — {have} — so this is "
        f"a name that was never logged, not a period without records."
    )


def _spelling(name: str) -> str:
    return " ".join(name.replace("_", " ").replace("-", " ").lower().split())


def _as_logged(exercise: str) -> str:
    """The logged name `exercise` means, when it differs only in how it is
    written: case, or underscores and hyphens for spaces.

    The model writes "bench_press" — `body_metric_trend` takes "body_mass", and
    it generalises — and every such call used to cost a step on "never logged"
    before a retry. Measured on a follow-up: three and four calls where one
    would do, and one turn ran out of steps. This is spelling, not synonyms:
    "bench" is not "bench press", and is still refused with the logged names.
    """
    wanted = _spelling(exercise)
    matches = [name for name in _exercise_names() if _spelling(name) == wanted]
    return matches[0] if len(matches) == 1 else exercise


def _exercise_names() -> list[str]:
    with readonly_connection() as conn:
        return [
            r.exercise
            for r in conn.execute(
                sa.text("select distinct exercise from workout_set order by exercise")
            )
        ]


def _metric_names() -> list[str]:
    with readonly_connection() as conn:
        return [
            r.metric
            for r in conn.execute(
                sa.text("select distinct metric from body_metric order by metric")
            )
        ]


#: Forge's tools. Tally's three are not here, and vice versa (rule 11): a
#: fitness turn does not pay for net worth schemas it will never call.
FORGE_TOOLS: list[BaseTool] = [lift_progression, body_metric_trend]
