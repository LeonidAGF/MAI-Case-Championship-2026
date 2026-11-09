"""Typed request/response models: the OpenAPI contract the frontend builds against (/docs)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.profiles.contract import ProfileInput, ProfileOut

ProfileRef = str | ProfileInput


class Point(BaseModel):
    lon: float
    lat: float


class TerritoryBrief(BaseModel):
    id: int
    external_id: str
    name: str
    level: Literal["region", "municipality", "settlement"]
    kind: str
    region_code: str | None
    parent_id: int | None
    cluster: str | None
    population: int | None
    is_admin_center: bool
    is_scored: bool
    centroid: Point | None


class Page(BaseModel):
    total: int
    limit: int
    offset: int


class TerritoryList(Page):
    items: list[TerritoryBrief]


class Breadcrumb(BaseModel):
    id: int
    name: str
    level: str


class DataCoverage(BaseModel):
    features_total: int
    own: int
    inherited: int
    missing: int


class TerritoryDetail(TerritoryBrief):
    region_name: str | None
    cluster_name: str | None
    breadcrumbs: list[Breadcrumb]
    children_count: int
    meta: dict[str, Any]
    data_coverage: DataCoverage | None
    geometry: dict[str, Any] | None = Field(None, description="GeoJSON (полигон района/субъекта)")


class Attractiveness(BaseModel):
    score: float | None
    badge: str | None
    label: str | None


class IndexItem(BaseModel):
    territory_id: int
    name: str
    total: float
    rank: int
    subindices: dict[str, float | None]
    attractiveness: Attractiveness | None


class ClusterStats(BaseModel):
    n: int
    median: float | None
    min: float | None
    max: float | None


class IndexMeta(BaseModel):
    profile: ProfileOut
    model_version: str
    data_revision: int
    scenario_run_id: int | None


class IndexSingle(IndexMeta):
    territory_id: int
    name: str
    cluster: str
    cluster_name: str
    total: float
    rank: int
    cluster_size: int
    subindices: dict[str, float | None]
    attractiveness: Attractiveness | None
    result_id: int


class GroupItem(BaseModel):
    territory_id: int
    name: str
    level: str
    n: int
    mean_total: float
    median_total: float
    min_total: float
    max_total: float
    subindices: dict[str, float | None]


class IndexCluster(IndexMeta, Page):
    cluster: str
    cluster_name: str
    cluster_allowed_by_profile: bool
    stats: ClusterStats
    items: list[IndexItem] = Field(default_factory=list)
    groups: list[GroupItem] | None = Field(None, description="Заполняется при group_by")


class WaterfallStep(BaseModel):
    feature_code: str
    label: str
    subindex: str
    raw_value: float | None
    unit: str
    normalized: float | None
    weight: float
    contribution: float
    median_contribution: float
    delta: float = Field(description="Отклонение от типичной территории кластера, баллы")
    imputed: bool
    inherited_from: str | None
    value_mode: str
    text: str
    typical_text: str | None


class ProCon(BaseModel):
    feature_code: str
    subindex: str
    delta: float
    text: str


class AttractivenessFactor(BaseModel):
    feature_code: str
    label: str
    raw_value: float | None
    unit: str
    normalized: float | None
    text: str


class ExplainResponse(IndexMeta):
    territory_id: int
    name: str
    cluster: str
    cluster_name: str
    total: float
    subindices: dict[str, float | None]
    attractiveness: Attractiveness | None
    baseline: float = Field(description="Типичная территория кластера: сумма медианных вкладов")
    waterfall: list[WaterfallStep]
    pluses: list[ProCon]
    minuses: list[ProCon]
    attractiveness_factors: list[AttractivenessFactor]
    summary: str
    result_id: int


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    territory_ids: list[int] = Field(..., min_length=2, max_length=3)
    profile: ProfileRef | None = Field(None, description="id пресета, строка p или объект профиля")


class CompareItem(BaseModel):
    territory_id: int
    name: str
    region_code: str | None
    total: float
    rank: int
    subindices: dict[str, float | None]
    attractiveness: Attractiveness | None
    pluses: list[ProCon]
    minuses: list[ProCon]
    result_id: int


class RadarAxis(BaseModel):
    code: str
    name: str


class RadarSeries(BaseModel):
    territory_id: int
    name: str
    values: list[float | None]


class Radar(BaseModel):
    axes: list[RadarAxis]
    series: list[RadarSeries]


class Leader(BaseModel):
    territory_id: int
    gap: float | None


class CompareResponse(BaseModel):
    cluster: str
    cluster_name: str
    cluster_stats: ClusterStats
    items: list[CompareItem]
    radar: Radar
    leaders: dict[str, Leader]
    summary: list[str]
    profile: ProfileOut


class ScenarioInfo(BaseModel):
    code: str
    name: str
    type: str
    method: str
    affects: list[str]
    description: str
    params_schema: dict[str, Any]
    example: dict[str, Any]


class WhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_code: str = Field(..., examples=["W1"])
    params: dict[str, Any] = Field(default_factory=dict)
    profile: ProfileRef | None = None
    method: Literal["auto", "graph", "analytical"] = "auto"


class FeatureChange(BaseModel):
    feature_code: str
    label: str
    unit: str
    before: float
    after: float
    delta_points: float
    text: str


class WhatIfResult(BaseModel):
    territory_id: int
    name: str
    cluster: str
    region_code: str | None
    total_before: float
    total_after: float
    delta: float
    delta_subindices: dict[str, float]
    changes: list[FeatureChange]


class WhatIfBrief(BaseModel):
    territory_id: int
    name: str
    cluster: str
    delta: float


class WhatIfResponse(BaseModel):
    run_id: int
    scenario_code: str
    scenario_name: str
    method_used: str
    params: dict[str, Any]
    plan: list[dict[str, Any]] | None
    affected: int
    results: list[WhatIfResult]
    winners: list[WhatIfBrief]
    losers: list[WhatIfBrief]
    summary: dict[str, Any]
    explanation: str
    warnings: list[str]
    profile: ProfileOut


class PresetOut(BaseModel):
    id: str
    name: str
    description: str
    key_thresholds: str
    profile: ProfileOut
    p: str = Field(description="Готовое значение параметра p для ссылки")


class EncodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: ProfileRef
    compress: bool = True


class EncodeResponse(BaseModel):
    p: str
    length: int
    query: str
    profile: ProfileOut


class FeatureInfo(BaseModel):
    code: str
    name: str
    unit: str
    direction: str
    subindex: str
    subindex_name: str
    level: str
    time_based: bool
    source: str
    weight: float


class SubindexInfo(BaseModel):
    code: str
    name: str
    short_name: str
    in_total: bool


class FeatureCatalog(BaseModel):
    model_version: str
    subindices: list[SubindexInfo]
    features: list[FeatureInfo]


class ClusterInfo(BaseModel):
    code: str
    name: str
    description: str
    params: dict[str, Any]
    territories: int


class RegionInfo(BaseModel):
    id: int
    code: str
    name: str
    municipalities: int
    settlements: int
    scored: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    model_version: str
    scoring_engine: str
    checks: dict[str, str]
    data_revision: int | None
