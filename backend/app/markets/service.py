"""
Sales-market attractiveness of settlements and its assessment for production sectors (tasks 4+5).

Market parts (config/markets.default.yaml) come from data the backend already holds; nothing is
estimated or invented:
  population   territory.population (OSM; known for part of the settlements)
  ml_gravity   territory.meta.ml: gravity_X x known_destination_population (ML gravity model, provisional;
               the sum covers the other settlements) + the settlement's own population
  poi          weighted count of OSM objects within radius_km (PostGIS, straight line)
  feature      feature_value in minutes by car, resolved like the index (car -> any -> foot)

Score (main metric): absolute 0..100 from the settlement's own values only, so it does not move
when other settlements change. Each part has a fixed scale (log for sizes, decay for travel
times); market = mean of its parts with data; sector = weighted mean of the markets with data
(coverage = their total weight, below min_coverage the sector is not assessed). Tier = fixed
thresholds on the score.
Additional metrics: percentile of the score across СКФО and inside the settlement's cluster A/B/V
("better than X% of settlements") and the place in the cluster.
A settlement without a value is left out of that part (no imputation). The table is built for all
settlements at once and cached per data revision and configuration.
"""

from __future__ import annotations

import asyncio
import math
import statistics
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationFailed
from app.markets.config import GRAVITY_SOURCES, MarketsConfig, PartCfg
from app.markets.texts import compare_text, fmt_score, part_text, scale_text, verdict
from app.modelcfg import ModelConfig
from app.scoring.matrix import DataRevision, load_values

TIERS = ("high", "medium", "low")
EXPLAIN_MIN_WEIGHT = 0.1  # lighter markets are not named as strengths or risks
EXPLAIN_TOP = 2
LEADERS = 5
DEGREES_PER_KM = 1 / 70.0  # bbox prefilter, wider than 1 km at СКФО latitudes; the exact test is geographic


