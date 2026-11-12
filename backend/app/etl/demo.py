"""
Synthetic ("demo") feature values: the marker and the cleanup.

The product runs on real data only. Older versions could load deterministic synthetic values
tagged source='synthetic-demo' and as_of=1900-01-01; `python -m app.cli seed` (and
`clear-demo-features`) removes them. /healthz, /data/coverage, metrics and the chat still detect
the marker, so leftovers are never shown as real. The generator lives in tests/synthetic.py and
feeds only the integration test database.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.runs import finish_run, start_run

SYNTHETIC_SOURCE = "synthetic-demo"
SYNTHETIC_AS_OF = dt.date(1900, 1, 1)


async def clear_demo_features(session: AsyncSession) -> dict[str, Any]:
    run_id = await start_run(session, "demo-features-clear", SYNTHETIC_SOURCE)
    deleted = (
        await session.execute(text("DELETE FROM feature_value WHERE source = :s"), {"s": SYNTHETIC_SOURCE})
    ).rowcount
    stats = {"deleted": deleted}
    await finish_run(session, run_id, stats)
    return {"etl_run_id": run_id, **stats}
