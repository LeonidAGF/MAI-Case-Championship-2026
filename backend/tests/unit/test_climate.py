"""Seasonal 2025 preference and project-color invariants."""

import csv

import pytest
from pydantic import ValidationError

from app.climate.service import ClimateService, color_for, preference_match, signed_deviation
from app.profiles.contract import ClimateRange, ProfileFilters
from app.scoring.contract import FeatureSpec, ScoreRequest
from app.scoring.reference import ReferenceIndexEngine


def test_project_colors_and_signed_deviation() -> None:
    assert color_for(24, 55, "summer")["color"] == "green"
    assert color_for(20, 55, "summer") == {"color": "yellow", "temperature_deviation_c": -2,
                                              "humidity_deviation_pct": 0}
    assert color_for(32, 55, "summer")["color"] == "red"
    assert color_for(24, 90, "summer")["color"] == "red"
    assert signed_deviation(-15, -12, -8) == -3


def test_preference_validation_and_match() -> None:
    with pytest.raises(ValidationError):
        ClimateRange(temperature_min_c=26, temperature_max_c=22)
    with pytest.raises(ValidationError):
        ProfileFilters(climate_match_min=0.7)
    preferred = ClimateRange(temperature_min_c=22, temperature_max_c=26,
                             humidity_min_pct=40, humidity_max_pct=70)
    assert preference_match({"summer": {"temperature_c": 24, "humidity_pct": 55}},
                            {"summer": preferred}) == 1
    assert preference_match({"summer": {"temperature_c": 20, "humidity_pct": 55}},
                            {"summer": preferred}) == 0.8


def test_climate_preference_changes_s6_but_preserves_component_sum() -> None:
    reference = {1: {"F24": 2.0}, 2: {"F24": 20.0}}
    base = ScoreRequest(
        "V", {}, [FeatureSpec("F24", "S6", "lower_better", 1)],
        ["S1", "S2", "S3", "S4", "S5", "S6"], None,
        {"S1": 0, "S2": 0, "S3": 0, "S4": 0, "S5": 0, "S6": 100},
        {"climate_sensitivity": "mid"}, reference, reference,
    )
    original = ReferenceIndexEngine().score(base)
    personal = ReferenceIndexEngine().score(ScoreRequest(
        base.cluster_code, base.cluster_params, base.features, base.total_subindices,
        base.attractiveness_subindex, base.subindex_weights, base.filters,
        base.reference, base.targets, {1: 0.2, 2: 1.0},
    ))
    assert original[1].total == pytest.approx(100)
    assert personal[1].total == pytest.approx(40)
    assert personal[1].climate_adjustment_points == pytest.approx(-60)
    assert personal[2].total == original[2].total
    assert all(sum(c.contribution for c in score.components) == pytest.approx(score.total)
               for score in personal.values())


def test_climate_store_rejects_incomplete_seasons(tmp_path) -> None:
    path = tmp_path / "climate_2025_settlements.csv"
    row = {"settlement_osm_id": "1", "year": "2025", "season": "summer",
           "grid_lon": "44", "grid_lat": "43", "temperature_c": "24", "humidity_pct": "50",
           "wind_m_s": "2", "warm_days": "100", "extreme_days": "10", "wet_days": "20",
           "days_in_season": "92", "grid_distance_km": "10"}
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(ValueError, match="Incomplete"):
        _ = ClimateService(tmp_path).settlements
