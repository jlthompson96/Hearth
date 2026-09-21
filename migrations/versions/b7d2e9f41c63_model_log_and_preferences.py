"""model log and preferences

`model_log`: every exchange with the model, and every tool run, verbatim — the
Model log screen. Entries cascade with their thread and their question, so
thread retention reaches them without a sweep of its own; a shorter model-log
retention sweeps by `started_at`.

`preference`: what the Settings screen changes. Read where it is used, every time,
so a saved change is in effect on the next question — unlike `.env`, which is
read once at startup.

The read-only role reads both through the default privileges granted in
fa7860f9535d.

Revision ID: b7d2e9f41c63
Revises: e1a4c7b92d05
Create Date: 2026-09-21 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d2e9f41c63"
down_revision: Union[str, Sequence[str], None] = "e1a4c7b92d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("caller", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind in ('route', 'step', 'tool', 'title')", name=op.f("ck_model_log_kind_known")
        ),
        sa.CheckConstraint("duration_ms >= 0", name=op.f("ck_model_log_duration_not_negative")),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["thread.id"],
            name=op.f("fk_model_log_thread_id_thread"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["message.id"],
            name=op.f("fk_model_log_question_id_message"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_log")),
    )
    op.create_index("ix_model_log_question_id_seq", "model_log", ["question_id", "seq"])
    op.create_index("ix_model_log_started_at", "model_log", ["started_at"])

    op.create_table(
        "preference",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_preference")),
    )


def downgrade() -> None:
    op.drop_table("preference")
    op.drop_index("ix_model_log_started_at", table_name="model_log")
    op.drop_index("ix_model_log_question_id_seq", table_name="model_log")
    op.drop_table("model_log")
