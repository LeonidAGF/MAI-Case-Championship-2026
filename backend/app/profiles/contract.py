"""Profile contract (team report, section 9.2) — one JSON object for URL, localStorage and API.

{ "v": 1,
  "preset": "family" | null,
  "weights": {"S1": 25, "S2": 30, "S3": 15, "S4": 15, "S5": 10, "S6": 5},
  "filters": { ...nine filters... } }
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROFILE_SCHEMA_VERSION = 1
TOTAL_SUBINDICES: tuple[str, ...] = ("S1", "S2", "S3", "S4", "S5", "S6")
WEIGHT_SUM_TOLERANCE = 1.0

CLUSTERS_BY_TERRITORY_TYPES: dict[str, tuple[str, ...]] = {
    "cities": ("A",),
    "with_towns": ("A", "B"),
    "all": ("A", "B", "V"),
}


class ProfileFilters(BaseModel):
    """Nine constructor filters (table 1). Defaults apply to omitted filters."""

    model_config = ConfigDict(extra="forbid")

    mobility: Literal["car", "no_car"] = Field("car", description="Есть авто / Нет авто")
    medicine_threshold_min: Literal[15, 30, 60] = Field(30, description="Порог медицины, мин")
    family: Literal["single", "couple", "children"] = Field("single", description="Состав семьи")
    work: Literal["offline", "remote"] = Field("offline", description="Режим работы")
    travel_frequency: Literal["often", "rare", "none"] = Field(
        "rare", description="Частота дальних поездок"
    )
    climate_sensitivity: Literal["high", "mid", "low"] = Field(
        "mid", description="Климатическая чувствительность"
    )
    territory_types: Literal["cities", "with_towns", "all"] = Field(
        "all", description="Тип территории-кандидата (кластеры А/Б/В)"
    )
    pace: Literal["active", "calm"] = Field("calm", description="Темп жизни")
    realtor_mode: bool = Field(False, description="Режим риелтора (бейдж S7)")


def _canonical_number(x: float) -> float | int:
    x = round(float(x), 4)
    return int(x) if x.is_integer() else x


class Profile(BaseModel):
    """Fully resolved profile."""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    preset: str | None = None
    weights: dict[str, float]
    filters: ProfileFilters = Field(default_factory=ProfileFilters)

    @field_validator("weights")
    @classmethod
    def _check_weights(cls, value: dict[str, float]) -> dict[str, float]:
        keys = set(value)
        expected = set(TOTAL_SUBINDICES)
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(f"weights must contain exactly S1..S6 (missing={missing}, extra={extra})")
        for k, w in value.items():
            if not (0 <= float(w) <= 100):
                raise ValueError(f"weight {k} must be within 0..100")
        total = sum(float(w) for w in value.values())
        if abs(total - 100.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"weights must sum to 100 (got {total:g})")
        return {k: float(value[k]) for k in TOTAL_SUBINDICES}

    # --- helpers -------------------------------------------------------------
    def canonical_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "preset": self.preset,
            "weights": {k: _canonical_number(self.weights[k]) for k in TOTAL_SUBINDICES},
            "filters": self.filters.model_dump(mode="json"),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def hash(self) -> str:
        """Identity of the *calculation* (weights + filters); the preset label is excluded."""
        body = self.canonical_dict()
        body.pop("preset", None)
        body.pop("v", None)
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def mode(self) -> str:
        """Travel-time matrix to use: car, or walking/public transport."""
        return "car" if self.filters.mobility == "car" else "foot"

    @property
    def allowed_clusters(self) -> tuple[str, ...]:
        return CLUSTERS_BY_TERRITORY_TYPES[self.filters.territory_types]


class ProfileInput(BaseModel):
    """What clients may send: a partial profile merged over a preset or defaults."""

    model_config = ConfigDict(extra="forbid")

    v: int = PROFILE_SCHEMA_VERSION
    preset: str | None = None
    weights: dict[str, float] | None = None
    filters: dict[str, Any] | None = None


class ProfileOut(BaseModel):
    v: int
    preset: str | None
    weights: dict[str, float]
    filters: ProfileFilters
    hash: str = Field(description="Хеш расчётной части профиля (веса + фильтры)")
    allowed_clusters: list[str]

    @classmethod
    def of(cls, profile: Profile) -> "ProfileOut":
        return cls(
            v=profile.v,
            preset=profile.preset,
            weights={k: _canonical_number(v) for k, v in profile.weights.items()},
            filters=profile.filters,
            hash=profile.hash,
            allowed_clusters=list(profile.allowed_clusters),
        )
