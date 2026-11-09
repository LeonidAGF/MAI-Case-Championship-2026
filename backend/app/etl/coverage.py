"""Data completeness report (stage 1 DoD + checklist item 4: "отчёт о полноте POI по семи субъектам")."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.io import parse_number
from app.etl.territories import read_csv_dicts
from app.modelcfg import ModelConfig

LOW_COMPLETENESS = 0.7


async def coverage_report(session: AsyncSession, cfg: ModelConfig, *, seeds_dir: Path) -> dict[str, Any]:
    regions = {
        r[0]: r[1]
        for r in await session.execute(text("SELECT region_code, name FROM territory WHERE level = 'region' ORDER BY name"))
    }
    clusters: dict[str, dict[str, int]] = {}
    for region, cluster, n in await session.execute(
        text("SELECT region_code, cluster_code, count(*) FROM territory WHERE is_scored GROUP BY 1, 2")
    ):
        clusters.setdefault(region, {})[cluster] = n

    feature_rows = await session.execute(
        text(
            """
            WITH s AS (SELECT id, parent_id, region_code FROM territory WHERE is_scored),
                 fv AS (SELECT DISTINCT territory_id, feature_code FROM feature_value),
                 r AS (SELECT id, region_code FROM territory WHERE level = 'region')
            SELECT s.region_code, f.code,
                   count(*) FILTER (WHERE own.territory_id IS NOT NULL) AS own,
                   count(*) FILTER (WHERE own.territory_id IS NULL
                                    AND (par.territory_id IS NOT NULL OR reg.territory_id IS NOT NULL)) AS inherited,
                   count(*) AS total
            FROM s CROSS JOIN feature f
            LEFT JOIN fv own ON own.territory_id = s.id AND own.feature_code = f.code
            LEFT JOIN fv par ON par.territory_id = s.parent_id AND par.feature_code = f.code
            LEFT JOIN r ON r.region_code = s.region_code
            LEFT JOIN fv reg ON reg.territory_id = r.id AND reg.feature_code = f.code
            GROUP BY s.region_code, f.code
            """
        )
    )
    features: dict[str, dict[str, Any]] = {}
    per_region_fill: dict[str, list[float]] = {}
    for region, code, own, inherited, total in feature_rows:
        share = (own + inherited) / total if total else 0.0
        features.setdefault(code, {})[region] = {"own": own, "inherited": inherited, "total": total, "share": round(share, 3)}
        per_region_fill.setdefault(region, []).append(share)

    imputed = dict(
        (await session.execute(text("SELECT feature_code, count(*) FROM feature_value WHERE imputed GROUP BY 1"))).all()
    )
    sources = [
        {"source": s, "values": n}
        for s, n in await session.execute(
            text("SELECT source, count(*) FROM feature_value GROUP BY source ORDER BY count(*) DESC LIMIT 20")
        )
    ]
    poi: dict[str, dict[str, int]] = {}
    for region, cat, n in await session.execute(
        text(
            """
            SELECT coalesce(t.region_code, 'outside'), p.category, count(*)
            FROM poi p LEFT JOIN territory t ON t.id = p.territory_id GROUP BY 1, 2
            """
        )
    ):
        poi.setdefault(region, {})[cat] = n

    official = []
    for row in read_csv_dicts(seeds_dir / "official_poi_counts.csv"):
        expected = parse_number(row.get("official_count"))
        actual = poi.get(row["region_code"], {}).get(row["category"], 0)
        ratio = actual / expected if expected else None
        official.append({
            "region_code": row["region_code"], "category": row["category"], "osm": actual,
            "official": expected, "ratio": round(ratio, 3) if ratio is not None else None,
            "low": ratio is not None and ratio < LOW_COMPLETENESS, "source": row.get("source"),
        })

    runs = [
        {"id": r[0], "kind": r[1], "source": r[2], "status": r[3], "started_at": r[4], "finished_at": r[5], "error": r[6]}
        for r in await session.execute(
            text(
                "SELECT id, kind, source, status, started_at, finished_at, error FROM etl_run ORDER BY id DESC LIMIT 15"
            )
        )
    ]
    synthetic = bool(
        (await session.execute(text("SELECT EXISTS(SELECT 1 FROM feature_value WHERE source = 'synthetic-demo')"))).scalar()
    )
    return {
        "synthetic_demo_data": synthetic,
        "regions": [
            {
                "region_code": code,
                "name": name,
                "scored_by_cluster": clusters.get(code, {}),
                "feature_fill_share": round(sum(per_region_fill.get(code, [0])) / max(1, len(per_region_fill.get(code, []))), 3),
                "poi_by_category": poi.get(code, {}),
            }
            for code, name in regions.items()
        ],
        "features": {
            code: {"name": f.name, "by_region": features.get(code, {}), "imputed_values": imputed.get(code, 0)}
            for code, f in cfg.features.items()
        },
        "poi_outside_skfo": poi.get("outside", {}),
        "poi_vs_official": official,
        "feature_sources": sources,
        "recent_etl_runs": runs,
    }
