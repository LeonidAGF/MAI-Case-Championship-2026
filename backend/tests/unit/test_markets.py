"""Sales markets and sector assessments (tasks 4+5): methodology config, absolute scores, texts."""

from __future__ import annotations

import copy
import math

import pytest
import yaml
from pydantic import ValidationError

from app.core.config import BACKEND_DIR
from app.etl.ml_results import _record
from app.markets.config import MarketsConfig, ScaleCfg, load_markets_config
from app.markets.service import MarketService, percentiles
from app.markets.texts import compare_text, fmt_duration, part_text, scale_text, verdict
from app.modelcfg import load_model_config
from app.scoring.matrix import DataRevision

MARKETS_YAML = BACKEND_DIR / "config" / "markets.default.yaml"
MODEL_YAML = BACKEND_DIR / "config" / "model.default.yaml"


@pytest.fixture(scope="module")
def raw_cfg() -> dict:
    with MARKETS_YAML.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def service() -> MarketService:
    return MarketService(load_model_config(MODEL_YAML), load_markets_config(MARKETS_YAML), DataRevision())


def values(**over: float | None) -> dict[str, float | None]:
    """A complete settlement: every part has data."""
    base = {"population": 10_000.0, "buyers_30": 100_000.0, "buyers_120": 2_000_000.0, "institutions": 20.0,
            "retail_hubs": 5.0, "food_service": 10.0, "rail": 30.0, "airport": 60.0, "regional_center": 60.0,
            "big_city": 45.0}
    return {**base, **over}


# --- config ---------------------------------------------------------------------------
def test_default_config_is_valid(service: MarketService) -> None:
    m = service.mcfg
    assert set(m.markets) == {"local", "catchment", "regional", "institutional", "retail", "interregional"}
    assert len(m.sectors) == 6
    for sector in m.sectors.values():
        assert math.isclose(sum(sector.weights.values()), 1.0, abs_tol=0.01)
    assert {p.direction for _, p in m.parts if p.source == "feature"} == {"lower_better"}
    assert len(m.config_hash) == 16


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda c: c["sectors"]["food"]["weights"].update(local=0.5), "sum to 1"),
        (lambda c: c["sectors"]["food"]["weights"].update(space=0.0), "unknown markets"),
        (lambda c: c["markets"]["local"]["parts"][0]["scale"].pop("full"), "zero < full"),
        (lambda c: c["markets"]["local"]["parts"][0].update(scale={"type": "decay", "half_life": 30}), "travel-time"),
        (lambda c: c["markets"]["retail"]["parts"][0]["categories"].update(bank=1), "unknown"),
        (lambda c: c.update(tiers={"high": 30, "medium": 60}), "medium must be below high"),
        (lambda c: c["markets"]["local"]["parts"][0].update(direction="lower_better"), "Extra inputs"),
    ],
)
def test_config_rejects_inconsistent_methodology(raw_cfg: dict, mutate, message: str) -> None:  # noqa: ANN001
    cfg = copy.deepcopy(raw_cfg)
    mutate(cfg)
    with pytest.raises(ValidationError, match=message):
        MarketsConfig.model_validate(cfg)


def test_config_features_must_exist_in_the_model(raw_cfg: dict) -> None:
    cfg = MarketsConfig.model_validate(raw_cfg)
    with pytest.raises(ValueError, match="F15"):
        cfg.check_features({"F14", "F16", "F21"})


# --- scales and percentiles --------------------------------------------------------------
def test_log_scale_anchors_and_clamping() -> None:
    s = ScaleCfg(type="log", zero=100, full=1_000_000)
    assert s.score(50) == 0 and s.score(100) == 0 and s.score(5_000_000) == 100
    assert [round(s.score(v)) for v in (1_000, 10_000, 100_000)] == [25, 50, 75]
    counts = ScaleCfg(type="log", zero=0, full=500)
    assert counts.score(0) == 0 and counts.score(500) == 100 and 0 < counts.score(1) < counts.score(2)


def test_decay_scale_halves_every_half_life() -> None:
    s = ScaleCfg(type="decay", half_life=30)
    assert s.score(0) == 100 and s.score(30) == pytest.approx(50) and s.score(90) == pytest.approx(12.5)
    assert s.direction == "lower_better"


def test_percentiles_share_ties() -> None:
    assert percentiles({1: 1.0, 2: 2.0, 3: 2.0, 4: 3.0}) == {1: 0.0, 2: 50.0, 3: 50.0, 4: 100.0}
    assert percentiles({7: 5.0}) == {7: 50.0}
    assert percentiles({}) == {}


