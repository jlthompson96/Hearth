"""Load the golden fixture dataset.

Every figure here is invented. Nothing in this file came from a real export,
and nothing in it should ever be replaced with something that did.

The evals reuse this dataset, which makes determinism a requirement rather than
a nicety: a case that asserts "$104,200.00 appears verbatim in the answer" is
only meaningful if the same seed always produces the same balance. Identifiers
are derived with uuid5 from a fixed namespace, so even the primary keys are
stable across runs and machines.

The shape of the data is deliberate. Coverage is uneven — the brokerage account
opens partway through the year, and one month of retirement data is missing, as
though an export skipped it. A fixture where every account has every month
would let a net worth trend look correct while the coverage handling underneath
it was entirely broken.

    make seed
"""

import argparse
import datetime as dt
import sys
import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from config import get_settings
from db.models import (
    Account,
    BalanceSnapshot,
    BodyMetric,
    HoldingSnapshot,
    Message,
    Thread,
    Workout,
    WorkoutSet,
)

#: Fixed so that generated ids are identical on every machine and every run.
NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

YEAR = 2024
MONTH_ENDS = [
    dt.date(YEAR, month, day)
    for month, day in [
        (1, 31),
        (2, 29),
        (3, 31),
        (4, 30),
        (5, 31),
        (6, 30),
        (7, 31),
        (8, 31),
        (9, 30),
        (10, 31),
        (11, 30),
        (12, 31),
    ]
]

#: label -> (kind, opened_on, opening balance, monthly change)
ACCOUNTS: dict[str, tuple[str, dt.date, str, str]] = {
    "Everyday Checking": ("checking", dt.date(2023, 1, 1), "4200.00", "50.00"),
    "Emergency Savings": ("savings", dt.date(2023, 1, 1), "15000.00", "250.00"),
    "Retirement": ("retirement", dt.date(2023, 1, 1), "82000.00", "900.00"),
    # A liability, stored negative. Net worth is then a plain sum with no
    # kind-dependent sign juggling hidden inside a query.
    "Credit Card": ("credit", dt.date(2023, 1, 1), "-1800.00", "50.00"),
    # Opens in March: before that it is not missing data, it did not exist.
    "Brokerage": ("brokerage", dt.date(YEAR, 3, 1), "24000.00", "1100.00"),
}

#: An export that skipped a month. Coverage must notice.
MISSING = {("Retirement", dt.date(YEAR, 7, 31))}

HOLDINGS = [("VTI", "120.00000000", "230.00"), ("BND", "300.00000000", "72.00")]

LIFTS = {"back squat": ("100.000", "2.500"), "bench press": ("70.000", "1.250")}


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{kind}:{key}")


def seed(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}

    accounts: dict[str, Account] = {}
    for label, (kind, opened_on, _, _) in ACCOUNTS.items():
        account = Account(
            id=_id("account", label),
            label=label,
            kind=kind,
            currency="USD",
            opened_on=opened_on,
        )
        session.add(account)
        accounts[label] = account
    counts["account"] = len(accounts)

    balances = 0
    for label, (_, opened_on, opening, monthly) in ACCOUNTS.items():
        for index, as_of in enumerate(MONTH_ENDS):
            if as_of < opened_on or (label, as_of) in MISSING:
                continue
            elapsed = index - next(i for i, d in enumerate(MONTH_ENDS) if d >= opened_on)
            session.add(
                BalanceSnapshot(
                    account_id=accounts[label].id,
                    as_of=as_of,
                    balance=Decimal(opening) + Decimal(monthly) * elapsed,
                )
            )
            balances += 1
    counts["balance_snapshot"] = balances

    holdings = 0
    for as_of in MONTH_ENDS:
        if as_of < ACCOUNTS["Brokerage"][1]:
            continue
        for symbol, quantity, price in HOLDINGS:
            session.add(
                HoldingSnapshot(
                    account_id=accounts["Brokerage"].id,
                    as_of=as_of,
                    symbol=symbol,
                    quantity=Decimal(quantity),
                    price=Decimal(price),
                    market_value=Decimal(quantity) * Decimal(price),
                )
            )
            holdings += 1
    counts["holding_snapshot"] = holdings

    for index, as_of in enumerate(MONTH_ENDS):
        session.add(
            BodyMetric(
                as_of=as_of,
                metric="body_mass",
                value=Decimal("82.500") - Decimal("0.400") * index,
                unit="kg",
            )
        )
    counts["body_metric"] = len(MONTH_ENDS)

    sets = 0
    for index, as_of in enumerate(MONTH_ENDS):
        workout = Workout(
            id=_id("workout", as_of.isoformat()),
            performed_on=as_of,
            kind="strength",
            duration_minutes=Decimal("62.00"),
        )
        session.add(workout)
        for exercise, (start, step) in LIFTS.items():
            for set_number in (1, 2, 3):
                session.add(
                    WorkoutSet(
                        workout_id=workout.id,
                        exercise=exercise,
                        set_number=set_number,
                        reps=5,
                        weight=Decimal(start) + Decimal(step) * index,
                        weight_unit="kg",
                    )
                )
                sets += 1

        # Bodyweight: no load recorded, which the progression tool must skip
        # rather than read as zero.
        for set_number in (1, 2, 3):
            session.add(
                WorkoutSet(
                    workout_id=workout.id,
                    exercise="pull-up",
                    set_number=set_number,
                    reps=6 + index // 4,
                    weight=None,
                    weight_unit=None,
                )
            )
            sets += 1
    counts["workout"] = len(MONTH_ENDS)
    counts["workout_set"] = sets

    thread = Thread(id=_id("thread", "golden"), title="Net worth check")
    session.add(thread)
    for role, content in [
        ("user", "how has my net worth moved this year"),
        ("assistant", "Coverage is incomplete before March 2024."),
    ]:
        session.add(Message(thread_id=thread.id, role=role, content=content))
    counts["thread"] = 1
    counts["message"] = 2

    return counts


TABLES = [
    "workout_set",
    "workout",
    "body_metric",
    "holding_snapshot",
    "balance_snapshot",
    "account",
    "message",
    "thread",
    "search_audit",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="seed even though imported data is present (it will be deleted)",
    )
    args = parser.parse_args(argv)

    engine = sa.create_engine(get_settings().database_url)

    with Session(engine) as session:
        imported = session.execute(sa.text("select count(*) from import_batch")).scalar_one()
        if imported and not args.force:
            print(
                f"Refusing to seed: {imported} import batch(es) present. This "
                "database holds imported data, and seeding deletes it. Re-run "
                "with --force if that is genuinely what you want.",
                file=sys.stderr,
            )
            return 1

        session.execute(sa.text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        counts = seed(session)
        session.commit()

    for table, count in counts.items():
        print(f"  {count:>4}  {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
