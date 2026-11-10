from app.chat.catalog import builtin_documents
from app.chat.grounding import check_grounding
from app.modelcfg import load_model_config
from app.core.config import BACKEND_DIR


def test_catalog_uses_active_config() -> None:
    cfg = load_model_config(BACKEND_DIR / "config" / "model.default.yaml")
    docs = builtin_documents(cfg)
    assert len(docs) == 4 + len(cfg.subindices) + len(cfg.features) + len(cfg.clusters) + len(cfg.presets)
    assert any(doc["title"].startswith("F01:") and cfg.features["F01"].name in doc["text"] for doc in docs)
    assert any("synthetic-demo" in doc["text"] for doc in docs)


def test_grounding_rejects_small_numbers_and_years_without_evidence() -> None:
    report = check_grounding("Больница в 3 минутах по данным 2025 года", [])
    assert not report.ok
    assert set(report.ungrounded) == {"3", "2025"}
