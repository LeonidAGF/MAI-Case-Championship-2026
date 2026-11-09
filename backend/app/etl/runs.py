"""etl_run bookkeeping: every load is recorded; data-changing runs bump the data revision."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def start_run(session: AsyncSession, kind: str, source: str, *, bumps_revision: bool = True) -> int:
    return (
        await session.execute(
            text(
                "INSERT INTO etl_run (kind, source, status, bumps_revision) "
                "VALUES (:k, :s, 'running', :b) RETURNING id"
            ),
            {"k": kind, "s": source, "b": bumps_revision},
        )
    ).scalar_one()


async def finish_run(session: AsyncSession, run_id: int, stats: dict[str, Any]) -> None:
    await session.execute(
        text(
            "UPDATE etl_run SET status = 'success', finished_at = now(), stats_json = CAST(:st AS jsonb) WHERE id = :id"
        ),
        {"st": json.dumps(stats, ensure_ascii=False, default=str), "id": run_id},
    )


async def record_failure(session: AsyncSession, kind: str, source: str, error: str) -> None:
    await session.execute(
        text(
            "INSERT INTO etl_run (kind, source, status, bumps_revision, finished_at, error) "
            "VALUES (:k, :s, 'failed', false, now(), :e)"
        ),
        {"k": kind, "s": source, "e": error[:4000]},
    )
