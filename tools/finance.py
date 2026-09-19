"""Finance query and compute functions.

Every number an agent reports comes from here. The model picks which function
to call and with what arguments; it never writes SQL and never does arithmetic.
That is not a stylistic preference — an 8B model at Q4 will produce a confident
sum that is wrong by a digit, and a personal finance assistant that does this
once is worthless thereafter.

Two consequences shape the code:

  - Results are computed, compact and already rounded. Prompts receive these
    objects, never raw rows. Retrieved context competes with conversation
    history for 8,192 tokens, so a function that returns four hundred balances
    has broken the turn regardless of whether its arithmetic was right.

  - Coverage travels with the trend it qualifies, and `Coverage.caveat` writes
    the sentence out in full. Leaving the model to compose "your data only
    covers part of this period" from a list of dates is exactly the kind of
    task it fails quietly.

`as_of`, `start` and `end` are always explicit required arguments. None of them
defaults to today: a question about "this year" has to be turned into dates by
the caller, where the assumption is visible.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

CENTS = Decimal("0.01")
PERCENT = Decimal("0.1")


class UnknownAccountError(LookupError):
    """Raised rather than returning an empty history for a label that does not
    exist — an empty result reads as "no movement", which is a different and
    much more misleading answer."""


@dataclass(frozen=True)
class BalancePoint:
    as_of: dt.date
    balance: Decimal


@dataclass(frozen=True)
class Coverage:
    """How much of a period the underlying data actually covers.

    Backfilled history is uneven across accounts. Naive summing makes net worth
    appear to jump on the day an account's data begins, which is an artifact of
    the import rather than anything that happened.
    """

    complete_from: dt.date | None
    incomplete_dates: tuple[dt.date, ...]

    @property
    def is_complete(self) -> bool:
        return not self.incomplete_dates

    def caveat(self) -> str | None:
        """The sentence an agent must state before describing the trend."""
        if self.is_complete:
            return None
        count = len(self.incomplete_dates)
        dates = ", ".join(d.isoformat() for d in self.incomplete_dates[:3])
        if count > 3:
            dates += f", and {count - 3} more"
        if self.complete_from is None:
            return (
                f"No date in this period has data for every account "
                f"({count} incomplete: {dates}). Treat the totals as partial."
            )
        return (
            f"{count} date(s) in this period are missing data for at least one "
            f"account ({dates}). Figures are complete only from "
            f"{self.complete_from.isoformat()} onward."
        )


@dataclass(frozen=True)
class BalanceHistory:
    account_label: str
    currency: str
    start: dt.date
    end: dt.date
    points: tuple[BalancePoint, ...]
    change: Decimal | None


@dataclass(frozen=True)
class NetWorthTrend:
    start: dt.date
    end: dt.date
    points: tuple[BalancePoint, ...]
    change: Decimal | None
    coverage: Coverage


@dataclass(frozen=True)
class AllocationSlice:
    symbol: str
    market_value: Decimal
    percentage: Decimal


@dataclass(frozen=True)
class Allocation:
    as_of_requested: dt.date
    as_of_used: dt.date | None
    total: Decimal
    slices: tuple[AllocationSlice, ...]


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS)


def _change(points: tuple[BalancePoint, ...]) -> Decimal | None:
    """None rather than zero for a single point: one observation is not a
    change of nothing, it is not enough to say."""
    if len(points) < 2:
        return None
    return _money(points[-1].balance - points[0].balance)


def get_balance_history(
    conn: sa.Connection, account_label: str, start: dt.date, end: dt.date
) -> BalanceHistory:
    """Balances for one account across a period, oldest first."""
    account = conn.execute(
        sa.text("select id, currency from account where label = :label"),
        {"label": account_label},
    ).one_or_none()
    if account is None:
        raise UnknownAccountError(account_label)

    rows = conn.execute(
        sa.text(
            "select as_of, balance from balance_snapshot "
            "where account_id = :account_id and as_of between :start and :end "
            "order by as_of"
        ),
        {"account_id": account.id, "start": start, "end": end},
    ).all()

    points = tuple(BalancePoint(as_of=r.as_of, balance=_money(r.balance)) for r in rows)
    return BalanceHistory(
        account_label=account_label,
        currency=account.currency,
        start=start,
        end=end,
        points=points,
        change=_change(points),
    )


def _coverage(conn: sa.Connection, start: dt.date, end: dt.date) -> Coverage:
    rows = conn.execute(
        sa.text(
            "select as_of, is_complete from snapshot_coverage "
            "where as_of between :start and :end order by as_of"
        ),
        {"start": start, "end": end},
    ).all()

    incomplete = tuple(r.as_of for r in rows if not r.is_complete)
    if not rows:
        return Coverage(complete_from=None, incomplete_dates=())
    if not incomplete:
        return Coverage(complete_from=rows[0].as_of, incomplete_dates=())

    # Complete "from" the first date after the last gap: everything at or after
    # it can be compared without caveat, and everything before it cannot.
    last_gap = incomplete[-1]
    after_gap = [r.as_of for r in rows if r.as_of > last_gap]
    return Coverage(
        complete_from=after_gap[0] if after_gap else None,
        incomplete_dates=incomplete,
    )


def get_net_worth_trend(conn: sa.Connection, start: dt.date, end: dt.date) -> NetWorthTrend:
    """Total across all accounts per snapshot date, with coverage.

    Liabilities are stored as negative balances, so this is a plain sum: no
    account kind gets special arithmetic hidden inside the query, and a new
    kind cannot silently change what net worth means.
    """
    rows = conn.execute(
        sa.text(
            "select as_of, sum(balance) as total from balance_snapshot "
            "where as_of between :start and :end group by as_of order by as_of"
        ),
        {"start": start, "end": end},
    ).all()

    points = tuple(BalancePoint(as_of=r.as_of, balance=_money(r.total)) for r in rows)
    return NetWorthTrend(
        start=start,
        end=end,
        points=points,
        change=_change(points),
        coverage=_coverage(conn, start, end),
    )


def get_allocation(conn: sa.Connection, as_of: dt.date) -> Allocation:
    """Holdings by symbol on the latest snapshot date at or before `as_of`.

    The date actually used is returned rather than assumed. Asking for today
    and silently answering with March is the sort of thing that makes a number
    untrustworthy without ever being wrong.
    """
    used = conn.execute(
        sa.text("select max(as_of) from holding_snapshot where as_of <= :as_of"),
        {"as_of": as_of},
    ).scalar()

    if used is None:
        return Allocation(as_of_requested=as_of, as_of_used=None, total=Decimal("0.00"), slices=())

    rows = conn.execute(
        sa.text(
            "select symbol, sum(market_value) as value from holding_snapshot "
            "where as_of = :used group by symbol order by sum(market_value) desc"
        ),
        {"used": used},
    ).all()

    total = _money(sum((r.value for r in rows), Decimal("0")))
    slices = tuple(
        AllocationSlice(
            symbol=r.symbol,
            market_value=_money(r.value),
            percentage=((r.value / total * 100).quantize(PERCENT) if total else Decimal("0.0")),
        )
        for r in rows
    )
    return Allocation(as_of_requested=as_of, as_of_used=used, total=total, slices=slices)
