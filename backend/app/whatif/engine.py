"""What-if engine: scenario -> feature overrides -> standard scoring pipeline -> before/after.

Report section 7: "прогон сценария создаёт запись scenario_run, движок копирует затронутые
feature_value с поправками (или пересчитывает времена через OSRM), затем по обычному
конвейеру строится index_result с scenario_run_id и полным набором component".

Normalization stays fitted on the base data, so territories outside the scenario never
move; only changed rows are re-scored.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationFailed
from app.explain.texts import fmt_num, plural
from app.modelcfg import ModelConfig
from app.profiles.contract import Profile
from app.scoring.matrix import MODE_PREFERENCE, load_values_all_modes
from app.scoring.service import ScenarioOverlay, ScoringService
from app.scoring.snapshots import persist_snapshot, r1
from app.whatif import catalog as C
from app.whatif.geo import analytical_minutes, haversine_km
from app.whatif.osrm import OsrmClient, OsrmUnavailable

EPS = 1e-6
MIN_DELTA = 0.05
TERRITORY_FORMS = ("территория", "территории", "территорий")


@dataclass(frozen=True)
class Effect:
    type: str  # time | count | presence | area
    feature: str
    threshold_min: float = 0.0
    reach_mode: str | None = None  # force the mode used to test reachability (F09: walking)


KIND_EFFECTS: dict[str, list[Effect]] = {
    "hospital": [Effect("time", "F01"), Effect("count", "F03", 30)],
    "clinic": [Effect("time", "F02"), Effect("count", "F03", 30)],
    "fap": [Effect("time", "F02"), Effect("count", "F03", 30)],
    "ambulance": [Effect("time", "F04")],
    "kindergarten": [Effect("time", "F05")],
    "school": [Effect("time", "F06"), Effect("count", "F08", 15)],
    "college": [Effect("time", "F07")],
    "university": [Effect("time", "F07")],
    "railway_station": [Effect("time", "F15")],
    "cafe": [Effect("count", "F09", 15, reach_mode="foot")],
    "cinema": [Effect("presence", "F10", 30)],
    "community_centre": [Effect("presence", "F10", 30)],
    "sports": [Effect("count", "F11", 15)],
    "park": [Effect("area", "F12", 15)],
    "mall": [Effect("presence", "F13", 30)],
    "marketplace": [Effect("presence", "F13", 30)],
}
POI_CATEGORIES: dict[str, list[str]] = {
    "hospital": ["hospital"],
    "clinic": ["clinic"],
    "fap": ["clinic"],
    "ambulance": ["ambulance"],
}
CATEGORY_KIND = {"hospital": "hospital", "clinic": "clinic", "ambulance": "ambulance"}


@dataclass
class TerritoryInfo:
    id: int
    name: str
    level: str
    cluster_code: str | None
    parent_id: int | None
    region_code: str | None
    lon: float | None
    lat: float | None
    population: int | None
    is_scored: bool


class WorkingSet:
    """Base values (territory -> feature -> mode -> value) plus overrides made by handlers."""

    def __init__(self) -> None:
        self.base: dict[int, dict[str, dict[str, float]]] = {}
        self.over: dict[tuple[int, str, str], float] = {}

    def modes(self, tid: int, code: str) -> list[str]:
        return list(self.base.get(tid, {}).get(code, {}))

    def get(self, tid: int, code: str, mode: str) -> float | None:
        if (tid, code, mode) in self.over:
            return self.over[(tid, code, mode)]
        return self.base.get(tid, {}).get(code, {}).get(mode)

    def set(self, tid: int, code: str, mode: str, value: float) -> None:
        self.over[(tid, code, mode)] = float(value)

    def changes(self) -> list[dict[str, Any]]:
        out = []
        for (tid, code, mode), new in sorted(self.over.items()):
            old = self.base.get(tid, {}).get(code, {}).get(mode)
            if old is None or abs(new - old) <= EPS:
                continue
            out.append({"territory_id": tid, "feature_code": code, "mode": mode, "old": old, "new": new})
        return out


@dataclass
class WhatIfContext:
    session: AsyncSession
    settings: Settings
    osrm: OsrmClient
    feature_codes: list[str]
    method_pref: str = "auto"
    ws: WorkingSet = field(default_factory=WorkingSet)
    info: dict[int, TerritoryInfo] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    methods_used: set[str] = field(default_factory=set)
    plan: list[dict[str, Any]] | None = None

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    async def load(self, tids: list[int]) -> None:
        missing = [t for t in dict.fromkeys(tids) if t not in self.info]
        if not missing:
            return
        rows = await self.session.execute(
            text(
                """
                SELECT id, name, level, cluster_code, parent_id, region_code,
                       ST_X(centroid), ST_Y(centroid), population, is_scored
                FROM territory WHERE id = ANY(:ids)
                """
            ),
            {"ids": missing},
        )
        for r in rows:
            self.info[r[0]] = TerritoryInfo(*r)
        self.ws.base.update(await load_values_all_modes(self.session, missing, self.feature_codes))

    async def scored_within(self, lon: float, lat: float, radius_km: float) -> list[int]:
        rows = await self.session.execute(
            text(
                """
                SELECT id FROM territory
                WHERE is_scored AND centroid IS NOT NULL
                  AND ST_DWithin(centroid::geography,
                                 ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius_m)
                ORDER BY ST_Distance(centroid::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography)
                LIMIT :limit
                """
            ),
            {"lon": lon, "lat": lat, "radius_m": radius_km * 1000.0, "limit": self.settings.whatif_max_affected},
        )
        return [r[0] for r in rows]

    async def scope(self, territory_ids: list[int] | None, municipality_id: int | None) -> list[int]:
        out: list[int] = []
        if municipality_id is not None:
            level = (
                await self.session.execute(
                    text("SELECT level FROM territory WHERE id = :id"), {"id": municipality_id}
                )
            ).scalar_one_or_none()
            if level != "municipality":
                raise ValidationFailed(
                    "municipality_id должен указывать на муниципальный район/округ",
                    code="scenario_params_invalid",
                )
            rows = await self.session.execute(
                text("SELECT id FROM territory WHERE parent_id = :m AND is_scored ORDER BY id"),
                {"m": municipality_id},
            )
            out.extend(r[0] for r in rows)
        if territory_ids:
            rows = await self.session.execute(
                text("SELECT id FROM territory WHERE id = ANY(:ids) AND is_scored"), {"ids": territory_ids}
            )
            found = {r[0] for r in rows}
            unknown = sorted(set(territory_ids) - found)
            if unknown:
                self.warn(f"Пропущены территории вне расчёта индекса: {unknown[:10]}")
            out.extend(t for t in territory_ids if t in found)
        out = list(dict.fromkeys(out))
        if not out:
            raise ValidationFailed("Сценарий не затрагивает ни одной территории", code="scenario_empty_scope")
        return out[: self.settings.whatif_max_affected]

    async def minutes_from_point(self, tids: list[int], lon: float, lat: float, mode: str) -> dict[int, float | None]:
        pts = [(t, self.info[t]) for t in tids if self.info[t].lon is not None]
        floor = self.settings.whatif_min_access_min
        if self.method_pref != "analytical" and self.osrm.available(mode):
            try:
                mins = await self.osrm.minutes_to_point(mode, [(i.lon, i.lat) for _, i in pts], (lon, lat))
                self.methods_used.add("graph")
                return {t: (None if m is None else max(floor, m)) for (t, _), m in zip(pts, mins, strict=True)}
            except OsrmUnavailable as exc:
                self.warn(f"OSRM недоступен ({exc}) — использован аналитический метод")
        elif self.method_pref == "graph":
            self.warn("OSRM не настроен — использован аналитический метод")
        self.methods_used.add("analytical")
        speed = self.settings.whatif_car_speed_kmh if mode == "car" else self.settings.whatif_foot_speed_kmh
        return {
            t: max(
                floor,
                analytical_minutes(
                    haversine_km(i.lon, i.lat, lon, lat), speed_kmh=speed, detour=self.settings.whatif_detour_factor
                ),
            )
            for t, i in pts
        }

    async def find_poi(self, poi_id: int | None, lon: float | None, lat: float | None, categories: list[str]) -> dict:
        if poi_id is not None:
            row = (
                await self.session.execute(
                    text("SELECT id, category, ST_X(geom), ST_Y(geom), name FROM poi WHERE id = :id"),
                    {"id": poi_id},
                )
            ).first()
        else:
            row = (
                await self.session.execute(
                    text(
                        """
                        SELECT id, category, ST_X(geom), ST_Y(geom), name FROM poi
                        WHERE category = ANY(:cats)
                          AND ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, 3000)
                        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                        LIMIT 1
                        """
                    ),
                    {"cats": categories, "lon": lon, "lat": lat},
                )
            ).first()
        if row is None:
            raise ValidationFailed(
                "Объект не найден: укажите poi_id или точку не дальше 3 км от объекта "
                "(POI загружаются командой `python -m app.cli load-poi`)",
                code="poi_not_found",
            )
        return {"id": row[0], "category": row[1], "lon": row[2], "lat": row[3], "name": row[4]}


def _reach_mode(effect: Effect, value_mode: str) -> str:
    if effect.reach_mode:
        return effect.reach_mode
    return "car" if value_mode == "any" else value_mode


def _apply(effect: Effect, old: float, minutes: float, params: BaseModel) -> float:
    if effect.type == "time":
        return min(old, minutes)
    if minutes > effect.threshold_min:
        return old
    if effect.type == "count":
        return old + 1
    if effect.type == "presence":
        return 1.0 if old <= 1 else min(old, minutes)
    if effect.type == "area":
        return old + float(getattr(params, "park_area_ha", 0.0))
    return old


# --------------------------------------------------------------------------- handlers
async def _open_poi(ctx: WhatIfContext, p: Any) -> None:
    effects = KIND_EFFECTS[p.kind]
    tids = await ctx.scored_within(p.lon, p.lat, p.radius_km)
    if not tids:
        ctx.warn(f"В радиусе {fmt_num(p.radius_km)} км от точки нет территорий, участвующих в расчёте")
        return
    await ctx.load(tids)
    needed = {_reach_mode(e, m) for e in effects for t in tids for m in ctx.ws.modes(t, e.feature)}
    times = {m: await ctx.minutes_from_point(tids, p.lon, p.lat, m) for m in sorted(needed)}
    missing = 0
    for e in effects:
        for t in tids:
            modes = ctx.ws.modes(t, e.feature)
            if not modes:
                missing += 1
                continue
            for m in modes:
                minutes = times.get(_reach_mode(e, m), {}).get(t)
                old = ctx.ws.get(t, e.feature, m)
                if minutes is None or old is None:
                    continue
                new = _apply(e, old, minutes, p)
                if abs(new - old) > EPS:
                    ctx.ws.set(t, e.feature, m, new)
    if missing:
        ctx.warn(f"Нет исходных данных для {missing} пар «территория — признак»: они не пересчитаны")


async def _close_poi(ctx: WhatIfContext, p: C.W2Params) -> None:
    poi = await ctx.find_poi(p.poi_id, p.lon, p.lat, POI_CATEGORIES[p.kind])
    kind = CATEGORY_KIND.get(poi["category"], p.kind)
    cats = POI_CATEGORIES.get(kind, [poi["category"]])
    effects = KIND_EFFECTS[kind]
    rows = (
        await ctx.session.execute(
            text(
                """
                SELECT tt.territory_id, tt.mode, tt.seconds FROM travel_time tt
                JOIN territory t ON t.id = tt.territory_id AND t.is_scored
                WHERE tt.poi_id = :id
                """
            ),
            {"id": poi["id"]},
        )
    ).all()

    if rows:  # precomputed OSRM matrix (graph method)
        ctx.methods_used.add("graph")
        tids = sorted({r[0] for r in rows})
        await ctx.load(tids)
        alt_rows = await ctx.session.execute(
            text(
                """
                SELECT tt.territory_id, tt.mode, min(tt.seconds) FROM travel_time tt
                JOIN poi ON poi.id = tt.poi_id
                WHERE tt.territory_id = ANY(:tids) AND poi.category = ANY(:cats) AND poi.id <> :id
                GROUP BY tt.territory_id, tt.mode
                """
            ),
            {"tids": tids, "cats": cats, "id": poi["id"]},
        )
        alt = {(r[0], r[1]): float(r[2]) for r in alt_rows}
        no_alt = 0
        for tid, mode, closed_s in rows:
            next_s = alt.get((tid, mode))
            if next_s is not None and next_s < closed_s:
                continue  # the closed object was not the nearest one
            value_modes = [m for m in ("any", mode) if m == mode or (m == "any" and mode == "car")]
            for e in effects:
                for m in value_modes:
                    old = ctx.ws.get(tid, e.feature, m)
                    if old is None:
                        continue
                    if e.type == "time":
                        if next_s is None:
                            no_alt += 1
                            continue
                        ctx.ws.set(tid, e.feature, m, max(old, old * next_s / max(closed_s, 1.0)))
                    elif e.type == "count" and closed_s / 60.0 <= e.threshold_min:
                        ctx.ws.set(tid, e.feature, m, max(0.0, old - 1))
        if no_alt:
            ctx.warn(f"Для {no_alt} значений не найден альтернативный объект в матрице времени — они не пересчитаны")
        return

    # Analytical: distance ratio to the next nearest object of the same category.
    ctx.methods_used.add("analytical")
    cand = await ctx.session.execute(
        text(
            """
            SELECT t.id,
                   ST_Distance(t.centroid::geography, cp.geom::geography) / 1000.0 AS d_closed,
                   nxt.km AS d_next
            FROM territory t
            CROSS JOIN (SELECT geom FROM poi WHERE id = :id) cp
            LEFT JOIN LATERAL (
                SELECT ST_Distance(t.centroid::geography, p.geom::geography) / 1000.0 AS km
                FROM poi p WHERE p.category = ANY(:cats) AND p.id <> :id
                ORDER BY p.geom <-> t.centroid LIMIT 1
            ) nxt ON true
            WHERE t.is_scored AND ST_DWithin(t.centroid::geography, cp.geom::geography, :radius_m)
            """
        ),
        {"id": poi["id"], "cats": cats, "radius_m": p.radius_km * 1000.0},
    )
    cand_rows = cand.all()
    await ctx.load([r[0] for r in cand_rows])
    no_alt = 0
    for tid, d_closed, d_next in cand_rows:
        if d_next is None:
            no_alt += 1
            continue
        if d_closed > d_next:
            continue
        ratio = min(10.0, d_next / max(d_closed, 0.5))
        for e in effects:
            for m in ctx.ws.modes(tid, e.feature):
                old = ctx.ws.get(tid, e.feature, m)
                if old is None:
                    continue
                if e.type == "time":
                    ctx.ws.set(tid, e.feature, m, old * ratio)
                elif e.type == "count":
                    speed = ctx.settings.whatif_car_speed_kmh if _reach_mode(e, m) == "car" else ctx.settings.whatif_foot_speed_kmh
                    if analytical_minutes(d_closed, speed_kmh=speed, detour=ctx.settings.whatif_detour_factor) <= e.threshold_min:
                        ctx.ws.set(tid, e.feature, m, max(0.0, old - 1))
    if no_alt:
        ctx.warn(f"Для {no_alt} территорий нет альтернативного объекта той же категории — они не пересчитаны")


async def _scale(ctx: WhatIfContext, tids: list[int], codes: tuple[str, ...], factor: float) -> None:
    await ctx.load(tids)
    ctx.methods_used.add("analytical")
    for t in tids:
        for code in codes:
            for m in ctx.ws.modes(t, code):
                old = ctx.ws.get(t, code, m)
                if old is not None:
                    ctx.ws.set(t, code, m, old * factor)


async def _road_repair(ctx: WhatIfContext, p: C.W6Params) -> None:
    tids = await ctx.scope(p.territory_ids, p.municipality_id)
    await _scale(ctx, tids, ("F14", "F21"), 1.0 - p.percent / 100.0)


async def _bus_route(ctx: WhatIfContext, p: C.W7Params) -> None:
    tids = await ctx.scope(p.territory_ids, p.municipality_id)
    await ctx.load(tids)
    ctx.methods_used.add("analytical")
    for t in tids:
        for m in ctx.ws.modes(t, "F19"):
            old = ctx.ws.get(t, "F19", m)
            if old is not None and old > 0:
                ctx.ws.set(t, "F19", m, 0.0)
        for m in ctx.ws.modes(t, "F17"):
            old = ctx.ws.get(t, "F17", m)
            if old is not None and old > p.stop_access_min:
                ctx.ws.set(t, "F17", m, p.stop_access_min)


async def _airport(ctx: WhatIfContext, p: C.W8Params) -> None:
    if p.poi_id is not None:
        poi = await ctx.find_poi(p.poi_id, None, None, ["airport"])
        lon, lat = poi["lon"], poi["lat"]
    else:
        lon, lat = p.lon, p.lat
    tids = await ctx.scored_within(lon, lat, p.radius_km)  # type: ignore[arg-type]
    if not tids:
        ctx.warn("В радиусе аэропорта нет территорий, участвующих в расчёте")
        return
    await _scale(ctx, tids, ("F16",), 1.0 - p.percent / 100.0)


def _worst(ctx: WhatIfContext, tids: list[int], code: str) -> int | None:
    best: tuple[float, int] | None = None
    for t in tids:
        value = next((ctx.ws.get(t, code, m) for m in MODE_PREFERENCE["car"] if ctx.ws.get(t, code, m) is not None), None)
        if value is not None and (best is None or value > best[0]):
            best = (value, t)
    return best[1] if best else None


async def _package(ctx: WhatIfContext, p: C.W10Params) -> None:
    settlements = await ctx.scope(None, p.municipality_id)
    if p.steps:
        plan = [s.model_dump() for s in p.steps]
    else:
        await ctx.load(settlements)
        plan = []
        for code, kind, scen in (("F02", "fap", "W1"), ("F06", "school", "W3")):
            worst = _worst(ctx, settlements, code)
            if worst is None:
                ctx.warn(f"Нет данных для выбора места ({kind}) — шаг {scen} пропущен")
                continue
            info = ctx.info[worst]
            plan.append(
                {
                    "scenario_code": scen,
                    "params": {"lat": round(info.lat, 6), "lon": round(info.lon, 6), "kind": kind, "radius_km": 15},
                    "reason": f"худшая доступность в районе: «{info.name}»",
                }
            )
        plan.append({"scenario_code": "W7", "params": {"municipality_id": p.municipality_id}, "reason": "все сёла района"})
    for step in plan:
        sdef = C.CATALOG[step["scenario_code"]]
        params = _validate(sdef, step["params"])
        await HANDLERS[sdef.code](ctx, params)
    ctx.plan = plan


Handler = Callable[[WhatIfContext, Any], Awaitable[None]]
HANDLERS: dict[str, Handler] = {
    "W1": _open_poi,
    "W2": _close_poi,
    "W3": _open_poi,
    "W4": _open_poi,
    "W5": _open_poi,
    "W6": _road_repair,
    "W7": _bus_route,
    "W8": _airport,
    "W9": _open_poi,
    "W10": _package,
}


def _validate(sdef: C.ScenarioDef, params: dict[str, Any]) -> BaseModel:
    try:
        return sdef.params_model.model_validate(params or {})
    except ValidationError as exc:
        details = [{"loc": [str(x) for x in e["loc"]], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        raise ValidationFailed(
            f"Некорректные параметры сценария {sdef.code}", code="scenario_params_invalid", details=details
        ) from exc


# --------------------------------------------------------------------------- service
class WhatIfService:
    def __init__(self, cfg: ModelConfig, settings: Settings, scoring: ScoringService, osrm: OsrmClient) -> None:
        self.cfg = cfg
        self.settings = settings
        self.scoring = scoring
        self.osrm = osrm

    async def run(
        self,
        session: AsyncSession,
        *,
        scenario_code: str,
        params: dict[str, Any],
        profile: Profile,
        profile_id: str | None,
        method: str,
        model_version_id: int,
    ) -> dict[str, Any]:
        sdef = C.CATALOG.get(scenario_code.upper())
        if sdef is None:
            raise NotFoundError(f"Сценарий {scenario_code} не найден", code="scenario_not_found")
        validated = _validate(sdef, params)
        ctx = WhatIfContext(
            session=session,
            settings=self.settings,
            osrm=self.osrm,
            feature_codes=self.scoring.feature_codes,
            method_pref=method,
        )
        await HANDLERS[sdef.code](ctx, validated)
        changes = ctx.ws.changes()
        method_used = "+".join(sorted(ctx.methods_used)) or sdef.method
        revision = await self.scoring.revision.get(session)

        run_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO scenario_run (scenario_code, params, method_used, profile_hash, status,
                        overrides, affected_territory_ids, warnings, data_revision, model_version_id)
                    VALUES (:code, CAST(:params AS jsonb), :method, :phash, 'success', CAST(:over AS jsonb),
                        :affected, CAST(:warnings AS jsonb), :rev, :mv)
                    RETURNING id
                    """
                ),
                {
                    "code": sdef.code,
                    "params": json.dumps({**validated.model_dump(mode="json"), "method": method}, ensure_ascii=False),
                    "method": method_used,
                    "phash": profile.hash,
                    "over": json.dumps(changes),
                    "affected": sorted({c["territory_id"] for c in changes}),
                    "warnings": json.dumps(ctx.warnings, ensure_ascii=False),
                    "rev": revision,
                    "mv": model_version_id,
                },
            )
        ).scalar_one()

        evaluation = await self.evaluate(
            session,
            run_id=run_id,
            changes=changes,
            profile=profile,
            profile_id=profile_id,
            model_version_id=model_version_id,
            persist=True,
        )
        explanation = self._explain(sdef, evaluation)
        summary = {**evaluation["summary"], "explanation": explanation, "plan": ctx.plan}
        await session.execute(
            text("UPDATE scenario_run SET summary = CAST(:s AS jsonb) WHERE id = :id"),
            {"s": json.dumps(summary, ensure_ascii=False), "id": run_id},
        )
        return {
            "run_id": run_id,
            "scenario_code": sdef.code,
            "scenario_name": sdef.name,
            "method_used": method_used,
            "params": validated.model_dump(mode="json"),
            "plan": ctx.plan,
            **evaluation,
            "explanation": explanation,
            "warnings": ctx.warnings + evaluation.pop("warnings", []),
        }

    async def evaluate(
        self,
        session: AsyncSession,
        *,
        run_id: int,
        changes: list[dict[str, Any]],
        profile: Profile,
        profile_id: str | None,
        model_version_id: int,
        persist: bool,
    ) -> dict[str, Any]:
        overlay = ScenarioOverlay.from_rows(run_id, changes)
        affected = sorted(overlay.overrides)
        info: dict[int, tuple[str, str | None, str | None]] = {}
        if affected:
            rows = await session.execute(
                text("SELECT id, name, cluster_code, region_code FROM territory WHERE id = ANY(:ids)"),
                {"ids": affected},
            )
            info = {r[0]: (r[1], r[2], r[3]) for r in rows}

        by_cluster: dict[str, list[int]] = {}
        for tid in affected:
            cluster = info.get(tid, ("", None, None))[1]
            if cluster:
                by_cluster.setdefault(cluster, []).append(tid)

        results: list[dict[str, Any]] = []
        snapshots: list[tuple[Any, Any, Any, str]] = []
        for cluster, tids in sorted(by_cluster.items()):
            base = await self.scoring.score_cluster(session, cluster, profile)
            rows = {
                t: overlay.apply(t, base.matrix.values[t], base.matrix.provenance.get(t, {}), profile.mode)
                for t in tids
                if t in base.matrix.values
            }
            after = await self.scoring.score_rows(session, cluster, profile, rows)
            for t in rows:
                before_s, after_s = base.scores[t], after[t]
                results.append(
                    self._result_item(t, info[t], cluster, before_s, after_s, base.matrix.values[t], rows[t])
                )
                snapshots.append((before_s, after_s, base.matrix.provenance.get(t, {}), cluster))

        results.sort(key=lambda r: (-r["delta"], r["territory_id"]))
        winners = [self._brief(r) for r in results if r["delta"] > MIN_DELTA][:10]
        losers = [self._brief(r) for r in sorted(results, key=lambda r: r["delta"]) if r["delta"] < -MIN_DELTA][:10]

        warnings: list[str] = []
        if persist and snapshots:
            limit = self.settings.whatif_persist_limit
            ranked = sorted(snapshots, key=lambda s: -abs(s[1].total - s[0].total))
            if len(ranked) > limit:
                warnings.append(f"Снимки сохранены для {limit} территорий с наибольшим изменением из {len(ranked)}")
            for before_s, after_s, prov, cluster in ranked[:limit]:
                common = dict(
                    cfg=self.cfg, provenance=prov, cluster_code=cluster, mode=profile.mode,
                    profile_hash=profile.hash, profile_id=profile_id, model_version_id=model_version_id,
                    data_revision=(await self.scoring.revision.get(session)),
                )
                await persist_snapshot(session, score=before_s, scenario_run_id=None, **common)
                await persist_snapshot(session, score=after_s, scenario_run_id=run_id, **common)

        deltas = [r["delta"] for r in results]
        by_cluster_summary = {
            c: {
                "affected": len([r for r in results if r["cluster"] == c]),
                "avg_delta": round(statistics.fmean([r["delta"] for r in results if r["cluster"] == c]), 2),
            }
            for c in by_cluster
        }
        summary = {
            "affected": len(results),
            "avg_delta": round(statistics.fmean(deltas), 2) if deltas else 0.0,
            "max_delta": max(deltas) if deltas else 0.0,
            "min_delta": min(deltas) if deltas else 0.0,
            "by_cluster": by_cluster_summary,
        }
        return {
            "affected": len(results),
            "results": results,
            "winners": winners,
            "losers": losers,
            "summary": summary,
            "warnings": warnings,
        }

    def _result_item(self, tid, info, cluster, before_s, after_s, before_row, after_row) -> dict[str, Any]:  # noqa: ANN001
        delta = round(after_s.total - before_s.total, 2)
        subs = self.cfg.total_subindices
        delta_sub = {
            s: round((after_s.subindices.get(s) or 0.0) - (before_s.subindices.get(s) or 0.0), 2) for s in subs
        }
        contrib_before = {c.feature_code: c.contribution for c in before_s.components}
        changes = []
        for c in after_s.components:
            old, new = before_row.get(c.feature_code), after_row.get(c.feature_code)
            if old is None or new is None or abs(new - old) <= EPS:
                continue
            f = self.cfg.features[c.feature_code]
            changes.append(
                {
                    "feature_code": c.feature_code,
                    "label": f.name,
                    "unit": f.unit,
                    "before": round(old, 2),
                    "after": round(new, 2),
                    "delta_points": round(c.contribution - contrib_before.get(c.feature_code, 0.0), 2),
                    "text": f"{f.name}: {fmt_num(old)} → {fmt_num(new)} {f.unit}".strip(),
                }
            )
        changes.sort(key=lambda x: -abs(x["delta_points"]))
        return {
            "territory_id": tid,
            "name": info[0],
            "cluster": cluster,
            "region_code": info[2],
            "total_before": r1(before_s.total),
            "total_after": r1(after_s.total),
            "delta": delta,
            "delta_subindices": delta_sub,
            "changes": changes,
        }

    @staticmethod
    def _brief(r: dict[str, Any]) -> dict[str, Any]:
        return {"territory_id": r["territory_id"], "name": r["name"], "cluster": r["cluster"], "delta": r["delta"]}

    @staticmethod
    def _explain(sdef: C.ScenarioDef, ev: dict[str, Any]) -> str:
        n = ev["affected"]
        if n == 0:
            return f"Сценарий «{sdef.name}» не изменил индекс ни одной территории при заданных параметрах."
        top = ev["results"][0]
        worst = min(ev["results"], key=lambda r: r["delta"])
        text_ = (
            f"Сценарий «{sdef.name}»: затронуто {n} {plural(n, TERRITORY_FORMS)}; "
            f"среднее изменение индекса {fmt_num(ev['summary']['avg_delta'], 2, signed=True)}."
        )
        if top["delta"] > MIN_DELTA:
            reason = top["changes"][0]["text"] if top["changes"] else ""
            text_ += f" Больше всего выиграла «{top['name']}» ({fmt_num(top['delta'], 1, signed=True)})"
            text_ += f": {reason[:1].lower() + reason[1:]}." if reason else "."
        if worst["delta"] < -MIN_DELTA:
            reason = worst["changes"][0]["text"] if worst["changes"] else ""
            text_ += f" Больше всего потеряла «{worst['name']}» ({fmt_num(worst['delta'], 1, signed=True)})"
            text_ += f": {reason[:1].lower() + reason[1:]}." if reason else "."
        return text_


def stored_changes(overrides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [o for o in overrides if all(k in o for k in ("territory_id", "feature_code", "mode", "new"))]
