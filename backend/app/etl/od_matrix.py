"""Inter-city travel-time matrix («Точно», ~180 cities) -> normalized pairs (+ optional F14/F21).

Layouts: long (from, to, time) or square (first column = city, other columns = cities).
Units: minutes by default; hours when the time header mentions час/hour or --unit hours.
Output: a normalized CSV (from_name, to_name, from_territory_id, to_territory_id, minutes)
for ML's F14 precompute; with --to-features also writes for SKFO cities in the matrix:
  F14 = time to the subject capital, F21 = time to the nearest other 50k+ city (mode car).
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.features import write_feature_values
from app.etl.io import find_column, norm_header, parse_number, read_table
from app.etl.names import normalize_search
from app.etl.runs import finish_run, start_run

FROM = ["from", "origin", "откуда", "город отправления", "город 1", "city_from", "from_city"]
TO = ["to", "destination", "куда", "город прибытия", "город 2", "city_to", "to_city"]
TIME = ["minutes", "time_min", "время в пути", "время", "duration", "hours", "часы", "time", "value", "значение"]


async def load_od_matrix(
    session: AsyncSession,
    path: Path,
    *,
    unit: str | None = None,
    out: Path | None = None,
    to_features: bool = False,
) -> dict[str, Any]:
    rows = read_table(path)
    if not rows:
        raise ValueError(f"{path}: file is empty")
    headers = list(rows[0])
    c_from, c_to, c_time = find_column(headers, FROM), find_column(headers, TO), find_column(headers, TIME)
    pairs: list[tuple[str, str, float]] = []
    if c_from and c_to and c_time:
        hours = unit == "hours" or (unit is None and any(w in norm_header(c_time) for w in ("час", "hour")))
        for r in rows:
            v = parse_number(r.get(c_time))
            if v is not None and r.get(c_from) and r.get(c_to):
                pairs.append((str(r[c_from]).strip(), str(r[c_to]).strip(), v * 60 if hours else v))
    else:
        name_col = headers[0]
        hours = unit == "hours"
        for r in rows:
            origin = str(r.get(name_col) or "").strip()
            for h in headers[1:]:
                v = parse_number(r.get(h))
                if origin and v is not None and h.strip() and h.strip() != origin:
                    pairs.append((origin, h.strip(), v * 60 if hours else v))
    if not pairs:
        raise ValueError("no city pairs parsed")

    cities = await session.execute(
        text(
            """
            SELECT id, name_normalized, region_code, population, is_admin_center FROM territory
            WHERE level = 'settlement' AND kind IN ('city', 'town')
            """
        )
    )
    by_name: dict[str, list[Any]] = {}
    for row in cities:
        by_name.setdefault(row.name_normalized, []).append(row)

    def resolve(name: str) -> Any | None:
        cands = by_name.get(normalize_search(name), [])
        return cands[0] if len(cands) == 1 else None

    normalized = []
    for a, b, minutes in pairs:
        ra, rb = resolve(a), resolve(b)
        normalized.append((a, b, ra.id if ra else None, rb.id if rb else None, round(minutes, 1), ra, rb))

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["from_name", "to_name", "from_territory_id", "to_territory_id", "minutes"])
            for a, b, ta, tb, m, _, _ in normalized:
                w.writerow([a, b, ta or "", tb or "", m])

    written = 0
    run_id = None
    if to_features:
        run_id = await start_run(session, "od_matrix", path.name)
        f14: dict[int, float] = {}
        f21: dict[int, float] = {}
        for _a, _b, ta, tb, m, ra, rb in normalized:
            if ra is None or rb is None or ta == tb:
                continue
            if rb.is_admin_center and rb.region_code == ra.region_code:
                f14[ta] = min(f14.get(ta, m), m)
            if (rb.population or 0) > 50_000:
                f21[ta] = min(f21.get(ta, m), m)
        today = dt.date.today()
        records = [
            {"tid": tid, "code": code, "mode": "car", "as_of": today, "value": v, "imputed": False,
             "source": f"tochno:{path.name}", "run": run_id}
            for code, data in (("F14", f14), ("F21", f21))
            for tid, v in data.items()
        ]
        written = await write_feature_values(session, records)
    stats = {
        "file": str(path), "pairs": len(pairs),
        "pairs_with_skfo_city": sum(1 for p in normalized if p[2] or p[3]),
        "skfo_cities_matched": len({p[2] for p in normalized if p[2]} | {p[3] for p in normalized if p[3]}),
        "normalized_csv": str(out) if out else None, "feature_values_written": written,
    }
    if run_id is not None:
        await finish_run(session, run_id, stats)
    return {"etl_run_id": run_id, **stats}
