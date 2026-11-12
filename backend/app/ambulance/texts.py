"""
Russian phrasing of ambulance travel times (deterministic, no LLM). Texts never contain feature,
subindex or scenario codes.
"""

from __future__ import annotations

from app.explain.texts import MINUTES, fmt_num, plural

CATEGORY_LABEL = {"ambulance": "станция скорой помощи", "hospital": "больница"}
HIGHWAY_LABEL = {
    "motorway": "автомагистраль",
    "motorway_link": "съезд с автомагистрали",
    "trunk": "магистральная дорога (федеральная трасса)",
    "trunk_link": "съезд с магистральной дороги",
    "primary": "дорога регионального значения",
    "primary_link": "съезд с дороги регионального значения",
    "secondary": "межмуниципальная дорога",
    "secondary_link": "съезд с межмуниципальной дороги",
    "tertiary": "дорога между сёлами, главная улица",
    "tertiary_link": "съезд с местной дороги",
    "unclassified": "дорога местного значения",
    "road": "дорога неизвестного класса",
    "residential": "жилая улица",
    "living_street": "жилая зона",
    "service": "проезд: двор, парковка, подъезд",
    "track": "грунтовая дорога: полевая, лесная",
}
STATUS_PHRASE = {
    "met": "укладывается в норматив {norm}",
    "borderline": "на границе норматива {norm}",
    "not_met": "дольше норматива {norm}",
}


def minutes_text(x: float) -> str:
    """«14 минут», «1 минута», «меньше минуты»; fractions under 10 minutes keep one decimal."""
    if x < 0.95:
        return "меньше минуты"
    value = round(x) if x >= 10 else round(x, 1)
    if float(value).is_integer():
        n = int(value)
        return f"{n} {plural(n, MINUTES)}"
    return f"{fmt_num(value)} минуты"  # 2,5 минуты


def range_text(lo: float, hi: float) -> str:
    if lo < 0.95:
        return f"до {minutes_text(hi)}"
    lo_txt, hi_txt = minutes_text(lo), minutes_text(hi)
    return f"от {lo_txt.split()[0]} до {hi_txt}"


def norm_text(norm: float) -> str:
    return minutes_text(norm)


def category_label(category: str) -> str:
    return CATEGORY_LABEL.get(category, category)


def estimate_text(minutes: float, low: float, high: float, status: str, norm: float) -> str:
    phrase = STATUS_PHRASE[status].format(norm=norm_text(norm))
    lead = "меньше чем за минуту" if minutes < 0.95 else f"примерно за {minutes_text(minutes)}"
    return f"Скорая помощь доедет {lead} ({range_text(low, high)}) — это {phrase}."


def base_text(category: str, name: str | None, distance_km: float) -> str:
    label = category_label(category)
    named = f"{label} «{name}»" if name else f"{label} (без названия на карте)"
    return f"Ближайшая база — {named}, {fmt_num(distance_km)} км по дорогам."


def terrain_text(terrain_min: float, climb_m: int | None, steep_km: float | None, max_height_m: int | None,
                 steep_grade: float) -> str | None:
    if terrain_min < 0.5:
        return None
    parts = []
    if climb_m:
        parts.append(f"подъём {fmt_num(climb_m, 0)} м")
    if steep_km and steep_km >= 0.1:
        parts.append(f"крутые участки (уклон от {fmt_num(steep_grade * 100, 0)}%) — {fmt_num(steep_km)} км")
    if max_height_m is not None:
        parts.append(f"высшая точка — {fmt_num(max_height_m, 0)} м над уровнем моря")
    tail = f": {', '.join(parts)}" if parts else ""
    return f"Рельеф добавляет около {minutes_text(terrain_min)}{tail}."


HOSPITAL_NOTE = (
    "Ближе всего больница: подстанций скорой помощи в OpenStreetMap отмечено мало, а бригады районных "
    "больниц базируются при больницах."
)
NO_TERRAIN_NOTE = "Высота и уклон дорог не учтены: нет данных рельефа SRTM для этого маршрута."
MAP_GAP_NOTE = (
    "Время может быть завышено, если ближайшая больница или подстанция скорой помощи не отмечена "
    "на карте OpenStreetMap."
)
STALE_NOTE = "Параметры модели изменились после расчёта: время нужно пересчитать."
UNAVAILABLE_TEXT = (
    "Оценки нет: в пределах {km} км от населённого пункта нет дороги, по которой можно доехать от станций "
    "скорой помощи и больниц."
)


def far_access_note(access_m: int) -> str:
    return (f"Населённый пункт в {fmt_num(access_m / 1000)} км от ближайшей дороги на карте: "
            "этот участок оценён по прямой.")


def dispatch_note(dispatch: float) -> str:
    if dispatch <= 0:
        return "Это время в пути от базы, без приёма вызова и сборов бригады."
    return f"Включает {minutes_text(dispatch)} на приём вызова и выезд бригады."


def accuracy_note(minus: float, plus: float) -> str:
    return (
        "Оценка приблизительная: на каждой дороге — максимальная разрешённая скорость из OpenStreetMap, "
        f"с поправками на уклон и высоту по рельефу SRTM; погрешность от −{fmt_num(minus * 100, 0)}% "
        f"до +{fmt_num(plus * 100, 0)}%."
    )


def norm_note(norm: float) -> str:
    return (
        f"Норматив доезда при экстренном вызове — {norm_text(norm)} с момента вызова; регион может "
        "скорректировать его с учётом транспортной доступности и географии."
    )


def percent(share: float | None) -> str:
    return "—" if share is None else f"{fmt_num(share * 100, 0)}%"


def summary_text(scope: str, stats: dict, norm: float) -> str:
    if not stats["estimated"]:
        return f"{scope}: оценок времени доезда скорой нет."
    within = stats["within_norm"]
    text = (
        f"{scope}: за {norm_text(norm)} скорая доезжает до {percent(within['share'])} населённых пунктов "
        f"({within['settlements']} из {stats['estimated']} с оценкой); медиана — "
        f"{minutes_text(stats['median_minutes'])}, у каждого десятого пункта дольше "
        f"{minutes_text(stats['p90_minutes'])}."
    )
    if stats["population_settlements"]:
        text += (
            f" Среди пунктов с известным населением ({stats['population_settlements']}, "
            f"{fmt_num(stats['population_known'], 0)} жителей) в норматив попадают {percent(within['population_share'])} жителей."
        )
    return text
