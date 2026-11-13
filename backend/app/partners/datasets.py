"""
Datasets of the /v1 data API, built with the same services as the web app (no second formula).

settlements     reference list: ids, names, district, subject, cluster, population, coordinates
features        feature values with unit, mode, date, source and flags; long (a row per value)
                or wide (a row per settlement, a column per feature)
index           index 0–100 and subindices under a profile, place in the cluster
ambulance       ambulance travel time, error band, 20-minute norm status, base (load-ambulance)
municipalities  ambulance coverage per district
scenario run    a what-if run: before and after for every changed value (own route)

Missing values stay empty. Feature values carry their source, so synthetic leftovers
(source 'synthetic-demo') never pass for real data; `meta.synthetic_demo_data` flags them too.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ambulance.texts import CATEGORY_LABEL
from app.core.errors import NotFoundError
from app.etl.names import normalize_search
from app.etl.demo import SYNTHETIC_SOURCE
from app.modelcfg import ModelConfig
from app.partners.tables import Column, Table
from app.profiles.contract import Profile
from app.scoring.matrix import load_values
from app.whatif.catalog import CATALOG
from app.whatif.engine import stored_changes

if TYPE_CHECKING:
    from app.container import Container

ATTRIBUTION = "Содержит данные © участники OpenStreetMap (лицензия ODbL 1.0) и рельеф SRTM (NASA); расчёт — Pulse Score"
ATTRIBUTION_ASCII = "Data (c) OpenStreetMap contributors (ODbL 1.0) and SRTM (NASA); computed by Pulse Score"

CLUSTER_WORDS = {"A": "А", "B": "Б", "V": "В"}
PLACE_WORDS = {"city": "город", "town": "город или посёлок", "village": "село", "hamlet": "хутор, деревня",
               "isolated_dwelling": "отдельное жильё"}
MODE_WORDS = {"car": "на машине", "foot": "пешком", "any": "любой"}
LEVEL_WORDS = {"municipality": "район", "region": "субъект"}
NORM_WORDS = {"met": "в нормативе", "borderline": "на границе норматива", "not_met": "дольше норматива"}

TID = Column("territory_id", "id территории", "int")
NAME = Column("name", "Населённый пункт")
MUNICIPALITY_ID = Column("municipality_id", "id района", "int")
MUNICIPALITY = Column("municipality", "Район")
REGION_CODE = Column("region_code", "Код субъекта")
REGION = Column("region", "Субъект")
CLUSTER = Column("cluster", "Кластер (А — города, Б — райцентры и посёлки, В — сёла)", words=CLUSTER_WORDS)
POPULATION = Column("population", "Население по OpenStreetMap, чел.", "int")


@dataclass
class Ctx:
    c: Container
    session: AsyncSession
    region: str | None = None
    municipality_id: int | None = None
    territory_id: int | None = None
    cluster: str | None = None
    profile: Profile | None = None
    mode: str = "car"
    layout: str = "long"


@dataclass(frozen=True)
class DatasetDef:
    name: str
    title: str
    description: str
    params: tuple[str, ...]
    columns: Callable[[ModelConfig], list[Column]]
    build: Callable[[Ctx], Awaitable[Table]]


# --- shared ------------------------------------------------------------------------------------
async def check_filters(ctx: Ctx) -> None:
    if ctx.region:
        found = (
            await ctx.session.execute(
                text("SELECT 1 FROM territory WHERE level = 'region' AND region_code = :r"), {"r": ctx.region}
            )
        ).first()
        if found is None:
            raise NotFoundError(f"Субъект {ctx.region} не найден", code="region_not_found")
    if ctx.municipality_id is not None:
        found = (
            await ctx.session.execute(
                text("SELECT 1 FROM territory WHERE level = 'municipality' AND id = :id"), {"id": ctx.municipality_id}
            )
        ).first()
        if found is None:
            raise NotFoundError("Район не найден", code="municipality_not_found")


async def base_meta(ctx: Ctx) -> dict[str, Any]:
    synthetic = (
        await ctx.session.execute(
            text("SELECT EXISTS(SELECT 1 FROM feature_value WHERE source = :s)"), {"s": SYNTHETIC_SOURCE}
        )
    ).scalar_one()
    return {
        "generated_at": dt.datetime.now(dt.UTC),
        "data_revision": await ctx.c.scoring.revision.get(ctx.session),
        "model_version": ctx.c.cfg.version,
        "synthetic_demo_data": bool(synthetic),
        "filters": {k: v for k, v in (("region", ctx.region), ("municipality_id", ctx.municipality_id),
                                      ("territory_id", ctx.territory_id), ("cluster", ctx.cluster)) if v is not None},
        "attribution": ATTRIBUTION,
    }


async def settlement_rows(ctx: Ctx) -> list[Any]:
    conds, params = ["t.level = 'settlement'", "t.is_scored"], {}
    if ctx.region:
        conds.append("t.region_code = :region")
        params["region"] = ctx.region
    if ctx.municipality_id is not None:
        conds.append("t.parent_id = :mid")
        params["mid"] = ctx.municipality_id
    if ctx.cluster:
        conds.append("t.cluster_code = :cluster")
        params["cluster"] = ctx.cluster
    if ctx.territory_id is not None:
        conds.append("t.id = :territory_id")
        params["territory_id"] = ctx.territory_id
    where = " AND ".join(conds)  # fixed fragments only; values are bound parameters
    rows = await ctx.session.execute(
        text(
            f"""
            SELECT t.id, t.external_id, t.name, t.kind, t.cluster_code, t.is_admin_center, t.parent_id,
                   m.name AS municipality, t.region_code, r.name AS region, t.population,
                   ST_X(t.centroid) AS lon, ST_Y(t.centroid) AS lat
            FROM territory t
            LEFT JOIN territory m ON m.id = t.parent_id
            LEFT JOIN territory r ON r.level = 'region' AND r.region_code = t.region_code
            WHERE {where}
            ORDER BY t.region_code, m.name, t.name, t.id
            """  # noqa: S608
        ),
        params,
    )
    return rows.all()


async def require_settlement(ctx: Ctx) -> Any:
    rows = await settlement_rows(ctx)
    if not rows:
        raise NotFoundError("Населённый пункт не найден для указанных фильтров", code="settlement_not_found")
    return rows[0]


async def search_settlements(ctx: Ctx, query: str, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    normalized = normalize_search(query)
    if not normalized:
        return 0, []
    conditions = ["t.level = 'settlement'", "t.is_scored",
                  "(strpos(t.name_normalized, :query) > 0 OR similarity(t.name_normalized, :query) > 0.35)"]
    params: dict[str, Any] = {"query": normalized, "limit": limit, "offset": offset}
    if ctx.region:
        conditions.append("t.region_code = :region")
        params["region"] = ctx.region
    if ctx.municipality_id is not None:
        conditions.append("t.parent_id = :municipality_id")
        params["municipality_id"] = ctx.municipality_id
    where = " AND ".join(conditions)
    total = (await ctx.session.execute(text(f"SELECT count(*) FROM territory t WHERE {where}"), params)).scalar_one()
    rows = (await ctx.session.execute(text(f"""
        SELECT t.id, t.name, t.kind, t.cluster_code, t.parent_id AS municipality_id,
               m.name AS municipality, t.region_code, r.name AS region, t.population
        FROM territory t
        LEFT JOIN territory m ON m.id = t.parent_id
        LEFT JOIN territory r ON r.level = 'region' AND r.region_code = t.region_code
        WHERE {where}
        ORDER BY (t.name_normalized = :query) DESC,
                 similarity(t.name_normalized, :query) DESC,
                 t.population DESC NULLS LAST, t.id
        LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    return total, [dict(row) for row in rows]


