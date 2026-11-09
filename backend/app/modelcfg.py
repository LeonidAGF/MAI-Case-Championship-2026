"""Loader/validator for the index model config (ML-owned YAML, see config/model.default.yaml)."""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.profiles.contract import TOTAL_SUBINDICES, WEIGHT_SUM_TOLERANCE, ProfileFilters

CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,7}$")


class SubindexCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    short_name: str
    in_total: bool = True


class FeatureCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    unit: str = ""
    direction: Literal["higher_better", "lower_better"]
    subindex: str
    weight: float = Field(1.0, ge=0)
    level: Literal["settlement", "municipality", "region"] = "settlement"
    time_based: bool = False
    source: str = ""
    description: str = ""


class ClusterCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class PresetCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    weights: dict[str, float]
    filters: dict[str, Any] = Field(default_factory=dict)
    key_thresholds: str = ""


class BadgeCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    label: str
    min_score: float


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    subindices: dict[str, SubindexCfg]
    features: dict[str, FeatureCfg]
    clusters: dict[str, ClusterCfg]
    presets: dict[str, PresetCfg]
    attractiveness_badges: list[BadgeCfg] = Field(default_factory=list)
    # Filled by validation (e.g. rescaled preset weights); logged at startup.
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "ModelConfig":
        for code in list(self.subindices) + list(self.features):
            if not CODE_RE.match(code):
                raise ValueError(f"invalid code {code!r}")
        totals = [c for c, s in self.subindices.items() if s.in_total]
        if tuple(totals) != TOTAL_SUBINDICES:
            raise ValueError(
                f"subindices in the total must be exactly {TOTAL_SUBINDICES} (profile contract 9.2), got {totals}"
            )
        for code, f in self.features.items():
            if f.subindex not in self.subindices:
                raise ValueError(f"feature {code}: unknown subindex {f.subindex}")
        if not self.clusters:
            raise ValueError("at least one cluster is required")
        for pid, preset in self.presets.items():
            if set(preset.weights) != set(TOTAL_SUBINDICES):
                raise ValueError(f"preset {pid}: weights must cover S1..S6")
            total = sum(preset.weights.values())
            if total <= 0:
                raise ValueError(f"preset {pid}: weights must be positive")
            if abs(total - 100) > WEIGHT_SUM_TOLERANCE:
                # Keep the authored proportions, rescale to the contract's 100 and report it.
                preset.weights = {k: round(v * 100.0 / total, 2) for k, v in preset.weights.items()}
                self.warnings.append(
                    f"preset {pid}: weights summed to {total:g}, rescaled proportionally to 100 "
                    f"({', '.join(f'{k}={v:g}' for k, v in preset.weights.items())})"
                )
            try:
                ProfileFilters(**preset.filters)
            except ValidationError as exc:
                raise ValueError(f"preset {pid}: invalid filters: {exc}") from exc
        return self

    # --- derived ---------------------------------------------------------------
    @property
    def total_subindices(self) -> list[str]:
        return [c for c, s in self.subindices.items() if s.in_total]

    @property
    def attractiveness_subindex(self) -> str | None:
        extra = [c for c, s in self.subindices.items() if not s.in_total]
        return extra[0] if extra else None

    def features_of(self, subindex: str) -> list[str]:
        return [c for c, f in self.features.items() if f.subindex == subindex]

    @property
    def config_hash(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def badge_for(self, score: float | None) -> dict[str, str] | None:
        if score is None:
            return None
        for badge in sorted(self.attractiveness_badges, key=lambda b: b.min_score, reverse=True):
            if score >= badge.min_score:
                return {"code": badge.code, "label": badge.label}
        return None


def load_model_config(path: Path | str) -> ModelConfig:
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return ModelConfig.model_validate(data)


@lru_cache(maxsize=4)
def get_model_config(path: str) -> ModelConfig:
    return load_model_config(path)
