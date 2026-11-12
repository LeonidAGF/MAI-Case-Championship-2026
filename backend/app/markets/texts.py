"""Russian phrasing for sales markets and sector assessments (no feature codes, no LLM)."""

from __future__ import annotations

import math

from app.explain.texts import fmt_minutes, fmt_num, plural
from app.markets.config import PartCfg, ScaleCfg

OBJECTS = ("объект", "объекта", "объектов")
PEOPLE = ("человек", "человека", "человек")
TIER_LABEL = {"high": "высокая", "medium": "средняя", "low": "низкая"}
SCOPE = {"cluster": "кластера", "skfo": "СКФО"}


def fmt_people(n: float) -> str:
    if n >= 1_000_000:
        return f"{fmt_num(n / 1_000_000, 1)} млн"
    if n >= 10_000:
        return f"{fmt_num(round(n / 1000), 0)} тыс."
    return fmt_num(round(n), 0)


def fmt_duration(minutes: float) -> str:
    if minutes < 60:
        return fmt_minutes(minutes)
    hours, rest = divmod(int(round(minutes)), 60)
    return f"{hours} ч {rest} мин" if rest else f"{hours} ч"


def fmt_score(score: float) -> str:
    return f"{fmt_num(score, 0)} из 100"


def scale_text(scale: ScaleCfg, unit: str) -> str:
    """How a value becomes the 0..100 score (shown in the methodology)."""
    if scale.type == "decay":
        return f"100 баллов — рядом; через каждые {fmt_num(scale.half_life, 0)} мин пути балл уменьшается вдвое"

    def fmt(x: float) -> str:
        return f"{fmt_people(x) if unit == 'чел.' else fmt_num(x, 0)} {unit}".strip()

    zero = "0 баллов — ни одного" if scale.zero == 0 else f"0 баллов — {fmt(scale.zero)} и меньше"  # type: ignore[arg-type]
    return (f"{zero}, 100 — {fmt(scale.full)} и больше; шкала логарифмическая: "  # type: ignore[arg-type]
            "каждое удвоение даёт примерно одинаковую прибавку")


def part_text(part: PartCfg, value: float | None) -> str:
    if value is None:
        what = "население неизвестно" if part.source == "population" else "нет данных"
        return f"{part.label}: {what}"
    if part.source == "population":
        n = round(value)
        return f"{part.label}: {fmt_people(value)} {plural(n, PEOPLE) if n < 10_000 else 'человек'}"
    if part.source.startswith("ml_gravity"):
        return f"{part.label}: около {fmt_people(value)} человек с учётом времени в пути"
    if part.source == "poi":
        n = round(value)
        return f"{part.label}: {fmt_num(n, 0)} {plural(n, OBJECTS)}"
    return f"{part.label} — {fmt_duration(value)}"


def compare_text(pct: float, scope: str = "skfo") -> str:
    """Position from a 0..100 percentile, rounded down so the claim is never overstated."""
    where = SCOPE[scope]
    if pct >= 100:
        return f"лучший показатель среди населённых пунктов {where}"
    if pct <= 0:
        return f"худший показатель среди населённых пунктов {where}"
    if pct >= 50:
        return f"лучше, чем у {math.floor(pct)}% населённых пунктов {where}"
    return f"хуже, чем у {math.floor(100 - pct)}% населённых пунктов {where}"


def verdict(
    name: str,
    sector: str,
    *,
    score: float | None,
    tier: str | None,
    percentile: float | None,
    rank: int | None,
    size: int,
    cluster_name: str,
    missing: list[str],
) -> str:
    if score is None or tier is None:
        return f"«{name}»: для отрасли «{sector}» недостаточно данных, чтобы оценить рынок сбыта."
    out = f"«{name}»: для отрасли «{sector}» привлекательность рынка сбыта {TIER_LABEL[tier]} — {fmt_score(score)}"
    if percentile is not None:
        out += f" ({compare_text(percentile, 'skfo')})"
    if rank:
        out += f"; {rank}-е место из {fmt_num(size, 0)} в кластере «{cluster_name}»"
    out += "."
    if missing:
        out += f" Не учтено из-за отсутствия данных: {', '.join(missing)}."
    return out
