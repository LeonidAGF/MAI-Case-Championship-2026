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


class KindOption(BaseModel):
    value: str
    label: str


class ScenarioInfo(BaseModel):
    code: str
    name: str
    type: str
    method: str
    target: Literal["point", "municipality"] = Field(
        description="point — нужны lat/lon (W2, W8: или poi_id); municipality — нужен municipality_id"
    )
    kinds: list[KindOption] | None = Field(None, description="Варианты kind с подписями; null — выбора нет")
    affects: list[str]
    description: str
    params_schema: dict[str, Any]
    example: dict[str, Any] = Field(description="Рабочий пример params (municipality_id — реальный район из базы)")


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


# --- sales markets and production sectors (tasks 4+5) ------------------------------
Tier = Literal["high", "medium", "low"]
SCORE_DOC = "Абсолютный балл 0–100 по собственным данным населённого пункта (фиксированные шкалы)"
TIER_DOC = "Уровень по баллу: high / medium / low (пороги — в /markets/catalog)"
PCT_SKFO = "Доп. метрика: перцентиль балла среди населённых пунктов СКФО (лучше, чем у X%)"
PCT_CLUSTER = "Доп. метрика: перцентиль балла среди населённых пунктов своего кластера А/Б/В"


class ScaleInfo(BaseModel):
    type: Literal["log", "decay"]
    zero: float | None = Field(None, description="log: значение, которому соответствует 0 баллов")
    full: float | None = Field(None, description="log: значение, которому соответствует 100 баллов")
    half_life: float | None = Field(None, description="decay: минуты, за которые балл уменьшается вдвое")


class MarketPartInfo(BaseModel):
    key: str
    label: str
    unit: str
    direction: Literal["higher_better", "lower_better"]
    source: Literal["population", "ml_gravity_30", "ml_gravity_60", "ml_gravity_120", "poi", "feature"]
    scale: ScaleInfo
    scale_text: str


class MarketInfo(BaseModel):
    code: str
    name: str
    description: str
    parts: list[MarketPartInfo]


class SectorInfo(BaseModel):
    code: str
    name: str
    examples: str
    description: str
    weights: dict[str, float] = Field(description="Вес каждого рынка сбыта для отрасли, сумма = 1")


class TierThresholds(BaseModel):
    high: float
    medium: float


class MarketCatalog(BaseModel):
    version: str
    radius_km: float = Field(description="Радиус подсчёта объектов OSM вокруг населённого пункта")
    min_coverage: float = Field(description="Минимальная доля веса рынков с данными, чтобы оценить отрасль")
    tiers: TierThresholds = Field(description="Пороги балла: high ≥ high, medium ≥ medium, иначе low")
    markets: list[MarketInfo]
    sectors: list[SectorInfo]
    notes: list[str]


class MarketPartValue(BaseModel):
    key: str
    label: str
    unit: str
    value: float | None = Field(description="Исходное значение: человек, объектов или минут; null — нет данных")
    score: float | None = Field(description="Балл части 0–100 по её шкале")
    text: str


class MarketScore(BaseModel):
    code: str
    name: str
    score: float | None = Field(description=f"{SCORE_DOC}: среднее частей")
    tier: Tier | None = Field(description=TIER_DOC)
    percentile: float | None = Field(description=PCT_SKFO)
    cluster_percentile: float | None = Field(description=PCT_CLUSTER)
    parts: list[MarketPartValue]
    text: str


class SectorAssessment(BaseModel):
    code: str
    name: str
    score: float | None = Field(description=f"{SCORE_DOC}: взвешенное среднее рынков с данными")
    tier: Tier | None = Field(description=TIER_DOC)
    percentile: float | None = Field(description=PCT_SKFO)
    cluster_percentile: float | None = Field(description=PCT_CLUSTER)
    cluster_rank: int | None = Field(description="Место в кластере по баллу (1 — лучшее)")
    cluster_size: int = Field(description="Сколько населённых пунктов кластера оценено для отрасли")
    coverage: float = Field(description="Доля веса рынков, по которым есть данные (0–1)")
    missing: list[str] = Field(description="Рынки без данных, не учтённые в оценке")
    verdict: str
    strengths: list[str]
    risks: list[str]


class TerritoryMarkets(BaseModel):
    territory_id: int
    name: str
    region_code: str | None
    municipality: str | None
    cluster: str
    cluster_name: str
    population: int | None
    markets: list[MarketScore]
    sectors: list[SectorAssessment] = Field(description="Отрасли от самой привлекательной к наименее")
    data_revision: int
    warnings: list[str]


class TierShares(BaseModel):
    high: float
    medium: float
    low: float
    unknown: float = Field(description="Доля без оценки (нет данных)")


class SectorLeader(BaseModel):
    territory_id: int
    name: str
    region_code: str | None
    municipality: str | None
    population: int | None
    score: float | None = Field(description=SCORE_DOC)
    tier: Tier | None
    percentile: float | None = Field(description=PCT_SKFO)
    cluster_rank: int | None


class ClusterMarket(BaseModel):
    code: str
    name: str
    median_score: float | None = Field(description="Медиана балла рынка по населённым пунктам кластера")
    medians: dict[str, float | None] = Field(description="Медианы исходных значений частей рынка")
    settlements: TierShares = Field(description="Доли населённых пунктов кластера по уровню рынка")
    population: TierShares = Field(description="То же, взвешенное по известному населению")


class ClusterSector(BaseModel):
    code: str
    name: str
    median_score: float | None = Field(description="Медиана балла отрасли по населённым пунктам кластера")
    settlements: TierShares
    population: TierShares
    leaders: list[SectorLeader] = Field(description="Лучшие населённые пункты кластера для отрасли")


class ClusterMarkets(BaseModel):
    cluster: str
    cluster_name: str
    settlements: int
    settlements_with_population: int
    population_known: int
    markets: list[ClusterMarket]
    sectors: list[ClusterSector]


class MarketClusters(BaseModel):
    region: str | None
    clusters: list[ClusterMarkets]
    data_revision: int
    warnings: list[str]


class SectorRatingItem(BaseModel):
    territory_id: int
    name: str
    region_code: str | None
    municipality: str | None
    cluster: str
    population: int | None
    score: float | None = Field(description=SCORE_DOC)
    tier: Tier | None
    percentile: float | None = Field(description=PCT_SKFO)
    cluster_percentile: float | None = Field(description=PCT_CLUSTER)
    cluster_rank: int | None
    cluster_size: int
    coverage: float
    missing: list[str]
    markets: dict[str, float | None] = Field(description="Балл каждого рынка сбыта (0–100)")
    strengths: list[str]
    risks: list[str]


class SectorRating(Page):
    sector: SectorInfo
    cluster: str | None
    region: str | None
    items: list[SectorRatingItem] = Field(description="По убыванию балла")
    data_revision: int
    warnings: list[str]
