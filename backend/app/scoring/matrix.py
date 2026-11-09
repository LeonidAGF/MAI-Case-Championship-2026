"""Feature matrix assembly: latest values per territory with mode resolution and inheritance.

* mode: travel-time features may exist per mobility mode (feature_value.mode = car|foot);
  a profile's mode is preferred, then 'any', then the other mode (flagged via value_mode).
* inheritance: settlements take municipality/region values for statistics published at
  those levels (F18–F23, F27–F30). ``inherited_from`` records where a value came from.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MODE_PREFERENCE = {"car": ("car", "any", "foot"), "foot": ("foot", "any", "car")}


@dataclass(frozen=True, slots=True)
class Provenance:
    inherited_from: str | None  # None (own value) | "municipality" | "region"
    upstream_imputed: bool  # imputed flag set by ETL (plan B data)
    value_mode: str  # car | foot | any


@dataclass
class ClusterMatrix:
    cluster_code: str
    mode: str
    data_revision: int
    territory_ids: list[int]
    values: dict[int, dict[str, float | None]]
    provenance: dict[int, dict[str, Provenance]]
    cluster_params: dict = field(default_factory=dict)


# territory -> feature -> mode -> (value, imputed, as_of); only the latest as_of is kept.
RawValues = dict[int, dict[str, dict[str, tuple[float | None, bool, object]]]]


async def _fetch(
    session: AsyncSession, ids: list[int], feature_codes: Sequence[str]
) -> tuple[RawValues, dict[int, int | None], dict[int, str]]:
    """Latest values of the territories and their ancestors (municipality, region)."""
    parent_of: dict[int, int | None] = {}
    level_of: dict[int, str] = {}
    frontier = ids
    for _ in range(3):
        rows = (
            await session.execute(
                text("SELECT id, parent_id, level FROM territory WHERE id = ANY(:ids)"),
                {"ids": frontier},
            )
        ).all()
        nxt = []
        for tid, parent, level in rows:
            parent_of[tid] = parent
            level_of[tid] = level
            if parent is not None and parent not in parent_of:
                nxt.append(parent)
        if not nxt:
            break
        frontier = nxt

    all_ids = list(parent_of)
    # No DISTINCT ON / ORDER BY: sorting ~10^5 rows costs more than keeping the latest as_of here.
    rows = (
        await session.execute(
            text(
                """
                SELECT territory_id, feature_code, mode, as_of, raw_value, imputed
                FROM feature_value
                WHERE territory_id = ANY(:ids) AND feature_code = ANY(:codes)
                """
            ),
            {"ids": all_ids, "codes": list(feature_codes)},
        )
    ).all()
    raw: RawValues = {}
    for tid, code, m, as_of, value, imputed in rows:
        by_mode = raw.setdefault(tid, {}).setdefault(code, {})
        current = by_mode.get(m)
        if current is None or as_of > current[2]:
            by_mode[m] = (value, bool(imputed), as_of)
    return raw, parent_of, level_of


def _chain(tid: int, parent_of: dict[int, int | None]) -> list[int]:
    chain = [tid]
    p = parent_of.get(tid)
    while p is not None and len(chain) < 3:
        chain.append(p)
        p = parent_of.get(p)
    return chain


async def load_values(
    session: AsyncSession,
    territory_ids: Sequence[int],
    feature_codes: Sequence[str],
    mode: str,
) -> tuple[dict[int, dict[str, float | None]], dict[int, dict[str, Provenance]]]:
    ids = list(dict.fromkeys(int(t) for t in territory_ids))
    if not ids:
        return {}, {}
    raw, parent_of, level_of = await _fetch(session, ids, feature_codes)

    prefs = MODE_PREFERENCE.get(mode, ("any", "car", "foot"))
    # Resolve the mode preference once per owner (territory or ancestor).
    preferred: dict[int, dict[str, tuple[float, bool, str]]] = {}
    for owner, codes in raw.items():
        best: dict[str, tuple[float, bool, str]] = {}
        for code, by_mode in codes.items():
            for m in prefs:
                hit = by_mode.get(m)
                if hit is not None and hit[0] is not None:
                    best[code] = (float(hit[0]), hit[1], m)
                    break
        preferred[owner] = best

    empty: dict[str, tuple[float, bool, str]] = {}
    values: dict[int, dict[str, float | None]] = {}
    provenance: dict[int, dict[str, Provenance]] = {}
    for tid in ids:
        chain = [(owner, None if depth == 0 else level_of.get(owner)) for depth, owner in enumerate(_chain(tid, parent_of))]
        row_v: dict[str, float | None] = {}
        row_p: dict[str, Provenance] = {}
        for code in feature_codes:
            for owner, inherited in chain:
                hit = preferred.get(owner, empty).get(code)
                if hit is not None:
                    row_v[code] = hit[0]
                    row_p[code] = Provenance(inherited_from=inherited, upstream_imputed=hit[1], value_mode=hit[2])
                    break
            else:
                row_v[code] = None
        values[tid] = row_v
        provenance[tid] = row_p
    return values, provenance


async def load_values_all_modes(
    session: AsyncSession, territory_ids: Sequence[int], feature_codes: Sequence[str]
) -> dict[int, dict[str, dict[str, float]]]:
    """territory -> feature -> {mode: value} (own value per mode, else the nearest ancestor's)."""
    ids = list(dict.fromkeys(int(t) for t in territory_ids))
    if not ids:
        return {}
    raw, parent_of, _ = await _fetch(session, ids, feature_codes)
    out: dict[int, dict[str, dict[str, float]]] = {}
    for tid in ids:
        chain = _chain(tid, parent_of)
        row: dict[str, dict[str, float]] = {}
        for code in feature_codes:
            for owner in chain:
                by_mode = {m: hit[0] for m, hit in raw.get(owner, {}).get(code, {}).items() if hit[0] is not None}
                if by_mode:
                    row[code] = {m: float(v) for m, v in by_mode.items()}
                    break
        out[tid] = row
    return out


async def cluster_member_ids(session: AsyncSession, cluster_code: str) -> list[int]:
    rows = await session.execute(
        text("SELECT id FROM territory WHERE cluster_code = :c AND is_scored ORDER BY id"),
        {"c": cluster_code},
    )
    return [r[0] for r in rows]


async def cluster_params(session: AsyncSession, cluster_code: str, fallback: dict) -> dict:
    row = (
        await session.execute(text("SELECT thresholds_json FROM cluster WHERE code = :c"), {"c": cluster_code})
    ).first()
    return dict(row[0]) if row and row[0] else dict(fallback)


class DataRevision:
    """Current data revision = last successful data-changing ETL run (cached briefly)."""

    def __init__(self, ttl_seconds: float = 3.0) -> None:
        self.ttl = ttl_seconds
        self._value: int | None = None
        self._at = 0.0

    async def get(self, session: AsyncSession) -> int:
        now = time.monotonic()
        if self._value is not None and now - self._at < self.ttl:
            return self._value
        value = (
            await session.execute(
                text("SELECT coalesce(max(id), 0) FROM etl_run WHERE status = 'success' AND bumps_revision")
            )
        ).scalar_one()
        self._value, self._at = int(value), now
        return self._value

    def invalidate(self) -> None:
        self._value = None


class MatrixStore:
    """In-process cache of cluster matrices keyed by (cluster, mode, data revision)."""

    def __init__(self, max_entries: int = 12) -> None:
        self.max_entries = max_entries
        self._cache: OrderedDict[tuple, ClusterMatrix] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(
        self,
        session: AsyncSession,
        *,
        cluster_code: str,
        mode: str,
        revision: int,
        feature_codes: Iterable[str],
        params_fallback: dict,
    ) -> ClusterMatrix:
        codes = tuple(feature_codes)
        key = (cluster_code, mode, revision, codes)
        async with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
            ids = await cluster_member_ids(session, cluster_code)
            values, prov = await load_values(session, ids, codes, mode)
            params = await cluster_params(session, cluster_code, params_fallback)
            matrix = ClusterMatrix(cluster_code, mode, revision, ids, values, prov, params)
            self._cache[key] = matrix
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
            return matrix

    def clear(self) -> None:
        self._cache.clear()
