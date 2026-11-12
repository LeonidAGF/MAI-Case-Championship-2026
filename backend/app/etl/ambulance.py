"""
Ambulance travel times -> ambulance_time, one row per scored settlement (`load-ambulance`).

Inputs, all real: ambulance bases = OSM objects in `poi` (categories of config/ambulance.default.yaml,
loaded by `seed-osm`), settlements = scored territories, the ML road graph (GRAPH_DIR, read-only)
and SRTM tiles (SRTM_DIR). The computation is app/ambulance/pipeline.py, run in a worker thread.
The run does not bump the data revision: the index does not depend on these times.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ambulance.config import AmbulanceConfig
from app.ambulance.pipeline import BasePoint, Place, compute
from app.etl.runs import finish_run, start_run

KIND = "ambulance"
BATCH = 1000

UPSERT = text(
    """
    INSERT INTO ambulance_time (territory_id, minutes, flat_minutes, distance_km, elevation_m, details, etl_run_id)
    VALUES (:tid, :minutes, :flat, :km, :elev, CAST(:details AS jsonb), :run)
    ON CONFLICT (territory_id) DO UPDATE SET minutes = EXCLUDED.minutes, flat_minutes = EXCLUDED.flat_minutes,
        distance_km = EXCLUDED.distance_km, elevation_m = EXCLUDED.elevation_m, details = EXCLUDED.details,
        etl_run_id = EXCLUDED.etl_run_id
    """
)


async def load_ambulance(
    session: AsyncSession,
    cfg: AmbulanceConfig,
    *,
    graph_dir: Path,
    dem_dir: Path | None,
    osm_base: str | None = None,
) -> dict[str, Any]:
    base_rows = (
        await session.execute(
            text(
                "SELECT external_id, category, name, ST_X(geom) AS lon, ST_Y(geom) AS lat FROM poi "
                "WHERE category = ANY(:cats) ORDER BY id"
            ),
            {"cats": cfg.bases.categories},
        )
    ).all()
    if not base_rows:
        raise ValueError(
            f"no ambulance bases ({', '.join(cfg.bases.categories)}) in the poi table: run `python -m app.cli seed-osm`"
        )
    place_rows = (
        await session.execute(
            text(
                "SELECT id, ST_X(centroid) AS lon, ST_Y(centroid) AS lat FROM territory "
                "WHERE level = 'settlement' AND is_scored AND centroid IS NOT NULL ORDER BY id"
            )
        )
    ).all()
    if not place_rows:
        raise ValueError("no scored settlements: run `python -m app.cli load-territories` (or seed)")
    bases = [BasePoint(r.external_id, r.category, r.name, float(r.lon), float(r.lat)) for r in base_rows]
    places = [Place(int(r.id), float(r.lon), float(r.lat)) for r in place_rows]

    results, stats = await asyncio.to_thread(compute, Path(graph_dir), dem_dir, cfg, bases, places)

    run_id = await start_run(session, KIND, str(graph_dir), bumps_revision=False)
    payload = [
        {"tid": r.territory_id, "minutes": r.minutes, "flat": r.flat_minutes, "km": r.distance_km,
         "elev": r.elevation_m, "details": json.dumps(r.details, ensure_ascii=False), "run": run_id}
        for r in results
    ]
    for i in range(0, len(payload), BATCH):
        await session.execute(UPSERT, payload[i : i + BATCH])
    removed = (
        await session.execute(
            text("DELETE FROM ambulance_time WHERE etl_run_id IS DISTINCT FROM :run"), {"run": run_id}
        )
    ).rowcount
    out = {
        "config_version": cfg.version,
        "model_hash": cfg.model_hash,
        "osm_base": osm_base,
        "rows": len(payload),
        "removed": removed,
        **stats,
    }
    await finish_run(session, run_id, out)
    return {"etl_run_id": run_id, **out}
