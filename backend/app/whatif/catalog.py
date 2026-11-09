"""Catalog of what-if scenarios W1–W10 (report section 8, table 9) with typed parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Rough bounding box of the North Caucasus Federal District (with margin).
LAT = {"ge": 40.5, "le": 47.0}
LON = {"ge": 39.5, "le": 49.5}


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Point(_Params):
    lat: float = Field(..., **LAT, description="Широта точки")
    lon: float = Field(..., **LON, description="Долгота точки")


class W1Params(_Point):
    kind: Literal["hospital", "clinic", "fap", "ambulance"] = "fap"
    radius_km: float = Field(40, gt=0, le=200, description="Радиус поиска затронутых территорий")
    name: str | None = None


class W2Params(_Params):
    poi_id: int | None = Field(None, description="Закрываемый объект (id из таблицы poi)")
    lat: float | None = Field(None, **LAT)
    lon: float | None = Field(None, **LON)
    kind: Literal["hospital", "clinic", "fap", "ambulance"] = "hospital"
    radius_km: float = Field(60, gt=0, le=200)

    @model_validator(mode="after")
    def _target(self) -> "W2Params":
        if self.poi_id is None and (self.lat is None or self.lon is None):
            raise ValueError("укажите poi_id или координаты lat/lon закрываемого объекта")
        return self


class W3Params(_Point):
    kind: Literal["kindergarten", "school"] = "school"
    radius_km: float = Field(25, gt=0, le=200)
    name: str | None = None


class W4Params(_Point):
    kind: Literal["college", "university"] = "college"
    radius_km: float = Field(80, gt=0, le=300)
    name: str | None = None


class W5Params(_Point):
    kind: Literal["railway_station"] = "railway_station"
    radius_km: float = Field(60, gt=0, le=300)
    name: str | None = None


class _Territories(_Params):
    territory_ids: list[int] | None = Field(None, max_length=2000)
    municipality_id: int | None = Field(None, description="Все населённые пункты района")

    @model_validator(mode="after")
    def _scope(self):  # noqa: ANN202
        if not self.territory_ids and self.municipality_id is None:
            raise ValueError("укажите territory_ids или municipality_id")
        return self


class W6Params(_Territories):
    percent: float = Field(20, gt=0, le=90, description="Сокращение времени в пути, %")


class W7Params(_Territories):
    stop_access_min: float = Field(5, ge=0, le=60, description="Время до новой остановки, мин")


class W8Params(_Params):
    percent: float = Field(15, gt=0, le=90, description="Сокращение времени до аэропорта, %")
    poi_id: int | None = None
    lat: float | None = Field(None, **LAT)
    lon: float | None = Field(None, **LON)
    radius_km: float = Field(150, gt=0, le=400)

    @model_validator(mode="after")
    def _target(self) -> "W8Params":
        if self.poi_id is None and (self.lat is None or self.lon is None):
            raise ValueError("укажите poi_id аэропорта или его координаты lat/lon")
        return self


class W9Params(_Point):
    kind: Literal["cafe", "cinema", "community_centre", "sports", "park", "mall", "marketplace"] = "park"
    park_area_ha: float = Field(5, gt=0, le=1000)
    radius_km: float = Field(25, gt=0, le=200)
    name: str | None = None


class W10Step(_Params):
    scenario_code: Literal["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9"]
    params: dict[str, Any]


class W10Params(_Params):
    municipality_id: int = Field(..., description="Район, для которого собирается пакет")
    steps: list[W10Step] | None = Field(
        None, description="Явный состав пакета; если не задан — автоплан W1+W3+W7"
    )


@dataclass(frozen=True)
class ScenarioDef:
    code: str
    name: str
    type: str
    method: str  # graph | analytical | composite
    affects: tuple[str, ...]
    description: str
    params_model: type[BaseModel]
    example: dict[str, Any]

    def params_schema(self) -> dict[str, Any]:
        return self.params_model.model_json_schema()


CATALOG: dict[str, ScenarioDef] = {
    d.code: d
    for d in [
        ScenarioDef("W1", "Открытие больницы/ФАП в точке", "add_poi", "graph", ("F01", "F02", "F03", "F04"),
                    "Новый медицинский объект: время до него по дорогам (OSRM) или аналитически",
                    W1Params, {"lat": 42.39, "lon": 47.08, "kind": "fap"}),
        ScenarioDef("W2", "Закрытие больницы (ремонт, карантин)", "remove_poi", "graph", ("F01", "F02", "F03", "F04"),
                    "Объект исключается: время растёт до следующего ближайшего",
                    W2Params, {"lat": 43.03, "lon": 44.68, "kind": "hospital"}),
        ScenarioDef("W3", "Открытие детского сада/школы", "add_poi", "graph", ("F05", "F06", "F08"),
                    "Новый объект образования в точке", W3Params, {"lat": 42.39, "lon": 47.08, "kind": "school"}),
        ScenarioDef("W4", "Открытие колледжа/филиала вуза", "add_poi", "graph", ("F07",),
                    "Новый колледж или филиал вуза", W4Params, {"lat": 42.98, "lon": 47.5, "kind": "college"}),
        ScenarioDef("W5", "Открытие ЖД-платформы/станции", "add_poi", "graph", ("F15",),
                    "Новый узел железнодорожной сети", W5Params, {"lat": 43.2, "lon": 46.85}),
        ScenarioDef("W6", "Ремонт трассы: время до регионального центра −X%", "scale_time", "analytical",
                    ("F14", "F21"), "Масштабирование времён маршрута для выбранных территорий",
                    W6Params, {"municipality_id": 1, "percent": 20}),
        ScenarioDef("W7", "Ввод автобусного маршрута село → райцентр", "bus_route", "analytical", ("F17", "F19"),
                    "Обнуление доли жителей без регулярного сообщения и новая остановка",
                    W7Params, {"municipality_id": 1, "stop_access_min": 5}),
        ScenarioDef("W8", "Расширение аэропорта / новые рейсы", "scale_time", "analytical", ("F16",),
                    "Поправка ко времени до аэропорта в радиусе", W8Params,
                    {"lat": 43.0, "lon": 47.65, "percent": 15}),
        ScenarioDef("W9", "Открытие ТЦ/кинотеатра/парка", "add_poi", "graph", ("F09", "F10", "F11", "F12", "F13"),
                    "Новый объект досуга или торговли", W9Params, {"lat": 43.03, "lon": 44.68, "kind": "park"}),
        ScenarioDef("W10", "Пакет для района: W1 + W3 + W7", "package", "composite",
                    ("F01", "F02", "F03", "F04", "F05", "F06", "F07", "F08", "F17", "F19"),
                    "Пакетное улучшение социального блока района одним прогоном; без steps — автоплан "
                    "(ФАП и школа в худших по доступности сёлах + автобус для всех сёл района)",
                    W10Params, {"municipality_id": 1}),
    ]
}
