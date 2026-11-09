"""Reference implementation of the published formula (report 4.3 + 5.3 a/b).

    index(t, p) = 100 * Σ_s w_s(p) * Σ_f w_f * norm_f(cluster(t), f)

* normalization only inside the cluster, fitted on ``request.reference``;
* winsorize at the cluster's [winsor_lower, winsor_upper] quantiles, then min-max;
* clusters smaller than ``min_size_for_minmax`` use quantile (rank) normalization;
* "lower is better" features are flipped; missing values -> cluster median, flagged;
* features without any data in the cluster are skipped and their weight is
  redistributed inside the subindex (same for subindices without data).

This is the backend's stand-in so the API works end-to-end. The model is ML's:
they replace it via SCORING_ENGINE without touching this file. Profile filters are
passed through in the request but not interpreted here.
"""

from __future__ import annotations

import math

import numpy as np

from app.scoring.contract import ComponentScore, FeatureSpec, ScoreRequest, TerritoryScore


def _normalizer(ref: np.ndarray, *, lo_q: float, hi_q: float, min_size: int):
    n = ref.size
    if n < min_size:
        srt = np.sort(ref)

        def quantile_norm(x: np.ndarray) -> np.ndarray:
            if n == 1:
                return np.full(x.shape, 0.5)
            less = np.searchsorted(srt, x, side="left")
            leq = np.searchsorted(srt, x, side="right")
            q = (less + 0.5 * (leq - less) - 0.5) / (n - 1)
            return np.clip(q, 0.0, 1.0)

        return quantile_norm

    lo = float(np.quantile(ref, lo_q))
    hi = float(np.quantile(ref, hi_q))

    def minmax(x: np.ndarray) -> np.ndarray:
        if hi <= lo:
            return np.full(x.shape, 0.5)
        return np.clip((x - lo) / (hi - lo), 0.0, 1.0)

    return minmax


class ReferenceIndexEngine:
    name = "reference"
    version = "1.0"

    def score(self, request: ScoreRequest) -> dict[int, TerritoryScore]:
        params = request.cluster_params or {}
        lo_q = float(params.get("winsor_lower", 0.01))
        hi_q = float(params.get("winsor_upper", 0.99))
        min_size = int(params.get("min_size_for_minmax", 10))

        target_ids = list(request.targets)
        n_targets = len(target_ids)
        features: list[FeatureSpec] = list(request.features)

        raw_cols: dict[str, np.ndarray] = {}
        norm_cols: dict[str, np.ndarray] = {}
        imputed_cols: dict[str, np.ndarray] = {}
        available: set[str] = set()

        for f in features:
            raw = np.array(
                [_num(request.targets[t].get(f.code)) for t in target_ids], dtype=float
            ).reshape(n_targets)
            raw_cols[f.code] = raw
            ref = np.array(
                [v for row in request.reference.values() if (v := _num(row.get(f.code))) is not None],
                dtype=float,
            )
            if ref.size == 0:
                continue
            available.add(f.code)
            median = float(np.median(ref))
            imputed = ~np.isfinite(raw)
            x = np.where(imputed, median, raw)
            norm = _normalizer(ref, lo_q=lo_q, hi_q=hi_q, min_size=min_size)(x)
            if f.direction == "lower_better":
                norm = 1.0 - norm
            norm_cols[f.code] = norm
            imputed_cols[f.code] = imputed

        # Feature shares inside each subindex (only features with data).
        by_sub: dict[str, list[FeatureSpec]] = {}
        for f in features:
            by_sub.setdefault(f.subindex, []).append(f)
        inner_share: dict[str, float] = {}
        sub_scores: dict[str, np.ndarray | None] = {}
        for sub, feats in by_sub.items():
            live = [f for f in feats if f.code in available and f.weight > 0]
            wsum = sum(f.weight for f in live)
            if not live or wsum <= 0:
                sub_scores[sub] = None
                continue
            acc = np.zeros(n_targets)
            for f in live:
                share = f.weight / wsum
                inner_share[f.code] = share
                acc += share * norm_cols[f.code]
            sub_scores[sub] = acc

        totals = list(request.total_subindices)
        live_totals = [s for s in totals if sub_scores.get(s) is not None]
        w_total = sum(max(0.0, float(request.subindex_weights.get(s, 0.0))) for s in live_totals)
        outer_share = {
            s: (max(0.0, float(request.subindex_weights.get(s, 0.0))) / w_total if w_total > 0 else 0.0)
            for s in live_totals
        }

        # Plain Python lists for the per-territory loop (numpy scalar indexing is slow).
        attr_sub = request.attractiveness_subindex
        total_arr = np.zeros(n_targets)
        cols: list[tuple[str, list, list | None, list | None, float]] = []
        for f in features:
            raw_list = [None if x != x else x for x in raw_cols[f.code].tolist()]  # NaN -> None
            if f.code not in available:
                cols.append((f.code, raw_list, None, None, 0.0))
                continue
            share = outer_share.get(f.subindex, 0.0) * inner_share.get(f.code, 0.0)
            total_arr += 100.0 * share * norm_cols[f.code]
            cols.append((f.code, raw_list, norm_cols[f.code].tolist(), imputed_cols[f.code].tolist(), share))
        sub_lists = {s: (None if arr is None else (100.0 * arr).tolist()) for s, arr in sub_scores.items()}
        sub_codes = list(dict.fromkeys([*by_sub, *totals]))
        totals_list = np.clip(total_arr, 0.0, 100.0).tolist()

        result: dict[int, TerritoryScore] = {}
        for i, tid in enumerate(target_ids):
            components = [
                ComponentScore(code, raw_list[i], None, 0.0, 0.0, False)
                if norm_list is None
                else ComponentScore(code, raw_list[i], norm_list[i], share, 100.0 * share * norm_list[i], imp_list[i])
                for code, raw_list, norm_list, imp_list, share in cols
            ]
            subindices = {s: (None if sub_lists.get(s) is None else sub_lists[s][i]) for s in sub_codes}  # type: ignore[index]
            result[tid] = TerritoryScore(
                territory_id=tid,
                total=totals_list[i],
                subindices=subindices,
                attractiveness=subindices.get(attr_sub) if attr_sub else None,
                components=components,
            )
        return result


def _num(v: object) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None
