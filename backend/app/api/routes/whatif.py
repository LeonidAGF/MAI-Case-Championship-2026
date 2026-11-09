from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, profile_query, rate_limit
from app.api.schemas import ScenarioInfo, WhatIfRequest, WhatIfResponse
from app.core.errors import ERROR_RESPONSES, NotFoundError
from app.profiles.contract import Profile, ProfileOut
from app.whatif.catalog import CATALOG
from app.whatif.engine import WhatIfService, stored_changes

router = APIRouter(tags=["what-if"])


@router.get("/whatif/scenarios", response_model=list[ScenarioInfo], summary="Каталог сценариев W1–W10")
async def scenarios() -> list[ScenarioInfo]:
    return [
        ScenarioInfo(
            code=d.code,
            name=d.name,
            type=d.type,
            method=d.method,
            affects=list(d.affects),
            description=d.description,
            params_schema=d.params_schema(),
            example=d.example,
        )
        for d in CATALOG.values()
    ]


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
