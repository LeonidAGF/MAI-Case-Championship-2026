"""ORM models. Schema = section 7 of the team report + supporting tables.

Section 7 tables: territory, cluster, feature, feature_value, profile, index_result,
component, scenario, scenario_run, knowledge_chunk.
Supporting tables: subindex (S1..S7 catalog), model_version ("версия модели в БД"),
etl_run (data revisions + completeness stats), poi and travel_time (what-if W1–W5/W9).

The migration in migrations/versions is written by hand and must stay in sync with
this module (tests/integration/test_migrations.py runs ``alembic check``).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from geoalchemy2 import Geometry
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.db.base import Base

LEVELS = ("region", "municipality", "settlement")
VALUE_MODES = ("any", "car", "foot")


def _ts(**kw: Any) -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), **kw)


class Subindex(Base):
    __tablename__ = "subindex"

    code: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    short_name: Mapped[str] = mapped_column(Text)
    in_total: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))


class Cluster(Base):
    __tablename__ = "cluster"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # Time thresholds and normalization parameters of the cluster (section 5.3).
    thresholds_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))


class Territory(Base):
    __tablename__ = "territory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stable source key: "iso:RU-DA", "osm:relation:1029091", "osm:node:34395335".
    external_id: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    name_normalized: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(16))
    # region: subject | municipality: municipal_district/municipal_okrug/city_okrug
    # settlement: city/town/village/hamlet/isolated_dwelling
    kind: Mapped[str] = mapped_column(String(32))
    region_code: Mapped[str | None] = mapped_column(String(8))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("territory.id", ondelete="SET NULL")
    )
    cluster_code: Mapped[str | None] = mapped_column(ForeignKey("cluster.code"))
    population: Mapped[int | None] = mapped_column(Integer)
    oktmo: Mapped[str | None] = mapped_column(String(11))
    is_admin_center: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_scored: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    centroid: Mapped[Any] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    geom: Mapped[Any] = deferred(
        mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=True)
    )
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[dt.datetime] = _ts()
    updated_at: Mapped[dt.datetime] = _ts(onupdate=func.now())

    __table_args__ = (
        CheckConstraint("level IN ('region', 'municipality', 'settlement')", name="level"),
        Index("ix_territory_level_cluster", "level", "cluster_code"),
        Index("ix_territory_parent_id", "parent_id"),
        Index("ix_territory_region_code", "region_code"),
        Index("ix_territory_oktmo", "oktmo"),
        Index("ix_territory_centroid", "centroid", postgresql_using="gist"),
        Index("ix_territory_geom", "geom", postgresql_using="gist"),
        Index(
            "ix_territory_name_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
    )


class Feature(Base):
    __tablename__ = "feature"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text, server_default=text("''"))
    direction: Mapped[str] = mapped_column(String(16))
    subindex_code: Mapped[str] = mapped_column(ForeignKey("subindex.code"))
    # Natural level of the data; settlements inherit municipality/region values.
    level: Mapped[str] = mapped_column(String(16), server_default=text("'settlement'"))
    # True for travel-time features that differ by mobility mode (car/foot).
    time_based: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    source: Mapped[str] = mapped_column(Text, server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    default_weight: Mapped[float] = mapped_column(Float, server_default=text("1.0"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))

    __table_args__ = (
        CheckConstraint("direction IN ('higher_better', 'lower_better')", name="direction"),
        CheckConstraint("level IN ('region', 'municipality', 'settlement')", name="level"),
    )


class EtlRun(Base):
    __tablename__ = "etl_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(Text, server_default=text("''"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'running'"))
    # Data-changing runs bump the data revision that keys caches and snapshots.
    bumps_revision: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    started_at: Mapped[dt.datetime] = _ts()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('running', 'success', 'failed')", name="status"),
    )


class FeatureValue(Base):
    __tablename__ = "feature_value"

    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territory.id", ondelete="CASCADE"), primary_key=True
    )
    feature_code: Mapped[str] = mapped_column(
        ForeignKey("feature.code", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(8), primary_key=True, server_default=text("'any'"))
    as_of: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    raw_value: Mapped[float | None] = mapped_column(Float)
    imputed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    source: Mapped[str] = mapped_column(Text, server_default=text("''"))
    etl_run_id: Mapped[int | None] = mapped_column(ForeignKey("etl_run.id", ondelete="SET NULL"))

    __table_args__ = (
        CheckConstraint("mode IN ('any', 'car', 'foot')", name="mode"),
        Index("ix_feature_value_feature_code", "feature_code"),
        Index("ix_feature_value_etl_run_id", "etl_run_id"),
    )


class Profile(Base):
    __tablename__ = "profile"

    # Preset code, e.g. "family". Custom profiles never reach the DB (URL/localStorage only).
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    is_preset: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    subindex_weights_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    filters_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    key_thresholds: Mapped[str] = mapped_column(Text, server_default=text("''"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    created_at: Mapped[dt.datetime] = _ts()


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(String(64))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[dt.datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("version", "engine", "config_hash", name="uq_model_version_identity"),
    )


class Scenario(Base):
    __tablename__ = "scenario"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(16))
    affects: Mapped[list[str]] = mapped_column(ARRAY(String(8)), server_default=text("'{}'"))
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))


class ScenarioRun(Base):
    __tablename__ = "scenario_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_code: Mapped[str] = mapped_column(ForeignKey("scenario.code"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    method_used: Mapped[str] = mapped_column(String(16))
    profile_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'success'"))
    # [{"territory_id", "feature_code", "mode", "old", "new"}]
    overrides: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    affected_territory_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), server_default=text("'{}'")
    )
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    warnings: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    data_revision: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_version.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = _ts()

    __table_args__ = (Index("ix_scenario_run_scenario_code", "scenario_code"),)


class IndexResult(Base):
    """Immutable snapshot of one territory's index under one profile (+ scenario)."""

    __tablename__ = "index_result"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    territory_id: Mapped[int] = mapped_column(ForeignKey("territory.id", ondelete="CASCADE"))
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("profile.id", ondelete="SET NULL"))
    profile_hash: Mapped[str] = mapped_column(String(64))
    scenario_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenario_run.id", ondelete="CASCADE")
    )
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE")
    )
    data_revision: Mapped[int] = mapped_column(Integer)
    cluster_code: Mapped[str] = mapped_column(String(2))
    mode: Mapped[str] = mapped_column(String(8))
    total: Mapped[float] = mapped_column(Float)
    subindices: Mapped[dict[str, Any]] = mapped_column(JSONB)
    attractiveness: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = _ts()

    __table_args__ = (
        UniqueConstraint(
            "territory_id",
            "profile_hash",
            "scenario_run_id",
            "model_version_id",
            "data_revision",
            name="uq_index_result_snapshot",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_index_result_territory_id", "territory_id"),
        Index("ix_index_result_scenario_run_id", "scenario_run_id"),
    )


class Component(Base):
    """Per-feature contribution of a snapshot: the single source for waterfall and texts."""

    __tablename__ = "component"

    result_id: Mapped[int] = mapped_column(
        ForeignKey("index_result.id", ondelete="CASCADE"), primary_key=True
    )
    feature_code: Mapped[str] = mapped_column(
        ForeignKey("feature.code", ondelete="CASCADE"), primary_key=True
    )
    raw_value: Mapped[float | None] = mapped_column(Float)
    normalized: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float)
    contribution: Mapped[float] = mapped_column(Float)
    imputed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    inherited_from: Mapped[str | None] = mapped_column(String(16))
    value_mode: Mapped[str] = mapped_column(String(8), server_default=text("'any'"))


