"""Manual entry: accounts, and balances typed in by hand.

A balance arrives as text — "$1,234.56", "-950.50" — and is read by the same
strict parser an import uses, so the two doors into the database agree on what
a dollar amount looks like. It is never parsed as a float on the way.
"""

import datetime as dt
import uuid
from decimal import Decimal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from api.refusals import REFUSALS
from db.session import readonly_connection
from db.writer import writer_connection
from ingest import manual
from ingest.errors import Refused
from ingest.importer import fixture_loaded
from ingest.values import ValueFormatError, parse_money

router = APIRouter(prefix="/api", tags=["manual entry"])


class Account(BaseModel):
    id: uuid.UUID
    label: str
    kind: str
    currency: str
    opened_on: dt.date | None
    closed_on: dt.date | None
    latest_as_of: dt.date | None
    latest_balance: Decimal | None


class Entry(BaseModel):
    id: int
    label: str
    as_of: dt.date
    balance: Decimal


class AccountListing(BaseModel):
    fixture_loaded: bool
    kinds: list[str]
    accounts: list[Account]
    #: The last few balances entered by hand, so a slip can be seen and removed.
    recent: list[Entry]


class NewAccount(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    kind: str
    opened_on: dt.date | None = None


class NewBalance(BaseModel):
    account: str = Field(min_length=1)
    #: Required, with no default (CLAUDE.md: `as_of` never defaults to today).
    as_of: dt.date
    #: US currency as typed: "$1,234.56", "-950.50", "(12.00)".
    balance: str = Field(min_length=1, max_length=32)


@router.get("/accounts", response_model=AccountListing, summary="Accounts and recent entries")
def list_accounts() -> AccountListing:
    with readonly_connection() as conn:
        return AccountListing(
            fixture_loaded=fixture_loaded(conn),
            kinds=list(manual.KINDS),
            accounts=[Account(**vars(a)) for a in manual.accounts(conn)],
            recent=[Entry(**vars(e)) for e in manual.recent_entries(conn)],
        )


@router.post(
    "/accounts",
    response_model=Account,
    status_code=201,
    responses=REFUSALS,
    summary="Create an account",
)
def create_account(request: NewAccount) -> Account:
    with writer_connection() as conn:
        created = manual.create_account(
            conn, label=request.label, kind=request.kind, opened_on=request.opened_on
        )
    return Account(**vars(created))


@router.post(
    "/balances",
    response_model=Entry,
    status_code=201,
    responses=REFUSALS,
    summary="Record a balance by hand",
)
def record_balance(request: NewBalance) -> Entry:
    try:
        amount = parse_money(request.balance)
    except ValueFormatError as error:
        raise Refused(f"Balance: {error}.") from None
    with writer_connection() as conn:
        entry = manual.record_balance(
            conn, label=request.account, as_of=request.as_of, balance=amount
        )
    return Entry(**vars(entry))


@router.delete(
    "/balances/{entry_id}",
    status_code=204,
    responses=REFUSALS,
    summary="Remove a balance entered by hand",
)
def delete_balance(entry_id: int) -> Response:
    with writer_connection() as conn:
        manual.remove_balance(conn, entry_id)
    return Response(status_code=204)
