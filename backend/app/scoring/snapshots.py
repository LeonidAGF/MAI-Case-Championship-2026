"""Immutable snapshots: index_result + component (one source of truth for waterfall & texts)."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modelcfg import ModelConfig
from app.scoring.contract import IndexEngine, TerritoryScore
from app.scoring.matrix import Provenance


def r1(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


def subindices_out(score: TerritoryScore, cfg: ModelConfig) -> dict[str, float | None]:
    return {s: r1(score.subindices.get(s)) for s in cfg.total_subindices}


def attractiveness_out(score: TerritoryScore, cfg: ModelConfig) -> dict | None:
    if score.attractiveness is None:
        return None
    badge = cfg.badge_for(score.attractiveness) or {}
    return {"score": r1(score.attractiveness), "badge": badge.get("code"), "label": badge.get("label")}


async def ensure_model_version(session: AsyncSession, cfg: ModelConfig, engine: IndexEngine) -> int:
    engine_id = f"{engine.name}@{engine.version}"
    row = (
        await session.execute(
            text(
                """
                INSERT INTO model_version (version, engine, config_hash, config_json, is_active)
                VALUES (:version, :engine, :hash, CAST(:cfg AS jsonb), true)
                ON CONFLICT ON CONSTRAINT uq_model_version_identity DO NOTHING
                RETURNING id
                """
            ),
            {
                "version": cfg.version,
                "engine": engine_id,
                "hash": cfg.config_hash,
                "cfg": json.dumps(cfg.model_dump(mode="json"), ensure_ascii=False),
            },
        )
    ).first()
    if row is None:
        row = (
            await session.execute(
                text(
                    "SELECT id FROM model_version WHERE version = :v AND engine = :e AND config_hash = :h"
                ),
                {"v": cfg.version, "e": engine_id, "h": cfg.config_hash},
            )
        ).first()
    mv_id = int(row[0])
    await session.execute(
        text("UPDATE model_version SET is_active = (id = :id) WHERE is_active OR id = :id"), {"id": mv_id}
    )
    return mv_id


async def persist_snapshot(
    session: AsyncSession,
    *,
    cfg: ModelConfig,
    score: TerritoryScore,
    provenance: dict[str, Provenance],
    cluster_code: str,
    mode: str,
    profile_hash: str,
    profile_id: str | None,
    scenario_run_id: int | None,
    model_version_id: int,
    data_revision: int,
) -> int:
    params = {
        "territory_id": score.territory_id,
        "profile_id": profile_id,
        "profile_hash": profile_hash,
        "scenario_run_id": scenario_run_id,
        "model_version_id": model_version_id,
        "data_revision": data_revision,
    }
    row = (
        await session.execute(
            text(
                """
                INSERT INTO index_result (territory_id, profile_id, profile_hash, scenario_run_id,
                    model_version_id, data_revision, cluster_code, mode, total, subindices, attractiveness)
                VALUES (:territory_id, :profile_id, :profile_hash, :scenario_run_id, :model_version_id,
                    :data_revision, :cluster_code, :mode, :total, CAST(:subindices AS jsonb),
                    CAST(:attractiveness AS jsonb))
                ON CONFLICT ON CONSTRAINT uq_index_result_snapshot DO NOTHING
                RETURNING id
                """
            ),
            {
                **params,
                "cluster_code": cluster_code,
                "mode": mode,
                "total": r1(score.total),
                "subindices": json.dumps({k: r1(v) for k, v in score.subindices.items()}),
                "attractiveness": json.dumps(attractiveness_out(score, cfg)),
            },
        )
    ).first()
    if row is None:  # identical snapshot already stored
        existing = (
            await session.execute(
                text(
                    """
                    SELECT id FROM index_result
                    WHERE territory_id = :territory_id AND profile_hash = :profile_hash
                      AND scenario_run_id IS NOT DISTINCT FROM :scenario_run_id
                      AND model_version_id = :model_version_id AND data_revision = :data_revision
                    """
                ),
                params,
            )
        ).first()
        return int(existing[0])
    result_id = int(row[0])
    comp_rows = [
        {
            "result_id": result_id,
            "feature_code": c.feature_code,
            "raw_value": c.raw_value,
            "normalized": c.normalized,
            "weight": c.weight,
            "contribution": c.contribution,
            "imputed": bool(c.imputed or (provenance.get(c.feature_code).upstream_imputed if c.feature_code in provenance else False)),
            "inherited_from": provenance[c.feature_code].inherited_from if c.feature_code in provenance else None,
            "value_mode": provenance[c.feature_code].value_mode if c.feature_code in provenance else "any",
        }
        for c in score.components
    ]
    if comp_rows:
        await session.execute(
            text(
                """
                INSERT INTO component (result_id, feature_code, raw_value, normalized, weight,
                    contribution, imputed, inherited_from, value_mode)
                VALUES (:result_id, :feature_code, :raw_value, :normalized, :weight, :contribution,
                    :imputed, :inherited_from, :value_mode)
                """
            ),
            comp_rows,
        )
    return result_id
