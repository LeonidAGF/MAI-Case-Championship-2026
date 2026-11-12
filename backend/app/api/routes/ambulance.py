"""Ambulance travel time to settlements and ambulance coverage of areas (app/ambulance)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, normalize_cluster, rate_limit
from app.api.schemas import AmbulanceList, AmbulanceMethod, AmbulanceSettlement, AmbulanceSummary
from app.core.errors import ERROR_RESPONSES, ErrorResponse

router = APIRouter(prefix="/ambulance", tags=["ambulance"], dependencies=[rate_limit("default", "rate_limit_per_minute")])

RESPONSES = {**ERROR_RESPONSES, 503: {"model": ErrorResponse, "description": "Время доезда ещё не рассчитано"}}
REGION_DOC = "Код субъекта ISO 3166-2, напр. RU-DA"


def _region(value: str | None) -> str | None:
    return value.strip().upper() if value else None


@router.get("/method", response_model=AmbulanceMethod, summary="Методика и параметры расчёта времени доезда скорой")
async def method(request: Request, session: AsyncSession = Depends(get_session)) -> AmbulanceMethod:
    return AmbulanceMethod(**await get_container(request).ambulance.method(session))


@router.get(
    "/settlements",
    response_model=AmbulanceList,
    responses=RESPONSES,
    summary="Время доезда скорой до населённых пунктов: список для карты и таблицы",
)
async def settlements(
    request: Request,
    region: str | None = Query(None, description=REGION_DOC),
    municipality_id: int | None = Query(None, description="id района (территории уровня municipality)"),
    cluster: str | None = Query(None, description="A | B | V (или А/Б/В)"),
    status: Literal["met", "borderline", "not_met", "none"] | None = Query(
        None, description="Норматив: met, borderline, not_met; none — нет оценки"
    ),
    sort: Literal["minutes", "name", "population", "elevation", "distance"] = "minutes",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AmbulanceList:
    result = await get_container(request).ambulance.settlements(
        session, region=_region(region), municipality_id=municipality_id, cluster=normalize_cluster(cluster),
        status=status, sort=sort, order=order, limit=limit, offset=offset,
    )
    return AmbulanceList(**result)


@router.get(
    "/settlements/{territory_id}",
    response_model=AmbulanceSettlement,
    responses=RESPONSES,
    summary="Время доезда скорой до населённого пункта: оценка, диапазон, норматив, база и маршрут",
)
async def settlement(
    territory_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> AmbulanceSettlement:
    return AmbulanceSettlement(**await get_container(request).ambulance.settlement(session, territory_id))


@router.get(
    "/summary",
    response_model=AmbulanceSummary,
    responses=RESPONSES,
    summary="Покрытие скорой помощью: доли населённых пунктов и жителей в пределах норматива",
)
async def summary(
    request: Request,
    region: str | None = Query(None, description=REGION_DOC),
    municipality_id: int | None = Query(None, description="id района: сводка по одному району"),
    group_by: Literal["region", "municipality"] | None = Query(None, description="Разбивка по субъектам или районам"),
    slowest: int = Query(10, ge=0, le=100, description="Сколько самых долгих пунктов показать"),
    session: AsyncSession = Depends(get_session),
) -> AmbulanceSummary:
    result = await get_container(request).ambulance.summary(
        session, region=_region(region), municipality_id=municipality_id, group_by=group_by, slowest=slowest,
    )
    return AmbulanceSummary(**result)
