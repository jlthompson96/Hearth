"""Importing an export: raw rows first, then snapshots, all or nothing.

The sequence, and why each step is where it is:

  1. Refuse a database holding the golden fixture. Real accounts summed with
     invented ones make a net worth that is neither.
  2. Hash the bytes. A file already imported is a no-op — before parsing, so a
     re-run cannot fail on anything the first run got past.
  3. Read the export (`ingest.export`): layout, structure and rule 4, all
     before a single write.
  4. Check the date given against the date in the filename, if it has one.
  5. In one savepoint: store the batch and its raw rows, then normalize *from
     the stored rows* — not from the file — and write the snapshots.

Step 5 reads back what it just wrote on purpose. The recovery path for a
normalizer bug found months later is `renormalize`, which has only `import_row`
to work from. Making the first import take the same road means that road is
exercised every time rather than trusted.

The savepoint is what makes "no partial writes" true when normalization fails
on row 5 after the raw rows are in. The caller owns the transaction; this owns
the atomicity of an import within it.
"""

import datetime as dt
import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

from db.models import Account, BalanceSnapshot, HoldingSnapshot, ImportBatch, ImportRow
from ingest.errors import Conflict, DateMismatch, FixtureLoaded, NotFound, UnknownAccounts
from ingest.export import normalizer_named, read_export
from ingest.normalizer import RawRow
from scripts.seed import FIXTURE_ACCOUNT_IDS


@dataclass(frozen=True)
class AccountImported:
    label: str
    balance: Decimal
    holdings: int


@dataclass(frozen=True)
class ImportResult:
    batch_id: uuid.UUID
    filename: str
    source_label: str
    as_of: dt.date
    rows: int
    already_imported: bool
    accounts: tuple[AccountImported, ...]


def fixture_loaded(conn: sa.Connection) -> bool:
    present: int = conn.execute(
        sa.select(sa.func.count()).where(Account.id.in_(FIXTURE_ACCOUNT_IDS))
    ).scalar_one()
    return present > 0


def refuse_if_fixture(conn: sa.Connection) -> None:
    if fixture_loaded(conn):
        raise FixtureLoaded(
            "This database holds the golden fixture — the invented accounts `make seed` "
            "loads. Real data does not go in beside it: a net worth summed across both is "
            "neither. Run `make unseed` to empty it, then try again. The evals keep their "
            "own copy and are unaffected."
        )


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def import_export(
    conn: sa.Connection, *, content: bytes, filename: str, as_of: dt.date
) -> ImportResult:
    """`as_of` is required and never defaulted: it is the date the export
    describes, which only the person holding it knows for certain."""
    refuse_if_fixture(conn)

    digest = file_sha256(content)
    existing = conn.execute(
        sa.select(ImportBatch.id, ImportBatch.as_of).where(ImportBatch.file_sha256 == digest)
    ).first()
    if existing is not None:
        if existing.as_of != as_of:
            raise Conflict(
                f"This exact file was already imported as of {existing.as_of:%Y-%m-%d}. "
                f"Importing it again as of {as_of:%Y-%m-%d} would record the same "
                "figures under a second date."
            )
        return _result(conn, existing.id, already_imported=True)

    export = read_export(content, filename)

    stated = export.normalizer.date_in_filename(filename)
    if stated is not None and stated != as_of:
        raise DateMismatch(
            f"The filename says {stated:%Y-%m-%d}; the import was asked to record "
            f"{as_of:%Y-%m-%d}. One of them is wrong, and it is not for the importer "
            "to decide which."
        )

    with conn.begin_nested():
        batch_id = conn.execute(
            sa.insert(ImportBatch)
            .values(
                file_sha256=digest,
                original_filename=filename,
                source_label=export.normalizer.source_label,
                normalizer=export.normalizer.name,
                status="raw",
                as_of=as_of,
                row_count=len(export.rows),
            )
            .returning(ImportBatch.id)
        ).scalar_one()
        conn.execute(
            sa.insert(ImportRow),
            [
                {"batch_id": batch_id, "row_number": row.number, "raw": dict(row.cells)}
                for row in export.rows
            ],
        )
        _normalize(conn, batch_id)

    return _result(conn, batch_id, already_imported=False)


def renormalize(conn: sa.Connection, batch_id: uuid.UUID) -> ImportResult:
    """Rebuild a batch's snapshots from its raw rows alone — the recovery path
    for a normalizer bug, which needs no file and no re-download."""
    if conn.execute(sa.select(ImportBatch.id).where(ImportBatch.id == batch_id)).first() is None:
        raise NotFound(f"no import batch {batch_id}")

    with conn.begin_nested():
        conn.execute(sa.delete(HoldingSnapshot).where(HoldingSnapshot.batch_id == batch_id))
        conn.execute(sa.delete(BalanceSnapshot).where(BalanceSnapshot.batch_id == batch_id))
        _normalize(conn, batch_id)
    return _result(conn, batch_id, already_imported=False)


def remove_import(conn: sa.Connection, batch_id: uuid.UUID) -> None:
    """Take an import back out: its snapshots, its raw rows and the batch.

    The snapshots are deleted explicitly. Their foreign key is ON DELETE SET
    NULL, so deleting the batch alone would leave them behind looking exactly
    like figures entered by hand.
    """
    with conn.begin_nested():
        conn.execute(sa.delete(HoldingSnapshot).where(HoldingSnapshot.batch_id == batch_id))
        conn.execute(sa.delete(BalanceSnapshot).where(BalanceSnapshot.batch_id == batch_id))
        deleted = conn.execute(sa.delete(ImportBatch).where(ImportBatch.id == batch_id))
        if deleted.rowcount == 0:
            raise NotFound(f"no import batch {batch_id}")