def _place(r: Any) -> dict[str, Any]:
    return {"territory_id": r.id, "name": r.name, "municipality_id": r.parent_id, "municipality": r.municipality,
            "region_code": r.region_code, "region": r.region, "cluster": r.cluster_code}


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


# --- settlements ----------------------------------------------------------------------------------
def settlement_columns(_: ModelConfig) -> list[Column]:
    return [TID, Column("external_id", "Код OpenStreetMap"), NAME, Column("kind", "Тип", words=PLACE_WORDS), CLUSTER,
            Column("is_admin_center", "Столица субъекта", "bool"), MUNICIPALITY_ID, MUNICIPALITY, REGION_CODE, REGION,
            POPULATION, Column("lon", "Долгота", "float"), Column("lat", "Широта", "float")]


async def build_settlements(ctx: Ctx) -> Table:
    rows = [
        {**_place(r), "external_id": r.external_id, "kind": r.kind, "is_admin_center": r.is_admin_center,
         "population": r.population, "lon": r.lon, "lat": r.lat}
        for r in await settlement_rows(ctx)
    ]
    return Table("settlements", "Населённые пункты", settlement_columns(ctx.c.cfg), rows, await base_meta(ctx))


# --- features -----------------------------------------------------------------------------------
def feature_columns(_: ModelConfig) -> list[Column]:
    return [TID, NAME, MUNICIPALITY_ID, REGION_CODE, Column("feature_code", "Код признака"),
            Column("feature", "Признак"), Column("subindex", "Субиндекс"), Column("unit", "Единица"),
            Column("value", "Значение", "float"),
            Column("value_mode", "Способ передвижения", words=MODE_WORDS),
            Column("inherited_from", "Значение района или субъекта", words=LEVEL_WORDS),
            Column("imputed", "Оценка вместо измерения", "bool"),
            Column("as_of", "Дата данных", "date"), Column("source", "Источник")]


