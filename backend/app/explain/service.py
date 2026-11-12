"""
Explanations from components: waterfall vs the cluster's typical territory + top-3 pros/cons.

Waterfall: baseline = Σ_f median contribution of the cluster ("типичная территория
кластера"); each step = contribution_f − median contribution_f; baseline + Σ steps = total.
Pluses/minuses: largest positive/negative steps (same definition as in compare).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.explain.texts import fact_text, fmt_num, typical_text
from app.modelcfg import ModelConfig
from app.scoring.service import ClusterScores

MIN_DELTA = 0.05  # points; smaller deltas are noise


def round_preserving_sum(values: list[float], target: float, digits: int = 1) -> list[float]:
    """Round values so that their rounded sum equals ``target`` (largest remainder method)."""
    if not values:
        return []
    scale = 10**digits
    target_units = int(round(target * scale))
    floors = [math.floor(v * scale) for v in values]
    remainders = [v * scale - f for v, f in zip(values, floors, strict=True)]
    diff = target_units - sum(floors)
    order = sorted(range(len(values)), key=lambda i: remainders[i], reverse=diff > 0)
    step = 1 if diff > 0 else -1
    for i in order[: abs(diff)]:
        floors[i] += step
    return [f / scale for f in floors]


@dataclass
class Explanation:
    territory_id: int
    total: float
    baseline: float
    steps: list[dict]
    pluses: list[dict]
    minuses: list[dict]
    attractiveness_factors: list[dict]
    summary: str


def explain_territory(
    cs: ClusterScores,
    tid: int,
    cfg: ModelConfig,
    *,
    cluster_name: str,
    territory_name: str,
) -> Explanation:
    score = cs.get(tid)
    stats = cs.feature_stats()
    prov = cs.matrix.provenance.get(tid, {})
    total_subs = set(cfg.total_subindices)

    raw_steps: list[dict] = []
    attractiveness: list[dict] = []
    for comp in score.components:
        fcfg = cfg.features[comp.feature_code]
        p = prov.get(comp.feature_code)
        fs = stats.get(comp.feature_code)
        mode = p.value_mode if p else "any"
        typical = typical_text(comp.feature_code, fs.median_raw if fs else None, unit=fcfg.unit, mode=mode)
        fact_kwargs = dict(
            name=fcfg.name,
            unit=fcfg.unit,
            mode=mode,
            imputed=comp.imputed,
            inherited_from=p.inherited_from if p else None,
            excluded=comp.normalized is None,
        )
        text = fact_text(comp.feature_code, comp.raw_value, **fact_kwargs)
        text_with_typical = fact_text(comp.feature_code, comp.raw_value, typical=typical, **fact_kwargs)
        item = {
            "feature_code": comp.feature_code,
            "label": fcfg.name,
            "subindex": fcfg.subindex,
            "raw_value": comp.raw_value,
            "unit": fcfg.unit,
            "normalized": None if comp.normalized is None else round(comp.normalized, 4),
            "weight": round(comp.weight, 6),
            "contribution": comp.contribution,
            "median_contribution": fs.median_contribution if fs else 0.0,
            "imputed": bool(comp.imputed or (p.upstream_imputed if p else False)),
            "inherited_from": p.inherited_from if p else None,
            "value_mode": mode,
            "text": text,
            "typical_text": typical,
            "_text_with_typical": text_with_typical,
        }
        if fcfg.subindex in total_subs:
            raw_steps.append(item)
        elif comp.normalized is not None:
            attractiveness.append(
                {k: item[k] for k in ("feature_code", "label", "raw_value", "unit", "normalized", "text")}
            )

    baseline = sum(s["median_contribution"] for s in raw_steps)
    total_r = round(score.total, 1)
    baseline_r = round(baseline, 1)
    deltas = [s["contribution"] - s["median_contribution"] for s in raw_steps]
    rounded = round_preserving_sum(deltas, total_r - baseline_r)
    for s, d, dr in zip(raw_steps, deltas, rounded, strict=True):
        s["delta_raw"] = d
        s["delta"] = dr
        s["contribution"] = round(s["contribution"], 3)
        s["median_contribution"] = round(s["median_contribution"], 3)

    candidates = [s for s in raw_steps if not s["imputed"] and s["normalized"] is not None]
    pluses = sorted((s for s in candidates if s["delta_raw"] > MIN_DELTA), key=lambda s: -s["delta_raw"])[:3]
    minuses = sorted((s for s in candidates if s["delta_raw"] < -MIN_DELTA), key=lambda s: s["delta_raw"])[:3]

    def _pc(s: dict) -> dict:
        return {
            "feature_code": s["feature_code"],
            "subindex": s["subindex"],
            "delta": round(s["delta_raw"], 1),
            "text": s["_text_with_typical"],
        }

    pluses_out = [_pc(s) for s in pluses]
    minuses_out = [_pc(s) for s in minuses]

    stats_total = cs.total_stats()
    median_total = stats_total["median"]
    if median_total is None or abs(total_r - median_total) < 2:
        position = "на уровне типичного"
    elif total_r > median_total:
        position = "выше типичного"
    else:
        position = "ниже типичного"
    summary = (
        f"«{territory_name}»: индекс {fmt_num(total_r)} из 100 — {position} для кластера "
        f"«{cluster_name}» (медиана {fmt_num(median_total) if median_total is not None else '—'}), "
        f"место {cs.rank_of(tid)} из {stats_total['n']}."
    )
    if pluses:
        summary += " Сильные стороны: " + "; ".join(_inline(s["text"]) for s in pluses[:2]) + "."
    if minuses:
        summary += " Слабые стороны: " + "; ".join(_inline(s["text"]) for s in minuses[:2]) + "."

    for s in raw_steps:
        s.pop("delta_raw", None)
        s.pop("_text_with_typical", None)
    return Explanation(
        territory_id=tid,
        total=total_r,
        baseline=baseline_r,
        steps=raw_steps,
        pluses=pluses_out,
        minuses=minuses_out,
        attractiveness_factors=attractiveness,
        summary=summary,
    )


def _inline(text: str) -> str:
    """Lower-case the first letter for use mid-sentence, keeping abbreviations (ЖД, ФАП)."""
    if len(text) >= 2 and text[0].isupper() and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]
