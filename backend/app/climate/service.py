"""Read the committed 2025 grid and settlement summaries; no network at request time."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.profiles.contract import ClimateRange

YEAR = 2025
SEASONS = ("winter", "spring", "summer", "autumn")
# Editorial targets for the second map, not climate normals or health standards.
TARGETS = {
    "winter": (-12.0, -8.0),
    "spring": (10.0, 18.0),
    "summer": (22.0, 26.0),
    "autumn": (8.0, 16.0),
}
HUMIDITY_TARGET = (40.0, 70.0)
YELLOW_TEMPERATURE_MARGIN_C = 4.0
YELLOW_HUMIDITY_MARGIN_PCT = 15.0


def signed_deviation(value: float, lower: float, upper: float) -> float:
    if value < lower:
        return round(value - lower, 3)
    if value > upper:
        return round(value - upper, 3)
    return 0.0


def color_for(temperature_c: float, humidity_pct: float, season: str) -> dict:
    lo, hi = TARGETS[season]
    dt = signed_deviation(temperature_c, lo, hi)
    dh = signed_deviation(humidity_pct, *HUMIDITY_TARGET)
    if dt == 0 and dh == 0:
        color = "green"
    elif abs(dt) <= YELLOW_TEMPERATURE_MARGIN_C and abs(dh) <= YELLOW_HUMIDITY_MARGIN_PCT:
        color = "yellow"
    else:
        color = "red"
    return {"color": color, "temperature_deviation_c": dt, "humidity_deviation_pct": dh}


def preference_match(rows: dict[str, dict], desired: dict[str, ClimateRange]) -> float | None:
    """Mean season suitability: 1 in a desired range, linearly decays outside it."""
    if not desired:
        return None
    scores = []
    for season, preferred in desired.items():
        actual = rows.get(season)
        if actual is None:
            return None
        t_delta = abs(signed_deviation(actual["temperature_c"], preferred.temperature_min_c,
                                       preferred.temperature_max_c))
        t_fit = max(0.0, 1.0 - t_delta / 10.0)
        if preferred.humidity_min_pct is not None:
            h_delta = abs(signed_deviation(actual["humidity_pct"], preferred.humidity_min_pct,
                                           preferred.humidity_max_pct))
            h_fit = max(0.0, 1.0 - h_delta / 30.0)
            t_fit *= h_fit
        scores.append(t_fit)
    return round(sum(scores) / len(scores), 6)


def _read(path: Path, key_field: str) -> dict[str, dict[str, dict]]:
    if not path.is_file():
        raise FileNotFoundError(f"Climate data missing: {path}")
    out = defaultdict(dict)
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if int(row["year"]) != YEAR or row["season"] not in SEASONS:
                raise ValueError(f"Invalid year/season in {path}")
            key = row[key_field]
            season = row["season"]
            if season in out[key]:
                raise ValueError(f"Duplicate climate row: {key}, {season}")
            parsed = dict(row)
            for field in ("grid_lon", "grid_lat", "temperature_c", "humidity_pct", "wind_m_s",
                          "grid_distance_km", "lon", "lat"):
                if field in parsed:
                    parsed[field] = float(parsed[field])
            for field in ("year", "warm_days", "extreme_days", "wet_days", "days_in_season"):
                parsed[field] = int(parsed[field])
            out[key][season] = parsed
    if not out or any(set(v) != set(SEASONS) for v in out.values()):
        raise ValueError(f"Incomplete climate data in {path}")
    return dict(out)


class ClimateService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._grid: dict[str, dict[str, dict]] | None = None
        self._settlements: dict[str, dict[str, dict]] | None = None

    @property
    def grid(self) -> dict[str, dict[str, dict]]:
        if self._grid is None:
            self._grid = self._load_grid()
        return self._grid

    def _load_grid(self) -> dict[str, dict[str, dict]]:
        path = self.data_dir / "climate_2025_grid.csv"
        if not path.is_file():
            raise FileNotFoundError(f"Climate data missing: {path}")
        out = defaultdict(dict)
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                key = f"{row['grid_lon']},{row['grid_lat']}"
                season = row["season"]
                if int(row["year"]) != YEAR or season not in SEASONS or season in out[key]:
                    raise ValueError(f"Invalid/duplicate grid row: {key}, {season}")
                out[key][season] = {k: (float(v) if k in {"grid_lon", "grid_lat", "temperature_c", "humidity_pct", "wind_m_s"}
                                        else int(v) if k in {"year", "warm_days", "extreme_days", "wet_days", "days_in_season"}
                                        else v) for k, v in row.items()}
        if not out or any(set(v) != set(SEASONS) for v in out.values()):
            raise ValueError("Incomplete climate grid")
        return dict(out)

    @property
    def settlements(self) -> dict[str, dict[str, dict]]:
        if self._settlements is None:
            self._settlements = _read(self.data_dir / "climate_2025_settlements.csv", "settlement_osm_id")
        return self._settlements

    def settlement(self, external_id: str) -> dict[str, dict] | None:
        return self.settlements.get(external_id.removeprefix("osm:node:"))

    def match(self, external_id: str, desired: dict[str, ClimateRange]) -> float | None:
        rows = self.settlement(external_id)
        return preference_match(rows, desired) if rows else None

    def grid_season(self, season: str) -> list[dict]:
        return [{**seasons[season], **color_for(seasons[season]["temperature_c"],
                                                seasons[season]["humidity_pct"], season)}
                for seasons in self.grid.values()]