def percentiles(values: dict[int, float]) -> dict[int, float]:
    """Rank percentile 0..100 (ties share the average rank); higher value = higher percentile."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    out: dict[int, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        p = 50.0 if n == 1 else 100.0 * ((i + j) / 2) / (n - 1)
        for k in range(i, j + 1):
            out[items[k][0]] = p
        i = j + 1
    return out


def _r1(x: float | None) -> float | None:
    return None if x is None else round(float(x), 1)


def _value(part: PartCfg, x: float | None) -> float | None:
    if x is None:
        return None
    return float(round(x)) if part.source in ("population", *GRAVITY_SOURCES) else round(float(x), 1)


@dataclass
class Settlement:
    id: int
    name: str
    region_code: str | None
    municipality: str | None
    cluster: str
    population: int | None


@dataclass
class Scope:
    """Additional metrics inside one comparison scope (a cluster or СКФО)."""

    market: dict[int, dict[str, float]]  # territory -> market -> percentile of the score
    sector: dict[int, dict[str, float]]  # territory -> sector -> percentile of the score
    rank: dict[str, dict[int, int]]  # sector -> territory -> place by score (1 = best)
    size: dict[str, int]  # sector -> settlements assessed


@dataclass
class MarketTable:
    revision: int
    settlements: dict[int, Settlement]
    raw: dict[int, dict[str, float | None]]  # territory -> part key -> value
    part: dict[int, dict[str, float]]  # territory -> part key -> score 0..100
    market: dict[int, dict[str, float]]  # territory -> market -> score 0..100
    sector: dict[int, dict[str, float]]  # territory -> sector -> score 0..100
    coverage: dict[int, dict[str, float]]  # territory -> sector -> weight of markets with data
    skfo: Scope
    clusters: dict[str, Scope]
    warnings: list[str]

    def cluster_scope(self, tid: int) -> Scope:
        return self.clusters[self.settlements[tid].cluster]


class MarketService:
    def __init__(self, cfg: ModelConfig, markets: MarketsConfig, revision: DataRevision) -> None:
        markets.check_features(set(cfg.features))
        self.cfg = cfg
        self.mcfg = markets
        self.revision = revision
        self._cache: tuple[tuple[int, str], MarketTable] | None = None
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._cache = None

    def tier(self, score: float | None) -> str | None:
        return self.mcfg.tiers.of(score)

    def catalog(self) -> dict[str, Any]:
        m = self.mcfg
        return {
            "version": m.version,
            "radius_km": m.radius_km,
            "min_coverage": m.min_coverage,
            "tiers": {"high": m.tiers.high, "medium": m.tiers.medium},
            "markets": [
                {"code": code, "name": mk.name, "description": mk.description,
                 "parts": [{"key": p.key, "label": p.label, "unit": p.unit, "direction": p.direction,
                            "source": p.source, "scale": p.scale.model_dump(exclude_none=True),
                            "scale_text": scale_text(p.scale, p.unit)} for p in mk.parts]}
                for code, mk in m.markets.items()
            ],
            "sectors": [self._sector_info(code) for code in m.sectors],
            "notes": list(m.notes),
        }

    def _sector_info(self, code: str) -> dict[str, Any]:
        s = self.mcfg.sectors[code]
        return {"code": code, "name": s.name, "examples": s.examples, "description": s.description,
                "weights": dict(s.weights)}

    # --- computation ----------------------------------------------------------------
    async def table(self, session: AsyncSession) -> MarketTable:
        key = (await self.revision.get(session), self.mcfg.config_hash)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        async with self._lock:
            if self._cache is not None and self._cache[0] == key:
                return self._cache[1]
            table = await self._compute(session, key[0])
            self._cache = (key, table)
            return table

    async def _compute(self, session: AsyncSession, revision: int) -> MarketTable:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT t.id, t.name, t.region_code, t.cluster_code, t.population, m.name AS municipality,
                           t.meta -> 'ml' AS ml
                    FROM territory t LEFT JOIN territory m ON m.id = t.parent_id
                    WHERE t.is_scored AND t.cluster_code IS NOT NULL
                    """
                )
            )
        ).all()
        settlements = {
            r.id: Settlement(r.id, r.name, r.region_code, r.municipality, r.cluster_code,
                             int(r.population) if r.population else None)
            for r in rows
        }
        ml = {r.id: (r.ml or {}) for r in rows}
        poi = await self._poi_counts(session)
        features = sorted({p.feature for _, p in self.mcfg.parts if p.feature})
        values = (await load_values(session, list(settlements), features, "car"))[0] if features else {}
        raw = {
            tid: {p.key: self._raw(p, s, ml[tid], poi, values.get(tid, {})) for _, p in self.mcfg.parts}
            for tid, s in settlements.items()
        }
        part, market, sector, coverage = self._score(raw)
        skfo = self._scope(list(settlements), market, sector)
        clusters = {
            cl: self._scope([tid for tid, s in settlements.items() if s.cluster == cl], market, sector)
            for cl in sorted({s.cluster for s in settlements.values()})
        }
        return MarketTable(revision, settlements, raw, part, market, sector, coverage, skfo, clusters,
                           self._warnings(settlements, raw, ml, poi))

    def _score(self, raw: dict[int, dict[str, float | None]]) -> tuple[dict, dict, dict, dict]:
        """Absolute scores: each settlement from its own values only."""
        part: dict[int, dict[str, float]] = {}
        market: dict[int, dict[str, float]] = {}
        sector: dict[int, dict[str, float]] = {}
        coverage: dict[int, dict[str, float]] = {}
        for tid, values in raw.items():
            part[tid] = {p.key: p.scale.score(v) for _, p in self.mcfg.parts if (v := values[p.key]) is not None}
            market[tid] = {}
            for code, mk in self.mcfg.markets.items():
                got = [part[tid][p.key] for p in mk.parts if p.key in part[tid]]
                if got:
                    market[tid][code] = sum(got) / len(got)
            sector[tid], coverage[tid] = {}, {}
            for code, sc in self.mcfg.sectors.items():
                avail = [(w, market[tid][m]) for m, w in sc.weights.items() if w > 0 and m in market[tid]]
                total = sum(w for w, _ in avail)
                coverage[tid][code] = round(total, 4)
                if total >= self.mcfg.min_coverage - 1e-9:
                    sector[tid][code] = sum(w * v for w, v in avail) / total
        return part, market, sector, coverage

    def _scope(self, members: list[int], market: dict, sector: dict) -> Scope:
        market_pct: dict[int, dict[str, float]] = {tid: {} for tid in members}
        for code in self.mcfg.markets:
            for tid, p in percentiles({t: market[t][code] for t in members if code in market[t]}).items():
                market_pct[tid][code] = p
        sector_pct: dict[int, dict[str, float]] = {tid: {} for tid in members}
        rank: dict[str, dict[int, int]] = {}
        size: dict[str, int] = {}
        for code in self.mcfg.sectors:
            scores = {t: sector[t][code] for t in members if code in sector[t]}
            for tid, p in percentiles(scores).items():
                sector_pct[tid][code] = p
            order = sorted(scores, key=lambda t: (-scores[t], t))
            rank[code] = {tid: i for i, tid in enumerate(order, 1)}
            size[code] = len(order)
        return Scope(market_pct, sector_pct, rank, size)

    async def _poi_counts(self, session: AsyncSession) -> dict[int, dict[str, int]] | None:
        """territory -> category -> objects within radius_km; None when the POI layer is empty."""
        cats = sorted({c for _, p in self.mcfg.parts for c in p.categories})
        if not cats or not (await session.execute(text("SELECT EXISTS (SELECT 1 FROM poi)"))).scalar():
            return None
        rows = await session.execute(
            text(
                """
                SELECT t.id, p.category, count(*) AS n
                FROM territory t
                JOIN poi p ON p.category = ANY(:cats)
                 AND ST_DWithin(t.centroid, p.geom, :deg)
                 AND ST_DWithin(t.centroid::geography, p.geom::geography, :m)
                WHERE t.is_scored AND t.centroid IS NOT NULL
                GROUP BY t.id, p.category
                """
            ),
            {"cats": cats, "deg": self.mcfg.radius_km * DEGREES_PER_KM, "m": self.mcfg.radius_km * 1000.0},
        )
        out: dict[int, dict[str, int]] = {}
        for tid, cat, n in rows:
            out.setdefault(tid, {})[cat] = int(n)
        return out

    @staticmethod
    def _raw(part: PartCfg, s: Settlement, ml: dict, poi: dict | None, values: dict) -> float | None:
        if part.source == "population":
            return float(s.population) if s.population else None
        if part.source in GRAVITY_SOURCES:
            share, total = ml.get(GRAVITY_SOURCES[part.source]), ml.get("known_destination_population")
            if share is None or total is None:
                return None
            # The ML sum covers the other settlements; the residents themselves are buyers at zero distance
            # (unknown own population adds nothing).
            buyers = float(share) * float(total) + float(s.population or 0)
            return buyers if math.isfinite(buyers) else None
        if part.source == "poi":
            if poi is None:
                return None
            counts = poi.get(s.id, {})
            return float(sum(w * counts.get(cat, 0) for cat, w in part.categories.items()))
        value = values.get(part.feature)
        return None if value is None else float(value)

    def _warnings(self, settlements: dict[int, Settlement], raw: dict, ml: dict, poi: dict | None) -> list[str]:
        out = []
        known = sum(1 for s in settlements.values() if s.population)
        if known < len(settlements):
            out.append(f"Население известно для {known} из {len(settlements)} населённых пунктов: "
                       "для остальных местный рынок не оценён")
        if settlements and not any(ml.values()):
            out.append("Нет результатов ML о доступном населении: выполните python -m app.cli load-ml-results")
        if poi is None:
            out.append("Слой объектов OpenStreetMap не загружен: выполните python -m app.cli seed-osm")
        for _, part in self.mcfg.parts:
            if part.source == "feature" and settlements and all(raw[t][part.key] is None for t in settlements):
                out.append(f"Нет значений признака «{self.cfg.features[part.feature].name}»")  # type: ignore[index]
        return out

    # --- views ------------------------------------------------------------------------
    def _market_view(self, t: MarketTable, tid: int, code: str) -> dict[str, Any]:
        mk = self.mcfg.markets[code]
        score = t.market[tid].get(code)
        parts = [{"key": p.key, "label": p.label, "unit": p.unit, "value": _value(p, t.raw[tid][p.key]),
                  "score": _r1(t.part[tid].get(p.key)), "text": part_text(p, t.raw[tid][p.key])} for p in mk.parts]
        return {"code": code, "name": mk.name, "score": _r1(score), "tier": self.tier(score),
                "percentile": _r1(t.skfo.market[tid].get(code)),
                "cluster_percentile": _r1(t.cluster_scope(tid).market[tid].get(code)),
                "parts": parts, "text": "; ".join(_lower_first(p["text"]) if i else p["text"]
                                                 for i, p in enumerate(parts))}

    def _explain(self, t: MarketTable, tid: int, code: str) -> tuple[list[str], list[str]]:
        """Markets above / below the middle of the scale (50), ordered by weight x distance from 50.

        The percentile is quoted only when it supports the statement (a strength better than most
        settlements of СКФО, a risk worse than most).
        """
        weights = self.mcfg.sectors[code].weights
        scores, pct = t.market[tid], t.skfo.market[tid]

        def say(m: str, strength: bool) -> str:
            view = self._market_view(t, tid, m)
            p = pct.get(m)
            supports = p is not None and (p >= 50 if strength else p < 50)
            position = f" — {compare_text(p, 'skfo')}" if supports else ""  # type: ignore[arg-type]
            return f"{view['name']}: {fmt_score(scores[m])}{position} ({_lower_first(view['text'])})"

        weighted = [(w * (scores[m] - 50.0), m) for m, w in weights.items() if w >= EXPLAIN_MIN_WEIGHT and m in scores]
        strengths = [say(m, True) for c, m in sorted(weighted, reverse=True) if c > 0][:EXPLAIN_TOP]
        risks = [say(m, False) for c, m in sorted(weighted) if c < 0][:EXPLAIN_TOP]
        return strengths, risks

    def _assessment(self, t: MarketTable, tid: int, code: str) -> dict[str, Any]:
        s = t.settlements[tid]
        sc = self.mcfg.sectors[code]
        own = t.cluster_scope(tid)
        score = t.sector[tid].get(code)
        percentile = t.skfo.sector[tid].get(code)
        rank, size = own.rank[code].get(tid), own.size[code]
        missing = [self.mcfg.markets[m].name.lower() for m, w in sc.weights.items() if w > 0 and m not in t.market[tid]]
        strengths, risks = self._explain(t, tid, code) if score is not None else ([], [])
        return {
            "code": code, "name": sc.name, "score": _r1(score), "tier": self.tier(score),
            "percentile": _r1(percentile), "cluster_percentile": _r1(own.sector[tid].get(code)),
            "cluster_rank": rank, "cluster_size": size, "coverage": t.coverage[tid][code], "missing": missing,
            "verdict": verdict(s.name, sc.name, score=score, tier=self.tier(score), percentile=percentile,
                               rank=rank, size=size, cluster_name=self.cfg.clusters[s.cluster].name, missing=missing),
            "strengths": strengths, "risks": risks,
        }

    async def territory(self, session: AsyncSession, territory_id: int) -> dict[str, Any]:
        t = await self.table(session)
        if territory_id not in t.settlements:
            row = (await session.execute(text("SELECT name FROM territory WHERE id = :id"), {"id": territory_id})).first()
            if row is None:
                raise NotFoundError("Территория не найдена", code="territory_not_found")
            raise ValidationFailed(f"«{row.name}» — не населённый пункт: рынки сбыта оцениваются для населённых пунктов",
                                   code="territory_not_settlement")
        s = t.settlements[territory_id]
        sectors = [self._assessment(t, territory_id, code) for code in self.mcfg.sectors]
        sectors.sort(key=lambda a: -1.0 if a["score"] is None else -a["score"])
        return {
            "territory_id": s.id, "name": s.name, "region_code": s.region_code, "municipality": s.municipality,
            "cluster": s.cluster, "cluster_name": self.cfg.clusters[s.cluster].name, "population": s.population,
            "markets": [self._market_view(t, territory_id, code) for code in self.mcfg.markets],
            "sectors": sectors, "data_revision": t.revision, "warnings": list(t.warnings),
        }

    async def clusters(self, session: AsyncSession, region: str | None = None) -> dict[str, Any]:
        t = await self.table(session)
        out = []
        for cl, scope in t.clusters.items():
            members = [tid for tid, s in t.settlements.items()
                       if s.cluster == cl and (region is None or s.region_code == region)]
            if not members:
                continue
            pop = {tid: float(t.settlements[tid].population or 0) for tid in members}
            ones = dict.fromkeys(members, 1.0)
            markets = []
            for code, mk in self.mcfg.markets.items():
                tiers = {tid: self.tier(t.market[tid].get(code)) for tid in members}
                markets.append({
                    "code": code, "name": mk.name,
                    "median_score": _r1(_median([t.market[tid].get(code) for tid in members])),
                    "medians": {p.key: _value(p, _median([t.raw[tid][p.key] for tid in members])) for p in mk.parts},
                    "settlements": _shares(ones, tiers), "population": _shares(pop, tiers),
                })
            sectors = []
            for code, sc in self.mcfg.sectors.items():
                tiers = {tid: self.tier(t.sector[tid].get(code)) for tid in members}
                best = sorted((tid for tid in members if tid in scope.rank[code]), key=lambda x: scope.rank[code][x])
                sectors.append({
                    "code": code, "name": sc.name,
                    "median_score": _r1(_median([t.sector[tid].get(code) for tid in members])),
                    "settlements": _shares(ones, tiers), "population": _shares(pop, tiers),
                    "leaders": [self._leader(t, tid, code) for tid in best[:LEADERS]],
                })
            out.append({
                "cluster": cl, "cluster_name": self.cfg.clusters[cl].name, "settlements": len(members),
                "settlements_with_population": sum(1 for v in pop.values() if v),
                "population_known": int(sum(pop.values())), "markets": markets, "sectors": sectors,
            })
        return {"region": region, "clusters": out, "data_revision": t.revision, "warnings": list(t.warnings)}

    def _leader(self, t: MarketTable, tid: int, code: str) -> dict[str, Any]:
        s = t.settlements[tid]
        score = t.sector[tid].get(code)
        return {"territory_id": tid, "name": s.name, "region_code": s.region_code, "municipality": s.municipality,
                "population": s.population, "score": _r1(score), "tier": self.tier(score),
                "percentile": _r1(t.skfo.sector[tid].get(code)), "cluster_rank": t.cluster_scope(tid).rank[code].get(tid)}

    async def sector_rating(
        self, session: AsyncSession, sector: str, *, cluster: str | None, region: str | None, limit: int, offset: int
    ) -> dict[str, Any]:
        if sector not in self.mcfg.sectors:
            raise NotFoundError(f"Отрасль «{sector}» не найдена. Доступны: {', '.join(self.mcfg.sectors)}",
                                code="sector_not_found")
        t = await self.table(session)
        members = [tid for tid, s in t.settlements.items()
                   if (cluster is None or s.cluster == cluster) and (region is None or s.region_code == region)
                   and sector in t.sector[tid]]
        members.sort(key=lambda tid: t.skfo.rank[sector][tid])  # by score (ties: lower id first)
        items = []
        for tid in members[offset: offset + limit]:
            a = self._assessment(t, tid, sector)
            s = t.settlements[tid]
            items.append({
                "territory_id": tid, "name": s.name, "region_code": s.region_code, "municipality": s.municipality,
                "cluster": s.cluster, "population": s.population,
                **{k: a[k] for k in ("score", "tier", "percentile", "cluster_percentile", "cluster_rank",
                                     "cluster_size", "coverage", "missing", "strengths", "risks")},
                "markets": {code: _r1(t.market[tid].get(code)) for code in self.mcfg.markets},
            })
        return {"sector": self._sector_info(sector), "cluster": cluster, "region": region, "items": items,
                "total": len(members), "limit": limit, "offset": offset, "data_revision": t.revision,
                "warnings": list(t.warnings)}


def _median(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return statistics.median(known) if known else None


def _shares(weight: dict[int, float], tiers: dict[int, str | None]) -> dict[str, float]:
    out = dict.fromkeys((*TIERS, "unknown"), 0.0)
    total = sum(weight.values())
    if total <= 0:
        return out
    for tid, w in weight.items():
        out[tiers[tid] or "unknown"] += w
    return {k: round(v / total, 3) for k, v in out.items()}


def _lower_first(s: str) -> str:
    """Lower-case the first letter unless it starts an abbreviation (ТЦ, ЖД)."""
    if len(s) < 2 or s[1].isupper():
        return s
    return s[:1].lower() + s[1:]