def _feature_label(cfg: ModelConfig, code: str) -> str:
    f = cfg.features[code]
    return f"{f.name}, {f.unit}" if f.unit else f.name


async def build_features(ctx: Ctx) -> Table:
    cfg = ctx.c.cfg
    places = await settlement_rows(ctx)
    codes = list(cfg.features)
    values, provenance = await load_values(ctx.session, [r.id for r in places], codes, ctx.mode)
    meta = {**await base_meta(ctx), "mode": ctx.mode, "layout": ctx.layout}
    if ctx.layout == "wide":
        columns = [TID, NAME, MUNICIPALITY_ID, MUNICIPALITY, REGION_CODE, CLUSTER] + [
            Column(code, _feature_label(cfg, code), "float") for code in codes
        ]
        rows = [{**_place(r), **{code: values[r.id].get(code) for code in codes}} for r in places]
        return Table("features", "Признаки населённых пунктов", columns, rows, meta)
    rows = []
    for r in places:
        for code in codes:
            value = values[r.id].get(code)
            if value is None:
                continue  # no value: no row (the wide layout shows it as an empty cell)
            p = provenance[r.id][code]
            f = cfg.features[code]
            rows.append({
                "territory_id": r.id, "name": r.name, "municipality_id": r.parent_id, "region_code": r.region_code,
                "feature_code": code, "feature": f.name, "subindex": cfg.subindices[f.subindex].name, "unit": f.unit,
                "value": value, "value_mode": p.value_mode, "inherited_from": p.inherited_from,
                "imputed": p.upstream_imputed, "as_of": p.as_of, "source": p.source,
            })
    return Table("features", "Признаки населённых пунктов", feature_columns(cfg), rows, meta)


# --- index --------------------------------------------------------------------------------------
def index_columns(cfg: ModelConfig) -> list[Column]:
    return [TID, NAME, MUNICIPALITY_ID, MUNICIPALITY, REGION_CODE, CLUSTER, Column("profile", "Профиль"),
            Column("total", "Индекс, 0–100", "float"), Column("rank", "Место в кластере", "int"),
            Column("cluster_size", "Пунктов в кластере", "int")] + [
        Column(code, f"{cfg.subindices[code].name}, 0–100", "float") for code in cfg.total_subindices
    ] + [Column("attractiveness", "Привлекательность (отдельный бейдж), 0–100", "float"),
         Column("data_revision", "Ревизия данных", "int"), Column("model_version", "Версия модели")]


async def build_index(ctx: Ctx) -> Table:
    c, cfg = ctx.c, ctx.c.cfg
    profile = ctx.profile or c.profiles.default()
    places = await settlement_rows(ctx)
    scores: dict[str, Any] = {}
    for code in sorted({r.cluster_code for r in places if r.cluster_code}):
        scores[code] = await c.scoring.score_cluster(ctx.session, code, profile)
    ranks = {code: {tid: i + 1 for i, tid in enumerate(cs.ranking())} for code, cs in scores.items()}
    label = c.profiles.pristine_preset_id(profile) or "custom"
    rows = []
    for r in places:
        cs = scores.get(r.cluster_code)
        s = cs.scores.get(r.id) if cs else None
        if s is None:
            continue
        rows.append({
            **_place(r), "profile": label, "total": _r(s.total), "rank": ranks[r.cluster_code][r.id],
            "cluster_size": len(cs.scores), **{code: _r(s.subindices.get(code)) for code in cfg.total_subindices},
            "attractiveness": _r(s.attractiveness), "data_revision": cs.data_revision, "model_version": cfg.version,
        })
    meta = {**await base_meta(ctx), "profile": label, "mode": profile.mode,
            "weights": dict(profile.weights)}
    return Table("index", "Индекс доступности", index_columns(cfg), rows, meta)


