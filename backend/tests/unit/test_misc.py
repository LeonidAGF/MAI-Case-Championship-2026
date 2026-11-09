import types
from pathlib import Path

import pytest
import yaml

from app.cache import MemoryCache
from app.chat.grounding import check_grounding, parse_numbers
from app.chat.service import echo_content
from app.core.config import BACKEND_DIR
from app.core.ratelimit import RateLimiter
from app.etl.io import parse_number, read_csv
from app.etl.stats import parse_long, parse_wide
from app.modelcfg import ModelConfig, load_model_config
from app.whatif.catalog import CATALOG, W6Params
from app.whatif.engine import Effect, WorkingSet, _apply

CFG_PATH = BACKEND_DIR / "config" / "model.default.yaml"


def test_default_config_and_report_inconsistency() -> None:
    cfg = load_model_config(CFG_PATH)
    assert cfg.total_subindices == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert cfg.attractiveness_subindex == "S7"
    assert len(cfg.features) == 30 and set(cfg.presets) == {"family", "young_specialist", "pensioner", "remote_worker", "realtor"}
    # Table 2 of the report: the realtor row sums to 90 -> rescaled proportionally, with a warning.
    assert any("realtor" in w for w in cfg.warnings)
    assert abs(sum(cfg.presets["realtor"].weights.values()) - 100) < 0.1
    assert cfg.badge_for(57.7) == {"code": "stable", "label": "Стабильная"}


def test_config_rejects_unknown_subindex() -> None:
    data = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    data["features"]["F01"]["subindex"] = "S9"
    with pytest.raises(ValueError):
        ModelConfig.model_validate(data)


def test_grounding() -> None:
    evidence = ['{"total": 63.4, "rank": 12, "items": [{"value": "Ближайшая школа — 47 минут"}]}']
    ok = check_grounding("Индекс 63,4 (≈63), 12-е место; школа в 47 минутах. Топ-3 и F05, S1, W10.", evidence)
    assert ok.ok, ok.ungrounded
    bad = check_grounding("Индекс 71,2 и 140 минут до больницы", evidence)
    assert bad.ungrounded == ["140", "71,2"]
    assert [v for _, v in parse_numbers("145 000 руб. и −2,5%")] == [145000.0, -2.5]


def test_echo_content_drops_internal_blocks_before_fallback() -> None:
    b = lambda t: types.SimpleNamespace(type=t)  # noqa: E731
    blocks = [b("thinking"), b("text"), b("tool_use"), b("fallback"), b("thinking"), b("tool_use"), b("text")]
    assert [x.type for x in echo_content(blocks)] == ["text", "fallback", "thinking", "tool_use", "text"]
    assert len(echo_content([b("text"), b("tool_use")])) == 2


async def test_rate_limiter_memory() -> None:
    rl = RateLimiter(MemoryCache(), enabled=True)
    results = [await rl.hit("t", "1.2.3.4", 3) for _ in range(5)]
    assert [ok for ok, _ in results] == [True, True, True, False, False]
    assert results[-1][1] >= 1
    assert (await RateLimiter(MemoryCache(), enabled=False).hit("t", "x", 0))[0]


def test_effects() -> None:
    p = W6Params(municipality_id=1)
    assert _apply(Effect("time", "F02"), 47.0, 12.0, p) == 12.0
    assert _apply(Effect("time", "F02"), 10.0, 12.0, p) == 10.0
    assert _apply(Effect("count", "F03", 30), 2.0, 25.0, p) == 3.0
    assert _apply(Effect("count", "F03", 30), 2.0, 45.0, p) == 2.0
    assert _apply(Effect("presence", "F10", 30), 0.0, 20.0, p) == 1.0
    ws = WorkingSet()
    ws.base = {1: {"F02": {"car": 47.0, "foot": 120.0}}}
    ws.set(1, "F02", "car", 12.0)
    ws.set(1, "F02", "foot", 120.0)  # unchanged -> not a change
    assert ws.changes() == [{"territory_id": 1, "feature_code": "F02", "mode": "car", "old": 47.0, "new": 12.0}]


def test_catalog_schemas() -> None:
    assert list(CATALOG) == [f"W{i}" for i in range(1, 11)]
    for d in CATALOG.values():
        d.params_model.model_validate(d.example)
        assert d.params_schema()["type"] == "object"


def test_stats_parsers(tmp_path: Path) -> None:
    long_csv = tmp_path / "bdmo.csv"
    long_csv.write_bytes(
        "Регион;МО;ОКТМО;Показатель;Год;Период;Значение\n"
        "Республика Дагестан;Ахтынский район;82606000;Уровень безработицы;2022;год;12,5\n"
        "Республика Дагестан;Ахтынский район;82606000;Уровень безработицы;2023;год;11,9\n"
        "Республика Дагестан;Ахтынский район;82606000;Число предприятий;2023;год;140\n".encode("cp1251")
    )
    rows = read_csv(long_csv)
    parsed = parse_long(rows, indicator="безработ", period=None)
    assert [(r.year, r.value) for r in parsed] == [(2022, 12.5), (2023, 11.9)]
    wide_csv = tmp_path / "wide.csv"
    wide_csv.write_text("Муниципальное образование,2021,2022,2023\nКурский район,5.1,4.8,\n", encoding="utf-8")
    w = parse_wide(read_csv(wide_csv), year=None)
    assert (w[0].municipality, w[0].year, w[0].value) == ("Курский район", 2022, 4.8)
    assert parse_number("1 234,5") == 1234.5 and parse_number("—") is None
