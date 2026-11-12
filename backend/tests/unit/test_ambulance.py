"""Ambulance travel time: configuration, speed rules, reporting rules and texts.

Only formulas on literal values here (no datasets). The computation on the road graph is tested on
the real committed data in tests/integration/test_real_ambulance.py.
"""

import re

import numpy as np
import pytest
import yaml

from app.ambulance import texts
from app.ambulance.config import AmbulanceConfig, load_ambulance_config
from app.ambulance.dem import tile_name, tile_url, tiles_covering
from app.ambulance.service import AmbulanceService
from app.ambulance.speeds import height_factor, parse_maxspeed, slope_factor
from app.core.config import BACKEND_DIR, Settings

CFG_PATH = BACKEND_DIR / "config" / "ambulance.default.yaml"
CFG = load_ambulance_config(CFG_PATH)
CODES = re.compile(r"\b[FSW]\d{1,2}\b")


def test_default_config() -> None:
    assert CFG.bases.categories == ["ambulance", "hospital"]
    assert CFG.norm_minutes == 20 and CFG.dispatch_minutes == 0
    assert CFG.speeds.kmh("trunk", urban=False) == 90 and CFG.speeds.kmh("trunk", urban=True) == 60
    assert CFG.speeds.kmh("residential", urban=False) == 60 and CFG.speeds.kmh("living_street", urban=False) == 20
    assert CFG.speeds.kmh("unknown_type", urban=True) == CFG.speeds.default


def test_model_hash_covers_only_the_model_keys() -> None:
    data = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    report_only = AmbulanceConfig.model_validate({**data, "norm_minutes": 30, "dispatch_minutes": 3})
    assert report_only.model_hash == CFG.model_hash
    faster = AmbulanceConfig.model_validate({**data, "speeds": {**data["speeds"], "default": 40}})
    assert faster.model_hash != CFG.model_hash


@pytest.mark.parametrize(
    "patch",
    [
        {"unknown_key": 1},
        {"slope": {"enabled": True, "baseline_m": 100, "max_grade": 0.3, "steep_grade": 0.08,
                   "uphill": [[0.06, 0.9], [0.03, 1.0]], "downhill": [[0.03, 1.0]]}},  # x not increasing
        {"height": {"enabled": True, "points": [[500, 1.2]]}},  # a factor above 1
        {"thresholds": [30, 20]},
    ],
)
def test_config_rejects_bad_values(patch: dict) -> None:
    data = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    with pytest.raises(ValueError):
        AmbulanceConfig.model_validate({**data, **patch})


@pytest.mark.parametrize(
    ("raw", "kmh"),
    [("60", 60.0), ("60 km/h", 60.0), ("30 mph", 48.28032), ("RU:urban", 60.0), ("ru:rural", 90.0),
     ("RU:Urban", 60.0), ("60;40", 60.0), ("none", None), ("signals", None), ("500", None), (None, None)],
)
def test_parse_maxspeed(raw: str | None, kmh: float | None) -> None:
    assert parse_maxspeed(raw, CFG.speeds) == (pytest.approx(kmh) if kmh is not None else None)


def test_slope_factor() -> None:
    grades = np.array([0.0, 0.02, -0.02, 0.06, -0.06, 0.12, -0.12, 0.25, -0.25, np.nan])
    f = slope_factor(grades, CFG.slope)
    assert f[0] == f[1] == f[2] == 1.0  # gentle grades: no slowdown
    assert f[-1] == 1.0  # unknown grade: no slowdown
    assert 0 < f[7] < f[5] < f[3] < 1 and 0 < f[8] < f[6] < f[4] < 1  # steeper = slower
    assert f[3] < f[4] and f[5] < f[6]  # uphill is slower than the same grade downhill
    assert f[7] == CFG.slope.uphill[-1][1]  # flat beyond the last point


