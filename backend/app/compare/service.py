"""Side-by-side comparison of 2–3 territories inside one cluster (stage 5)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailed
from app.explain.service import explain_territory
from app.explain.texts import fmt_num
from app.modelcfg import ModelConfig
from app.profiles.contract import Profile
from app.scoring.service import ScoringService
from app.scoring.snapshots import attractiveness_out, persist_snapshot, r1, subindices_out

SIGNIFICANT_GAP = 5.0  # points of a subindex worth mentioning


async def compare_territories(
    session: AsyncSession,
    *,
    scoring: ScoringService,
    cfg: ModelConfig,
    territory_ids: list[int],
    profile: Profile,
    profile_id: str | None,
    model_version_id: int,
) -> dict[str, Any]:
    ids = list(dict.fromkeys(territory_ids))
    if len(ids) != len(territory_ids):
        raise ValidationFailed("Территории в сравнении не должны повторяться", code="compare_duplicates")
    if not 2 <= len(ids) <= 3:
        raise ValidationFailed("Сравнивать можно 2–3 территории", code="compare_count")

    rows = await session.execute(
        text("SELECT id, name, cluster_code, is_scored, region_code FROM territory WHERE id = ANY(:ids)"),
        {"ids": ids},
    )
    info = {r[0]: r for r in rows}
    missing = [t for t in ids if t not in info]
    if missing:
        raise NotFoundError(f"Территории не найдены: {missing}", code="territory_not_found")
    not_scored = [info[t][1] for t in ids if not info[t][3] or info[t][2] is None]
    if not_scored:
        raise ValidationFailed(
            "Индекс считается только для населённых пунктов; не участвуют: " + ", ".join(not_scored),
            code="territory_not_scored",
        )
    clusters = {info[t][2] for t in ids}
    if len(clusters) > 1:
        parts = [
            f"«{info[t][1]}» — {cfg.clusters[info[t][2]].name.lower()} ({info[t][2]})" if info[t][2] in cfg.clusters
            else f"«{info[t][1]}» ({info[t][2]})"
            for t in ids
        ]
        raise ValidationFailed(
            "Сравнение возможно только внутри одного кластера (город с городом, село с селом): " + "; ".join(parts),
            code="cluster_mismatch",
            details={"clusters": {str(t): info[t][2] for t in ids}},
        )
    cluster = clusters.pop()
    cs = await scoring.score_cluster(session, cluster, profile)
    cluster_name = cfg.clusters[cluster].name if cluster in cfg.clusters else cluster
    revision = cs.data_revision

    items = []
    for t in ids:
        score = cs.get(t)
        expl = explain_territory(cs, t, cfg, cluster_name=cluster_name, territory_name=info[t][1])
        result_id = await persist_snapshot(
            session, cfg=cfg, score=score, provenance=cs.matrix.provenance.get(t, {}), cluster_code=cluster,
            mode=profile.mode, profile_hash=profile.hash, profile_id=profile_id, scenario_run_id=None,
            model_version_id=model_version_id, data_revision=revision,
        )
        items.append(
            {
                "territory_id": t,
                "name": info[t][1],
                "region_code": info[t][4],
                "total": r1(score.total),
                "rank": cs.rank_of(t),
                "subindices": subindices_out(score, cfg),
                "attractiveness": attractiveness_out(score, cfg),
                "pluses": expl.pluses,
                "minuses": expl.minuses,
                "result_id": result_id,
            }
        )

    subs = cfg.total_subindices
    radar = {
        "axes": [{"code": s, "name": cfg.subindices[s].short_name} for s in subs],
        "series": [
            {"territory_id": it["territory_id"], "name": it["name"], "values": [it["subindices"][s] for s in subs]}
            for it in items
        ],
    }
    leaders = {}
    for s in subs:
        ranked = sorted((it for it in items if it["subindices"][s] is not None), key=lambda it: -it["subindices"][s])
        if not ranked:
            continue
        gap = ranked[0]["subindices"][s] - ranked[1]["subindices"][s] if len(ranked) > 1 else None
        leaders[s] = {"territory_id": ranked[0]["territory_id"], "gap": r1(gap) if gap is not None else None}

    summary = _summary(items, leaders, cfg)
    return {
        "cluster": cluster,
        "cluster_name": cluster_name,
        "cluster_stats": cs.total_stats(),
        "items": items,
        "radar": radar,
        "leaders": leaders,
        "summary": summary,
    }


def _summary(items: list[dict], leaders: dict[str, dict], cfg: ModelConfig) -> list[str]:
    names = {it["territory_id"]: it["name"] for it in items}
    out: list[str] = []
    best = max(items, key=lambda it: it["total"])
    others = [it for it in items if it is not best]
    lead = min(best["total"] - o["total"] for o in others)
    if lead < 1:
        out.append("Итоговые индексы практически равны.")
    else:
        out.append(f"По итоговому индексу впереди «{best['name']}» (+{fmt_num(lead)}).")
    strengths: dict[int, list[str]] = {}
    for s, lead_info in leaders.items():
        if lead_info["gap"] is not None and lead_info["gap"] >= SIGNIFICANT_GAP:
            strengths.setdefault(lead_info["territory_id"], []).append(
                f"{cfg.subindices[s].name.lower()} (+{fmt_num(lead_info['gap'])})"
            )
    for tid, parts in strengths.items():
        out.append(f"«{names[tid]}» сильнее: " + ", ".join(parts) + ".")
    if len(out) == 1 and not strengths:
        out.append("Существенных различий по субиндексам (5+ баллов) нет.")
    return out
