from __future__ import annotations

import statistics
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session, normalize_cluster, profile_query, rate_limit
from app.api.schemas import GroupItem, IndexCluster, IndexItem, IndexSingle
from app.container import Container
from app.core.errors import ERROR_RESPONSES, NotFoundError, ValidationFailed
from app.profiles.contract import Profile, ProfileOut
from app.scoring.service import ClusterScores, ScenarioOverlay
from app.scoring.snapshots import attractiveness_out, persist_snapshot, r1, subindices_out
from app.whatif.engine import stored_changes

router = APIRouter(tags=["index"], dependencies=[rate_limit("default", "rate_limit_per_minute")])


async def load_overlay(session: AsyncSession, run_id: int | None) -> ScenarioOverlay | None:
    if run_id is None:
        return None
    overrides = (
        await session.execute(text("SELECT overrides FROM scenario_run WHERE id = :id"), {"id": run_id})
    ).scalar_one_or_none()
    if overrides is None:
        raise NotFoundError(f"Прогон сценария {run_id} не найден", code="scenario_run_not_found")
    return ScenarioOverlay.from_rows(run_id, stored_changes(overrides))


async def scored_territory(session: AsyncSession, territory_id: int) -> Any:
    row = (
        await session.execute(
            text("SELECT id, name, cluster_code, is_scored FROM territory WHERE id = :id"), {"id": territory_id}
        )
    ).first()
    if row is None:
        raise NotFoundError("Территория не найдена", code="territory_not_found")
    if not row.is_scored or row.cluster_code is None:
        raise ValidationFailed(
            f"«{row.name}» не участвует в расчёте индекса (индекс считается для населённых пунктов; "
            "для районов используйте group_by)",
            code="territory_not_scored",
        )
    return row


def _meta(c: Container, profile: Profile, cs: ClusterScores, scenario_run_id: int | None) -> dict[str, Any]:
    return {
        "profile": ProfileOut.of(profile),
        "model_version": c.cfg.version,
        "data_revision": cs.data_revision,
        "scenario_run_id": scenario_run_id,
    }


