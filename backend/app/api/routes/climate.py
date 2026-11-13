"""2025 seasonal reanalysis on the NASA POWER grid, independent of user preferences."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, profile_query, rate_limit
from app.climate.service import (
    HUMIDITY_TARGET, SEASONS, TARGETS, YEAR, YELLOW_HUMIDITY_MARGIN_PCT,
    YELLOW_TEMPERATURE_MARGIN_C, ClimateService, color_for,
)
from app.core.errors import NotFoundError, ServiceUnavailable
from app.profiles.contract import Profile

router = APIRouter(prefix="/climate/2025", tags=["climate"], dependencies=[rate_limit("default", "rate_limit_per_minute")])
Season = Literal["winter", "spring", "summer", "autumn"]


class ClimateTarget(BaseModel):
    temperature_min_c: float
    temperature_max_c: float
    humidity_min_pct: float
    humidity_max_pct: float
    yellow_temperature_margin_c: float = YELLOW_TEMPERATURE_MARGIN_C
    yellow_humidity_margin_pct: float = YELLOW_HUMIDITY_MARGIN_PCT
    kind: str = "project_target_not_climate_normal"


class ClimateValue(BaseModel):
    season: Season
    temperature_c: float
    humidity_pct: float
    wind_m_s: float
    warm_days: int
    extreme_days: int
    wet_days: int
    days_in_season: int
    color: Literal["green", "yellow", "red"]
    temperature_deviation_c: float
    humidity_deviation_pct: float


class ClimateCell(ClimateValue):
    grid_lon: float
    grid_lat: float


class ClimateGridResponse(BaseModel):
    year: int = YEAR
    period_label: str = "данные за 2025 год"
    source: str = "NASA POWER Daily API"
    time_standard: str = "UTC"
    grid_resolution_degrees: dict[str, float] = Field(default_factory=lambda: {"latitude": 0.5, "longitude": 0.625})
    target: ClimateTarget
    cells: list[ClimateCell]


class ClimateRegion(BaseModel):
    territory_id: int
    region_code: str
    name: str
    grid_cells: int
    season: Season
    temperature_c: float | None
    humidity_pct: float | None
    wind_m_s: float | None
    average_warm_days: float | None
    average_extreme_days: float | None
    average_wet_days: float | None
    color: Literal["green", "yellow", "red"] | None
    temperature_deviation_c: float | None
    humidity_deviation_pct: float | None


class ClimateRegionsResponse(BaseModel):
    year: int = YEAR
    period_label: str = "данные за 2025 год"
    source: str = "NASA POWER Daily API"
    time_standard: str = "UTC"
    target: ClimateTarget
    regions: list[ClimateRegion]


class ClimateSettlementResponse(BaseModel):
    year: int = YEAR
    period_label: str = "данные за 2025 год"
    source: str = "NASA POWER Daily API"
    time_standard: str = "UTC"
    territory_id: int
    name: str
    region_code: str
    grid_lon: float
    grid_lat: float
    grid_distance_km: float
    warm_days: int
    extreme_days: int
    wet_days: int
    seasons: list[ClimateValue]
    personal_match: float | None = Field(description="0..1; null if desired_climate was not set")


def _target(season: str) -> ClimateTarget:
    lo, hi = TARGETS[season]
    hlo, hhi = HUMIDITY_TARGET
    return ClimateTarget(temperature_min_c=lo, temperature_max_c=hi,
                         humidity_min_pct=hlo, humidity_max_pct=hhi)


def _values(row: dict, season: str) -> dict:
    return {"season": season, **{k: row[k] for k in ("temperature_c", "humidity_pct", "wind_m_s",
             "warm_days", "extreme_days", "wet_days", "days_in_season")},
            **color_for(row["temperature_c"], row["humidity_pct"], season)}


def _service(request: Request) -> ClimateService:
    climate = get_container(request).climate
    try:
        climate.grid
        climate.settlements
    except (FileNotFoundError, ValueError) as exc:
        raise ServiceUnavailable("Данные за 2025 год недоступны", code="climate_data_unavailable") from exc
    return climate


async def _region_cells(session: AsyncSession, climate: ClimateService) -> tuple[list, dict[str, list[str]]]:
    regions = (await session.execute(text(
        "SELECT id, region_code, name FROM territory WHERE level = 'region' ORDER BY region_code"
    ))).all()
    points = [{"lon": season["winter"]["grid_lon"], "lat": season["winter"]["grid_lat"]}
              for season in climate.grid.values()]
    rows = await session.execute(text("""
        WITH p AS (
            SELECT lon, lat FROM jsonb_to_recordset(CAST(:points AS jsonb)) AS j(lon double precision, lat double precision)
        )
        SELECT t.region_code, p.lon, p.lat FROM territory t JOIN p
          ON ST_Intersects(t.geom, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326))
        WHERE t.level = 'region'
    """), {"points": json.dumps(points)})
    mapping: dict[str, list[str]] = defaultdict(list)
    for code, lon, lat in rows:
        mapping[code].append(f"{lon},{lat}")
    return regions, mapping


@router.get("/grid", response_model=ClimateGridResponse,
            summary="Сезонная сетка 2025 года для плавной окраски карты")
async def grid(
    request: Request, season: Season = "summer", region_code: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> ClimateGridResponse:
    climate = _service(request)
    keys = None
    if region_code:
        regions, by_region = await _region_cells(session, climate)
        known = {r.region_code for r in regions}
        code = region_code.upper()
        if code not in known:
            raise NotFoundError("Субъект не найден", code="region_not_found")
        keys = set(by_region[code])
    cells = [ClimateCell(grid_lon=row["grid_lon"], grid_lat=row["grid_lat"], **_values(row, season))
             for key, seasons in climate.grid.items() if keys is None or key in keys
             for row in [seasons[season]]]
    return ClimateGridResponse(target=_target(season), cells=cells)


@router.get("/regions", response_model=ClimateRegionsResponse,
            summary="Сводка по субъектам СКФО за выбранный сезон 2025 года")
async def regions(
    request: Request, season: Season = "summer", session: AsyncSession = Depends(get_session),
) -> ClimateRegionsResponse:
    climate = _service(request)
    region_rows, membership = await _region_cells(session, climate)
    out = []
    for r in region_rows:
        cells = [climate.grid[key][season] for key in set(membership[r.region_code])]
        if cells:
            mean_t = round(statistics.fmean(v["temperature_c"] for v in cells), 2)
            mean_h = round(statistics.fmean(v["humidity_pct"] for v in cells), 2)
            mean_w = round(statistics.fmean(v["wind_m_s"] for v in cells), 2)
            means = {"temperature_c": mean_t, "humidity_pct": mean_h, "wind_m_s": mean_w,
                     "average_warm_days": round(statistics.fmean(v["warm_days"] for v in cells), 1),
                     "average_extreme_days": round(statistics.fmean(v["extreme_days"] for v in cells), 1),
                     "average_wet_days": round(statistics.fmean(v["wet_days"] for v in cells), 1),
                     **color_for(mean_t, mean_h, season)}
        else:
            means = {k: None for k in ("temperature_c", "humidity_pct", "wind_m_s",
                     "average_warm_days", "average_extreme_days", "average_wet_days", "color",
                     "temperature_deviation_c", "humidity_deviation_pct")}
        out.append(ClimateRegion(territory_id=r.id, region_code=r.region_code, name=r.name,
                                 grid_cells=len(cells), season=season, **means))
    return ClimateRegionsResponse(target=_target(season), regions=out)


@router.get("/settlements/{territory_id}", response_model=ClimateSettlementResponse,
            summary="Паспорт условий 2025 года для населённого пункта")
async def settlement(
    territory_id: int, request: Request, profile: Profile = Depends(profile_query),
    session: AsyncSession = Depends(get_session),
) -> ClimateSettlementResponse:
    climate = _service(request)
    row = (await session.execute(text(
        "SELECT external_id, name, region_code, level FROM territory WHERE id = :id"
    ), {"id": territory_id})).first()
    if row is None or row.level != "settlement":
        raise NotFoundError("Населённый пункт не найден", code="settlement_not_found")
    data = climate.settlement(row.external_id)
    if data is None:
        raise ServiceUnavailable("Для населённого пункта нет данных за 2025 год", code="climate_settlement_missing")
    annual = data["winter"]
    return ClimateSettlementResponse(
        territory_id=territory_id, name=row.name, region_code=row.region_code,
        grid_lon=annual["grid_lon"], grid_lat=annual["grid_lat"],
        grid_distance_km=annual["grid_distance_km"], warm_days=annual["warm_days"],
        extreme_days=annual["extreme_days"], wet_days=annual["wet_days"],
        seasons=[ClimateValue(**_values(data[s], s)) for s in SEASONS],
        personal_match=climate.match(row.external_id, profile.filters.desired_climate or {}),
    )
