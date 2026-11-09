from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, rate_limit
from app.api.schemas import CompareRequest, CompareResponse
from app.compare.service import compare_territories
from app.core.errors import ERROR_RESPONSES
from app.profiles.contract import ProfileOut

router = APIRouter(tags=["compare"], dependencies=[rate_limit("default", "rate_limit_per_minute")])


@router.post(
    "/compare",
    response_model=CompareResponse,
    responses=ERROR_RESPONSES,
    summary="Сравнение 2–3 территорий одного кластера (радар, лидеры, плюсы/минусы)",
)
async def compare(
    body: CompareRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> CompareResponse:
    c = get_container(request)
    profile = c.profiles.resolve(body.profile)
    result = await compare_territories(
        session,
        scoring=c.scoring,
        cfg=c.cfg,
        territory_ids=body.territory_ids,
        profile=profile,
        profile_id=c.profiles.pristine_preset_id(profile),
        model_version_id=await c.model_version_id(),
    )
    await session.commit()
    return CompareResponse(**result, profile=ProfileOut.of(profile))
