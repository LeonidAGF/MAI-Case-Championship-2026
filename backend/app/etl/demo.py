"""DEV ONLY: deterministic synthetic feature values so the frontend can integrate before ML's
real features are loaded. Every value is tagged source='synthetic-demo' and as_of=1900-01-01;
/healthz and /data/coverage report it loudly. Never present these numbers as real.
Remove with `python -m app.cli clear-demo-features`.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.features import write_feature_values
from app.etl.runs import finish_run, start_run
from app.whatif.geo import haversine_km

SYNTHETIC_SOURCE = "synthetic-demo"
SYNTHETIC_AS_OF = dt.date(1900, 1, 1)
CLUSTER_FACTOR = {"A": 0.35, "B": 0.65, "V": 1.0}
TIME_BASE = {"F01": 25, "F02": 10, "F05": 8, "F06": 8, "F07": 35, "F15": 30, "F16": 80, "F17": 15}
MUNICIPAL_RANGES = {
    "F18": (10, 70), "F19": (0, 40), "F20": (5, 40), "F22": (40, 250), "F23": (3, 30),
    "F27": (-500, 800), "F28": (-8, 10), "F29": (10, 400), "F30": (5, 60),
}


async def seed_demo_features(session: AsyncSession, *, seed: int = 7) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                """
                SELECT t.id, t.cluster_code, t.region_code, ST_X(t.centroid), ST_Y(t.centroid), t.population,
                       t.is_admin_center
                FROM territory t WHERE t.is_scored AND t.centroid IS NOT NULL
                """
            )
        )
    ).all()
    if not rows:
        raise ValueError("no scored territories: run `python -m app.cli seed` first")
    capitals = {r[2]: (r[3], r[4]) for r in rows if r[6]}
    big = [(r[3], r[4]) for r in rows if (r[5] or 0) > 50_000]
    municipalities = [r[0] for r in await session.execute(text("SELECT id FROM territory WHERE level = 'municipality'"))]

    run_id = await start_run(session, "demo-features", SYNTHETIC_SOURCE)
    out: list[dict[str, Any]] = []

    def put(tid: int, code: str, mode: str, value: float) -> None:
        out.append({"tid": tid, "code": code, "mode": mode, "as_of": SYNTHETIC_AS_OF, "value": round(value, 2),
                    "imputed": False, "source": SYNTHETIC_SOURCE, "run": run_id})

    for tid, cluster, region, lon, lat, population, is_capital in rows:
        rng = random.Random(f"{seed}:{tid}")
        cap = capitals.get(region)
        d_cap = haversine_km(lon, lat, *cap) if cap else 80.0
        d_big = 0.0 if (population or 0) > 50_000 else min((haversine_km(lon, lat, x, y) for x, y in big), default=60.0)
        remote = min(1.5, d_cap / 150.0)
        factor = CLUSTER_FACTOR.get(cluster, 1.0)

        def jitter() -> float:
            return rng.uniform(0.7, 1.3)

        for code, base in TIME_BASE.items():
            car = max(2.0, base * factor * (0.6 + remote) * jitter())
            put(tid, code, "car", car)
            put(tid, code, "foot", car * rng.uniform(2.5, 4.0) + 5)
        put(tid, "F04", "any", max(3.0, 15 * factor * (0.6 + remote) * jitter()))
        f14 = 5 + rng.uniform(0, 5) if is_capital else d_cap * 1.35 / 55 * 60 * jitter()
        put(tid, "F14", "car", f14)
        put(tid, "F14", "foot", f14 * 2.2)
        f21 = 5 + rng.uniform(0, 5) if d_big == 0 else d_big * 1.35 / 55 * 60 * jitter()
        put(tid, "F21", "car", f21)
        put(tid, "F21", "foot", f21 * 2.2)
        richness = max(0.05, (1.3 - remote) / factor)
        put(tid, "F03", "car", round(max(0, 6 * richness * jitter())))
        put(tid, "F03", "foot", round(max(0, 2 * richness * jitter())))
        put(tid, "F08", "car", round(max(0, 3 * richness * jitter())))
        put(tid, "F08", "foot", round(max(0, 1.2 * richness * jitter())))
        put(tid, "F09", "any", round(max(0, 8 * richness * jitter())))
        put(tid, "F10", "any", 1.0 if rng.random() < min(0.95, 0.25 * richness) else 0.0)
        put(tid, "F11", "car", round(max(0, 4 * richness * jitter())))
        put(tid, "F11", "foot", round(max(0, 1.5 * richness * jitter())))
        put(tid, "F12", "car", max(0.0, 20 * richness * jitter()))
        put(tid, "F12", "foot", max(0.0, 6 * richness * jitter()))
        put(tid, "F13", "any", 1.0 if rng.random() < min(0.95, 0.3 * richness) else 0.0)
        put(tid, "F24", "any", 20 + 40 * rng.random() + 10 * (lat - 42))
        put(tid, "F25", "any", 10 + 30 * rng.random())
        put(tid, "F26", "any", 2 + 4 * rng.random())

    for mid in municipalities:
        rng = random.Random(f"{seed}:m:{mid}")
        for code, (lo, hi) in MUNICIPAL_RANGES.items():
            put(mid, code, "any", rng.uniform(lo, hi))

    written = await write_feature_values(session, out)
    stats = {"values_written": written, "territories": len(rows), "municipalities": len(municipalities),
             "warning": "SYNTHETIC DATA — for frontend integration only"}
    await finish_run(session, run_id, stats)
    return {"etl_run_id": run_id, **stats}


async def clear_demo_features(session: AsyncSession) -> dict[str, Any]:
    run_id = await start_run(session, "demo-features-clear", SYNTHETIC_SOURCE)
    deleted = (
        await session.execute(text("DELETE FROM feature_value WHERE source = :s"), {"s": SYNTHETIC_SOURCE})
    ).rowcount
    stats = {"deleted": deleted}
    await finish_run(session, run_id, stats)
    return {"etl_run_id": run_id, **stats}
