from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import Container
from app.core.errors import RateLimited
from app.profiles.contract import Profile

CLUSTER_ALIASES = {"A": "A", "B": "B", "V": "V", "А": "A", "Б": "B", "В": "V"}


def get_container(request: Request) -> Container:
    return request.app.state.container


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    container = get_container(request)
    async with container.sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def profile_query(
    request: Request,
    profile: str | None = Query(None, description="id пресета (family, pensioner, ...)"),
    p: str | None = Query(
        None, description="Сериализованный профиль из URL: base64url(JSON) или base64url(deflate-raw(JSON))"
    ),
) -> Profile:
    return get_container(request).profiles.from_query(profile, p)


def normalize_cluster(value: str | None) -> str | None:
    if value is None:
        return None
    code = CLUSTER_ALIASES.get(value.strip().upper())
    if code is None:
        from app.core.errors import ValidationFailed

        raise ValidationFailed("Кластер должен быть A, B или V (А/Б/В)", code="cluster_invalid")
    return code


def client_ip(request: Request, trust_forwarded: bool) -> str:
    if trust_forwarded:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, setting: str):  # noqa: ANN201
    async def _dep(request: Request) -> None:
        c = get_container(request)
        limit = int(getattr(c.settings, setting))
        allowed, retry = await c.rate_limiter.hit(bucket, client_ip(request, c.settings.trust_forwarded_for), limit)
        if not allowed:
            raise RateLimited(
                "Слишком много запросов, попробуйте позже",
                details={"retry_after": retry, "limit_per_minute": limit},
                headers={"Retry-After": str(retry)},
            )

    return Depends(_dep)
