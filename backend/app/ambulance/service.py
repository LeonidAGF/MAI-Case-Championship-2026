"""
Ambulance travel times for the API (/ambulance/*): rows of ambulance_time, cached per run.

Reporting rules (config/ambulance.default.yaml, applied here, not stored):
  estimate     dispatch_minutes + travel time
  error band   travel × (1 − minus) … travel × (1 + plus), at least ± min_minutes
  norm status  met: even the upper bound fits the norm; not_met: even the lower bound exceeds it;
               borderline: the band contains the norm
Shares of residents use settlements with known population only (OSM knows it for about half
of them) and always say how many settlements and people that is.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ambulance import texts
from app.ambulance.config import AmbulanceConfig
from app.core.errors import NotFoundError, ServiceUnavailable, ValidationFailed

KIND = "ambulance"
RUN_TTL_S = 3.0
FAR_ACCESS_M = 500
SOURCES = [
    "Дорожный граф ML-части по OpenStreetMap (graph/*.parquet): типы дорог, теги maxspeed, мосты и тоннели",
    "Станции скорой помощи и больницы из OpenStreetMap (© участники OpenStreetMap, ODbL 1.0)",
    "Рельеф SRTM (NASA): плитки 3″ (≈90 м), усреднённые из SRTM 1″ зеркала AWS Terrain Tiles — высота и уклон дорог",
]


def _r(x: float | None, digits: int = 1) -> float | None:
    return None if x is None else round(float(x), digits)


@dataclass
class Row:
    territory_id: int
    name: str
    region_code: str | None
    municipality_id: int | None
    municipality: str | None
    cluster: str | None
    population: int | None
    lon: float | None
    lat: float | None
    travel: float | None
    flat: float | None
    distance_km: float | None
    elevation_m: float | None
    details: dict[str, Any]


@dataclass
class Table:
    run_id: int
    computed_at: dt.datetime | None
    stats: dict[str, Any]
    rows: dict[int, Row]
    regions: dict[str, tuple[int, str]]  # region code -> (territory id, name)
    municipalities: dict[int, tuple[str, str | None]] = field(default_factory=dict)  # id -> (name, region)


class AmbulanceService:
    def __init__(self, cfg: AmbulanceConfig) -> None:
        self.cfg = cfg
        self._table: Table | None = None
        self._checked_at = 0.0
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._table = None
        self._checked_at = 0.0

    # --- data -----------------------------------------------------------------------------
    async def _latest_run(self, session: AsyncSession) -> Any:
        return (
            await session.execute(
                text(
                    "SELECT id, finished_at, stats_json FROM etl_run WHERE kind = :k AND status = 'success' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"k": KIND},
            )
        ).first()

    async def table(self, session: AsyncSession) -> Table:
        if self._table is not None and time.monotonic() - self._checked_at < RUN_TTL_S:
            return self._table
        async with self._lock:
            run = await self._latest_run(session)
            self._checked_at = time.monotonic()
            if run is None:
                raise ServiceUnavailable(
                    "Время доезда скорой ещё не рассчитано: выполните python -m app.cli load-ambulance",
                    code="ambulance_not_computed",
                )
            if self._table is None or self._table.run_id != run.id:
                self._table = await self._load(session, run)
            return self._table

    async def _load(self, session: AsyncSession, run: Any) -> Table:
        rows = await session.execute(
            text(
                """
                SELECT a.territory_id, a.minutes, a.flat_minutes, a.distance_km, a.elevation_m, a.details,
                       t.name, t.region_code, t.cluster_code, t.population, t.parent_id, m.name AS municipality,
                       ST_X(t.centroid) AS lon, ST_Y(t.centroid) AS lat
                FROM ambulance_time a
                JOIN territory t ON t.id = a.territory_id
                LEFT JOIN territory m ON m.id = t.parent_id
                WHERE t.is_scored
                """
            )
        )
        table_rows = {
            r.territory_id: Row(
                r.territory_id, r.name, r.region_code, r.parent_id, r.municipality, r.cluster_code,
                int(r.population) if r.population else None, r.lon, r.lat, r.minutes, r.flat_minutes,
                r.distance_km, r.elevation_m, dict(r.details or {}),
            )
            for r in rows
        }
        regions = {
            r.region_code: (r.id, r.name)
            for r in await session.execute(text("SELECT id, region_code, name FROM territory WHERE level = 'region'"))
        }
        municipalities = {
            r.id: (r.name, r.region_code)
            for r in await session.execute(
                text("SELECT id, name, region_code FROM territory WHERE level = 'municipality'")
            )
        }
        return Table(run.id, run.finished_at, dict(run.stats_json or {}), table_rows, regions, municipalities)

    def stale(self, table: Table | None) -> bool:
        return table is not None and table.stats.get("model_hash") != self.cfg.model_hash

    # --- reporting rules ------------------------------------------------------------------
    def estimate(self, travel: float) -> dict[str, Any]:
        u, norm, dispatch = self.cfg.uncertainty, self.cfg.norm_minutes, self.cfg.dispatch_minutes
        down = min(travel, max(travel * u.minus, u.min_minutes))
        up = max(travel * u.plus, u.min_minutes)
        minutes, low, high = dispatch + travel, dispatch + travel - down, dispatch + travel + up
        status = "met" if high <= norm else ("not_met" if low > norm else "borderline")
        return {
            "minutes": _r(minutes), "minutes_low": _r(low), "minutes_high": _r(high), "travel_minutes": _r(travel),
            "dispatch_minutes": dispatch, "norm_minutes": norm, "norm_status": status,
        }

    def total(self, row: Row) -> float | None:
        return None if row.travel is None else self.cfg.dispatch_minutes + row.travel

    # --- one settlement --------------------------------------------------------------------
    async def settlement(self, session: AsyncSession, territory_id: int) -> dict[str, Any]:
        table = await self.table(session)
        row = table.rows.get(territory_id)
        if row is None:
            t = (
                await session.execute(
                    text("SELECT name, level, is_scored FROM territory WHERE id = :id"), {"id": territory_id}
                )
            ).first()
            if t is None:
                raise NotFoundError("Территория не найдена", code="territory_not_found")
            if t.level != "settlement":
                raise ValidationFailed(
                    f"«{t.name}» — не населённый пункт: время доезда считается для населённых пунктов, "
                    "для районов и субъектов используйте /ambulance/summary",
                    code="territory_not_settlement",
                )
            raise NotFoundError(
                f"Для «{t.name}» нет расчёта времени доезда: пересчитайте его (python -m app.cli load-ambulance)",
                code="ambulance_not_computed_for_territory",
            )
        return self._settlement_out(table, row)

    def _settlement_out(self, table: Table, row: Row) -> dict[str, Any]:
        cfg = self.cfg
        d = row.details
        stale = self.stale(table)
        region = table.regions.get(row.region_code or "")
        out: dict[str, Any] = {
            "territory_id": row.territory_id, "name": row.name, "region_code": row.region_code,
            "region_name": region[1] if region else None, "municipality_id": row.municipality_id,
            "municipality_name": row.municipality, "cluster": row.cluster, "population": row.population,
            "location": {"lon": row.lon, "lat": row.lat} if row.lon is not None else None,
            "elevation_m": row.elevation_m, "model_version": cfg.version, "computed_at": table.computed_at,
            "stale": stale, "flags": [], "notes": [],
        }
        if row.travel is None:
            out.update(available=False, estimate=None, base=None, route=None, reason=d.get("reason") or "unreachable",
                       text=texts.UNAVAILABLE_TEXT.format(km=texts.fmt_num(cfg.access.max_km)))
            if stale:
                out["flags"].append("stale")
                out["notes"].append(texts.STALE_NOTE)
            return out

        est = self.estimate(row.travel)
        base = d.get("base") or {}
        route = d.get("route") or {}
        flat = row.flat if row.flat is not None else row.travel
        terrain_min = max(0.0, row.travel - flat)
        access_m = int(d.get("access_m") or 0)
        out.update(
            available=True, estimate=est, reason=None,
            base={
                "external_id": base.get("external_id", ""), "name": base.get("name"), "category": base.get("category", ""),
                "category_label": texts.category_label(base.get("category", "")),
                "location": {"lon": base.get("lon"), "lat": base.get("lat")}, "access_m": int(base.get("access_m") or 0),
            },
            route={
                "distance_km": _r(row.distance_km, 2), "flat_minutes": _r(flat + cfg.dispatch_minutes),
                "terrain_minutes": _r(terrain_min), "climb_m": route.get("climb_m"), "descent_m": route.get("descent_m"),
                "max_height_m": route.get("max_height_m"), "steep_km": route.get("steep_km"),
                "urban_km": route.get("urban_km", 0.0), "osm_speed_km": route.get("tagged_km", 0.0),
                "no_terrain_km": route.get("no_dem_km", 0.0), "access_m": access_m,
            },
        )
        parts = [
            texts.estimate_text(est["minutes"], est["minutes_low"], est["minutes_high"], est["norm_status"], cfg.norm_minutes),
            texts.base_text(base.get("category", ""), base.get("name"), row.distance_km or 0.0),
        ]
        terrain = texts.terrain_text(terrain_min, route.get("climb_m"), route.get("steep_km"), route.get("max_height_m"),
                                     cfg.slope.steep_grade)
        if terrain:
            parts.append(terrain)
        out["text"] = " ".join(parts)
        notes, flags = out["notes"], out["flags"]
        if est["norm_status"] != "met":
            notes.append(texts.MAP_GAP_NOTE)
        if base.get("category") == "hospital":
            flags.append("hospital_base")
            notes.append(texts.HOSPITAL_NOTE)
        if access_m > FAR_ACCESS_M:
            flags.append("far_from_roads")
            notes.append(texts.far_access_note(access_m))
        if not d.get("terrain") or (row.distance_km and route.get("no_dem_km", 0) > 0.5 * row.distance_km):
            flags.append("no_terrain")
            notes.append(texts.NO_TERRAIN_NOTE)
        if stale:
            flags.append("stale")
            notes.append(texts.STALE_NOTE)
        notes.append(texts.dispatch_note(cfg.dispatch_minutes))
        notes.append(texts.accuracy_note(cfg.uncertainty.minus, cfg.uncertainty.plus))
        notes.append(texts.norm_note(cfg.norm_minutes))
        return out

    # --- lists and summaries ------------------------------------------------------------------
    def item(self, row: Row) -> dict[str, Any]:
        est = self.estimate(row.travel) if row.travel is not None else None
        base = row.details.get("base") or {}
        flat = row.flat if row.flat is not None else row.travel
        return {
            "territory_id": row.territory_id, "name": row.name, "region_code": row.region_code,
            "municipality_id": row.municipality_id, "municipality_name": row.municipality, "cluster": row.cluster,
            "population": row.population,
            "location": {"lon": row.lon, "lat": row.lat} if row.lon is not None else None,
            "elevation_m": row.elevation_m,
            "minutes": est["minutes"] if est else None,
            "minutes_low": est["minutes_low"] if est else None,
            "minutes_high": est["minutes_high"] if est else None,
            "norm_status": est["norm_status"] if est else None,
            "distance_km": _r(row.distance_km, 2),
            "terrain_minutes": _r(max(0.0, row.travel - flat)) if row.travel is not None and flat is not None else None,
            "base_name": base.get("name") if est else None,
            "base_category": base.get("category") if est else None,
        }

    def _select(self, table: Table, *, region: str | None, municipality_id: int | None,
                cluster: str | None = None) -> list[Row]:
        if region and region not in table.regions:
            raise NotFoundError(f"Субъект {region} не найден", code="region_not_found")
        if municipality_id is not None and municipality_id not in table.municipalities:
            raise NotFoundError("Район не найден", code="municipality_not_found")
        return [
            r for r in table.rows.values()
            if (region is None or r.region_code == region)
            and (municipality_id is None or r.municipality_id == municipality_id)
            and (cluster is None or r.cluster == cluster)
        ]

    async def settlements(
        self, session: AsyncSession, *, region: str | None, municipality_id: int | None, cluster: str | None,
        status: str | None, sort: str, order: str, limit: int, offset: int,
    ) -> dict[str, Any]:
        table = await self.table(session)
        rows = self._select(table, region=region, municipality_id=municipality_id, cluster=cluster)
        items = [self.item(r) for r in rows]
        if status == "none":
            items = [i for i in items if i["minutes"] is None]
        elif status:
            items = [i for i in items if i["norm_status"] == status]
        known = [i for i in items if i[self._sort_key(sort)] is not None]
        unknown = [i for i in items if i[self._sort_key(sort)] is None]
        key = self._sort_key(sort)
        known.sort(key=lambda i: (i[key], -i["territory_id"]) if key != "name" else (i["name"], i["territory_id"]),
                   reverse=(order == "desc"))
        unknown.sort(key=lambda i: i["territory_id"])
        ordered = known + unknown  # no estimate (or no value to sort by) always last
        return {
            "items": ordered[offset : offset + limit], "total": len(ordered), "limit": limit, "offset": offset,
            "norm_minutes": self.cfg.norm_minutes, "computed_at": table.computed_at, "stale": self.stale(table),
        }

    @staticmethod
    def _sort_key(sort: str) -> str:
        return {"minutes": "minutes", "name": "name", "population": "population", "elevation": "elevation_m",
                "distance": "distance_km"}[sort]

    def stats(self, rows: list[Row]) -> dict[str, Any]:
        est = [r for r in rows if r.travel is not None]
        minutes = np.array([self.total(r) for r in est], dtype=np.float64)
        with_pop = [r for r in est if r.population and r.population > 0]
        pop_total = sum(r.population or 0 for r in with_pop)

        def reach(limit: float) -> dict[str, Any]:
            hit = [r for r in est if (self.total(r) or 0) <= limit + 1e-9]
            people = sum(r.population or 0 for r in hit if r.population and r.population > 0)
            return {
                "minutes": limit, "settlements": len(hit),
                "share": round(len(hit) / len(est), 4) if est else None,
                "population": people, "population_share": round(people / pop_total, 4) if pop_total else None,
            }

        status = {"met": 0, "borderline": 0, "not_met": 0}
        for r in est:
            status[self.estimate(r.travel)["norm_status"]] += 1
        hospital = sum(1 for r in est if (r.details.get("base") or {}).get("category") == "hospital")
        return {
            "settlements": len(rows), "estimated": len(est), "without_estimate": len(rows) - len(est),
            "median_minutes": _r(float(np.median(minutes))) if minutes.size else None,
            "p90_minutes": _r(float(np.quantile(minutes, 0.9))) if minutes.size else None,
            "max_minutes": _r(float(minutes.max())) if minutes.size else None,
            "within_norm": reach(self.cfg.norm_minutes),
            "thresholds": [reach(t) for t in self.cfg.thresholds],
            "norm_status": status,
            "population_settlements": len(with_pop), "population_known": pop_total,
            "hospital_base_share": round(hospital / len(est), 4) if est else None,
        }

    async def summary(
        self, session: AsyncSession, *, region: str | None, municipality_id: int | None,
        group_by: str | None, slowest: int,
    ) -> dict[str, Any]:
        table = await self.table(session)
        rows = self._select(table, region=region, municipality_id=municipality_id)
        if municipality_id is not None:
            name, _ = table.municipalities[municipality_id]
            scope = {"territory_id": municipality_id, "name": name, "level": "municipality"}
        elif region:
            rid, name = table.regions[region]
            scope = {"territory_id": rid, "name": name, "level": "region"}
        else:
            scope = {"territory_id": None, "name": "СКФО", "level": "federal_district"}
        groups: list[dict[str, Any]] = []
        if group_by:
            buckets: dict[Any, list[Row]] = {}
            for r in rows:
                buckets.setdefault(r.region_code if group_by == "region" else r.municipality_id, []).append(r)
            for key, members in buckets.items():
                if key is None:
                    continue
                if group_by == "region":
                    tid, name = table.regions.get(key, (None, key))
                    groups.append({"territory_id": tid, "code": key, "name": name, "level": "region",
                                   "stats": self.stats(members)})
                else:
                    name, code = table.municipalities.get(key, ("", None))
                    groups.append({"territory_id": key, "code": code, "name": name, "level": "municipality",
                                   "stats": self.stats(members)})
            groups.sort(key=lambda g: (g["stats"]["within_norm"]["share"] is None,
                                       g["stats"]["within_norm"]["share"] or 0.0, g["name"]))
        stats = self.stats(rows)
        slow = sorted((r for r in rows if r.travel is not None), key=lambda r: (-(r.travel or 0), r.territory_id))
        return {
            "scope": scope, "norm_minutes": self.cfg.norm_minutes, "dispatch_minutes": self.cfg.dispatch_minutes,
            "stats": stats, "groups": groups, "slowest": [self.item(r) for r in slow[:slowest]],
            "text": texts.summary_text(scope["name"], stats, self.cfg.norm_minutes),
            "model_version": self.cfg.version, "computed_at": table.computed_at, "stale": self.stale(table),
        }

    # --- methodology ------------------------------------------------------------------------
    async def method(self, session: AsyncSession) -> dict[str, Any]:
        cfg = self.cfg
        try:
            table: Table | None = await self.table(session)
        except ServiceUnavailable:
            table = None
        run = table.stats if table else {}
        speeds = [
            {"highway": h, "label": texts.HIGHWAY_LABEL.get(h, h), "urban_kmh": cfg.speeds.kmh(h, urban=True),
             "rural_kmh": cfg.speeds.kmh(h, urban=False)}
            for h in cfg.speeds.by_type
        ]
        return {
            "model_version": cfg.version, "computed_at": table.computed_at if table else None,
            "run_id": table.run_id if table else None, "stale": self.stale(table),
            "norm_minutes": cfg.norm_minutes, "thresholds": cfg.thresholds, "dispatch_minutes": cfg.dispatch_minutes,
            "uncertainty": cfg.uncertainty.model_dump(), "speeds": speeds, "default_kmh": cfg.speeds.default,
            "maxspeed_tags": cfg.speeds.maxspeed_tags, "urban": cfg.urban.model_dump(),
            "slope": cfg.slope.model_dump(), "height": cfg.height.model_dump(), "access": cfg.access.model_dump(),
            "base_categories": cfg.bases.categories, "sources": SOURCES,
            "run": {k: run[k] for k in ("osm_base", "graph", "bases", "speeds", "terrain", "settlements", "minutes")
                    if k in run},
            "description": [
                "Скорость на каждом участке дороги — максимальная разрешённая: тег maxspeed из OpenStreetMap, "
                "а без него — предел для типа дороги (60 км/ч в населённых пунктах, 90 вне их, 20 в жилых зонах).",
                "Населённые пункты определяются по плотности жилых улиц, поэтому трасса внутри села считается "
                "городской.",
                "Скорость уменьшается на подъёмах и спусках (уклон по рельефу SRTM вдоль направления движения) "
                "и на высоте в горах; мосты и тоннели считаются ровными.",
                "Время считается от базы, которая доедет быстрее всех: станции скорой помощи или больницы.",
                texts.accuracy_note(cfg.uncertainty.minus, cfg.uncertainty.plus),
                texts.norm_note(cfg.norm_minutes),
            ],
        }
