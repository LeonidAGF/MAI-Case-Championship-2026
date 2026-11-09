import re

import pytest

from app.core.config import BACKEND_DIR
from app.explain.service import round_preserving_sum
from app.explain.texts import SPECS, fact_text, fmt_minutes, fmt_num, plural
from app.modelcfg import load_model_config

CFG = load_model_config(BACKEND_DIR / "config" / "model.default.yaml")
FORMS = ("минута", "минуты", "минут")


@pytest.mark.parametrize(
    ("n", "word"),
    [(1, "минута"), (2, "минуты"), (5, "минут"), (11, "минут"), (12, "минут"), (21, "минута"), (22, "минуты"),
     (25, "минут"), (101, "минута"), (111, "минут")],
)
def test_plural(n: int, word: str) -> None:
    assert plural(n, FORMS) == word


def test_number_format() -> None:
    assert fmt_num(12.5) == "12,5"
    assert fmt_num(145000) == "145 000"
    assert fmt_num(-3.2) == "−3,2"
    assert fmt_num(2.0, signed=True) == "+2"
    assert fmt_minutes(47.4) == "47 минут"


def test_examples_from_the_report() -> None:
    assert fact_text("F05", 8, name="", unit="мин", mode="foot") == "Ближайший детский сад — 8 минут пешком или на транспорте"
    assert fact_text("F02", 47, name="", unit="мин", mode="car") == "Ближайшая поликлиника, амбулатория или ФАП — 47 минут на машине"


def test_every_feature_has_readable_text_without_codes() -> None:
    for code, f in CFG.features.items():
        for value in (0, 1, 7.5, 63):
            txt = fact_text(code, value, name=f.name, unit=f.unit, mode="car", inherited_from="municipality",
                            typical="10 минут")
            assert not re.search(r"\b[FSW]\d{1,2}\b", txt), txt
            assert txt[0].isupper() or txt[0].isdigit(), txt
            assert txt.count("(") <= 1
        assert "нет данных" in fact_text(code, None, name=f.name, unit=f.unit)
    assert set(SPECS) >= set(CFG.features)


@pytest.mark.parametrize("values", [[0.123, 0.456, -0.3, 1.04], [1 / 3] * 6, [5.55, -2.25, 0.05]])
def test_round_preserving_sum(values: list[float]) -> None:
    target = round(sum(values), 1)
    rounded = round_preserving_sum(values, target)
    assert round(sum(rounded), 1) == target
    assert all(abs(a - b) < 0.1 + 1e-9 for a, b in zip(rounded, values, strict=True))
