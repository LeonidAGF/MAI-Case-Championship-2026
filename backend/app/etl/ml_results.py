"""
ML gravity results -> territory.meta.ml (read-only use of the ML part's committed exports).

ml_results/settlement_gravity_observed.csv is produced by scripts/15_calculate_gravity.py (status
provisional). For every settlement it holds the share of the known СКФО population that can be
reached, weighted by travel-time decay with a half-life of 30 / 60 / 120 minutes:
    gravity_X = sum_j pop_j * 2^(-t_ij / X) / known_destination_population
known_destination_population is the population of the other settlements with known population, so
gravity_X * known_destination_population = buyers within reach (decay-weighted people, the
settlement itself excluded). The loader stores these numbers per settlement as meta.ml; nothing is
recomputed here.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.io import parse_number, read_table
from app.etl.keys import TerritoryKeyResolver
from app.etl.runs import finish_run, start_run

GRAVITY_FILE = "settlement_gravity_observed.csv"
KEY_COLUMN = "settlement_id"  # OSM node id of the settlement (territory.external_id = osm:node:<id>)
SHARES = {  # column -> meta.ml key; shares of the known population, 0..1
    "gravity_observed_30": "gravity_30",
    "gravity_observed_60": "gravity_60",
    "gravity_observed_120": "gravity_120",
    "known_population_reachability": "reachability",
    "population_data_coverage": "population_data_coverage",
}
COUNTS = {"known_destination_population": "known_destination_population"}
REQUIRED = (KEY_COLUMN, "known_destination_population", "gravity_observed_30", "gravity_observed_120")
BATCH = 2000

UPDATE = text("UPDATE territory SET meta = meta || jsonb_build_object('ml', CAST(:ml AS jsonb)) WHERE id = :id")


def _record(row: dict[str, Any], stats: dict[str, int]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for col, key in SHARES.items():
        value = parse_number(row.get(col))
        if value is not None and not 0.0 <= value <= 1.0 + 1e-9:
            stats["invalid_values"] += 1
            value = None
        out[key] = value
    for col, key in COUNTS.items():
        value = parse_number(row.get(col))
        if value is not None and (value <= 0 or not math.isfinite(value)):
            stats["invalid_values"] += 1
            value = None
        out[key] = value
    return out


async def load_ml_results(session: AsyncSession, ml_dir: Path, *, replace: bool = True) -> dict[str, Any]:
    path = Path(ml_dir) / GRAVITY_FILE
    rows = read_table(path)
    if not rows:
        raise ValueError(f"{path}: file is empty")
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        raise ValueError(f"{path}: missing columns {missing} (expected the output of scripts/15_calculate_gravity.py)")

    resolver = await TerritoryKeyResolver.create(session)
    stats = {"rows": len(rows), "matched": 0, "unknown_settlements": 0, "with_gravity": 0, "invalid_values": 0}
    records: dict[int, dict[str, float | None]] = {}
    for row in rows:
        tid = resolver.resolve(row.get(KEY_COLUMN), "node")
        if tid is None:
            stats["unknown_settlements"] += 1
            continue
        rec = _record(row, stats)
        records[tid] = rec
        if rec["gravity_30"] is not None and rec["known_destination_population"] is not None:
            stats["with_gravity"] += 1
    stats["matched"] = len(records)

    source = f"ml:{path.name}"
    run_id = await start_run(session, "ml_results", source)
    cleared = 0
    if replace:
        cleared = (
            await session.execute(text("UPDATE territory SET meta = meta - 'ml' WHERE meta -> 'ml' IS NOT NULL"))
        ).rowcount
    payload = [{"id": tid, "ml": json.dumps({**rec, "source": path.name})} for tid, rec in records.items()]
    for i in range(0, len(payload), BATCH):
        await session.execute(UPDATE, payload[i : i + BATCH])
    out = {**stats, "cleared": cleared, "file": str(path)}
    await finish_run(session, run_id, out)
    return {"etl_run_id": run_id, **out}
