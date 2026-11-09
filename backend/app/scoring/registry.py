"""Load the IndexEngine implementation named by SCORING_ENGINE ("module.path:ClassName")."""

from __future__ import annotations

import importlib

from app.scoring.contract import IndexEngine


class EngineLoadError(RuntimeError):
    pass


def load_engine(dotted: str) -> IndexEngine:
    module_name, _, attr = dotted.partition(":")
    if not module_name or not attr:
        raise EngineLoadError(f"SCORING_ENGINE must look like 'package.module:ClassName', got {dotted!r}")
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attr)
    except (ImportError, AttributeError) as exc:
        raise EngineLoadError(f"cannot import scoring engine {dotted!r}: {exc}") from exc
    engine = factory() if callable(factory) and isinstance(factory, type) else factory
    if not isinstance(engine, IndexEngine):
        raise EngineLoadError(f"{dotted!r} does not implement IndexEngine (name, version, score())")
    return engine
