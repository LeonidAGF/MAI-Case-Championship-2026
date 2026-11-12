"""
Deterministic Russian phrasing of feature values (no LLM): "Ближайшая школа — 12 минут на машине".

Texts never contain feature codes (stage 3 DoD). Features ML adds later fall back to
"<name>: <value> <unit>".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MODE_PHRASE = {"car": "на машине", "foot": "пешком или на транспорте", "any": ""}
MINUTES = ("минута", "минуты", "минут")


def plural(n: float, forms: tuple[str, str, str]) -> str:
    n_int = abs(int(round(n)))
    if n_int % 10 == 1 and n_int % 100 != 11:
        return forms[0]
    if 2 <= n_int % 10 <= 4 and not 12 <= n_int % 100 <= 14:
        return forms[1]
    return forms[2]


def fmt_num(x: float, digits: int = 1, signed: bool = False) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    value = round(float(x), digits)
    if value.is_integer():
        body = f"{int(value):,}".replace(",", " ")
    else:
        body = f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")
    if signed and value > 0:
        body = "+" + body
    return body.replace("-", "−")


def fmt_minutes(x: float) -> str:
    if x < 0.5:  # the object is in the settlement itself (same road-graph node)
        return "меньше минуты"
    m = int(round(x))
    return f"{m} {plural(m, MINUTES)}"


@dataclass(frozen=True)
class Spec:
    kind: str  # time | count | percent | number | presence
    subject: str
    forms: tuple[str, str, str] | None = None
    suffix: str = ""
    unit: str = ""
    signed: bool = False


SPECS: dict[str, Spec] = {
    "F01": Spec("time", "Ближайшая больница"),
    "F02": Spec("time", "Ближайшая поликлиника, амбулатория или ФАП"),
    "F03": Spec("count", "", ("медучреждение", "медучреждения", "медучреждений"), "в пределах 30 минут"),
    "F04": Spec("time", "Станция скорой помощи"),
    "F05": Spec("time", "Ближайший детский сад"),
    "F06": Spec("time", "Ближайшая школа"),
    "F07": Spec("time", "Ближайший колледж или вуз"),
    "F08": Spec("count", "", ("школа", "школы", "школ"), "в пределах 15 минут"),
    "F09": Spec("count", "", ("кафе или ресторан", "кафе или ресторана", "кафе и ресторанов"), "в пешей доступности"),
    "F10": Spec("presence", "Кинотеатр или дом культуры"),
    "F11": Spec("count", "", ("спортивный объект", "спортивных объекта", "спортивных объектов"), "в пределах 15 минут"),
    "F12": Spec("number", "Парки в радиусе 15 минут", unit="га"),
    "F13": Spec("presence", "Торговый центр или рынок"),
    "F14": Spec("time", "До регионального центра"),
    "F15": Spec("time", "До ЖД-станции"),
    "F16": Spec("time", "До аэропорта"),
    "F17": Spec("time", "До автостанции"),
    "F18": Spec("percent", "Местные дороги вне норматива"),
    "F19": Spec("percent", "Жители без регулярного автобусного сообщения"),
    "F20": Spec("number", "Юрлица и ИП", unit="на 1000 жителей"),
    "F21": Spec("time", "До ближайшего города от 50 тыс. жителей"),
    "F22": Spec("number", "Оборот розничной торговли", unit="тыс. ₽ на жителя"),
    "F23": Spec("percent", "Безработица"),
    "F24": Spec("count", "", ("дискомфортный день", "дискомфортных дня", "дискомфортных дней"), "в году: ниже −15 °C или выше +30 °C"),
    "F25": Spec("count", "", ("день", "дня", "дней"), "в году с осадками более 5 мм"),
    "F26": Spec("number", "Ветер в холодный период", unit="м/с"),
    "F27": Spec("number", "Миграционный прирост", unit="чел. в год", signed=True),
    "F28": Spec("percent", "Население за 5 лет", signed=True),
    "F29": Spec("count", "", ("новое юрлицо или ИП", "новых юрлица или ИП", "новых юрлиц и ИП"), "в год"),
    "F30": Spec("number", "Доходы бюджета", unit="тыс. ₽ на жителя"),
}

LEVEL_NOTE = {"municipality": "данные по району", "region": "данные по субъекту"}


def _value_phrase(spec: Spec, value: float, mode: str) -> str:
    if spec.kind == "time":
        phrase = f"{spec.subject} — {fmt_minutes(value)}"
        mode_txt = MODE_PHRASE.get(mode, "")
        return f"{phrase} {mode_txt}".strip()
    if spec.kind == "count":
        n = int(round(value))
        return f"{n} {plural(n, spec.forms or ('', '', ''))} {spec.suffix}".strip()
    if spec.kind == "percent":
        return f"{spec.subject} — {fmt_num(value, 1, spec.signed)}%"
    if spec.kind == "presence":
        if value in (0, 1):
            return f"{spec.subject}: {'есть' if value >= 1 else 'нет'}"
        return f"{spec.subject} — {fmt_minutes(value)}"
    return f"{spec.subject} — {fmt_num(value, 1, spec.signed)} {spec.unit}".strip()


def fact_text(
    code: str,
    value: float | None,
    *,
    name: str,
    unit: str,
    mode: str = "any",
    imputed: bool = False,
    inherited_from: str | None = None,
    typical: str | None = None,
    excluded: bool = False,
) -> str:
    """One readable phrase; notes (data level, typical value) share a single parenthesis.

    ``excluded``: the feature has no data in the whole cluster, so it is left out of the index
    (no estimate is used for it); ``imputed``: this territory's value is replaced by the median.
    """
    spec = SPECS.get(code)
    subject = (spec.subject if spec and spec.subject else None) or name
    if excluded:
        text = f"{subject}: нет данных — признак не участвует в расчёте"
        return text[0].upper() + text[1:]
    if value is None or imputed:
        text = f"{subject}: нет данных — принята типичная для кластера оценка"
        return text[0].upper() + text[1:]
    if spec is None:
        text = f"{name}: {fmt_num(value)} {unit}".strip()
    else:
        text = _value_phrase(spec, value, mode)
    notes = [n for n in (LEVEL_NOTE.get(inherited_from or ""), f"типично для кластера — {typical}" if typical else None) if n]
    if notes:
        text = f"{text} ({'; '.join(notes)})"
    return text[0].upper() + text[1:] if text else text


def typical_text(code: str, value: float | None, *, unit: str, mode: str = "any") -> str | None:
    """Short 'typical for the cluster' clause."""
    if value is None:
        return None
    spec = SPECS.get(code)
    if spec is None:
        return f"{fmt_num(value)} {unit}".strip()
    if spec.kind in ("time",) or (spec.kind == "presence" and value not in (0, 1)):
        return fmt_minutes(value)
    if spec.kind == "count":
        n = int(round(value))
        return f"{n} {plural(n, spec.forms or ('', '', ''))}"
    if spec.kind == "percent":
        return f"{fmt_num(value, 1, spec.signed)}%"
    if spec.kind == "presence":
        return "есть" if value >= 1 else "нет"
    return f"{fmt_num(value, 1, spec.signed)} {spec.unit}".strip()
