"""Scoring orchestration: matrix -> engine -> results (+ what-if overlay, + caching)."""

from __future__ import annotations

import statistics
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.metrics import SCORE_CALLS, SCORE_COMPUTE_DURATION
from app.modelcfg import ModelConfig
from app.profiles.contract import Profile
from app.scoring.contract import FeatureSpec, IndexEngine, ScoreRequest, TerritoryScore
from app.scoring.matrix import MODE_PREFERENCE, ClusterMatrix, DataRevision, MatrixStore, Provenance


@dataclass
class ScenarioOverlay:
    """What-if corrections: territory -> feature -> mode -> new raw value."""

    run_id: int
    overrides: dict[int, dict[str, dict[str, float | None]]]

    @classmethod
    def from_rows(cls, run_id: int, rows: list[Mapping]) -> "ScenarioOverlay":
        out: dict[int, dict[str, dict[str, float | None]]] = {}
        for r in rows:
            out.setdefault(int(r["territory_id"]), {}).setdefault(r["feature_code"], {})[r["mode"]] = r["new"]
        return cls(run_id, out)

    def apply(
        self,
        tid: int,
        row: Mapping[str, float | None],
        prov: Mapping[str, Provenance],
        mode: str,
    ) -> dict[str, float | None]:
        new = dict(row)
        for code, by_mode in self.overrides.get(tid, {}).items():
            base_mode = prov[code].value_mode if code in prov else None
            if base_mode and base_mode in by_mode:
                new[code] = by_mode[base_mode]
                continue
            for m in MODE_PREFERENCE.get(mode, ("any",)):
                if m in by_mode:
                    new[code] = by_mode[m]
                    break
        return new


@dataclass
class FeatureStats:
    median_normalized: float | None
    median_raw: float | None
    share: float  # weight share in the total (identical for all territories of the cluster)

    @property
    def median_contribution(self) -> float:
        if self.median_normalized is None:
            return 0.0
        return 100.0 * self.share * self.median_normalized


@dataclass
class ClusterScores:
    cluster_code: str
    profile: Profile
    matrix: ClusterMatrix
    scores: dict[int, TerritoryScore]
    scenario_run_id: int | None = None
    _feature_stats: dict[str, FeatureStats] | None = field(default=None, repr=False)
    _ranking: list[int] | None = field(default=None, repr=False)

    @property
    def data_revision(self) -> int:
        return self.matrix.data_revision

    def get(self, tid: int) -> TerritoryScore:
        try:
            return self.scores[tid]
        except KeyError:
            raise NotFoundError(
                "Территория не участвует в расчёте индекса", code="territory_not_scored"
            ) from None

    def feature_stats(self) -> dict[str, FeatureStats]:
        if self._feature_stats is None:
            norms: dict[str, list[float]] = {}
            shares: dict[str, float] = {}
            for s in self.scores.values():
                for c in s.components:
                    shares.setdefault(c.feature_code, c.weight)
                    if c.normalized is not None:
                        norms.setdefault(c.feature_code, []).append(c.normalized)
            raws: dict[str, list[float]] = {}
            for row in self.matrix.values.values():
                for code, v in row.items():
                    if v is not None:
                        raws.setdefault(code, []).append(v)
            self._feature_stats = {
                code: FeatureStats(
                    median_normalized=statistics.median(norms[code]) if norms.get(code) else None,
                    median_raw=statistics.median(raws[code]) if raws.get(code) else None,
                    share=shares.get(code, 0.0),
                )
                for code in shares
            }
        return self._feature_stats

    def ranking(self) -> list[int]:
        if self._ranking is None:
            self._ranking = sorted(self.scores, key=lambda t: (-self.scores[t].total, t))
        return self._ranking

    def rank_of(self, tid: int) -> int:
        return self.ranking().index(tid) + 1

    def total_stats(self) -> dict[str, float | int | None]:
        totals = [s.total for s in self.scores.values()]
        if not totals:
            return {"n": 0, "median": None, "min": None, "max": None}
        return {
            "n": len(totals),
            "median": round(statistics.median(totals), 1),
            "min": round(min(totals), 1),
            "max": round(max(totals), 1),
        }


