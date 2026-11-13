from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, normalize_cluster, profile_query, rate_limit
from app.api.schemas import Breadcrumb, DataCoverage, Point, TerritoryBrief, TerritoryDetail, TerritoryList
from app.core.errors import ERROR_RESPONSES, NotFoundError, ValidationFailed
from app.etl.names import normalize_search
from app.profiles.contract import Profile
from app.scoring.matrix import load_values

router = APIRouter(tags=["territories"], dependencies=[rate_limit("default", "rate_limit_per_minute")])

Level = Literal["region", "municipality", "settlement"]
BRIEF_COLUMNS = """
    t.id, t.external_id, t.name, t.level, t.kind, t.region_code, t.parent_id, t.cluster_code,
    t.population, t.is_admin_center, t.is_scored, ST_X(t.centroid) AS lon, ST_Y(t.centroid) AS lat
"""
DEFAULT_TOLERANCE = {"region": 0.005, "municipality": 0.002, "settlement": 0.0}


def _brief(r: Any) -> TerritoryBrief:
    return TerritoryBrief(
        id=r.id,
        external_id=r.external_id,
        name=r.name,
        level=r.level,
        kind=r.kind,
        region_code=r.region_code,
        parent_id=r.parent_id,
        cluster=r.cluster_code,
        population=r.population,
        is_admin_center=r.is_admin_center,
        is_scored=r.is_scored,
        centroid=Point(lon=r.lon, lat=r.lat) if r.lon is not None else None,
    )


def _parse_bbox(bbox: str | None) -> dict[str, float] | None:
    if not bbox:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox.split(","))
    except ValueError:
        raise ValidationFailed("bbox: ожидается minLon,minLat,maxLon,maxLat", code="bbox_invalid") from None
    if x1 >= x2 or y1 >= y2:
        raise ValidationFailed("bbox: min должен быть меньше max", code="bbox_invalid")
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _filters(
    *,
    level: str | None,
    cluster: str | None,
    region: str | None,
    parent_id: int | None,
    scored_only: bool,
    bbox: dict[str, float] | None,
    q: str | None,
) -> tuple[str, dict[str, Any]]:
    conds, params = ["true"], {}
    if level:
        conds.append("t.level = :level")
        params["level"] = level
    if cluster:
        conds.append("t.cluster_code = :cluster")
        params["cluster"] = cluster
    if region:
        conds.append("t.region_code = :region")
        params["region"] = region.upper()
    if parent_id is not None:
        conds.append("t.parent_id = :parent_id")
        params["parent_id"] = parent_id
    if scored_only:
        conds.append("t.is_scored")
    if bbox:
        conds.append("ST_Intersects(coalesce(t.geom, t.centroid), ST_MakeEnvelope(:x1, :y1, :x2, :y2, 4326))")
        params.update(bbox)
    if q:
        conds.append("(t.name_normalized LIKE :q_like OR similarity(t.name_normalized, :q) > 0.35)")
        params["q"] = normalize_search(q)
        params["q_like"] = f"%{params['q']}%"
    return " AND ".join(conds), params


