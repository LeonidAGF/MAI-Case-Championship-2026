"""Organisations, their API keys and manually approved payment orders for the /v1 data API.

Revision ID: 0003_organisations_payments
Revises: 0002_ambulance_time
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003_organisations_payments"
down_revision: str | None = "0002_ambulance_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "organisation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("contact_email", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organisation"),
        sa.UniqueConstraint("name", name="uq_organisation_name"),
    )
    op.create_table(
        "api_key",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", pg.ARRAY(sa.String(32)), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], name="fk_api_key_organisation_id_organisation", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_key"),
        sa.UniqueConstraint("prefix", name="uq_api_key_prefix"),
        sa.UniqueConstraint("key_hash", name="uq_api_key_key_hash"),
    )
    op.create_index("ix_api_key_organisation_id", "api_key", ["organisation_id"])

    op.create_table(
        "payment_order",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organisation_id", sa.Integer(), nullable=False),
        sa.Column("months", sa.SmallInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default=sa.text("'RUB'"), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'awaiting_payment'"), nullable=False),
        sa.Column("payment_reference", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("confirmed_by", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('awaiting_payment', 'paid', 'fulfilled', 'canceled')", name="ck_payment_order_status"
        ),
        sa.CheckConstraint("months > 0", name="ck_payment_order_months"),
        sa.CheckConstraint("amount > 0", name="ck_payment_order_amount"),
        sa.ForeignKeyConstraint(
            ["organisation_id"], ["organisation.id"], name="fk_payment_order_organisation_id_organisation"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payment_order"),
    )
    op.create_index("ix_payment_order_organisation_id", "payment_order", ["organisation_id"])

    op.create_table(
        "payment_event",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("details", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["payment_order.id"], name="fk_payment_event_order_id_payment_order", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payment_event"),
    )
    op.create_index("ix_payment_event_order_id", "payment_event", ["order_id"])


def downgrade() -> None:
    op.drop_table("payment_event")
    op.drop_table("payment_order")
    op.drop_table("api_key")
    op.drop_table("organisation")
