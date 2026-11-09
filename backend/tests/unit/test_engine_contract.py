"""Invariants every IndexEngine must satisfy (run against the reference implementation)."""

import random

import pytest

from app.scoring.contract import FeatureSpec, ScoreRequest
from app.scoring.reference import ReferenceIndexEngine

FEATURES = [
    FeatureSpec("F01", "S1", "lower_better", 1.0),
    FeatureSpec("F03", "S1", "higher_better", 1.0),
    FeatureSpec("F05", "S2", "lower_better", 2.0),
    FeatureSpec("F14", "S4", "lower_better", 1.0),
    FeatureSpec("F27", "S7", "higher_better", 1.0),
]
TOTALS = ["S1", "S2", "S3", "S4", "S5", "S6"]
WEIGHTS = {"S1": 25, "S2": 30, "S3": 15, "S4": 15, "S5": 10, "S6": 5}
PARAMS = {"winsor_lower": 0.01, "winsor_upper": 0.99, "min_size_for_minmax": 10}


def matrix(n: int, seed: int = 1) -> dict[int, dict[str, float | None]]:
    rng = random.Random(seed)
    return {
        i: {"F01": rng.uniform(5, 90), "F03": float(rng.randint(0, 20)), "F05": rng.uniform(3, 60),
            "F14": rng.uniform(10, 300), "F27": rng.uniform(-200, 400)}
        for i in range(1, n + 1)
    }


def request(ref, targets=None, weights=WEIGHTS, params=PARAMS) -> ScoreRequest:  # noqa: ANN001
    return ScoreRequest("V", params, FEATURES, TOTALS, "S7", weights, {}, ref, targets if targets is not None else ref)


def test_waterfall_adds_up_and_ranges() -> None:
    ref = matrix(200)
    res = ReferenceIndexEngine().score(request(ref))
    assert set(res) == set(ref)
    for s in res.values():
        assert 0 <= s.total <= 100
        assert abs(sum(c.contribution for c in s.components) - s.total) < 1e-9
        assert [c.feature_code for c in s.components] == [f.code for f in FEATURES]
        for v in s.subindices.values():
            assert v is None or 0 <= v <= 100
        assert s.subindices["S3"] is None  # no features -> no data
        assert s.attractiveness == s.subindices["S7"]
        assert next(c for c in s.components if c.feature_code == "F27").weight == 0.0  # S7 not in total


def test_deterministic() -> None:
    ref = matrix(50)
    a = ReferenceIndexEngine().score(request(ref))
    b = ReferenceIndexEngine().score(request(ref))
    assert {k: v.total for k, v in a.items()} == {k: v.total for k, v in b.items()}


def test_direction_and_best_worst() -> None:
    ref = {1: {"F01": 5.0, "F03": 20.0, "F05": 3.0, "F14": 10.0, "F27": 100.0}}
    ref.update({i: {"F01": 50.0, "F03": 5.0, "F05": 30.0, "F14": 150.0, "F27": 0.0} for i in range(2, 30)})
    ref[30] = {"F01": 90.0, "F03": 0.0, "F05": 60.0, "F14": 300.0, "F27": -100.0}
    res = ReferenceIndexEngine().score(request(ref))
    assert res[1].total > res[2].total > res[30].total
    assert res[1].subindices["S1"] == pytest.approx(100.0)
    assert res[30].subindices["S1"] == pytest.approx(0.0)


def test_missing_value_imputed_with_median_and_flagged() -> None:
    ref = matrix(40)
    ref[7]["F05"] = None
    res = ReferenceIndexEngine().score(request(ref))
    comp = next(c for c in res[7].components if c.feature_code == "F05")
    assert comp.imputed and comp.raw_value is None and comp.normalized is not None


def test_feature_without_data_is_redistributed() -> None:
    ref = matrix(40)
    for row in ref.values():
        row["F03"] = None
    res = ReferenceIndexEngine().score(request(ref))
    s = res[1]
    f03 = next(c for c in s.components if c.feature_code == "F03")
    assert f03.normalized is None and f03.weight == 0
    assert abs(sum(c.weight for c in s.components) - 1.0) < 1e-9  # weights still sum to 1


def test_small_cluster_uses_rank_normalization() -> None:
    ref = {i: {"F01": float(i), "F03": float(i), "F05": float(i), "F14": float(i), "F27": float(i)} for i in range(1, 6)}
    res = ReferenceIndexEngine().score(request(ref))
    f03 = [next(c for c in res[i].components if c.feature_code == "F03").normalized for i in range(1, 6)]
    assert f03 == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])


def test_whatif_targets_do_not_move_reference() -> None:
    ref = matrix(100)
    base = ReferenceIndexEngine().score(request(ref))
    improved = {7: {**ref[7], "F01": 1.0}}
    after = ReferenceIndexEngine().score(request(ref, improved))
    assert after[7].total > base[7].total
    assert set(after) == {7}  # only modified rows are scored


def test_profile_weights_change_total_but_not_subindices() -> None:
    ref = matrix(60)
    a = ReferenceIndexEngine().score(request(ref))
    b = ReferenceIndexEngine().score(request(ref, weights={"S1": 100, "S2": 0, "S3": 0, "S4": 0, "S5": 0, "S6": 0}))
    assert a[3].subindices == b[3].subindices
    assert b[3].total == pytest.approx(b[3].subindices["S1"])
