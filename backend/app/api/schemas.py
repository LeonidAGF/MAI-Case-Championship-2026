"""Typed request/response models: the OpenAPI contract the frontend builds against (/docs)."""

from __future__ import annotations

import datetime as dt
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
    climate_match: float | None = None
    climate_adjustment_points: float = 0.0


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
    climate_match: float | None = None
    climate_adjustment_points: float = 0.0


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
    display_in_bar_chart: bool = Field(True, description="Фронтенд: климатические компоненты S6 показывать в паспорте, не в зелёных полосах")


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
    climate_delta: float = Field(0, description="Сумма delta климатических компонентов внутри waterfall")
    climate_contribution: float = Field(0, description="Вклад S6 в общий балл")
    climate_match: float | None = None
    climate_adjustment_points: float = 0.0
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


# --- ambulance travel time (/ambulance) ------------------------------------------------------

NormStatus = Literal["met", "borderline", "not_met"]


class AmbulanceEstimate(BaseModel):
    minutes: float = Field(description="Оценка времени доезда, мин: время в пути (+ приём вызова, если задан)")
    minutes_low: float = Field(description="Нижняя граница диапазона погрешности, мин")
    minutes_high: float = Field(description="Верхняя граница диапазона погрешности, мин")
    travel_minutes: float = Field(description="Только время в пути от базы, мин")
    dispatch_minutes: float = Field(description="Приём вызова и выезд бригады, добавленные к пути, мин")
    norm_minutes: float
    norm_status: NormStatus = Field(
        description="met — даже верхняя граница в нормативе; not_met — даже нижняя дольше; borderline — норматив внутри диапазона"
    )


class AmbulanceBase(BaseModel):
    external_id: str
    name: str | None
    category: str = Field(description="ambulance — станция скорой помощи, hospital — больница")
    category_label: str
    location: Point
    access_m: int = Field(description="От объекта до ближайшей дороги графа, м")


class AmbulanceRoute(BaseModel):
    distance_km: float | None = Field(description="Длина маршрута по дорогам, км")
    flat_minutes: float | None = Field(description="Тот же маршрут без поправок на уклон и высоту, мин")
    terrain_minutes: float | None = Field(description="Сколько добавляют уклон и высота, мин")
    climb_m: int | None
    descent_m: int | None
    max_height_m: int | None = Field(description="Высшая точка маршрута над уровнем моря, м (SRTM)")
    steep_km: float | None = Field(description="Участки с уклоном от порога крутизны, км")
    urban_km: float = Field(description="Участки в населённых пунктах (скорость как в населённом пункте), км")
    osm_speed_km: float = Field(description="Участки со скоростью из тега maxspeed OpenStreetMap, км")
    no_terrain_km: float = Field(description="Участки без данных рельефа, км")
    access_m: int = Field(description="От населённого пункта до ближайшей дороги графа, м")


class AmbulanceSettlement(BaseModel):
    territory_id: int
    name: str
    region_code: str | None
    region_name: str | None
    municipality_id: int | None
    municipality_name: str | None
    cluster: str | None
    population: int | None
    location: Point | None
    elevation_m: float | None = Field(description="Высота населённого пункта над уровнем моря, м (SRTM)")
    available: bool
    estimate: AmbulanceEstimate | None
    base: AmbulanceBase | None
    route: AmbulanceRoute | None
    reason: str | None = Field(description="Почему нет оценки: far_from_roads — далеко от дорог, по которым можно доехать")
    flags: list[str] = Field(description="hospital_base, far_from_roads, no_terrain, stale")
    text: str
    notes: list[str]
    model_version: str
    computed_at: dt.datetime | None
    stale: bool = Field(description="Параметры модели изменились после расчёта: нужен load-ambulance")


class AmbulanceListItem(BaseModel):
    territory_id: int
    name: str
    region_code: str | None
    municipality_id: int | None
    municipality_name: str | None
    cluster: str | None
    population: int | None
    location: Point | None
    elevation_m: float | None
    minutes: float | None
    minutes_low: float | None
    minutes_high: float | None
    norm_status: NormStatus | None
    distance_km: float | None
    terrain_minutes: float | None
    base_name: str | None
    base_category: str | None


class AmbulanceList(Page):
    items: list[AmbulanceListItem]
    norm_minutes: float
    computed_at: dt.datetime | None
    stale: bool


