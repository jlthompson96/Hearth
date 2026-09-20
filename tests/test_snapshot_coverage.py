"""The coverage view, and the determinism the evals depend on.

Backfilled history is uneven. Summing balances per date without accounting for
that makes net worth appear to leap on the day an account's data begins — an
artifact of coverage, not something that happened. These tests pin down the
distinction the view exists to draw: a date before an account opened is not a
gap, and a date an export skipped is.
"""

import datetime as dt
from decimal import Decimal

import sqlalchemy as sa

from scripts.seed import YEAR


def _coverage(conn: sa.Connection) -> list[tuple[dt.date, int, int, bool]]:
    return [
        (as_of, int(expected), int(present), bool(complete))
        for as_of, expected, present, complete in conn.execute(
            sa.text(
                "select as_of, accounts_expected, accounts_present, is_complete "
                "from snapshot_coverage order by as_of"
            )
        ).all()
    ]


def test_months_before_an_account_opened_are_not_gaps(seeded: sa.Connection) -> None:
    """The brokerage account opens in March. January is not missing it."""
    coverage = {row[0]: row for row in _coverage(seeded)}

    assert coverage[dt.date(YEAR, 1, 31)][1] == 4
    assert coverage[dt.date(YEAR, 1, 31)][3] is True
    assert coverage[dt.date(YEAR, 3, 31)][1] == 5


def test_a_month_an_export_skipped_is_a_gap(seeded: sa.Connection) -> None:
    incomplete = [row[0] for row in _coverage(seeded) if not row[3]]
    assert incomplete == [dt.date(YEAR, 7, 31)]


def test_every_other_month_is_complete(seeded: sa.Connection) -> None:
    coverage = _coverage(seeded)
    assert len(coverage) == 12
    assert sum(1 for row in coverage if row[3]) == 11


def test_seeded_figures_are_exact(seeded: sa.Connection) -> None:
    """The evals assert that a figure appears verbatim in an answer, so the
    fixture has to produce the same number every run, on every machine."""
    total = seeded.execute(
        sa.text("select sum(balance) from balance_snapshot where as_of = :as_of"),
        {"as_of": dt.date(YEAR, 12, 31)},
    ).scalar_one()

    # 4750.00 checking + 17750.00 savings + 91900.00 retirement
    # + 33900.00 brokerage - 1250.00 credit card
    assert total == Decimal("147050.00")


def test_seed_generates_stable_identifiers(seeded: sa.Connection) -> None:
    """Primary keys are derived with uuid5, not randomly, so a fixture row can
    be referred to by id from an eval case."""
    first = seeded.execute(sa.text("select id from account where label = 'Brokerage'")).scalar_one()
    again = seeded.execute(sa.text("select id from account where label = 'Brokerage'")).scalar_one()

    assert first == again
    assert str(first) == "d7a7f2e9-38d4-5930-8db8-482d715bd21e"