def test_height_factor() -> None:
    f = height_factor(np.array([0.0, 500.0, 1250.0, 2000.0, 4000.0, np.nan]), CFG.height)
    assert f[0] == f[1] == 1.0 and f[-1] == 1.0
    assert 1.0 > f[2] > f[3] > f[4] > 0
    assert f[2] == pytest.approx((0.97 + 0.93) / 2)


@pytest.mark.parametrize(
    ("travel", "low", "high", "status"),
    [(5.0, 4.0, 6.0, "met"), (10.0, 9.0, 11.5, "met"), (18.0, 16.2, 20.7, "borderline"),
     (30.0, 27.0, 34.5, "not_met"), (0.5, 0.0, 1.5, "met")],
)
def test_error_band_and_norm(travel: float, low: float, high: float, status: str) -> None:
    est = AmbulanceService(CFG).estimate(travel)
    assert (est["minutes_low"], est["minutes"], est["minutes_high"]) == (low, travel, high)
    assert est["norm_status"] == status and est["norm_minutes"] == 20


def test_dispatch_time_is_added_outside_the_band() -> None:
    cfg = CFG.model_copy(update={"dispatch_minutes": 3.0})
    est = AmbulanceService(cfg).estimate(10.0)
    assert (est["minutes_low"], est["minutes"], est["minutes_high"], est["travel_minutes"]) == (12.0, 13.0, 14.5, 10.0)


def test_texts() -> None:
    assert texts.minutes_text(0.4) == "меньше минуты"
    assert texts.minutes_text(1.0) == "1 минута" and texts.minutes_text(2.5) == "2,5 минуты"
    assert texts.minutes_text(22.4) == "22 минуты" and texts.minutes_text(25) == "25 минут"
    assert texts.range_text(12.6, 17.2) == "от 13 до 17 минут"
    assert texts.estimate_text(0.7, 0.0, 1.7, "met", 20).startswith("Скорая помощь доедет меньше чем за минуту")
    assert "дольше норматива 20 минут" in texts.estimate_text(34, 31, 39, "not_met", 20)
    assert texts.terrain_text(0.2, 10, 0.0, 500, 0.08) is None
    terrain = texts.terrain_text(3.4, 850, 7.0, 1780, 0.08)
    assert terrain == ("Рельеф добавляет около 3,4 минуты: подъём 850 м, крутые участки (уклон от 8%) — 7 км, "
                       "высшая точка — 1 780 м над уровнем моря.")  # fmt_num groups digits with a no-break space
    stats = {"estimated": 10, "within_norm": {"share": 0.8, "settlements": 8, "population_share": 0.95},
             "median_minutes": 9.0, "p90_minutes": 25.0, "population_settlements": 4, "population_known": 12000}
    summary = texts.summary_text("Район", stats, 20)
    assert "80%" in summary and "95% жителей" in summary
    every = [terrain, summary, texts.HOSPITAL_NOTE, texts.MAP_GAP_NOTE, texts.NO_TERRAIN_NOTE, texts.STALE_NOTE,
             texts.far_access_note(1200), texts.dispatch_note(0), texts.dispatch_note(2), texts.accuracy_note(0.1, 0.15),
             texts.norm_note(20), texts.UNAVAILABLE_TEXT, *texts.HIGHWAY_LABEL.values(), *texts.CATEGORY_LABEL.values()]
    assert not [t for t in every if CODES.search(t)]


def test_srtm_tiles_and_settings() -> None:
    assert tile_name(43, 44) == "N43E044" and tile_name(-1, -73) == "S01W073"
    url = tile_url(Settings(_env_file=None).srtm_url, 43, 44)
    assert url.endswith("/skadi/N43/N43E044.hgt.gz")
    assert tiles_covering(np.array([44.68, 44.99, 47.5, -0.5]), np.array([43.02, 43.99, 42.98, -0.5])) == [
        (-1, -1), (42, 47), (43, 44)]
    settings = Settings(_env_file=None)
    assert settings.srtm_arcsec == 3 and settings.srtm_dir == BACKEND_DIR / "data" / "srtm"