class ScoringService:
    def __init__(
        self,
        cfg: ModelConfig,
        engine: IndexEngine,
        store: MatrixStore,
        revision: DataRevision,
        *,
        lru_size: int = 8,
    ) -> None:
        self.cfg = cfg
        self.engine = engine
        self.store = store
        self.revision = revision
        self.feature_specs = [
            FeatureSpec(code=code, subindex=f.subindex, direction=f.direction, weight=f.weight, level=f.level)
            for code, f in cfg.features.items()
        ]
        self.feature_codes = [f.code for f in self.feature_specs]
        self._lru_size = lru_size
        self._results: OrderedDict[tuple, ClusterScores] = OrderedDict()

    def clear(self) -> None:
        self._results.clear()
        self.store.clear()
        self.revision.invalidate()

    async def matrix(self, session: AsyncSession, cluster_code: str, mode: str) -> ClusterMatrix:
        rev = await self.revision.get(session)
        fallback = self.cfg.clusters[cluster_code].params if cluster_code in self.cfg.clusters else {}
        return await self.store.get(
            session,
            cluster_code=cluster_code,
            mode=mode,
            revision=rev,
            feature_codes=self.feature_codes,
            params_fallback=fallback,
        )

    def build_request(
        self, matrix: ClusterMatrix, profile: Profile, targets: Mapping[int, Mapping[str, float | None]]
    ) -> ScoreRequest:
        return ScoreRequest(
            cluster_code=matrix.cluster_code,
            cluster_params=matrix.cluster_params,
            features=self.feature_specs,
            total_subindices=self.cfg.total_subindices,
            attractiveness_subindex=self.cfg.attractiveness_subindex,
            subindex_weights=profile.weights,
            filters=profile.filters.model_dump(mode="json"),
            reference=matrix.values,
            targets=targets,
        )

    async def _run(self, request: ScoreRequest) -> dict[int, TerritoryScore]:
        if not request.targets:
            return {}
        started = time.perf_counter()
        try:
            return await anyio.to_thread.run_sync(self.engine.score, request)
        finally:
            SCORE_COMPUTE_DURATION.labels(self.engine.name, request.cluster_code).observe(time.perf_counter() - started)

    async def score_cluster(
        self,
        session: AsyncSession,
        cluster_code: str,
        profile: Profile,
        overlay: ScenarioOverlay | None = None,
    ) -> ClusterScores:
        matrix = await self.matrix(session, cluster_code, profile.mode)
        key = (cluster_code, profile.mode, matrix.data_revision, profile.hash, overlay.run_id if overlay else None)
        hit = self._results.get(key)
        if hit is not None:
            SCORE_CALLS.labels(cluster_code, "hit").inc()
            self._results.move_to_end(key)
            return hit
        SCORE_CALLS.labels(cluster_code, "miss").inc()
        if overlay is None:
            targets: Mapping[int, Mapping[str, float | None]] = matrix.values
        else:
            targets = {
                tid: overlay.apply(tid, matrix.values[tid], matrix.provenance.get(tid, {}), profile.mode)
                for tid in matrix.territory_ids
            }
        scores = await self._run(self.build_request(matrix, profile, targets))
        result = ClusterScores(cluster_code, profile, matrix, scores, overlay.run_id if overlay else None)
        self._results[key] = result
        while len(self._results) > self._lru_size:
            self._results.popitem(last=False)
        return result

    async def score_rows(
        self,
        session: AsyncSession,
        cluster_code: str,
        profile: Profile,
        targets: Mapping[int, Mapping[str, float | None]],
    ) -> dict[int, TerritoryScore]:
        """Score arbitrary (e.g. what-if modified) rows against the cluster's base normalization."""
        matrix = await self.matrix(session, cluster_code, profile.mode)
        return await self._run(self.build_request(matrix, profile, targets))
