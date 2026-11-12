"""
Loader/validator of the sales-market and sector methodology (config/markets.default.yaml).

The file is a methodology, like the model YAML: markets are groups of indicators computed from
data the backend already has; every indicator has a fixed scale that turns its value into an
absolute 0..100 score (independent of the other settlements); sectors weigh the markets.
Validation is strict (unknown keys, weights that don't sum to 1, unknown feature codes or POI
categories, inconsistent scales fail at startup).
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.etl.poi import CATEGORIES as POI_CATEGORIES

WEIGHT_TOLERANCE = 0.01
GRAVITY_SOURCES = {"ml_gravity_30": "gravity_30", "ml_gravity_60": "gravity_60", "ml_gravity_120": "gravity_120"}


class ScaleCfg(BaseModel):
    """Value -> absolute score 0..100.

    log    sizes (people, objects): 0 at `zero` and below, 100 at `full` and above, linear in
           ln(1 + value) between, so every x10 adds the same number of points
    decay  travel times in minutes: 100 * 2^(-minutes / half_life), as in the ML gravity model
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["log", "decay"]
    zero: float | None = Field(None, ge=0)
    full: float | None = None
    half_life: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _params(self) -> "ScaleCfg":
        if self.type == "log":
            if self.zero is None or self.full is None or self.full <= self.zero or self.half_life is not None:
                raise ValueError("log scale needs zero < full (and no half_life)")
        elif self.half_life is None or self.zero is not None or self.full is not None:
            raise ValueError("decay scale needs half_life only")
        return self

    @property
    def direction(self) -> Literal["higher_better", "lower_better"]:
        return "higher_better" if self.type == "log" else "lower_better"

    def score(self, value: float) -> float:
        x = max(float(value), 0.0)
        if self.type == "decay":
            return 100.0 * 2.0 ** (-x / self.half_life)  # type: ignore[operator]
        lo, hi = math.log1p(self.zero), math.log1p(self.full)  # type: ignore[arg-type]
        return 100.0 * min(1.0, max(0.0, (math.log1p(x) - lo) / (hi - lo)))


class PartCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    source: Literal["population", "ml_gravity_30", "ml_gravity_60", "ml_gravity_120", "poi", "feature"]
    label: str
    unit: str = ""
    scale: ScaleCfg
    categories: dict[str, float] = Field(default_factory=dict)  # source=poi: category -> weight
    feature: str | None = None  # source=feature

    @model_validator(mode="after")
    def _source_fields(self) -> "PartCfg":
        if self.source == "poi":
            unknown = sorted(set(self.categories) - POI_CATEGORIES)
            if not self.categories or unknown or any(w <= 0 for w in self.categories.values()):
                raise ValueError(f"part {self.key}: positive weights of known poi categories required, unknown: {unknown}")
        elif self.categories:
            raise ValueError(f"part {self.key}: categories are only for source=poi")
        if (self.source == "feature") != (self.feature is not None):
            raise ValueError(f"part {self.key}: feature is required for source=feature only")
        if self.scale.type == "decay" and self.source != "feature":
            raise ValueError(f"part {self.key}: decay scale is for travel-time features")
        return self

    @property
    def direction(self) -> Literal["higher_better", "lower_better"]:
        return self.scale.direction


class MarketCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    parts: list[PartCfg] = Field(min_length=1)


class SectorCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    examples: str = ""
    description: str = ""
    weights: dict[str, float]


class TiersCfg(BaseModel):
    """Absolute score thresholds: high >= high, medium >= medium, otherwise low."""

    model_config = ConfigDict(extra="forbid")
    high: float = Field(gt=0, lt=100)
    medium: float = Field(gt=0, lt=100)

    @model_validator(mode="after")
    def _order(self) -> "TiersCfg":
        if self.medium >= self.high:
            raise ValueError("tiers: medium must be below high")
        return self

    def of(self, score: float | None) -> str | None:
        if score is None:
            return None
        return "high" if score >= self.high else "medium" if score >= self.medium else "low"


class MarketsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    radius_km: float = Field(gt=0, le=200)
    min_coverage: float = Field(gt=0, le=1)
    tiers: TiersCfg
    markets: dict[str, MarketCfg]
    sectors: dict[str, SectorCfg]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references(self) -> "MarketsConfig":
        keys = [p.key for m in self.markets.values() for p in m.parts]
        if len(keys) != len(set(keys)):
            raise ValueError("part keys must be unique across markets")
        for code, sector in self.sectors.items():
            unknown = sorted(set(sector.weights) - set(self.markets))
            if unknown:
                raise ValueError(f"sector {code}: unknown markets {unknown}")
            if any(w < 0 for w in sector.weights.values()):
                raise ValueError(f"sector {code}: weights must be non-negative")
            total = sum(sector.weights.values())
            if abs(total - 1.0) > WEIGHT_TOLERANCE:
                raise ValueError(f"sector {code}: weights must sum to 1 (got {total:g})")
        return self

    def check_features(self, feature_codes: set[str]) -> None:
        missing = sorted({p.feature for m in self.markets.values() for p in m.parts if p.feature} - feature_codes)
        if missing:
            raise ValueError(f"markets config uses features missing from the model: {missing}")

    @property
    def parts(self) -> list[tuple[str, PartCfg]]:
        return [(code, p) for code, m in self.markets.items() for p in m.parts]

    @property
    def config_hash(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_markets_config(path: Path | str) -> MarketsConfig:
    with Path(path).open(encoding="utf-8") as fh:
        return MarketsConfig.model_validate(yaml.safe_load(fh))