@router.get("/territories", response_model=TerritoryList, summary="Список территорий для карты, поиска и фильтров")
async def list_territories(
    request: Request,
    level: Level | None = None,
    cluster: str | None = Query(None, description="A | B | V (или А/Б/В)"),
    region: str | None = Query(None, description="Код субъекта ISO 3166-2, напр. RU-DA"),
    parent_id: int | None = None,
    q: str | None = Query(None, min_length=2, max_length=100, description="Поиск по названию"),
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    scored_only: bool = False,
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> TerritoryList:
    c = get_container(request)
    limit = min(limit, c.settings.max_page_size)
    where, params = _filters(
        level=level, cluster=normalize_cluster(cluster), region=region, parent_id=parent_id,
        scored_only=scored_only, bbox=_parse_bbox(bbox), q=q,
    )
    order = (
        "similarity(t.name_normalized, :q) DESC, t.population DESC NULLS LAST, t.id"
        if q
        else "CASE t.level WHEN 'region' THEN 0 WHEN 'municipality' THEN 1 ELSE 2 END, "
        "t.population DESC NULLS LAST, t.name, t.id"
    )
    total = (await session.execute(text(f"SELECT count(*) FROM territory t WHERE {where}"), params)).scalar_one()
    rows = await session.execute(
        text(f"SELECT {BRIEF_COLUMNS} FROM territory t WHERE {where} ORDER BY {order} LIMIT :limit OFFSET :offset"),
        {**params, "limit": limit, "offset": offset},
    )
    return TerritoryList(items=[_brief(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get(
    "/territories/geojson",
    summary="GeoJSON слоя карты (полигоны районов/субъектов или точки населённых пунктов)",
    response_class=JSONResponse,
)
async def territories_geojson(
    request: Request,
    level: Level = "municipality",
    region: str | None = None,
    cluster: str | None = None,
    bbox: str | None = None,
    simplify: float | None = Query(None, ge=0, le=0.1, description="Допуск упрощения, градусы"),
    with_index: bool = Query(False, description="Добавить total/rank (только level=settlement)"),
    profile: Profile = Depends(profile_query),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    c = get_container(request)
    where, params = _filters(
        level=level, cluster=normalize_cluster(cluster), region=region, parent_id=None,
        scored_only=False, bbox=_parse_bbox(bbox), q=None,
    )
    params["tol"] = DEFAULT_TOLERANCE[level] if simplify is None else simplify
    rows = (
        await session.execute(
            text(
                f"""
                SELECT t.id, t.name, t.level, t.kind, t.region_code, t.parent_id, t.cluster_code,
                       t.population, t.is_scored,
                       ST_AsGeoJSON(CASE WHEN t.geom IS NOT NULL AND :tol > 0
                                         THEN ST_SimplifyPreserveTopology(t.geom, :tol)
                                         ELSE coalesce(t.geom, t.centroid) END, 5) AS g
                FROM territory t WHERE {where} ORDER BY t.id
                """
            ),
            params,
        )
    ).all()
    scores: dict[int, tuple[float, int]] = {}
    if with_index and level == "settlement":
        for cl in sorted({r.cluster_code for r in rows if r.cluster_code}):
            cs = await c.scoring.score_cluster(session, cl, profile)
            threshold = profile.filters.climate_match_min
            scores.update({tid: (round(s.total, 1), cs.rank_of(tid)) for tid, s in cs.scores.items()
                           if threshold is None or (s.climate_match is not None and s.climate_match >= threshold)})
    features = []
    for r in rows:
        if with_index and level == "settlement" and profile.filters.climate_match_min is not None and r.id not in scores:
            continue
        if r.g is None:
            continue
        props: dict[str, Any] = {
            "id": r.id, "name": r.name, "level": r.level, "kind": r.kind, "region_code": r.region_code,
            "parent_id": r.parent_id, "cluster": r.cluster_code, "population": r.population,
            "is_scored": r.is_scored,
        }
        if r.id in scores:
            props["total"], props["rank"] = scores[r.id]
        features.append({"type": "Feature", "id": r.id, "geometry": json.loads(r.g), "properties": props})
    return JSONResponse({"type": "FeatureCollection", "features": features}, media_type="application/geo+json")


@router.get(
    "/territories/{territory_id}",
    response_model=TerritoryDetail,
    responses=ERROR_RESPONSES,
    summary="Карточка территории: метаданные, иерархия, геометрия",
)
async def get_territory(
    territory_id: int,
    request: Request,
    with_geometry: bool = True,
    session: AsyncSession = Depends(get_session),
) -> TerritoryDetail:
    c = get_container(request)
    row = (
        await session.execute(
            text(
                f"""
                SELECT {BRIEF_COLUMNS}, t.meta,
                       CASE WHEN :geo THEN ST_AsGeoJSON(ST_SimplifyPreserveTopology(t.geom, 0.0005), 6) END AS g
                FROM territory t WHERE t.id = :id
                """
            ),
            {"id": territory_id, "geo": with_geometry},
        )
    ).first()
    if row is None:
        raise NotFoundError("Территория не найдена", code="territory_not_found")

    crumbs: list[Breadcrumb] = []
    parent = row.parent_id
    while parent is not None and len(crumbs) < 3:
        p = (
            await session.execute(text("SELECT id, name, level, parent_id FROM territory WHERE id = :id"), {"id": parent})
        ).first()
        if p is None:
            break
        crumbs.insert(0, Breadcrumb(id=p.id, name=p.name, level=p.level))
        parent = p.parent_id
    region_name = (
        await session.execute(
            text("SELECT name FROM territory WHERE level = 'region' AND region_code = :r"), {"r": row.region_code}
        )
    ).scalar_one_or_none()
    children = (
        await session.execute(text("SELECT count(*) FROM territory WHERE parent_id = :id"), {"id": territory_id})
    ).scalar_one()

    coverage = None
    if row.is_scored:
        values, prov = await load_values(session, [territory_id], c.scoring.feature_codes, "car")
        own = sum(1 for code, v in values[territory_id].items() if v is not None and not prov[territory_id][code].inherited_from)
        inherited = sum(1 for code in prov[territory_id] if prov[territory_id][code].inherited_from)
        total = len(c.scoring.feature_codes)
        coverage = DataCoverage(features_total=total, own=own, inherited=inherited, missing=total - own - inherited)

    brief = _brief(row)
    return TerritoryDetail(
        **brief.model_dump(),
        region_name=region_name,
        cluster_name=c.cfg.clusters[row.cluster_code].name if row.cluster_code in c.cfg.clusters else None,
        breadcrumbs=crumbs,
        children_count=children,
        meta=row.meta or {},
        data_coverage=coverage,
        geometry=json.loads(row.g) if row.g else None,
    )
