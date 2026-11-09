"""Seed reference tables from the model config + scenario catalog (idempotent upserts)."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modelcfg import ModelConfig
from app.whatif.catalog import CATALOG


async def seed_reference(session: AsyncSession, cfg: ModelConfig) -> dict[str, int]:
    await session.execute(
        text(
            """
            INSERT INTO subindex (code, name, short_name, in_total, sort_order)
            VALUES (:code, :name, :short_name, :in_total, :sort_order)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, short_name = EXCLUDED.short_name,
                in_total = EXCLUDED.in_total, sort_order = EXCLUDED.sort_order
            """
        ),
        [
            {"code": c, "name": s.name, "short_name": s.short_name, "in_total": s.in_total, "sort_order": i}
            for i, (c, s) in enumerate(cfg.subindices.items())
        ],
    )
    await session.execute(
        text(
            """
            INSERT INTO cluster (code, name, description, thresholds_json, sort_order)
            VALUES (:code, :name, :description, CAST(:params AS jsonb), :sort_order)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
                thresholds_json = EXCLUDED.thresholds_json, sort_order = EXCLUDED.sort_order
            """
        ),
        [
            {"code": c, "name": cl.name, "description": cl.description, "params": json.dumps(cl.params), "sort_order": i}
            for i, (c, cl) in enumerate(cfg.clusters.items())
        ],
    )
    await session.execute(
        text(
            """
            INSERT INTO feature (code, name, unit, direction, subindex_code, level, time_based, source,
                description, default_weight, sort_order)
            VALUES (:code, :name, :unit, :direction, :subindex, :level, :time_based, :source,
                :description, :weight, :sort_order)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, unit = EXCLUDED.unit,
                direction = EXCLUDED.direction, subindex_code = EXCLUDED.subindex_code, level = EXCLUDED.level,
                time_based = EXCLUDED.time_based, source = EXCLUDED.source, description = EXCLUDED.description,
                default_weight = EXCLUDED.default_weight, sort_order = EXCLUDED.sort_order
            """
        ),
        [
            {
                "code": code, "name": f.name, "unit": f.unit, "direction": f.direction, "subindex": f.subindex,
                "level": f.level, "time_based": f.time_based, "source": f.source, "description": f.description,
                "weight": f.weight, "sort_order": i,
            }
            for i, (code, f) in enumerate(cfg.features.items())
        ],
    )
    await session.execute(
        text(
            """
            INSERT INTO profile (id, is_preset, name, description, subindex_weights_json, filters_json,
                key_thresholds, sort_order)
            VALUES (:id, true, :name, :description, CAST(:weights AS jsonb), CAST(:filters AS jsonb),
                :key_thresholds, :sort_order)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
                subindex_weights_json = EXCLUDED.subindex_weights_json, filters_json = EXCLUDED.filters_json,
                key_thresholds = EXCLUDED.key_thresholds, sort_order = EXCLUDED.sort_order
            """
        ),
        [
            {
                "id": pid, "name": p.name, "description": p.description, "weights": json.dumps(p.weights),
                "filters": json.dumps(p.filters), "key_thresholds": p.key_thresholds, "sort_order": i,
            }
            for i, (pid, p) in enumerate(cfg.presets.items())
        ],
    )
    await session.execute(
        text("DELETE FROM profile WHERE is_preset AND NOT (id = ANY(:ids))"), {"ids": list(cfg.presets)}
    )
    await session.execute(
        text(
            """
            INSERT INTO scenario (code, name, type, method, affects, params_json, description, sort_order)
            VALUES (:code, :name, :type, :method, :affects, CAST(:params AS jsonb), :description, :sort_order)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, type = EXCLUDED.type, method = EXCLUDED.method,
                affects = EXCLUDED.affects, params_json = EXCLUDED.params_json,
                description = EXCLUDED.description, sort_order = EXCLUDED.sort_order
            """
        ),
        [
            {
                "code": d.code, "name": d.name, "type": d.type, "method": d.method, "affects": list(d.affects),
                "params": json.dumps({"schema": d.params_schema(), "example": d.example}, ensure_ascii=False),
                "description": d.description, "sort_order": i,
            }
            for i, d in enumerate(CATALOG.values())
        ],
    )
    return {
        "subindices": len(cfg.subindices),
        "clusters": len(cfg.clusters),
        "features": len(cfg.features),
        "presets": len(cfg.presets),
        "scenarios": len(CATALOG),
    }
