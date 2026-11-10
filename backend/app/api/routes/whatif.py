from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, profile_query, rate_limit
from app.api.schemas import ScenarioInfo, WhatIfRequest, WhatIfResponse
from app.core.errors import ERROR_RESPONSES, NotFoundError
from app.core.metrics import WHATIF_AFFECTED, WHATIF_DELTA, WHATIF_DURATION, WHATIF_RUNS
from app.profiles.contract import Profile, ProfileOut
from app.whatif.catalog import CATALOG
from app.whatif.engine import WhatIfService, stored_changes

router = APIRouter(tags=["what-if"])
log = logging.getLogger(__name__)


async def _example_municipality(session: AsyncSession) -> int | None:
    """A real district for the examples: the one around the W1 example point, else the first one."""
    point = CATALOG["W1"].example
    row = (
        await session.execute(
            text(
                """
                SELECT id FROM territory
                WHERE level = 'municipality' AND geom IS NOT NULL
                  AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
                LIMIT 1
                """
            ),
            {"lon": point["lon"], "lat": point["lat"]},
        )
    ).scalar_one_or_none()
    if row is None:
        row = (
            await session.execute(text("SELECT id FROM territory WHERE level = 'municipality' ORDER BY id LIMIT 1"))
        ).scalar_one_or_none()
    return row


@router.get("/whatif/scenarios", response_model=list[ScenarioInfo], summary="Каталог сценариев W1–W10")
async def scenarios(session: AsyncSession = Depends(get_session)) -> list[ScenarioInfo]:
    municipality = await _example_municipality(session)
    out = []
    for d in CATALOG.values():
        example = dict(d.example)
        if "municipality_id" in example and municipality is not None:
            example["municipality_id"] = municipality
        out.append(
            ScenarioInfo(
                code=d.code,
                name=d.name,
                type=d.type,
                method=d.method,
                target=d.target,
                kinds=d.kind_options(),
                affects=list(d.affects),
                description=d.description,
                params_schema=d.params_schema(),
                example=example,
            )
        )
    return out


@router.post(
    "/whatif/run",
    response_model=WhatIfResponse,
    responses=ERROR_RESPONSES,
    dependencies=[rate_limit("whatif", "rate_limit_whatif_per_minute")],
    summary="Прогон сценария: снимок «до/после», дельты субиндексов, победители и проигравшие",
)
async def run(body: WhatIfRequest, request: Request, session: AsyncSession = Depends(get_session)) -> WhatIfResponse:
    c = get_container(request)
    profile = c.profiles.resolve(body.profile)
    scenario = body.scenario_code.upper() if body.scenario_code.upper() in CATALOG else "unknown"
    started = time.perf_counter()
    try:
        result = await c.whatif.run(
            session,
            scenario_code=body.scenario_code,
            params=body.params,
            profile=profile,
            profile_id=c.profiles.pristine_preset_id(profile),
            method=body.method,
            model_version_id=await c.model_version_id(),
        )
        await session.commit()
    except Exception:
        WHATIF_RUNS.labels(scenario, body.method, "failure").inc()
        WHATIF_DURATION.labels(scenario, body.method).observe(time.perf_counter() - started)
        raise
    method_used = result["method_used"]
    WHATIF_RUNS.labels(scenario, method_used, "success").inc()
    WHATIF_DURATION.labels(scenario, method_used).observe(time.perf_counter() - started)
    WHATIF_AFFECTED.labels(scenario).observe(result["affected"])
    WHATIF_DELTA.labels(scenario).observe(result["summary"]["avg_delta"])
    log.info("whatif_run", extra={"scenario": scenario, "method_used": method_used, "affected": result["affected"]})
    return WhatIfResponse(**result, profile=ProfileOut.of(profile))


@router.get(
    "/whatif/runs/{run_id}",
    response_model=WhatIfResponse,
    responses=ERROR_RESPONSES,
    dependencies=[rate_limit("default", "rate_limit_per_minute")],
    summary="Повторно показать прогон (например, по ссылке) под любой профиль",
)
async def get_run(
    run_id: int,
    request: Request,
    profile: Profile = Depends(profile_query),
    session: AsyncSession = Depends(get_session),
) -> WhatIfResponse:
    c = get_container(request)
    row = (
        await session.execute(
            text(
                "SELECT scenario_code, params, method_used, overrides, summary, warnings FROM scenario_run WHERE id = :id"
            ),
            {"id": run_id},
        )
    ).first()
    if row is None:
        raise NotFoundError(f"Прогон сценария {run_id} не найден", code="scenario_run_not_found")
    evaluation = await c.whatif.evaluate(
        session,
        run_id=run_id,
        changes=stored_changes(row.overrides),
        profile=profile,
        profile_id=c.profiles.pristine_preset_id(profile),
        model_version_id=await c.model_version_id(),
        persist=False,
    )
    sdef = CATALOG[row.scenario_code]
    extra_warnings = evaluation.pop("warnings", [])
    params = {k: v for k, v in (row.params or {}).items() if k != "method"}
    return WhatIfResponse(
        run_id=run_id,
        scenario_code=sdef.code,
        scenario_name=sdef.name,
        method_used=row.method_used,
        params=params,
        plan=(row.summary or {}).get("plan"),
        **evaluation,
        explanation=WhatIfService._explain(sdef, {**evaluation}),
        warnings=list(row.warnings or []) + extra_warnings,
        profile=ProfileOut.of(profile),
    )
