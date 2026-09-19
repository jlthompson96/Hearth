"""snapshot coverage view

Backfilled history is uneven across accounts: one export reaches back six years,
another starts last spring. Summing balances per date without accounting for
that makes net worth appear to leap on the day an account's data begins, which
is an artifact of coverage rather than anything that happened.

This view is what lets the trend tools report `complete_from` and
`incomplete_dates` instead of a confident wrong number. An agent describing a
trend must state the gap first.
"""

from alembic import op

revision = "fb1ff5eda05b"
down_revision = "db1becaa84fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW snapshot_coverage AS
        WITH observed AS (
            SELECT DISTINCT as_of FROM balance_snapshot
        ),
        expected AS (
            -- An account cannot be missing data before it was opened or after
            -- it was closed, so coverage is measured against the accounts that
            -- actually existed on each date.
            SELECT o.as_of, count(a.id) AS accounts_expected
            FROM observed o
            LEFT JOIN account a
              ON (a.opened_on IS NULL OR a.opened_on <= o.as_of)
             AND (a.closed_on IS NULL OR a.closed_on > o.as_of)
            GROUP BY o.as_of
        ),
        present AS (
            SELECT as_of, count(DISTINCT account_id) AS accounts_present
            FROM balance_snapshot
            GROUP BY as_of
        )
        SELECT
            e.as_of,
            e.accounts_expected,
            p.accounts_present,
            (p.accounts_present >= e.accounts_expected) AS is_complete
        FROM expected e
        JOIN present p USING (as_of)
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS snapshot_coverage")
