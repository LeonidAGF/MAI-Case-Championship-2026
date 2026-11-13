from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Query, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.container import Container
from app.core.errors import Forbidden, RateLimited, Unauthorized
from app.partners.keys import key_from_headers
from app.partners.store import BEARER, Principal, authenticate
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


_bearer = HTTPBearer(
    auto_error=False, scheme_name="OrganisationKey",
    description="Ключ API организации: заголовок `Authorization: Bearer pulse_…`",
)
_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False, scheme_name="OrganisationKeyHeader",
    description="Тот же ключ в заголовке `X-API-Key`, если Authorization занят",
)


def api_key(scope: str = "datasets"):  # noqa: ANN201
    """Organisation key check for /v1 routes: valid, not revoked or expired, scope, per-key rate limit."""

    async def _dep(
        request: Request,
        bearer: HTTPAuthorizationCredentials | None = Security(_bearer),
        header_key: str | None = Security(_key_header),
    ) -> Principal:
        c = get_container(request)
        raw = key_from_headers(bearer.credentials if bearer else None, header_key)
        if raw is None:
            raise Unauthorized(
                "Нужен ключ API: передайте заголовок Authorization: Bearer <ключ>", code="api_key_missing",
                headers=BEARER,
            )
        principal = await authenticate(c.sessionmaker, raw)
        request.state.partner_organisation_id = principal.organisation_id
        request.state.partner_key_id = principal.key_id
        if scope not in principal.scopes:
            raise Forbidden("Этот ключ не даёт доступа к запросу", code="api_key_scope")
        limit = principal.rate_limit_per_minute or c.settings.rate_limit_api_key_per_minute
        allowed, retry = await c.rate_limiter.hit("apikey", str(principal.key_id), limit)
        if not allowed:
            raise RateLimited(
                "Слишком много запросов с этим ключом, попробуйте позже",
                details={"retry_after": retry, "limit_per_minute": limit},
                headers={"Retry-After": str(retry)},
            )
        return principal

    return _dep


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
