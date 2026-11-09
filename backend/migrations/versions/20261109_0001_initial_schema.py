"""Initial schema: extensions + section 7 tables + supporting tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")
EMPTY_JSON = sa.text("'{}'::jsonb")
EMPTY_LIST_JSON = sa.text("'[]'::jsonb")


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "subindex",
        sa.Column("code", sa.String(4), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("short_name", sa.Text(), nullable=False),
        sa.Column("in_total", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_subindex"),
    )

    op.create_table(
        "cluster",
        sa.Column("code", sa.String(2), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("thresholds_json", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_cluster"),
    )

    op.create_table(
        "territory",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_normalized", sa.Text(), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("region_code", sa.String(8), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("cluster_code", sa.String(2), nullable=True),
        sa.Column("population", sa.Integer(), nullable=True),
        sa.Column("oktmo", sa.String(11), nullable=True),
        sa.Column("is_admin_center", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_scored", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("centroid", Geometry("POINT", srid=4326, spatial_index=False), nullable=True),
        sa.Column("geom", Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=True),
        sa.Column("meta", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("level IN ('region', 'municipality', 'settlement')", name="ck_territory_level"),
        sa.ForeignKeyConstraint(["parent_id"], ["territory.id"], name="fk_territory_parent_id_territory", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cluster_code"], ["cluster.code"], name="fk_territory_cluster_code_cluster"),
        sa.PrimaryKeyConstraint("id", name="pk_territory"),
        sa.UniqueConstraint("external_id", name="uq_territory_external_id"),
    )
    op.create_index("ix_territory_level_cluster", "territory", ["level", "cluster_code"])
    op.create_index("ix_territory_parent_id", "territory", ["parent_id"])
    op.create_index("ix_territory_region_code", "territory", ["region_code"])
    op.create_index("ix_territory_oktmo", "territory", ["oktmo"])
    op.create_index("ix_territory_centroid", "territory", ["centroid"], postgresql_using="gist")
    op.create_index("ix_territory_geom", "territory", ["geom"], postgresql_using="gist")
    op.create_index(
        "ix_territory_name_trgm",
        "territory",
        ["name_normalized"],
        postgresql_using="gin",
        postgresql_ops={"name_normalized": "gin_trgm_ops"},
    )

    op.create_table(
        "feature",
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("subindex_code", sa.String(4), nullable=False),
        sa.Column("level", sa.String(16), server_default=sa.text("'settlement'"), nullable=False),
        sa.Column("time_based", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("default_weight", sa.Float(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("direction IN ('higher_better', 'lower_better')", name="ck_feature_direction"),
        sa.CheckConstraint("level IN ('region', 'municipality', 'settlement')", name="ck_feature_level"),
        sa.ForeignKeyConstraint(["subindex_code"], ["subindex.code"], name="fk_feature_subindex_code_subindex"),
        sa.PrimaryKeyConstraint("code", name="pk_feature"),
    )

    op.create_table(
        "etl_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'running'"), nullable=False),
        sa.Column("bumps_revision", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _ts("started_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats_json", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('running', 'success', 'failed')", name="ck_etl_run_status"),
        sa.PrimaryKeyConstraint("id", name="pk_etl_run"),
    )

    op.create_table(
        "feature_value",
        sa.Column("territory_id", sa.Integer(), nullable=False),
        sa.Column("feature_code", sa.String(8), nullable=False),
        sa.Column("mode", sa.String(8), server_default=sa.text("'any'"), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("raw_value", sa.Float(), nullable=True),
        sa.Column("imputed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("etl_run_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("mode IN ('any', 'car', 'foot')", name="ck_feature_value_mode"),
        sa.ForeignKeyConstraint(["territory_id"], ["territory.id"], name="fk_feature_value_territory_id_territory", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feature_code"], ["feature.code"], name="fk_feature_value_feature_code_feature", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["etl_run_id"], ["etl_run.id"], name="fk_feature_value_etl_run_id_etl_run", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("territory_id", "feature_code", "mode", "as_of", name="pk_feature_value"),
    )
    op.create_index("ix_feature_value_feature_code", "feature_value", ["feature_code"])
    op.create_index("ix_feature_value_etl_run_id", "feature_value", ["etl_run_id"])

    op.create_table(
        "profile",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("is_preset", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("subindex_weights_json", pg.JSONB(), nullable=False),
        sa.Column("filters_json", pg.JSONB(), nullable=False),
        sa.Column("key_thresholds", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_profile"),
    )

    op.create_table(
        "model_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("config_json", pg.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_model_version"),
        sa.UniqueConstraint("version", "engine", "config_hash", name="uq_model_version_identity"),
    )

    op.create_table(
        "scenario",
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("affects", pg.ARRAY(sa.String(8)), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("params_json", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_scenario"),
    )

    op.create_table(
        "scenario_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scenario_code", sa.String(8), nullable=False),
        sa.Column("params", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        sa.Column("method_used", sa.String(16), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), server_default=sa.text("'success'"), nullable=False),
        sa.Column("overrides", pg.JSONB(), server_default=EMPTY_LIST_JSON, nullable=False),
        sa.Column("affected_territory_ids", pg.ARRAY(sa.Integer()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("summary", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        sa.Column("warnings", pg.JSONB(), server_default=EMPTY_LIST_JSON, nullable=False),
        sa.Column("data_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["scenario_code"], ["scenario.code"], name="fk_scenario_run_scenario_code_scenario"),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_version.id"], name="fk_scenario_run_model_version_id_model_version", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_scenario_run"),
    )
    op.create_index("ix_scenario_run_scenario_code", "scenario_run", ["scenario_code"])

    op.create_table(
        "index_result",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("territory_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.String(32), nullable=True),
        sa.Column("profile_hash", sa.String(64), nullable=False),
        sa.Column("scenario_run_id", sa.Integer(), nullable=True),
        sa.Column("model_version_id", sa.Integer(), nullable=False),
        sa.Column("data_revision", sa.Integer(), nullable=False),
        sa.Column("cluster_code", sa.String(2), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("total", sa.Float(), nullable=False),
        sa.Column("subindices", pg.JSONB(), nullable=False),
        sa.Column("attractiveness", pg.JSONB(), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["territory_id"], ["territory.id"], name="fk_index_result_territory_id_territory", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profile.id"], name="fk_index_result_profile_id_profile", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scenario_run_id"], ["scenario_run.id"], name="fk_index_result_scenario_run_id_scenario_run", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_version.id"], name="fk_index_result_model_version_id_model_version", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_index_result"),
        sa.UniqueConstraint(
            "territory_id",
            "profile_hash",
            "scenario_run_id",
            "model_version_id",
            "data_revision",
            name="uq_index_result_snapshot",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_index_result_territory_id", "index_result", ["territory_id"])
    op.create_index("ix_index_result_scenario_run_id", "index_result", ["scenario_run_id"])

    op.create_table(
        "component",
        sa.Column("result_id", sa.BigInteger(), nullable=False),
        sa.Column("feature_code", sa.String(8), nullable=False),
        sa.Column("raw_value", sa.Float(), nullable=True),
        sa.Column("normalized", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("contribution", sa.Float(), nullable=False),
        sa.Column("imputed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("inherited_from", sa.String(16), nullable=True),
        sa.Column("value_mode", sa.String(8), server_default=sa.text("'any'"), nullable=False),
        sa.ForeignKeyConstraint(["result_id"], ["index_result.id"], name="fk_component_result_id_index_result", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feature_code"], ["feature.code"], name="fk_component_feature_code_feature", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("result_id", "feature_code", name="pk_component"),
    )

    op.create_table(
        "poi",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("territory_id", sa.Integer(), nullable=True),
        sa.Column("geom", Geometry("POINT", srid=4326, spatial_index=False), nullable=False),
        sa.Column("tags", pg.JSONB(), server_default=EMPTY_JSON, nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("etl_run_id", sa.Integer(), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["territory_id"], ["territory.id"], name="fk_poi_territory_id_territory", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["etl_run_id"], ["etl_run.id"], name="fk_poi_etl_run_id_etl_run", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_poi"),
        sa.UniqueConstraint("external_id", name="uq_poi_external_id"),
    )
    op.create_index("ix_poi_category", "poi", ["category"])
    op.create_index("ix_poi_territory_id", "poi", ["territory_id"])
    op.create_index("ix_poi_geom", "poi", ["geom"], postgresql_using="gist")

    op.create_table(
        "travel_time",
        sa.Column("territory_id", sa.Integer(), nullable=False),
        sa.Column("poi_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("etl_run_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("mode IN ('car', 'foot')", name="ck_travel_time_mode"),
        sa.ForeignKeyConstraint(["territory_id"], ["territory.id"], name="fk_travel_time_territory_id_territory", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["poi_id"], ["poi.id"], name="fk_travel_time_poi_id_poi", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["etl_run_id"], ["etl_run.id"], name="fk_travel_time_etl_run_id_etl_run", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("territory_id", "poi_id", "mode", name="pk_travel_time"),
    )
    op.create_index("ix_travel_time_poi_id", "travel_time", ["poi_id"])

    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("territory_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column(
            "tsv",
            pg.TSVECTOR(),
            sa.Computed("to_tsvector('russian', coalesce(title, '') || ' ' || text)", persisted=True),
            nullable=True,
        ),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["territory_id"], ["territory.id"], name="fk_knowledge_chunk_territory_id_territory", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunk"),
    )
    op.create_index("ix_knowledge_chunk_territory_id", "knowledge_chunk", ["territory_id"])
    op.create_index("ix_knowledge_chunk_tsv", "knowledge_chunk", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    for table in (
        "knowledge_chunk",
        "travel_time",
        "poi",
        "component",
        "index_result",
        "scenario_run",
        "scenario",
        "model_version",
        "profile",
        "feature_value",
        "etl_run",
        "feature",
        "territory",
        "cluster",
        "subindex",
    ):
        op.drop_table(table)
    # Extensions are intentionally kept: other databases objects may depend on them.
