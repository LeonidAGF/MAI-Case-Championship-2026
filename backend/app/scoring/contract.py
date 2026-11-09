"""Backend <-> ML contract for the index calculator ("Калькулятор → UI", section 9.3).

ML implements ``IndexEngine`` in their own module and sets
``SCORING_ENGINE=their.module:TheirEngine``. The backend never changes because of it:
it assembles the feature matrix (with inheritance and mobility mode already resolved),
calls ``score`` and persists/serves the result.

Invariants the backend relies on (checked by tests/unit/test_engine_contract.py):
  * deterministic: same request -> same result;
  * ``total`` is 0..100 and equals the sum of component contributions (waterfall adds up);
  * subindex values are 0..100 or None (no data);
  * one ComponentScore per requested feature, in request order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

Matrix = Mapping[int, Mapping[str, float | None]]


@dataclass(frozen=True)
class FeatureSpec:
    code: str
    subindex: str
    direction: str  # "higher_better" | "lower_better"
    weight: float  # weight inside its subindex
    level: str = "settlement"


@dataclass(frozen=True)
class ScoreRequest:
    cluster_code: str
    cluster_params: Mapping[str, Any]
    features: Sequence[FeatureSpec]
    total_subindices: Sequence[str]  # S1..S6
    attractiveness_subindex: str | None  # S7 (badge, not part of the total)
    subindex_weights: Mapping[str, float]  # profile weights in percent, keys = total_subindices
    filters: Mapping[str, Any]  # profile filters (engine may use them)
    # All scored territories of the cluster with base (unmodified) values: normalization
    # is fitted on this matrix, so a what-if never moves unaffected territories.
    reference: Matrix
    # Rows to score (the same matrix, a subset, or what-if modified copies).
    targets: Matrix


@dataclass(slots=True)
class ComponentScore:
    feature_code: str
    raw_value: float | None
    normalized: float | None  # 0..1, None when the feature has no data in the cluster
    weight: float  # share of the feature in the total (0..1); 0 for badge-only features
    contribution: float  # points of the 0..100 total
    imputed: bool = False  # value replaced by the cluster median


@dataclass(slots=True)
class TerritoryScore:
    territory_id: int
    total: float
    subindices: dict[str, float | None]
    attractiveness: float | None
    components: list[ComponentScore] = field(default_factory=list)


@runtime_checkable
class IndexEngine(Protocol):
    name: str
    version: str

    def score(self, request: ScoreRequest) -> dict[int, TerritoryScore]: ...
