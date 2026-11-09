"""Municipal statistics parsers (BE2 stage 1): ЕМИСС / Росстат БДМО (incl. «Точно» exports) /
ФНС aggregates / wide Rosstat tables -> feature_value at municipality (or subject) level.

Layouts (auto-detected; headers matched case-insensitively, ё = е):
  long  — region | МО | ОКТМО | показатель | год | период | значение  (БДМО / ЕМИСС / Точно:
          "CSV (UTF-8, разделитель ;)"); filter rows with --indicator / --period;
  wide  — МО (+ регион/ОКТМО) and year columns 2019, 2020, ... (Rosstat tables, ФНС pivots).
Municipalities are matched by OKTMO when known, else by region + normalized name + kind
(app/etl/names.py). Sub-municipal rows (поселения, OKTMO not ending with 000) are skipped.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.features import write_feature_values
from app.etl.io import find_column, norm_header, parse_number, read_table
from app.etl.names import KIND_OF_MUNICIPALITY, MunicipalityMatcher, MunicipalityRef, digits, normalize_municipality, region_code_from
from app.etl.runs import finish_run, start_run

COLUMNS = {
    "region": ["регион", "субъект рф", "субъект", "region", "region_name"],
    "municipality": ["мо", "муниципальное образование", "наименование мо", "муниципалитет", "municipality",
                     "наименование муниципального образования", "территория", "mun_name", "municipality_name"],
    "oktmo": ["октмо", "код октмо", "oktmo", "oktmo_code", "код по октмо"],
    "indicator": ["показатель", "наименование показателя", "indicator", "indicator_name"],
    "year": ["год", "year"],
    "period": ["период", "period", "period_name"],
    "value": ["значение", "значение показателя", "value", "indicator_value"],
}
SUB_MUNICIPAL = re.compile(r"поселени|сельсовет|\bсело\b|станица|\bпоселок\b|\bаул\b|\bхутор\b|внутригородск", re.I)
YEAR_HEADER = re.compile(r"^\s*((?:19|20)\d{2})(?:\s*г\.?)?\s*$")


@dataclass
class StatRow:
    region: str | None
    municipality: str | None
    oktmo: str | None
    year: int | None
    value: float


def _col(headers: list[str], key: str) -> str | None:
    return find_column(headers, COLUMNS[key])


def _year(v: Any) -> int | None:
    m = re.search(r"(19|20)\d{2}", str(v or ""))
    return int(m.group(0)) if m else None


def parse_long(rows: list[dict[str, Any]], *, indicator: str | None, period: str | None) -> list[StatRow]:
    headers = list(rows[0])
    c_val = _col(headers, "value")
    if c_val is None:
        raise ValueError("long layout: value column (значение/value) not found")
    c_reg, c_mun, c_okt = _col(headers, "region"), _col(headers, "municipality"), _col(headers, "oktmo")
    c_ind, c_year, c_per = _col(headers, "indicator"), _col(headers, "year"), _col(headers, "period")
    if indicator and not c_ind:
        raise ValueError("--indicator given but the file has no indicator column (показатель)")
    out = []
    for r in rows:
        if indicator and indicator.lower() not in norm_header(str(r.get(c_ind) or "")):
            continue
        if period and c_per and period.lower() not in norm_header(str(r.get(c_per) or "")):
            continue
        value = parse_number(r.get(c_val))
        if value is None:
            continue
        out.append(StatRow(
            region=r.get(c_reg) if c_reg else None,
            municipality=r.get(c_mun) if c_mun else None,
            oktmo=r.get(c_okt) if c_okt else None,
            year=_year(r.get(c_year)) if c_year else (_year(r.get(c_per)) if c_per else None),
            value=value,
        ))
    return out


def parse_wide(rows: list[dict[str, Any]], *, year: int | None) -> list[StatRow]:
    headers = list(rows[0])
    year_cols = {int(m.group(1)): h for h in headers if (m := YEAR_HEADER.match(str(h)))}
    if not year_cols:
        raise ValueError("wide layout: no year columns (2019, 2020, ...)")
    c_reg, c_mun, c_okt = _col(headers, "region"), _col(headers, "municipality"), _col(headers, "oktmo")
    if c_mun is None:
        c_mun = headers[0]
    out = []
    for r in rows:
        years = [year] if year else sorted(year_cols, reverse=True)
        for y in years:
            if y not in year_cols:
                continue
            value = parse_number(r.get(year_cols[y]))
            if value is not None:
                out.append(StatRow(r.get(c_reg) if c_reg else None, r.get(c_mun), r.get(c_okt) if c_okt else None, y, value))
                break
    return out


async def municipality_refs(session: AsyncSession) -> list[MunicipalityRef]:
    rows = await session.execute(
        text("SELECT id, name, region_code, kind, oktmo FROM territory WHERE level = 'municipality'")
    )
    refs = []
    for tid, name, region, kind, oktmo in rows:
        core, _ = normalize_municipality(name)
        refs.append(MunicipalityRef(tid, name, region, KIND_OF_MUNICIPALITY.get(kind, "district"), core, oktmo))
    return refs


async def load_stats(
    session: AsyncSession,
    path: Path,
    *,
    feature: str,
    layout: str = "auto",
    indicator: str | None = None,
    period: str | None = None,
    year: int | None = None,
    scale: float = 1.0,
    learn_oktmo: bool = False,
    dry_run: bool = False,
    source: str | None = None,
) -> dict[str, Any]:
    feature = feature.upper()
    exists = (await session.execute(text("SELECT 1 FROM feature WHERE code = :c"), {"c": feature})).first()
    if not exists:
        raise ValueError(f"unknown feature {feature}")
    rows = read_table(path)
    if not rows:
        raise ValueError(f"{path}: file is empty")
    headers = list(rows[0])
    if layout == "auto":
        layout = "long" if _col(headers, "value") else "wide"
    parsed = parse_long(rows, indicator=indicator, period=period) if layout == "long" else parse_wide(rows, year=year)
    if layout == "long" and year:
        parsed = [r for r in parsed if r.year == year]

    matcher = MunicipalityMatcher(await municipality_refs(session))
    regions = {r[0]: r[1] for r in await session.execute(text("SELECT region_code, id FROM territory WHERE level = 'region'"))}
    best: dict[int, StatRow] = {}
    methods: dict[str, int] = {}
    unmatched: list[str] = []
    skipped_sub = 0
    learned: dict[int, str] = {}
    for r in parsed:
        okt = digits(r.oktmo)
        name = (r.municipality or "").strip()
        if okt and len(okt) >= 8 and okt[5:8] != "000":
            skipped_sub += 1
            continue
        if name and SUB_MUNICIPAL.search(name):
            skipped_sub += 1
            continue
        region_code = region_code_from(r.region, okt)
        if not name and (not okt or len(okt) < 8):
            tid = regions.get(region_code or "")
            how = "region" if tid else "none"
        else:
            ref, how = matcher.match(name, region_code=region_code, oktmo=okt)
            tid = ref.id if ref else None
            if ref and okt and len(okt) >= 8 and not ref.oktmo:
                learned[ref.id] = okt[:8]
        methods[how] = methods.get(how, 0) + 1
        if tid is None:
            unmatched.append(f"{r.region or ''} / {name or okt}")
            continue
        prev = best.get(tid)
        if prev is None or (r.year or 0) >= (prev.year or 0):
            best[tid] = r

    run_id = None if dry_run else await start_run(session, "stats", source or path.name)
    records = [
        {"tid": tid, "code": feature, "mode": "any",
         "as_of": dt.date(r.year, 12, 31) if r.year else dt.date.today(),
         "value": r.value * scale, "imputed": False, "source": source or f"stats:{path.name}", "run": run_id}
        for tid, r in best.items()
    ]
    if not dry_run:
        await write_feature_values(session, records)
        if learn_oktmo:
            for tid, okt in learned.items():
                await session.execute(
                    text("UPDATE territory SET oktmo = :o WHERE id = :id AND oktmo IS NULL"), {"o": okt, "id": tid}
                )
    stats = {
        "file": str(path), "layout": layout, "feature": feature, "rows_parsed": len(parsed),
        "territories_written": 0 if dry_run else len(records), "match_methods": methods,
        "skipped_sub_municipal": skipped_sub, "unmatched": len(unmatched),
        "unmatched_examples": sorted(set(unmatched))[:30], "oktmo_learned": len(learned) if learn_oktmo else 0,
        "years": sorted({r.year for r in best.values() if r.year}), "dry_run": dry_run,
    }
    if run_id is not None:
        await finish_run(session, run_id, stats)
    return {"etl_run_id": run_id, **stats}