@router.get(
    "/index",
    response_model=IndexSingle | IndexCluster,
    responses=ERROR_RESPONSES,
    summary="Индекс территории или всех территорий кластера под профиль",
)
async def get_index(
    request: Request,
    territory_id: int | None = None,
    cluster: str | None = Query(None, description="A | B | V — все территории кластера"),
    region: str | None = Query(None, description="Фильтр списка по субъекту (RU-DA, ...)"),
    group_by: Literal["municipality", "region"] | None = Query(None, description="Агрегаты для хлороплета"),
    sort: str = Query("total", description="total | attractiveness | S1..S6"),
    order: Literal["asc", "desc"] = "desc",
    limit: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0),
    scenario_run_id: int | None = Query(None, description="Индекс с учётом прогона what-if"),
    profile: Profile = Depends(profile_query),
    session: AsyncSession = Depends(get_session),
) -> IndexSingle | IndexCluster:
    c = get_container(request)
    if (territory_id is None) == (cluster is None):
        raise ValidationFailed("Укажите ровно один параметр: territory_id или cluster", code="index_target_required")
    overlay = await load_overlay(session, scenario_run_id)
    mv_id = await c.model_version_id()

    if territory_id is not None:
        row = await scored_territory(session, territory_id)
        cs = await c.scoring.score_cluster(session, row.cluster_code, profile, overlay)
        score = cs.get(territory_id)
        result_id = await persist_snapshot(
            session, cfg=c.cfg, score=score, provenance=cs.matrix.provenance.get(territory_id, {}),
            cluster_code=row.cluster_code, mode=profile.mode, profile_hash=profile.hash,
            profile_id=c.profiles.pristine_preset_id(profile), scenario_run_id=scenario_run_id,
            model_version_id=mv_id, data_revision=cs.data_revision,
        )
        await session.commit()
        return IndexSingle(
            **_meta(c, profile, cs, scenario_run_id),
            territory_id=territory_id,
            name=row.name,
            cluster=row.cluster_code,
            cluster_name=c.cfg.clusters[row.cluster_code].name,
            total=r1(score.total),
            rank=cs.rank_of(territory_id),
            cluster_size=len(cs.scores),
            subindices=subindices_out(score, c.cfg),
            attractiveness=attractiveness_out(score, c.cfg),
            result_id=result_id,
            climate_match=score.climate_match,
            climate_adjustment_points=r1(score.climate_adjustment_points),
        )

    code = normalize_cluster(cluster)
    if code not in c.cfg.clusters:
        raise ValidationFailed(f"Кластер {code} не описан в конфигурации модели", code="cluster_invalid")
    allowed_sorts = {"total", "attractiveness", *c.cfg.total_subindices}
    if sort not in allowed_sorts:
        raise ValidationFailed(f"sort должен быть одним из: {', '.join(sorted(allowed_sorts))}", code="sort_invalid")
    limit = min(limit or c.settings.max_index_items, c.settings.max_index_items)
    region = region.upper() if region else None

    revision = await c.scoring.revision.get(session)
    cache_key = (
        f"index:{mv_id}:{revision}:{code}:{region}:{group_by}:{sort}:{order}:{limit}:{offset}:"
        f"{profile.hash}:{profile.preset}:{scenario_run_id}"
    )
    cached = await c.cache.get_json(cache_key)
    if cached is not None:
        return IndexCluster.model_validate(cached)

    cs = await c.scoring.score_cluster(session, code, profile, overlay)
    meta_rows = await session.execute(
        text(
            """
            SELECT t.id, t.name, t.region_code, t.parent_id, m.name AS parent_name,
                   r.id AS region_id, r.name AS region_name
            FROM territory t
            LEFT JOIN territory m ON m.id = t.parent_id
            LEFT JOIN territory r ON r.level = 'region' AND r.region_code = t.region_code
            WHERE t.cluster_code = :c AND t.is_scored
            """
        ),
        {"c": code},
    )
    meta = {r.id: r for r in meta_rows}
    threshold = profile.filters.climate_match_min
    tids = [t for t in cs.scores if t in meta and (region is None or meta[t].region_code == region)
            and (threshold is None or (cs.scores[t].climate_match is not None
                                       and cs.scores[t].climate_match >= threshold))]

    def sort_value(tid: int) -> float:
        s = cs.scores[tid]
        if sort == "total":
            v = s.total
        elif sort == "attractiveness":
            v = s.attractiveness
        else:
            v = s.subindices.get(sort)
        return -1.0 if v is None else float(v)

    tids.sort(key=lambda t: (sort_value(t), -t), reverse=(order == "desc"))
    page = tids[offset : offset + limit]
    items = [
        IndexItem(
            territory_id=t,
            name=meta[t].name,
            total=r1(cs.scores[t].total),
            rank=cs.rank_of(t),
            subindices=subindices_out(cs.scores[t], c.cfg),
            attractiveness=attractiveness_out(cs.scores[t], c.cfg),
            climate_match=cs.scores[t].climate_match,
            climate_adjustment_points=r1(cs.scores[t].climate_adjustment_points),
        )
        for t in page
    ]

    groups = None
    if group_by:
        buckets: dict[int, list[int]] = {}
        names: dict[int, str] = {}
        for t in tids:
            key = meta[t].parent_id if group_by == "municipality" else meta[t].region_id
            if key is None:
                continue
            buckets.setdefault(key, []).append(t)
            names[key] = meta[t].parent_name if group_by == "municipality" else meta[t].region_name
        groups = []
        for key, members in buckets.items():
            totals = [cs.scores[t].total for t in members]
            subs = {}
            for s in c.cfg.total_subindices:
                vals = [cs.scores[t].subindices.get(s) for t in members]
                vals = [v for v in vals if v is not None]
                subs[s] = r1(statistics.fmean(vals)) if vals else None
            groups.append(
                GroupItem(
                    territory_id=key, name=names[key], level=group_by, n=len(members),
                    mean_total=r1(statistics.fmean(totals)), median_total=r1(statistics.median(totals)),
                    min_total=r1(min(totals)), max_total=r1(max(totals)), subindices=subs,
                )
            )
        groups.sort(key=lambda g: -g.mean_total)

    result = IndexCluster(
        **_meta(c, profile, cs, scenario_run_id),
        cluster=code,
        cluster_name=c.cfg.clusters[code].name,
        cluster_allowed_by_profile=code in profile.allowed_clusters,
        stats=cs.total_stats(),
        items=items,
        groups=groups,
        total=len(tids),
        limit=limit,
        offset=offset,
    )
    await c.cache.set_json(cache_key, result.model_dump(mode="json"), c.settings.cache_ttl_seconds)
    return result
