"""The schema.

Snapshot modelling, not a transaction ledger. Transactions drag in merchant
categorisation, a never-finished project; a `transaction` table can be added
later without disturbing anything here.

Two rules are enforced structurally rather than by good intentions:

  - No account numbers exist. Not masked, not hashed, not last-four. There is
    no column for one, and there must never be. Accounts carry a label chosen
    by hand.
  - `as_of` is the date a fact was true and is always NOT NULL. It never
    defaults to today. `created_at` is when the row was written; the two are
    different questions and backfilled history separates them by years.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import (
    Base,
    duration,
    measurement,
    money,
    optional_measurement,
    price,
    quantity,
    timestamp,
    uuid_pk,
)

# --- ingestion ----------------------------------------------------------------


class ImportBatch(Base):
    """One CSV file, imported once.

    Idempotency lives in `file_sha256`: re-importing the same bytes is a no-op
    rather than a duplicate, so a failed evening can simply be repeated.
    """

    __tablename__ = "import_batch"

    id: Mapped[uuid_pk]
    file_sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    #: My label for where this came from. Not an institution's identifier.
    source_label: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which normalizer read it, so a bug six months out is attributable.
    normalizer: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'raw'"))
    #: The date the export describes. Stated at import, never inferred, and kept
    #: here so re-normalizing the raw rows later needs nothing but this table.
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[timestamp]

    rows: Mapped[list["ImportRow"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("status in ('raw', 'normalized', 'failed')", name="status_known"),
        CheckConstraint("row_count >= 0", name="row_count_non_negative"),
    )


class ImportRow(Base):
    """A raw CSV row, stored before normalization runs.

    This is the recovery path. A normalizer bug discovered six months from now
    must be fixable without re-downloading exports that may no longer be
    available, so the bytes are kept exactly as they arrived.
    """

    __tablename__ = "import_row"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_batch.id", ondelete="CASCADE"), nullable=False
    )
    #: 1-based, as a human reading the file would count.
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The row as read, keyed by its own header. Never reshaped on the way in.
    raw: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)

    batch: Mapped[ImportBatch] = relationship(back_populates="rows")

    __table_args__ = (
        UniqueConstraint("batch_id", "row_number"),
        CheckConstraint("row_number >= 1", name="row_number_positive"),
    )


# --- finance ------------------------------------------------------------------


class Account(Base):
    """An account, identified only by a label I chose.

    There is deliberately no account number, no mask, no last-four and no
    institution-issued identifier of any kind. If one is ever needed to tell two
    accounts apart, choose a better label.
    """

    __tablename__ = "account"

    id: Mapped[uuid_pk]
    label: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'USD'"))
    #: Bounds for coverage: an account cannot be missing data before it existed.
    opened_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    closed_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[timestamp]

    __table_args__ = (
        CheckConstraint(
            "kind in ('checking', 'savings', 'brokerage', 'retirement', "
            "'credit', 'loan', 'cash', 'other')",
            name="kind_known",
        ),
        CheckConstraint("length(label) > 0", name="label_not_empty"),
        CheckConstraint("length(currency) = 3", name="currency_is_iso_4217"),
        CheckConstraint(
            "closed_on is null or opened_on is null or closed_on >= opened_on",
            name="closed_after_opened",
        ),
    )


class BalanceSnapshot(Base):
    """What an account was worth on a given day."""

    __tablename__ = "balance_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False)
    balance: Mapped[money]
    #: Null means entered by hand rather than imported.
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batch.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[timestamp]

    __table_args__ = (
        UniqueConstraint("account_id", "as_of"),
        Index("ix_balance_snapshot_as_of", "as_of"),
    )


class HoldingSnapshot(Base):
    """A position held within an account on a given day.

    `market_value` is stored rather than derived from quantity and price:
    exports report it, and recomputing it here would be arithmetic performed in
    the wrong place. Quantity and price are nullable because not every line
    carries them — a cash line has a value and nothing else.
    """

    __tablename__ = "holding_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[quantity]
    price: Mapped[price]
    market_value: Mapped[money]
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batch.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[timestamp]

    __table_args__ = (
        UniqueConstraint("account_id", "as_of", "symbol"),
        CheckConstraint("length(symbol) > 0", name="symbol_not_empty"),
        Index("ix_holding_snapshot_as_of", "as_of"),
    )


# --- fitness ------------------------------------------------------------------


class BodyMetric(Base):
    """A measured body value on a given day.

    Key/value rather than a column per metric: the set of things worth tracking
    changes, and adding a metric should not require a migration.
    """

    __tablename__ = "body_metric"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[measurement]
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batch.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[timestamp]

    __table_args__ = (
        UniqueConstraint("as_of", "metric"),
        CheckConstraint("length(metric) > 0", name="metric_not_empty"),
        CheckConstraint("length(unit) > 0", name="unit_not_empty"),
    )


class Workout(Base):
    """One training session."""

    __tablename__ = "workout"

    id: Mapped[uuid_pk]
    performed_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    duration_minutes: Mapped[duration]
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batch.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[timestamp]

    sets: Mapped[list["WorkoutSet"]] = relationship(
        back_populates="workout", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "kind in ('strength', 'cardio', 'mobility', 'sport', 'other')",
            name="kind_known",
        ),
        CheckConstraint(
            "duration_minutes is null or duration_minutes > 0",
            name="duration_positive",
        ),
        Index("ix_workout_performed_on", "performed_on"),
    )


class WorkoutSet(Base):
    """One set within a session. Lift progression is computed from these."""

    __tablename__ = "workout_set"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workout_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workout.id", ondelete="CASCADE"), nullable=False
    )
    exercise: Mapped[str] = mapped_column(Text, nullable=False)
    set_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reps: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Null for bodyweight work.
    weight: Mapped[optional_measurement]
    weight_unit: Mapped[str | None] = mapped_column(Text, nullable=True)

    workout: Mapped[Workout] = relationship(back_populates="sets")

    __table_args__ = (
        UniqueConstraint("workout_id", "exercise", "set_number"),
        CheckConstraint("set_number >= 1", name="set_number_positive"),
        CheckConstraint("reps >= 0", name="reps_non_negative"),
        CheckConstraint("weight is null or weight >= 0", name="weight_non_negative"),
        CheckConstraint("(weight is null) = (weight_unit is null)", name="weight_has_unit"),
        Index("ix_workout_set_exercise", "exercise"),
    )


# --- conversation -------------------------------------------------------------


class Thread(Base):
    """A conversation.

    `id` is the LangGraph `thread_id`. Thread history is kept here rather than
    read out of the checkpointer: the checkpointer gives durable execution, not
    a readable history, and the UI needs the latter.
    """

    __tablename__ = "thread"

    id: Mapped[uuid_pk]
    #: Generated locally after the first turn, capped short. Null until then.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[timestamp]
    updated_at: Mapped[timestamp]
    #: Retention is a decision, not "forever by default".
    archived_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    #: Pinned threads are exempt from retention. Everything else is deleted a
    #: year after its last message (history.store.RETENTION).
    pinned_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )


class Message(Base):
    """One turn. Searched by Postgres full-text, not by embedding.

    You are looking for a thread you remember writing, so exact terms beat
    semantic similarity — and it costs no VRAM, which matters when the chat and
    embedding models are already sharing 8GB.
    """

    __tablename__ = "message"

    id: Mapped[uuid_pk]
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("thread.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which specialist produced this, for attribution in the UI. Null for the
    #: user's own turns.
    agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The tools an answer called, name and arguments — what the UI shows under
    #: it, so a figure's source survives a reload. Results are not kept: the
    #: answer already carries what they said.
    tool_calls: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    #: Stopped by the pre-flight check rather than answered. Shown differently,
    #: and its thread is never titled by the model.
    refused: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    #: How sure the router was. NUMERIC, like every figure in this schema.
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    #: Figures the answer states that no tool returned (agents.grounding) —
    #: rule 1, checked on every real answer rather than only in the evals.
    ungrounded: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    #: How much the answer was asked to say — brief, normal or detailed — so a
    #: rerun at another level is labelled as one after a reload.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[timestamp]

    thread: Mapped[Thread] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role in ('user', 'assistant', 'system', 'tool')", name="role_known"),
        Index("ix_message_thread_id_created_at", "thread_id", "created_at"),
        Index(
            "ix_message_content_fts",
            text("to_tsvector('english', content)"),
            postgresql_using="gin",
        ),
    )


# --- egress -------------------------------------------------------------------


class SearchAudit(Base):
    """Every query Errand attempted, allowed or refused.

    Refusals are recorded as well as successes. A blocked attempt is the more
    interesting row: it is evidence the egress filter did its job, and the UI
    shows both.
    """

    __tablename__ = "search_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    allowed: Mapped[bool] = mapped_column(nullable=False)
    #: Which validation rule rejected it. Null when allowed.
    violation: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("thread.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[timestamp]

    __table_args__ = (
        CheckConstraint("allowed or violation is not null", name="refusal_has_reason"),
        CheckConstraint("allowed or result_count is null", name="refusal_has_no_results"),
        Index("ix_search_audit_created_at", "created_at"),
    )