def _normalize(conn: sa.Connection, batch_id: uuid.UUID) -> None:
    batch = conn.execute(
        sa.select(ImportBatch.normalizer, ImportBatch.as_of).where(ImportBatch.id == batch_id)
    ).one()
    rows = [
        RawRow(number=number, cells=raw)
        for number, raw in conn.execute(
            sa.select(ImportRow.row_number, ImportRow.raw)
            .where(ImportRow.batch_id == batch_id)
            .order_by(ImportRow.row_number)
        )
    ]
    normalized = normalizer_named(batch.normalizer).normalize(rows)

    accounts = _account_ids(conn, normalized.balances)
    _refuse_conflicts(conn, accounts, batch.as_of, batch_id)

    conn.execute(
        sa.insert(HoldingSnapshot),
        [
            {
                "account_id": accounts[h.account],
                "as_of": batch.as_of,
                "symbol": h.symbol,
                "quantity": h.quantity,
                "price": h.price,
                "market_value": h.market_value,
                "batch_id": batch_id,
            }
            for h in normalized.holdings
        ],
    )
    conn.execute(
        sa.insert(BalanceSnapshot),
        [
            {
                "account_id": accounts[name],
                "as_of": batch.as_of,
                "balance": balance,
                "batch_id": batch_id,
            }
            for name, balance in normalized.balances.items()
        ],
    )
    conn.execute(
        sa.update(ImportBatch).where(ImportBatch.id == batch_id).values(status="normalized")
    )


def _account_ids(conn: sa.Connection, names: Iterable[str]) -> dict[str, uuid.UUID]:
    wanted = list(names)
    found: dict[str, uuid.UUID] = {
        label: account_id
        for label, account_id in conn.execute(
            sa.select(Account.label, Account.id).where(Account.label.in_(wanted))
        )
    }
    missing = [name for name in wanted if name not in found]
    if missing:
        known = conn.execute(sa.select(Account.label).order_by(Account.label)).scalars().all()
        raise UnknownAccounts(
            f"The export names accounts Hearth has no label for: {missing}. Create each "
            "one under Manual entry with exactly that label, then import again — accounts "
            "are never created by an import, because the export does not say what kind "
            f"each one is. Labels Hearth has: {list(known) or 'none yet'}."
        )
    return found


def _refuse_conflicts(
    conn: sa.Connection, accounts: dict[str, uuid.UUID], as_of: dt.date, batch_id: uuid.UUID
) -> None:
    """A figure already recorded for that account on that day is never
    overwritten by an import. Which one is right is not the importer's call."""
    clashes = conn.execute(
        sa.select(Account.label, ImportBatch.original_filename)
        .select_from(BalanceSnapshot)
        .join(Account, Account.id == BalanceSnapshot.account_id)
        .outerjoin(ImportBatch, ImportBatch.id == BalanceSnapshot.batch_id)
        .where(
            BalanceSnapshot.account_id.in_(accounts.values()),
            BalanceSnapshot.as_of == as_of,
            BalanceSnapshot.batch_id.is_distinct_from(batch_id),
        )
        .union(
            sa.select(Account.label, ImportBatch.original_filename)
            .select_from(HoldingSnapshot)
            .join(Account, Account.id == HoldingSnapshot.account_id)
            .outerjoin(ImportBatch, ImportBatch.id == HoldingSnapshot.batch_id)
            .where(
                HoldingSnapshot.account_id.in_(accounts.values()),
                HoldingSnapshot.as_of == as_of,
                HoldingSnapshot.batch_id.is_distinct_from(batch_id),
            )
        )
    ).all()
    if clashes:
        where = "; ".join(
            f"{label}, {f'from {source}' if source else 'entered by hand'}"
            for label, source in sorted(clashes)
        )
        raise Conflict(
            f"Figures for {as_of:%Y-%m-%d} are already recorded ({where}). An import never "
            "overwrites what is there. Remove the earlier one first if this file should "
            "replace it."
        )


def _result(conn: sa.Connection, batch_id: uuid.UUID, *, already_imported: bool) -> ImportResult:
    batch = conn.execute(
        sa.select(
            ImportBatch.original_filename,
            ImportBatch.source_label,
            ImportBatch.as_of,
            ImportBatch.row_count,
        ).where(ImportBatch.id == batch_id)
    ).one()
    holdings = (
        sa.select(sa.func.count())
        .where(
            HoldingSnapshot.batch_id == batch_id,
            HoldingSnapshot.account_id == BalanceSnapshot.account_id,
        )
        .scalar_subquery()
    )
    accounts = conn.execute(
        sa.select(Account.label, BalanceSnapshot.balance, holdings)
        .join(Account, Account.id == BalanceSnapshot.account_id)
        .where(BalanceSnapshot.batch_id == batch_id)
        .order_by(Account.label)
    ).all()
    return ImportResult(
        batch_id=batch_id,
        filename=batch.original_filename,
        source_label=batch.source_label,
        as_of=batch.as_of,
        rows=batch.row_count,
        already_imported=already_imported,
        accounts=tuple(
            AccountImported(label, balance, count) for label, balance, count in accounts
        ),
    )
