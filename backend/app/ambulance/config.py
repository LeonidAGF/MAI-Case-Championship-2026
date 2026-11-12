"""
Parameters of the ambulance travel-time model (config/ambulance.default.yaml, AMBULANCE_CONFIG_PATH).

Model keys (bases, speeds, urban, dem, slope, height, access, closed_access) are used by
`python -m app.cli load-ambulance`; their hash is stored with the run, so the API can tell that
results were computed with other values. Reporting keys (norm, thresholds, dispatch time, error
band) are applied by the API when it answers.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODEL_KEYS = ("bases", "speeds", "urban", "dem", "slope", "height", "access", "closed_access")
Curve = list[tuple[float, float]]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_curve(points: Curve, what: str) -> Curve:
    if not points:
        raise ValueError(f"{what}: at least one point is required")
    xs = [x for x, _ in points]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError(f"{what}: x values must increase strictly")
    if any(not 0 < y <= 1 for _, y in points):
        raise ValueError(f"{what}: speed multipliers must be in (0, 1]")
    return points


class Bases(_Strict):
    categories: list[str] = Field(min_length=1)
    snap_max_m: float = Field(gt=0)


class UrbanRural(_Strict):
    urban: float = Field(gt=0, le=200)
    rural: float = Field(gt=0, le=200)


class Speeds(_Strict):
    maxspeed_tags: dict[str, float]
    maxspeed_range: tuple[float, float]
    by_type: dict[str, float | UrbanRural]
    default: float = Field(gt=0, le=200)

    @field_validator("maxspeed_tags")
    @classmethod
    def _lower_keys(cls, value: dict[str, float]) -> dict[str, float]:
        return {k.strip().lower(): float(v) for k, v in value.items()}

    @field_validator("by_type")
    @classmethod
    def _positive(cls, value: dict[str, float | UrbanRural]) -> dict[str, float | UrbanRural]:
        for road, speed in value.items():
            if isinstance(speed, float | int) and not 0 < speed <= 200:
                raise ValueError(f"speeds.by_type.{road}: expected 0 < km/h <= 200")
        return value

    @model_validator(mode="after")
    def _range(self) -> Speeds:
        lo, hi = self.maxspeed_range
        if not 0 < lo < hi:
            raise ValueError("speeds.maxspeed_range: expected [low, high] with 0 < low < high")
        return self

    def kmh(self, highway: str | None, *, urban: bool) -> float:
        """Maximum speed of a road type without an OSM maxspeed tag."""
        value = self.by_type.get(highway or "", self.default)
        if isinstance(value, UrbanRural):
            return value.urban if urban else value.rural
        return float(value)


class Urban(_Strict):
    enabled: bool = True
    cell_m: float = Field(gt=0)
    street_types: list[str]
    min_street_km: float = Field(ge=0)


class Dem(_Strict):
    smooth_m: float = Field(ge=0)


class Slope(_Strict):
    enabled: bool = True
    baseline_m: float = Field(gt=0)
    max_grade: float = Field(gt=0, le=1)
    steep_grade: float = Field(gt=0, le=1)
    uphill: Curve
    downhill: Curve

    @model_validator(mode="after")
    def _curves(self) -> Slope:
        _check_curve(self.uphill, "slope.uphill")
        _check_curve(self.downhill, "slope.downhill")
        return self


class Height(_Strict):
    enabled: bool = True
    points: Curve

    @model_validator(mode="after")
    def _curve(self) -> Height:
        _check_curve(self.points, "height.points")
        return self


class Access(_Strict):
    speed_kmh: float = Field(gt=0)
    detour: float = Field(ge=1)
    max_km: float = Field(gt=0)

    def seconds(self, meters: float) -> float:
        return meters * self.detour / (self.speed_kmh / 3.6)


class Uncertainty(_Strict):
    minus: float = Field(ge=0, lt=1)
    plus: float = Field(ge=0)
    min_minutes: float = Field(ge=0)


class AmbulanceConfig(_Strict):
    version: str
    bases: Bases
    speeds: Speeds
    urban: Urban
    dem: Dem
    slope: Slope
    height: Height
    access: Access
    closed_access: list[str] = Field(default_factory=list)
    norm_minutes: float = Field(gt=0)
    thresholds: list[float] = Field(min_length=1)
    dispatch_minutes: float = Field(ge=0)
    uncertainty: Uncertainty

    @field_validator("thresholds")
    @classmethod
    def _thresholds(cls, value: list[float]) -> list[float]:
        if any(t <= 0 for t in value) or any(b <= a for a, b in zip(value, value[1:])):
            raise ValueError("thresholds: positive minutes in increasing order")
        return value

    @property
    def model_hash(self) -> str:
        """Hash of the keys that change computed times (stored with every load-ambulance run)."""
        data = self.model_dump(mode="json", include=set(MODEL_KEYS))
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def load_ambulance_config(path: Path | str) -> AmbulanceConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AmbulanceConfig.model_validate(data)


__all__ = ["AmbulanceConfig", "load_ambulance_config", "MODEL_KEYS"]