# --- ambulance ------------------------------------------------------------------------------------
def ambulance_columns(_: ModelConfig) -> list[Column]:
    return [TID, NAME, MUNICIPALITY_ID, MUNICIPALITY, REGION_CODE, CLUSTER, POPULATION,
            Column("elevation_m", "Высота над уровнем моря, м", "float"),
            Column("minutes", "Время доезда скорой, мин", "float"),
            Column("minutes_low", "Нижняя граница, мин", "float"), Column("minutes_high", "Верхняя граница, мин", "float"),
            Column("norm_minutes", "Норматив, мин", "float"),
            Column("norm_status", "Норматив", words=NORM_WORDS),
            Column("base_name", "Ближайшая база"), Column("base_category", "Тип базы", words=CATEGORY_LABEL),
            Column("distance_km", "Путь по дорогам, км", "float"),
            Column("terrain_minutes", "Добавляет рельеф, мин", "float"),
            Column("lon", "Долгота", "float"), Column("lat", "Широта", "float")]


async def _ambulance_rows(ctx: Ctx) -> tuple[Any, list[Any]]:
    table = await ctx.c.ambulance.table(ctx.session)  # 503 ambulance_not_computed before load-ambulance
    rows = [
        r for r in table.rows.values()
        if (ctx.region is None or r.region_code == ctx.region)
        and (ctx.municipality_id is None or r.municipality_id == ctx.municipality_id)
        and (ctx.cluster is None or r.cluster == ctx.cluster)
        and (ctx.territory_id is None or r.territory_id == ctx.territory_id)
    ]
    rows.sort(key=lambda r: (r.region_code or "", r.municipality or "", r.name, r.territory_id))
    return table, rows


async def _ambulance_meta(ctx: Ctx, table: Any) -> dict[str, Any]:
    svc = ctx.c.ambulance
    return {**await base_meta(ctx), "ambulance_model_version": svc.cfg.version, "computed_at": table.computed_at,
            "stale": svc.stale(table), "norm_minutes": svc.cfg.norm_minutes,
            "dispatch_minutes": svc.cfg.dispatch_minutes}


async def build_ambulance(ctx: Ctx) -> Table:
    svc = ctx.c.ambulance
    table, rows = await _ambulance_rows(ctx)
    out = []
    for r in rows:
        item = svc.item(r)
        loc = item.pop("location") or {}
        item["municipality"] = item.pop("municipality_name")
        out.append({**item, "norm_minutes": svc.cfg.norm_minutes, "lon": loc.get("lon"), "lat": loc.get("lat")})
    return Table("ambulance", "Время доезда скорой помощи", ambulance_columns(ctx.c.cfg), out,
                 await _ambulance_meta(ctx, table))


# --- municipalities -------------------------------------------------------------------------------
def municipality_columns(_: ModelConfig) -> list[Column]:
    return [MUNICIPALITY_ID, Column("name", "Район"), REGION_CODE, REGION,
            Column("settlements", "Населённых пунктов", "int"), Column("estimated", "С оценкой доезда", "int"),
            Column("within_norm", "В нормативе 20 минут", "int"),
            Column("within_norm_share", "Доля в нормативе", "float"),
            Column("borderline", "На границе норматива", "int"), Column("not_met", "Дольше норматива", "int"),
            Column("median_minutes", "Медиана доезда, мин", "float"),
            Column("p90_minutes", "90-й процентиль, мин", "float"), Column("max_minutes", "Максимум, мин", "float"),
            Column("population_settlements", "Пунктов с известным населением", "int"),
            Column("population_known", "Их население, чел.", "int"),
            Column("population_within_norm", "Из них в нормативе, чел.", "int"),
            Column("population_within_norm_share", "Доля жителей в нормативе", "float"),
            Column("hospital_base_share", "Доля пунктов, где база — больница", "float")]


async def build_municipalities(ctx: Ctx) -> Table:
    svc = ctx.c.ambulance
    table, rows = await _ambulance_rows(ctx)
    groups: dict[int, list[Any]] = {}
    for r in rows:
        if r.municipality_id is not None:
            groups.setdefault(r.municipality_id, []).append(r)
    out = []
    for mid, members in groups.items():
        name, code = table.municipalities.get(mid, ("", None))
        st = svc.stats(members)
        within = st["within_norm"]
        out.append({
            "municipality_id": mid, "name": name, "region_code": code,
            "region": table.regions.get(code or "", (None, None))[1],
            "settlements": st["settlements"], "estimated": st["estimated"], "within_norm": within["settlements"],
            "within_norm_share": within["share"], "borderline": st["norm_status"]["borderline"],
            "not_met": st["norm_status"]["not_met"], "median_minutes": st["median_minutes"],
            "p90_minutes": st["p90_minutes"], "max_minutes": st["max_minutes"],
            "population_settlements": st["population_settlements"], "population_known": st["population_known"],
            "population_within_norm": within["population"], "population_within_norm_share": within["population_share"],
            "hospital_base_share": st["hospital_base_share"],
        })
    out.sort(key=lambda g: (g["region_code"] or "", g["name"]))
    return Table("municipalities", "Покрытие скорой помощью по районам", municipality_columns(ctx.c.cfg), out,
                 await _ambulance_meta(ctx, table))