# --- absolute scores -------------------------------------------------------------------
def test_scores_follow_the_scales(service: MarketService) -> None:
    part, market, sector, coverage = service._score({1: values()})
    assert round(part[1]["population"]) == 50 and round(part[1]["rail"]) == 50
    retail = (part[1]["retail_hubs"] + part[1]["food_service"]) / 2
    assert market[1]["retail"] == pytest.approx(retail)
    weights = service.mcfg.sectors["food"].weights
    assert sector[1]["food"] == pytest.approx(sum(w * market[1][m] for m, w in weights.items()))
    assert coverage[1]["food"] == 1.0


def test_scores_do_not_depend_on_other_settlements(service: MarketService) -> None:
    """The main score is absolute; only the additional percentile metrics move with the others."""
    pair = {1: values(), 2: values(population=1_000.0, buyers_30=30_000.0)}
    _, _, alone, _ = service._score(pair)
    crowd = {**pair, 3: values(population=600_000.0, buyers_30=1_000_000.0, institutions=500.0)}
    _, market, with_city, _ = service._score(crowd)
    assert with_city[1] == alone[1] and with_city[2] == alone[2]

    before = service._scope([1, 2], *service._score(pair)[1:3])
    after = service._scope([1, 2, 3], market, with_city)
    assert before.sector[1]["food"] == 100.0 and after.sector[1]["food"] == 50.0
    assert after.rank["food"] == {3: 1, 1: 2, 2: 3} and after.size["food"] == 3


def test_missing_markets_are_left_out_and_reported(service: MarketService) -> None:
    raw = {1: values(population=None), 2: {k: None for k in values()} | {"population": 5_000.0}}
    _, market, sector, coverage = service._score(raw)
    weights = service.mcfg.sectors["food"].weights
    assert "local" not in market[1] and coverage[1]["food"] == pytest.approx(1 - weights["local"])
    expected = sum(w * market[1][m] for m, w in weights.items() if m != "local") / (1 - weights["local"])
    assert sector[1]["food"] == pytest.approx(expected)
    # Only the local market has data: below min_coverage, the sector is not assessed.
    assert coverage[2]["food"] == weights["local"] and "food" not in sector[2]


# --- texts -----------------------------------------------------------------------------
def test_part_texts(service: MarketService) -> None:
    parts = {p.key: p for _, p in service.mcfg.parts}
    assert part_text(parts["population"], 13_152) == "Жители: 13 тыс. человек"
    assert part_text(parts["population"], 2_341) == "Жители: 2 341 человек"  # fmt_num: non-breaking space
    assert part_text(parts["population"], None) == "Жители: население неизвестно"
    assert part_text(parts["buyers_30"], 1_140_000).endswith("около 1,1 млн человек с учётом времени в пути")
    assert part_text(parts["retail_hubs"], 1) == "ТЦ и рынки в радиусе 20 км: 1 объект"
    assert part_text(parts["rail"], 92) == "До ЖД-станции — 1 ч 32 мин"
    assert part_text(parts["airport"], 0.2) == "До аэропорта — меньше минуты"
    assert fmt_duration(120) == "2 ч" and fmt_duration(45) == "45 минут"
    assert "вдвое" in scale_text(parts["rail"].scale, "мин")
    assert scale_text(parts["population"].scale, "чел.").startswith("0 баллов — 100 чел. и меньше, 100 — 1 млн чел.")


def test_compare_text_never_overstates() -> None:
    assert compare_text(94.9) == "лучше, чем у 94% населённых пунктов СКФО"
    assert compare_text(17.8, "cluster") == "хуже, чем у 82% населённых пунктов кластера"
    assert compare_text(100.0) == "лучший показатель среди населённых пунктов СКФО"
    assert compare_text(0.0, "cluster") == "худший показатель среди населённых пунктов кластера"


def test_verdict() -> None:
    text = verdict("Ахты", "Пищевое производство", score=38.6, tier="medium", percentile=17.8, rank=169, size=180,
                   cluster_name="Промежуточные территории", missing=["местный рынок"])
    assert text == ("«Ахты»: для отрасли «Пищевое производство» привлекательность рынка сбыта средняя — 39 из 100 "
                    "(хуже, чем у 82% населённых пунктов СКФО); 169-е место из 180 в кластере «Промежуточные "
                    "территории». Не учтено из-за отсутствия данных: местный рынок.")
    assert "недостаточно данных" in verdict("Ахты", "Мебель", score=None, tier=None, percentile=None, rank=None,
                                            size=0, cluster_name="", missing=[])


# --- ML results loader -------------------------------------------------------------------
def test_ml_record_rejects_impossible_values() -> None:
    stats = {"invalid_values": 0}
    row = {"gravity_observed_30": "0.1", "gravity_observed_60": "1.7", "gravity_observed_120": "",
           "known_population_reachability": "0.99", "population_data_coverage": "0.45",
           "known_destination_population": "-5"}
    rec = _record(row, stats)
    assert rec["gravity_30"] == 0.1 and rec["gravity_60"] is None and rec["gravity_120"] is None
    assert rec["known_destination_population"] is None and stats["invalid_values"] == 2
