from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, rate_limit
from app.api.schemas import ClusterInfo, FeatureCatalog, FeatureInfo, RegionInfo, SubindexInfo
from app.etl.coverage import coverage_report

router = APIRouter(tags=["reference"], dependencies=[rate_limit("default", "rate_limit_per_minute")])


@router.get("/features", response_model=FeatureCatalog, summary="Справочник признаков F01–F30 и субиндексов")
async def features(request: Request) -> FeatureCatalog:
    cfg = get_container(request).cfg
    return FeatureCatalog(
        model_version=cfg.version,
        subindices=[
            SubindexInfo(code=c, name=s.name, short_name=s.short_name, in_total=s.in_total)
            for c, s in cfg.subindices.items()
        ],
        features=[
            FeatureInfo(
                code=code, name=f.name, unit=f.unit, direction=f.direction, subindex=f.subindex,
                subindex_name=cfg.subindices[f.subindex].name, level=f.level, time_based=f.time_based,
                source=f.source, weight=f.weight,
            )
            for code, f in cfg.features.items()
        ],
    )


@router.get("/clusters", response_model=list[ClusterInfo], summary="Кластеры сопоставимости А/Б/В")
async def clusters(request: Request, session: AsyncSession = Depends(get_session)) -> list[ClusterInfo]:
    cfg = get_container(request).cfg
    rows = await session.execute(
        text("SELECT code, name, description, thresholds_json FROM cluster ORDER BY sort_order, code")
    )
    counts = dict(
        (
            await session.execute(
                text("SELECT cluster_code, count(*) FROM territory WHERE is_scored GROUP BY cluster_code")
            )
        ).all()
    )
    db_clusters = {r.code: r for r in rows}
    out = []
    for code, ccfg in cfg.clusters.items():
        r = db_clusters.get(code)
        out.append(
            ClusterInfo(
                code=code,
                name=r.name if r else ccfg.name,
                description=r.description if r else ccfg.description,
                params=dict(r.thresholds_json) if r else ccfg.params,
                territories=int(counts.get(code, 0)),
            )
        )
    return out


@router.get("/regions", response_model=list[RegionInfo], summary="Субъекты СКФО")
async def regions(session: AsyncSession = Depends(get_session)) -> list[RegionInfo]:
    rows = await session.execute(
        text(
            """
            SELECT r.id, r.region_code, r.name,
                   count(*) FILTER (WHERE t.level = 'municipality') AS municipalities,
                   count(*) FILTER (WHERE t.level = 'settlement') AS settlements,
                   count(*) FILTER (WHERE t.is_scored) AS scored
            FROM territory r
            LEFT JOIN territory t ON t.region_code = r.region_code AND t.id <> r.id
            WHERE r.level = 'region'
            GROUP BY r.id, r.region_code, r.name
            ORDER BY r.name
            """
        )
    )
    return [
        RegionInfo(id=r.id, code=r.region_code, name=r.name, municipalities=r.municipalities,
                   settlements=r.settlements, scored=r.scored)
        for r in rows
    ]


@router.get("/data/coverage", summary="Отчёт о полноте данных: признаки и POI по субъектам")
async def data_coverage(request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    c = get_container(request)
    return await coverage_report(session, c.cfg, seeds_dir=c.settings.seeds_dir)