# --- scenario run (route /v1/scenario-runs/{id}) ---------------------------------------------------
def scenario_columns(_: ModelConfig) -> list[Column]:
    return [Column("run_id", "Прогон", "int"), Column("scenario", "Сценарий"), TID, NAME, REGION_CODE, CLUSTER,
            Column("total_before", "Индекс до", "float"), Column("total_after", "Индекс после", "float"),
            Column("delta", "Изменение индекса", "float"), Column("feature_code", "Код признака"),
            Column("feature", "Признак"), Column("unit", "Единица"),
            Column("before", "Значение до", "float"), Column("after", "Значение после", "float")]


async def build_scenario_run(c: Container, session: AsyncSession, run_id: int, profile: Profile) -> Table:
    row = (
        await session.execute(
            text("SELECT scenario_code, params, overrides, warnings FROM scenario_run WHERE id = :id"), {"id": run_id}
        )
    ).first()
    if row is None:
        raise NotFoundError(f"Прогон сценария {run_id} не найден", code="scenario_run_not_found")
    evaluation = await c.whatif.evaluate(
        session, run_id=run_id, changes=stored_changes(row.overrides), profile=profile,
        profile_id=c.profiles.pristine_preset_id(profile), model_version_id=await c.model_version_id(), persist=False,
    )
    scenario = CATALOG[row.scenario_code].name
    rows = []
    for res in evaluation["results"]:
        common = {"run_id": run_id, "scenario": scenario, "territory_id": res["territory_id"], "name": res["name"],
                  "region_code": res["region_code"], "cluster": res["cluster"], "total_before": res["total_before"],
                  "total_after": res["total_after"], "delta": res["delta"]}
        for ch in res["changes"]:
            rows.append({**common, "feature_code": ch["feature_code"], "feature": ch["label"], "unit": ch["unit"],
                         "before": ch["before"], "after": ch["after"]})
    meta = {**await base_meta(Ctx(c, session)), "run_id": run_id, "scenario": scenario,
            "params": {k: v for k, v in (row.params or {}).items() if k != "method"},
            "warnings": list(row.warnings or []) + list(evaluation.get("warnings", [])),
            "profile": c.profiles.pristine_preset_id(profile) or "custom"}
    return Table("scenario_run", "Результат сценария «что если»", scenario_columns(c.cfg), rows, meta)


DATASETS: dict[str, DatasetDef] = {
    d.name: d
    for d in (
        DatasetDef("settlements", "Населённые пункты",
                   "Справочник 3 403 населённых пунктов: id, код OpenStreetMap, район, субъект, кластер, население, координаты.",
                   ("region", "municipality_id", "territory_id", "cluster"), settlement_columns, build_settlements),
        DatasetDef("features", "Признаки населённых пунктов",
                   "Значения признаков с единицей, датой и источником. layout=long — строка на значение, "
                   "layout=wide — строка на пункт, столбец на признак. Пустое значение — нет данных.",
                   ("region", "municipality_id", "territory_id", "cluster", "mode", "layout"), feature_columns, build_features),
        DatasetDef("index", "Индекс доступности",
                   "Индекс 0–100 и субиндексы под профиль (profile=family и др. или p=…), место в кластере.",
                   ("region", "municipality_id", "territory_id", "cluster", "profile", "p"), index_columns, build_index),
        DatasetDef("ambulance", "Время доезда скорой помощи",
                   "Время доезда до каждого пункта: оценка, диапазон погрешности, норматив 20 минут, ближайшая база.",
                   ("region", "municipality_id", "territory_id", "cluster"), ambulance_columns, build_ambulance),
        DatasetDef("municipalities", "Покрытие скорой помощью по районам",
                   "Для каждого района: сколько пунктов и жителей скорая достигает за 20 минут, медиана и максимум.",
                   ("region", "municipality_id"), municipality_columns, build_municipalities),
    )
}
