from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, profile_query, rate_limit
from app.api.routes.index import load_overlay, scored_territory
from app.api.schemas import ExplainResponse
from app.core.errors import ERROR_RESPONSES
from app.explain.service import explain_territory
from app.profiles.contract import Profile, ProfileOut
from app.scoring.snapshots import attractiveness_out, persist_snapshot, subindices_out

router = APIRouter(tags=["explain"], dependencies=[rate_limit("default", "rate_limit_per_minute")])


@router.get(
    "/explain/{territory_id}",
    response_model=ExplainResponse,
    responses=ERROR_RESPONSES,
    summary="Причины балла: водопад вкладов, топ-3 плюса и минуса (без LLM)",
)
async def explain(
    territory_id: int,
    request: Request,
    scenario_run_id: int | None = Query(None),
    profile: Profile = Depends(profile_query),
    session: AsyncSession = Depends(get_session),
) -> ExplainResponse:
    c = get_container(request)
    row = await scored_territory(session, territory_id)
    overlay = await load_overlay(session, scenario_run_id)
    cs = await c.scoring.score_cluster(session, row.cluster_code, profile, overlay)
    cluster_name = c.cfg.clusters[row.cluster_code].name
    expl = explain_territory(cs, territory_id, c.cfg, cluster_name=cluster_name, territory_name=row.name)
    score = cs.get(territory_id)
    mv_id = await c.model_version_id()
    result_id = await persist_snapshot(
        session, cfg=c.cfg, score=score, provenance=cs.matrix.provenance.get(territory_id, {}),
        cluster_code=row.cluster_code, mode=profile.mode, profile_hash=profile.hash,
        profile_id=c.profiles.pristine_preset_id(profile), scenario_run_id=scenario_run_id,
        model_version_id=mv_id, data_revision=cs.data_revision,
    )
    await session.commit()
    return ExplainResponse(
        profile=ProfileOut.of(profile),
        model_version=c.cfg.version,
        data_revision=cs.data_revision,
        scenario_run_id=scenario_run_id,
        territory_id=territory_id,
        name=row.name,
        cluster=row.cluster_code,
        cluster_name=cluster_name,
        total=expl.total,
        subindices=subindices_out(score, c.cfg),
        attractiveness=attractiveness_out(score, c.cfg),
        baseline=expl.baseline,
        waterfall=expl.steps,
        pluses=expl.pluses,
        minuses=expl.minuses,
        attractiveness_factors=expl.attractiveness_factors,
        summary=expl.summary,
        result_id=result_id,
    )
