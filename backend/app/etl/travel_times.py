"""
Precomputed territory -> POI travel times (contract 9.3: "Матрица времени (territory_id,
poi_id, seconds, mode)"), produced by the ML/MLOps OSRM precompute. Used by what-if W2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.io import find_column, parse_number, read_table
from app.etl.keys import TerritoryKeyResolver
from app.etl.runs import finish_run, start_run


async def load_travel_times(session: AsyncSession, path: Path, *, replace: bool = False) -> dict[str, Any]:
    rows = read_table(path)
    if not rows:
        raise ValueError(f"{path}: file is empty")
    headers = list(rows[0])
    resolver = await TerritoryKeyResolver.create(session)
    key = resolver.detect(headers)
    if key is None:
        raise ValueError("no territory key column")
    poi_col = find_column(headers, ["poi_id"], exact_first=False)
    poi_ext_col = find_column(headers, ["poi_external_id", "poi_osm_id"], exact_first=False)
    mode_col = find_column(headers, ["mode"], exact_first=False)
    sec_col = find_column(headers, ["seconds", "duration_s", "time_s"], exact_first=False)
    min_col = find_column(headers, ["minutes", "duration_min", "time_min"], exact_first=False)
    if not (poi_col or poi_ext_col) or not (sec_col or min_col):
        raise ValueError("expected poi_id|poi_external_id and seconds|minutes columns")

    poi_by_ext = {r[0]: r[1] for r in await session.execute(text("SELECT external_id, id FROM poi"))}
    poi_ids = set(poi_by_ext.values())
    out: dict[tuple[int, int, str], dict[str, Any]] = {}
    unknown = 0
    for row in rows:
        tid = resolver.resolve(row.get(key[0]), key[1])
        if poi_col:
            raw = parse_number(row.get(poi_col))
            pid = int(raw) if raw is not None and int(raw) in poi_ids else None
        else:
            ext = str(row.get(poi_ext_col) or "").strip()
            pid = poi_by_ext.get(ext) or poi_by_ext.get(f"osm:node:{ext}")
        mode = str(row.get(mode_col) or "car").strip().lower() if mode_col else "car"
        seconds = parse_number(row.get(sec_col)) if sec_col else None
        if seconds is None and min_col:
            minutes = parse_number(row.get(min_col))
            seconds = minutes * 60 if minutes is not None else None
        if tid is None or pid is None or seconds is None or mode not in ("car", "foot"):
            unknown += 1
            continue
        out[(tid, pid, mode)] = {"tid": tid, "pid": pid, "mode": mode, "s": seconds}

    run_id = await start_run(session, "travel_times", path.name)
    if replace:
        await session.execute(text("DELETE FROM travel_time"))
    stmt = text(
        """
        INSERT INTO travel_time (territory_id, poi_id, mode, seconds, etl_run_id)
        VALUES (:tid, :pid, :mode, :s, :run)
        ON CONFLICT (territory_id, poi_id, mode) DO UPDATE SET seconds = EXCLUDED.seconds, etl_run_id = EXCLUDED.etl_run_id
        """
    )
    payload = [{**r, "run": run_id} for r in out.values()]
    for i in range(0, len(payload), 5000):
        await session.execute(stmt, payload[i : i + 5000])
    stats = {"file": str(path), "written": len(payload), "skipped": unknown}
    await finish_run(session, run_id, stats)
    return {"etl_run_id": run_id, **stats}
