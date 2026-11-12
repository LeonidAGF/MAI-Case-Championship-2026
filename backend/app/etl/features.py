"""
Load feature values exported by ML (contract 9.3 "ETL → БД": feature_value(territory, code, raw_value, as_of)).

Accepted layouts (parquet or CSV, auto-detected):
  long:  <territory key>, feature_code, raw_value [, mode, as_of, imputed]
  wide:  <territory key>, F01, F02, ... (optionally F01@car / F01@foot per mobility mode)
Territory key: territory_id | external_id | settlement_osm_id | osm_id |
               municipality_osm_relation_id | region_code (statistics at that level).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.io import find_column, parse_bool, parse_number, read_table
from app.etl.keys import TerritoryKeyResolver
from app.etl.runs import finish_run, start_run

MODES = {"any", "car", "foot"}
WIDE_COLUMN = re.compile(r"^([A-Za-z][A-Za-z0-9]*?)(?:[@:_]+(car|foot|any))?$")
BATCH = 5000

UPSERT = text(
    """
    INSERT INTO feature_value (territory_id, feature_code, mode, as_of, raw_value, imputed, source, etl_run_id)
    VALUES (:tid, :code, :mode, :as_of, :value, :imputed, :source, :run)
    ON CONFLICT (territory_id, feature_code, mode, as_of) DO UPDATE SET raw_value = EXCLUDED.raw_value,
        imputed = EXCLUDED.imputed, source = EXCLUDED.source, etl_run_id = EXCLUDED.etl_run_id
    """
)


def _as_of(value: Any, default: dt.date) -> dt.date:
    if value is None or value == "":
        return default
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value).strip()
    if re.fullmatch(r"\d{4}", s):
        return dt.date(int(s), 12, 31)
    return dt.date.fromisoformat(s[:10])


async def write_feature_values(session: AsyncSession, records: list[dict[str, Any]]) -> int:
    for i in range(0, len(records), BATCH):
        await session.execute(UPSERT, records[i : i + BATCH])
    return len(records)


async def load_features(
    session: AsyncSession,
    path: Path,
    *,
    as_of: dt.date | None = None,
    source: str | None = None,
    replace: bool = False,
    default_mode: str = "any",
) -> dict[str, Any]:
    rows = read_table(path)
    if not rows:
        raise ValueError(f"{path}: file is empty")
    headers = list(rows[0].keys())
    catalog = {r[0] for r in await session.execute(text("SELECT code FROM feature"))}
    if not catalog:
        raise ValueError("feature catalog is empty: run `python -m app.cli seed-reference` first")
    resolver = await TerritoryKeyResolver.create(session)
    key = resolver.detect(headers)
    if key is None:
        raise ValueError("no territory key column (territory_id | external_id | settlement_osm_id | osm_id | "
                         "municipality_osm_relation_id | region_code)")
    key_col, key_kind = key
    feature_col = find_column(headers, ["feature_code", "feature", "code", "признак"], exact_first=False)
    value_col = find_column(headers, ["raw_value", "value", "значение"], exact_first=False)
    mode_col = find_column(headers, ["mode", "режим"], exact_first=False)
    asof_col = find_column(headers, ["as_of", "date", "дата"], exact_first=False)
    imputed_col = find_column(headers, ["imputed"], exact_first=False)
    default_as_of = as_of or dt.date.today()
    source = source or f"ml:{path.name}"

    records: dict[tuple, dict[str, Any]] = {}
    unknown_territories: set[str] = set()
    unknown_features: set[str] = set()
    empty_values = 0

    def add(tid: int, code: str, mode: str, value: float | None, row: dict[str, Any]) -> None:
        nonlocal empty_values
        if value is None:
            empty_values += 1
            return
        when = _as_of(row.get(asof_col) if asof_col else None, default_as_of)
        records[(tid, code, mode, when)] = {
            "tid": tid, "code": code, "mode": mode, "as_of": when, "value": value,
            "imputed": parse_bool(row.get(imputed_col)) if imputed_col else False, "source": source,
        }

    long_format = bool(feature_col and value_col)
    wide_columns: list[tuple[str, str, str]] = []
    if not long_format:
        for h in headers:
            m = WIDE_COLUMN.match(h.strip())
            if m and m.group(1).upper() in catalog:
                wide_columns.append((h, m.group(1).upper(), m.group(2) or default_mode))
        if not wide_columns:
            raise ValueError("no feature columns found: expected long (feature_code, raw_value) or wide (F01, F02, ...)")

    for row in rows:
        tid = resolver.resolve(row.get(key_col), key_kind)
        if tid is None:
            unknown_territories.add(str(row.get(key_col)))
            continue
        if long_format:
            code = str(row.get(feature_col) or "").strip().upper()
            if code not in catalog:
                unknown_features.add(code)
                continue
            mode = str(row.get(mode_col) or default_mode).strip().lower() if mode_col else default_mode
            if mode not in MODES:
                raise ValueError(f"invalid mode {mode!r} (expected any | car | foot)")
            add(tid, code, mode, parse_number(row.get(value_col)), row)
        else:
            for col, code, mode in wide_columns:
                add(tid, code, mode, parse_number(row.get(col)), row)

    run_id = await start_run(session, "features", source)
    codes = sorted({r["code"] for r in records.values()})
    deleted = 0
    if replace and codes:
        deleted = (
            await session.execute(text("DELETE FROM feature_value WHERE feature_code = ANY(:codes)"), {"codes": codes})
        ).rowcount
    payload = [{**r, "run": run_id} for r in records.values()]
    written = await write_feature_values(session, payload)
    stats = {
        "file": str(path),
        "layout": "long" if long_format else "wide",
        "key_column": key_col,
        "values_written": written,
        "features": codes,
        "territories": len({r["tid"] for r in records.values()}),
        "replaced_rows": deleted,
        "empty_values": empty_values,
        "unknown_territories": len(unknown_territories),
        "unknown_territory_examples": sorted(unknown_territories)[:20],
        "unknown_features": sorted(unknown_features),
    }
    await finish_run(session, run_id, stats)
    return {"etl_run_id": run_id, **stats}
