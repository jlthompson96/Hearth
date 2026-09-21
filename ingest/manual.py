"""Manual entry: accounts, and balances typed in by hand.

The other way data enters (rule 3): no connection to anything, just a person
typing a figure they can see. It keeps the promises an import keeps — a label is
screened for anything resembling an account number, a figure already recorded
is never overwritten, and nothing goes in beside the golden fixture.

Accounts are only ever created here. An import names accounts but cannot say
what kind each one is, and the label is the person's to choose.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

from db.models import Account, BalanceSnapshot, ImportBatch
from ingest.errors import AccountNumberRefused, Conflict, NotFound, Refused, UnknownAccounts
from ingest.importer import refuse_if_fixture
from ingest.values import looks_like_account_number

#: Mirrors the `kind_known` check constraint on `account`.
KINDS = ("checking", "savings", "brokerage", "retirement", "credit", "loan", "cash", "other")


@dataclass(frozen=True)
class AccountView:
    id: uuid.UUID
    label: str
    kind: str
    currency: str
    opened_on: dt.date | None
    closed_on: dt.date | None
    latest_as_of: dt.date | None
    latest_balance: Decimal | None


@dataclass(frozen=True)
class Entry:
    id: int
    label: str
    as_of: dt.date
    balance: Decimal


def create_account(
    conn: sa.Connection, *, label: str, kind: str, opened_on: dt.date | None = None
) -> AccountView:
    refuse_if_fixture(conn)

    label = label.strip()
    if not label:
        raise Refused("An account needs a label.")
    if looks_like_account_number(label):
        raise AccountNumberRefused(
            "That label has four digits in a row, which is how an account number or its "
            "last four looks. Hearth never stores one, masked or not (rule 4). Choose a "
            "label without them."
        )
    if kind not in KINDS:
        raise Refused(f"Unknown kind {kind!r}. It must be one of {list(KINDS)}.")
    if conn.execute(sa.select(Account.id).where(Account.label == label)).first():
        raise Conflict(f"There is already an account labelled {label!r}.")

    conn.execute(sa.insert(Account).values(label=label, kind=kind, opened_on=opened_on))
    return next(a for a in accounts(conn) if a.label == label)


def record_balance(conn: sa.Connection, *, label: str, as_of: dt.date, balance: Decimal) -> Entry:
    """`as_of` is required and never defaulted. A liability is entered as a
    negative balance, which is how net worth stays a plain sum."""
    refuse_if_fixture(conn)

    account = conn.execute(
        sa.select(Account.id, Account.opened_on, Account.closed_on).where(Account.label == label)
    ).first()
    if account is None:
        raise UnknownAccounts(f"No account is labelled {label!r}. Create it first.")
    if account.opened_on and as_of < account.opened_on:
        raise Refused(
            f"{label} was opened on {account.opened_on:%Y-%m-%d}, after {as_of:%Y-%m-%d}. "
            "A balance from before it existed would read as missing data that never was."
        )
    if account.closed_on and as_of > account.closed_on:
        raise Refused(f"{label} was closed on {account.closed_on:%Y-%m-%d}.")

    existing = conn.execute(
        sa.select(ImportBatch.original_filename)
        .select_from(BalanceSnapshot)
        .outerjoin(ImportBatch, ImportBatch.id == BalanceSnapshot.batch_id)
        .where(BalanceSnapshot.account_id == account.id, BalanceSnapshot.as_of == as_of)
    ).first()
    if existing is not None:
        source = f"imported from {existing[0]}" if existing[0] else "entered by hand"
        raise Conflict(
            f"{label} already has a balance for {as_of:%Y-%m-%d}, {source}. A recorded "
            "figure is never overwritten; remove that one first if this replaces it."
        )

    entry_id = conn.execute(
        sa.insert(BalanceSnapshot)
        .values(account_id=account.id, as_of=as_of, balance=balance)
        .returning(BalanceSnapshot.id)
    ).scalar_one()
    return Entry(id=entry_id, label=label, as_of=as_of, balance=balance)


def remove_balance(conn: sa.Connection, entry_id: int) -> None:
    """Only a balance entered by hand. An imported one goes with its import:
    taking one account out of a batch leaves the rest describing a day that no
    longer adds up."""
    row = conn.execute(
        sa.select(BalanceSnapshot.batch_id).where(BalanceSnapshot.id == entry_id)
    ).first()
    if row is None:
        raise NotFound(f"no balance {entry_id}")
    if row.batch_id is not None:
        raise Refused(
            "That balance came from an import. Remove the import instead, under Data & imports."
        )
    conn.execute(sa.delete(BalanceSnapshot).where(BalanceSnapshot.id == entry_id))


def accounts(conn: sa.Connection) -> list[AccountView]:
    latest = (
        sa.select(BalanceSnapshot.as_of, BalanceSnapshot.balance)
        .where(BalanceSnapshot.account_id == Account.id)
        .order_by(BalanceSnapshot.as_of.desc())
        .limit(1)
        .lateral()
    )
    rows = conn.execute(
        sa.select(
            Account.id,
            Account.label,
            Account.kind,
            Account.currency,
            Account.opened_on,
            Account.closed_on,
            latest.c.as_of,
            latest.c.balance,
        )
        .outerjoin(latest, sa.true())
        .order_by(Account.label)
    ).all()
    return [AccountView(*row) for row in rows]


def recent_entries(conn: sa.Connection, limit: int = 10) -> list[Entry]:
    rows = conn.execute(
        sa.select(BalanceSnapshot.id, Account.label, BalanceSnapshot.as_of, BalanceSnapshot.balance)
        .join(Account, Account.id == BalanceSnapshot.account_id)
        .where(BalanceSnapshot.batch_id.is_(None))
        .order_by(BalanceSnapshot.created_at.desc(), BalanceSnapshot.id.desc())
        .limit(limit)
    ).all()
    return [Entry(*row) for row in rows]