class AmbulanceReach(BaseModel):
    minutes: float
    settlements: int = Field(description="Населённых пунктов, куда скорая доезжает за это время")
    share: float | None = Field(description="Их доля среди пунктов с оценкой")
    population: int = Field(description="Жители этих пунктов (только пункты с известным населением)")
    population_share: float | None = Field(description="Доля жителей среди пунктов с известным населением")


class AmbulanceStats(BaseModel):
    settlements: int
    estimated: int
    without_estimate: int
    median_minutes: float | None
    p90_minutes: float | None
    max_minutes: float | None
    within_norm: AmbulanceReach
    thresholds: list[AmbulanceReach]
    norm_status: dict[str, int]
    population_settlements: int = Field(description="Пункты с оценкой и известным населением")
    population_known: int = Field(description="Их население")
    hospital_base_share: float | None = Field(description="Доля пунктов, где ближайшая база — больница")


class AmbulanceGroup(BaseModel):
    territory_id: int | None
    code: str | None
    name: str
    level: Literal["region", "municipality"]
    stats: AmbulanceStats


class AmbulanceScope(BaseModel):
    territory_id: int | None
    name: str
    level: Literal["federal_district", "region", "municipality"]


class AmbulanceSummary(BaseModel):
    scope: AmbulanceScope
    norm_minutes: float
    dispatch_minutes: float
    stats: AmbulanceStats
    groups: list[AmbulanceGroup]
    slowest: list[AmbulanceListItem]
    text: str
    model_version: str
    computed_at: dt.datetime | None
    stale: bool


class AmbulanceSpeed(BaseModel):
    highway: str
    label: str
    urban_kmh: float
    rural_kmh: float


class AmbulanceMethod(BaseModel):
    model_version: str
    computed_at: dt.datetime | None
    run_id: int | None
    stale: bool
    norm_minutes: float
    thresholds: list[float]
    dispatch_minutes: float
    uncertainty: dict[str, float]
    speeds: list[AmbulanceSpeed]
    default_kmh: float
    maxspeed_tags: dict[str, float]
    urban: dict[str, Any]
    slope: dict[str, Any]
    height: dict[str, Any]
    access: dict[str, float]
    base_categories: list[str]
    sources: list[str]
    run: dict[str, Any] = Field(description="Статистика последнего расчёта: граф, базы, скорости, рельеф")
    description: list[str]


# --- data API for organisations (/v1) ----------------------------------------------------------


class V1Scope(BaseModel):
    code: str
    description: str


class V1Me(BaseModel):
    organisation: str
    key_prefix: str = Field(description="Начало ключа: по нему команда находит и отзывает ключ")
    key_name: str
    scopes: list[V1Scope]
    rate_limit_per_minute: int
    expires_at: dt.datetime | None = Field(description="До какого момента действует ключ (оплаченный срок)")


class V1Column(BaseModel):
    key: str = Field(description="Имя колонки в CSV для программ и ключ в JSON")
    label: str = Field(description="Заголовок колонки в CSV для Excel")
    type: str = Field(description="str, int, float, bool, date или datetime")
    values: dict[str, str] | None = Field(None, description="Русские слова для кодов в CSV для Excel")


class V1Dataset(BaseModel):
    name: str
    title: str
    description: str
    url: str
    formats: list[str]
    params: list[str]
    columns: list[V1Column]


class V1Catalog(BaseModel):
    meta: dict[str, Any] = Field(description="Ревизия данных, версия модели, время выгрузки, атрибуция")
    datasets: list[V1Dataset]
    notes: list[str]


class V1SettlementMatch(BaseModel):
    id: int
    name: str
    kind: str | None
    cluster_code: str | None
    municipality_id: int | None
    municipality: str | None
    region_code: str | None
    region: str | None
    population: int | None


class V1SettlementSearch(BaseModel):
    items: list[V1SettlementMatch]
    total: int
    limit: int
    offset: int


class V1SettlementDetail(BaseModel):
    settlement: dict[str, Any]
    index: dict[str, Any] | None
    features: list[dict[str, Any]]
    ambulance: dict[str, Any] | None
    ambulance_status: Literal["available", "no_estimate", "not_computed"]
    meta: dict[str, Any]
