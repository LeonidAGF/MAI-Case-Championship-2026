"""Ambulance travel times to settlements (app/ambulance, load-ambulance).

Revision ID: 0002_ambulance_time
Revises: 0001_initial
Create Date: 2026-11-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002_ambulance_time"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ambulance_time",
        sa.Column("territory_id", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Float(), nullable=True),
        sa.Column("flat_minutes", sa.Float(), nullable=True),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("elevation_m", sa.Float(), nullable=True),
        sa.Column("details", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("etl_run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["territory_id"], ["territory.id"], name="fk_ambulance_time_territory_id_territory", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["etl_run_id"], ["etl_run.id"], name="fk_ambulance_time_etl_run_id_etl_run", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("territory_id", name="pk_ambulance_time"),
    )


def downgrade() -> None:
    op.drop_table("ambulance_time")
