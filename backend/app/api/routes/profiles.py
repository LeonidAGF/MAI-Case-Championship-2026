from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.deps import get_container, rate_limit
from app.api.schemas import EncodeRequest, EncodeResponse, PresetOut
from app.core.errors import ERROR_RESPONSES, ValidationFailed
from app.profiles.codec import decode_param
from app.profiles.contract import ProfileOut

router = APIRouter(tags=["profiles"], dependencies=[rate_limit("default", "rate_limit_per_minute")])


@router.get("/profiles/presets", response_model=list[PresetOut], summary="Пять преднастроенных профилей")
async def presets(request: Request) -> list[PresetOut]:
    c = get_container(request)
    return [
        PresetOut(
            id=pv.id,
            name=pv.name,
            description=pv.description,
            key_thresholds=pv.key_thresholds,
            profile=ProfileOut.of(pv.profile),
            p=c.profiles.encode(pv.profile),
        )
        for pv in c.profiles.presets()
    ]


@router.post(
    "/profiles/encode",
    response_model=EncodeResponse,
    responses=ERROR_RESPONSES,
    summary="Профиль → параметр p для ссылки (помощник фронтенда и чата)",
)
async def encode(body: EncodeRequest, request: Request) -> EncodeResponse:
    c = get_container(request)
    profile = c.profiles.resolve(body.profile)
    p = c.profiles.encode(profile, compress=body.compress)
    if len(p) > c.settings.profile_param_max_len:
        raise ValidationFailed("Профиль не помещается в ссылку", code="profile_param_too_long")
    return EncodeResponse(p=p, length=len(p), query=f"?p={p}", profile=ProfileOut.of(profile))


@router.get(
    "/profiles/decode",
    response_model=ProfileOut,
    responses=ERROR_RESPONSES,
    summary="Параметр p → полный профиль (проверка ссылки)",
)
async def decode(request: Request, p: str = Query(..., description="Значение параметра p из адреса")) -> ProfileOut:
    c = get_container(request)
    return ProfileOut.of(c.profiles.resolve(decode_param(p, max_len=c.settings.profile_param_max_len)))
