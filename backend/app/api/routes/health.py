from __future__ import annotations

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from app.api.deps import get_container
from app.api.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/healthz", response_model=HealthResponse, summary="Проверка живости (compose, демо)")
async def healthz(request: Request, response: Response) -> HealthResponse:
    c = get_container(request)
    checks: dict[str, str] = {}
    status = "ok"
    revision: int | None = None
    try:
        async with c.sessionmaker() as session:
            await session.execute(text("SELECT 1"))
            revision = await c.scoring.revision.get(session)
            data = (
                await session.execute(
                    text(
                        """
                        SELECT (SELECT count(*) FROM territory WHERE is_scored),
                               EXISTS(SELECT 1 FROM feature_value),
                               EXISTS(SELECT 1 FROM feature_value WHERE source = 'synthetic-demo')
                        """
                    )
                )
            ).one()
        checks["db"] = "ok"
        checks["territories"] = f"{data[0]} scored" if data[0] else "empty (run: python -m app.cli seed)"
        checks["feature_data"] = "synthetic-demo (NOT real data)" if data[2] else ("loaded" if data[1] else "empty")
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        checks["db"] = f"error: {type(exc).__name__}"
        status = "down"

    try:  # separate session: a failed query must not affect the checks above
        async with c.sessionmaker() as session:
            known, total = (
                await session.execute(text("SELECT count(minutes), count(*) FROM ambulance_time"))
            ).one()
        checks["ambulance"] = (
            f"{known} of {total} settlements" if total else "not computed (run: python -m app.cli load-ambulance)"
        )
    except Exception as exc:  # noqa: BLE001 - health must report, not raise (e.g. migration not applied yet)
        checks["ambulance"] = f"error: {type(exc).__name__}"

    cache_ok = await c.cache.ping()
    checks["cache"] = c.cache.backend
    if c.settings.redis_enabled and not cache_ok:
        status = "degraded" if status == "ok" else status

    if c.osrm.configured:
        ok = [m for m in ("car", "foot") if c.osrm.available(m) and await c.osrm.ping(m)]
        checks["osrm"] = f"ok ({', '.join(ok)})" if ok else "unavailable (analytical what-if fallback)"
        if not ok and status == "ok":
            status = "degraded"
    else:
        checks["osrm"] = "disabled (analytical what-if)"
    checks["chat"] = c.chat.status() if c.chat else "disabled"

    if status == "down":
        response.status_code = 503
    return HealthResponse(
        status=status,
        version=c.settings.app_version,
        model_version=c.cfg.version,
        scoring_engine=f"{c.index_engine.name}@{c.index_engine.version}",
        checks=checks,
        data_revision=revision,
    )
