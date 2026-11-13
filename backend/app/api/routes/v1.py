"""Data API for organisations (/v1): CSV and JSON exports, by an organisation's API key.

The routes reuse the web app's services (index, features, ambulance, what-if). Access needs a key
issued by the team (`python -m app.cli key-issue`); limits are per key, not per IP.
"""

from __future__ import annotations

import datetime as dt
import time
from enum import Enum
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import api_key, get_container, get_session, normalize_cluster, profile_query
from app.api.schemas import V1Catalog, V1Me
from app.core.errors import KEY_RESPONSES, ErrorResponse
from app.partners.datasets import ATTRIBUTION_ASCII, DATASETS, Ctx, base_meta, build_scenario_run, check_filters, scenario_columns
from app.partners.store import SCOPES, Principal
from app.partners.tables import Table, to_csv, to_json
from app.partners.telemetry import record_export
from app.profiles.contract import Profile

DATASETS_KEY = api_key("datasets")  # one instance: FastAPI runs it once per request
router = APIRouter(prefix="/v1", tags=["organisations"], dependencies=[Depends(DATASETS_KEY)])


class DatasetName(str, Enum):
    settlements = "settlements"
    features = "features"
    index = "index"
    ambulance = "ambulance"
    municipalities = "municipalities"


Fmt = Literal["csv", "json"]
FILE_RESPONSES = {
    200: {"content": {"text/csv": {}, "application/json": {}}, "description": "Таблица в CSV или JSON"},
    503: {"model": ErrorResponse, "description": "Время доезда скорой ещё не рассчитано"},
    **KEY_RESPONSES,
}
NOTES = [
    "CSV для программ: запятая, точка в дробях, UTF-8, английские имена колонок. CSV для Excel (excel=true): "
    "точка с запятой, запятая в дробях, UTF-8 с BOM, русские заголовки.",
    "Пустая ячейка — нет данных, не ноль. Население известно примерно у половины населённых пунктов.",
    "Каждая выгрузка содержит дату и ревизию данных: сохраняйте файл, если на него будут ссылаться документы.",
    "Содержит данные © участники OpenStreetMap: при публикации указывайте источник (лицензия ODbL 1.0).",
]


def _filename(name: str, region: str | None, municipality_id: int | None) -> str:
    parts = ["pulse", name, *([region] if region else []), *([f"m{municipality_id}"] if municipality_id else []),
             dt.date.today().isoformat()]
    return "_".join(parts)


def _respond(
    request: Request, table: Table, fmt: str, excel: bool, filename: str, region: str, started: float,
) -> Response:
    headers = {
        "X-Data-Revision": str(table.meta.get("data_revision", "")),
        "X-Model-Version": str(table.meta.get("model_version", "")),
        "X-Attribution": ATTRIBUTION_ASCII,
    }
    if table.meta.get("synthetic_demo_data"):
        headers["X-Synthetic-Data"] = "true"
    if fmt == "json":
        response = JSONResponse(to_json(table), headers=headers)
    else:
        headers["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
        response = Response(to_csv(table, excel=excel), media_type="text/csv; charset=utf-8", headers=headers)
    rows = len(table.rows)
    size_bytes = len(response.body)
    record_export(table.name, fmt, region, rows, size_bytes, time.perf_counter() - started)
    request.state.partner_export_rows = rows
    request.state.partner_export_bytes = size_bytes
    return response


@router.get("/me", response_model=V1Me, responses=KEY_RESPONSES, summary="Проверка ключа: организация, права, срок")
async def me(request: Request, principal: Principal = Depends(DATASETS_KEY)) -> V1Me:
    c = get_container(request)
    return V1Me(
        organisation=principal.organisation, key_prefix=principal.prefix, key_name=principal.key_name,
        scopes=[{"code": s, "description": SCOPES.get(s, s)} for s in principal.scopes],
        rate_limit_per_minute=principal.rate_limit_per_minute or c.settings.rate_limit_api_key_per_minute,
        expires_at=principal.expires_at,
    )


@router.get("/datasets", response_model=V1Catalog, responses=KEY_RESPONSES,
            summary="Каталог наборов данных: колонки, параметры, дата и ревизия данных")
async def catalog(request: Request, session: AsyncSession = Depends(get_session)) -> V1Catalog:
    c = get_container(request)
    datasets = [
        {"name": d.name, "title": d.title, "description": d.description, "url": f"/v1/datasets/{d.name}.csv",
         "formats": ["csv", "json"], "params": [*d.params, "excel"], "columns": [col.info() for col in d.columns(c.cfg)]}
        for d in DATASETS.values()
    ]
    datasets.append({
        "name": "scenario_run", "title": "Результат сценария «что если»",
        "description": "Прогон сценария (POST /whatif/run): значения до и после для каждого затронутого пункта.",
        "url": "/v1/scenario-runs/{run_id}.csv", "formats": ["csv", "json"], "params": ["profile", "p", "excel"],
        "columns": [col.info() for col in scenario_columns(c.cfg)],
    })
    return V1Catalog(meta=await base_meta(Ctx(c, session)), datasets=datasets, notes=NOTES)


@router.get("/datasets/{dataset}.{fmt}", responses=FILE_RESPONSES, response_class=Response,
            summary="Набор данных в CSV или JSON (фильтры по субъекту, району, кластеру)")
async def dataset(
    dataset: DatasetName,
    fmt: Fmt,
    request: Request,
    region: str | None = Query(None, description="Код субъекта ISO 3166-2, напр. RU-DA"),
    municipality_id: int | None = Query(None, description="id района"),
    cluster: str | None = Query(None, description="A | B | V (или А/Б/В)"),
    mode: Literal["car", "foot"] = Query("car", description="features: способ передвижения для времени в пути"),
    layout: Literal["long", "wide"] = Query("long", description="features: строка на значение или на пункт"),
    excel: bool = Query(False, description="CSV для Excel: точка с запятой, запятая в дробях, русские заголовки"),
    profile: Profile = Depends(profile_query),
    session: AsyncSession = Depends(get_session),
) -> Response:
    started = time.perf_counter()
    c = get_container(request)
    region = region.strip().upper() if region else None
    ctx = Ctx(c, session, region=region, municipality_id=municipality_id, cluster=normalize_cluster(cluster),
              profile=profile, mode=mode, layout=layout)
    await check_filters(ctx)
    table = await DATASETS[dataset.value].build(ctx)
    observed_region = (
        region if region else "by_municipality" if municipality_id is not None else "all"
    )
    return _respond(request, table, fmt, excel, _filename(dataset.value, region, municipality_id), observed_region, started)


@router.get("/scenario-runs/{run_id}.{fmt}", responses=FILE_RESPONSES, response_class=Response,
            summary="Результат сценария «что если» в CSV или JSON: значения до и после")
async def scenario_run(
    run_id: int,
    fmt: Fmt,
    request: Request,
    excel: bool = Query(False, description="CSV для Excel"),
    profile: Profile = Depends(profile_query),
    session: AsyncSession = Depends(get_session),
) -> Response:
    started = time.perf_counter()
    c = get_container(request)
    table = await build_scenario_run(c, session, run_id, profile)
    return _respond(request, table, fmt, excel, f"pulse_scenario_run_{run_id}_{dt.date.today().isoformat()}", "n/a", started)