class Poi(Base):
    __tablename__ = "poi"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(Text, unique=True)
    category: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(Text)
    # Municipality that contains the POI (spatial binding during ETL).
    territory_id: Mapped[int | None] = mapped_column(
        ForeignKey("territory.id", ondelete="SET NULL")
    )
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326, spatial_index=False))
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    source: Mapped[str] = mapped_column(Text, server_default=text("''"))
    etl_run_id: Mapped[int | None] = mapped_column(ForeignKey("etl_run.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = _ts()

    __table_args__ = (
        Index("ix_poi_category", "category"),
        Index("ix_poi_territory_id", "territory_id"),
        Index("ix_poi_geom", "geom", postgresql_using="gist"),
    )


class TravelTime(Base):
    """Precomputed territory -> POI times (OSRM matrix, produced by ML/MLOps)."""

    __tablename__ = "travel_time"

    territory_id: Mapped[int] = mapped_column(
        ForeignKey("territory.id", ondelete="CASCADE"), primary_key=True
    )
    poi_id: Mapped[int] = mapped_column(ForeignKey("poi.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), primary_key=True)
    seconds: Mapped[float] = mapped_column(Float)
    etl_run_id: Mapped[int | None] = mapped_column(ForeignKey("etl_run.id", ondelete="SET NULL"))

    __table_args__ = (
        CheckConstraint("mode IN ('car', 'foot')", name="mode"),
        Index("ix_travel_time_poi_id", "poi_id"),
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    territory_id: Mapped[int | None] = mapped_column(
        ForeignKey("territory.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    text: Mapped[str] = mapped_column(Text)
    # Dimension depends on the embedding model chosen by MLOps -> unconstrained vector.
    embedding: Mapped[Any] = mapped_column(Vector(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('russian', coalesce(title, '') || ' ' || text)", persisted=True),
    )
    created_at: Mapped[dt.datetime] = _ts()

    __table_args__ = (
        Index("ix_knowledge_chunk_territory_id", "territory_id"),
        Index("ix_knowledge_chunk_tsv", "tsv", postgresql_using="gin"),
    )
