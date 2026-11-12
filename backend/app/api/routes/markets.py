"""Sales markets of settlements and their attractiveness for production sectors (tasks 4+5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, normalize_cluster, rate_limit
from app.api.schemas import MarketCatalog, MarketClusters, SectorRating, TerritoryMarkets
from app.core.errors import ERROR_RESPONSES

router = APIRouter(prefix="/markets", tags=["markets"], dependencies=[rate_limit("default", "rate_limit_per_minute")])

REGION_DOC = "Код субъекта ISO 3166-2, напр. RU-DA"


@router.get("/catalog", response_model=MarketCatalog, summary="Рынки сбыта, отрасли и их веса (методика)")
async def catalog(request: Request) -> MarketCatalog:
    return MarketCatalog(**get_container(request).markets.catalog())


@router.get(
    "/clusters",
    response_model=MarketClusters,
    responses=ERROR_RESPONSES,
    summary="Кластеры А/Б/В по рынкам сбыта: уровни рынков, отрасли и лучшие населённые пункты",
)
async def clusters(
    request: Request,
    region: str | None = Query(None, description=REGION_DOC),
    session: AsyncSession = Depends(get_session),
) -> MarketClusters:
    result = await get_container(request).markets.clusters(session, region.upper() if region else None)
    return MarketClusters(**result)


@router.get(
    "/sectors/{sector}",
    response_model=SectorRating,
    responses=ERROR_RESPONSES,
    summary="Рейтинг населённых пунктов для отрасли (по СКФО или внутри кластера)",
)
async def sector_rating(
    sector: str,
    request: Request,
    cluster: str | None = Query(None, description="A, B или V (А/Б/В): рейтинг внутри кластера"),
    region: str | None = Query(None, description=REGION_DOC),
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> SectorRating:
    result = await get_container(request).markets.sector_rating(
        session, sector, cluster=normalize_cluster(cluster), region=region.upper() if region else None,
        limit=limit, offset=offset,
    )
    return SectorRating(**result)


@router.get(
    "/territories/{territory_id}",
    response_model=TerritoryMarkets,
    responses=ERROR_RESPONSES,
    summary="Рынки сбыта населённого пункта и оценка для каждой отрасли",
)
async def territory_markets(
    territory_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> TerritoryMarkets:
    return TerritoryMarkets(**await get_container(request).markets.territory(session, territory_id))
