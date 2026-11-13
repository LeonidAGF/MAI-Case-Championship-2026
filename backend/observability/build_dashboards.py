from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parent / "grafana" / "dashboards"
PROMETHEUS = {"type": "prometheus", "uid": "atlas-prometheus"}
LOKI = {"type": "loki", "uid": "atlas-loki"}


def panel(
    number: int,
    title: str,
    expression: str,
    kind: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    description: str = "",
    unit: str = "short",
    datasource: dict[str, str] = PROMETHEUS,
    instant: bool = False,
    decimals: int | None = None,
    thresholds: list[tuple[float | None, str]] | None = None,
) -> dict:
    field_config: dict = {"defaults": {"unit": unit}, "overrides": []}
    if decimals is not None:
        field_config["defaults"]["decimals"] = decimals
    if thresholds:
        field_config["defaults"]["thresholds"] = {
            "mode": "absolute",
            "steps": [{"value": value, "color": color} for value, color in thresholds],
        }
    if kind in ("stat", "gauge"):
        options = {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "value",
            "graphMode": "area" if kind == "stat" else "none",
        }
    elif kind == "bargauge":
        options = {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "horizontal",
            "displayMode": "gradient",
            "showUnfilled": True,
        }
    elif kind == "logs":
        options = {"showTime": True, "showLabels": False, "showCommonLabels": False, "wrapLogMessage": True, "enableLogDetails": True}
    else:
        options = {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}}
    return {
        "id": number,
        "title": title,
        "description": description,
        "type": kind,
        "datasource": datasource,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": "A", "expr": expression, "datasource": datasource, "instant": instant}],
        "fieldConfig": field_config,
        "options": options,
    }


def dashboard(uid: str, title: str, description: str, panels: list[dict], variables: list[dict] | None = None) -> dict:
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": ["СКФО", "Атлас", "наблюдаемость"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {"list": variables or []},
        "annotations": {"list": []},
        "panels": panels,
    }


def query_variable(name: str, label: str, query: str, regex: str = "") -> dict:
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": PROMETHEUS,
        "query": {"query": query, "refId": "A"},
        "regex": regex,
        "refresh": 1,
        "includeAll": True,
        "allValue": ".*",
        "multi": True,
        "sort": 1,
        "current": {"text": "All", "value": "$__all"},
    }


service = dashboard(
    "atlas-service",
    "01 · Сервис и скорость",
    "Работа API, задержки, ошибки, загрузка расчётного движка и кэша.",
    [
        panel(1, "API доступен", "up{job=\"atlas-api\"}", "stat", 0, 0, 4, 4, description="1 — Prometheus получает метрики API; 0 — API или сбор недоступен.", thresholds=[(None, "red"), (1, "green")]),
        panel(2, "Запросы / мин", "(sum(rate(atlas_http_requests_total{route!=\"/healthz\"}[5m])) or vector(0)) * 60", "stat", 4, 0, 4, 4, unit="reqpm", decimals=1),
        panel(3, "Ошибки 5xx", "100 * (sum(rate(atlas_http_requests_total{route!=\"/healthz\",status=~\"5..\"}[5m])) or vector(0)) / clamp_min((sum(rate(atlas_http_requests_total{route!=\"/healthz\"}[5m])) or vector(0)), 0.001)", "stat", 8, 0, 4, 4, unit="percent", decimals=2, thresholds=[(None, "green"), (1, "yellow"), (5, "red")]),
        panel(4, "p95 ответа", "histogram_quantile(0.95, sum by (le) (rate(atlas_http_request_duration_seconds_bucket{route!=\"/healthz\"}[5m])))", "stat", 12, 0, 4, 4, unit="s", decimals=2),
        panel(5, "Запросы в работе", "atlas_http_inflight_requests", "stat", 16, 0, 4, 4),
        panel(6, "Попадания в кэш индекса", "100 * (sum(rate(atlas_scoring_calls_total{cache=\"hit\"}[5m])) or vector(0)) / clamp_min((sum(rate(atlas_scoring_calls_total[5m])) or vector(0)), 0.001)", "stat", 20, 0, 4, 4, unit="percent", decimals=1),
        panel(7, "Запросы по маршрутам", "sum by (route, method) (rate(atlas_http_requests_total{route!=\"/healthz\"}[5m])) * 60", "timeseries", 0, 4, 12, 8, unit="reqpm", description="Маршруты шаблонные: идентификаторы территорий не становятся метками; проверки работоспособности исключены."),
        panel(8, "p95 по маршрутам", "histogram_quantile(0.95, sum by (le, route) (rate(atlas_http_request_duration_seconds_bucket{route!=\"/healthz\"}[5m])))", "timeseries", 12, 4, 12, 8, unit="s"),
        panel(9, "Коды ответа", "sum by (status) (rate(atlas_http_requests_total{route!=\"/healthz\"}[5m])) * 60", "timeseries", 0, 12, 8, 8, unit="reqpm"),
        panel(10, "Время расчёта индекса p95", "histogram_quantile(0.95, sum by (le, cluster) (rate(atlas_scoring_compute_duration_seconds_bucket[5m])))", "timeseries", 8, 12, 8, 8, unit="s", description="Только вызов движка индекса; загрузка матрицы не включена."),
        panel(11, "Расчёты: попадания и промахи", "sum by (cluster, cache) (rate(atlas_scoring_calls_total[5m])) * 60", "timeseries", 16, 12, 8, 8, unit="reqpm"),
        panel(12, "Поиски в кэше API", "sum by (backend, outcome) (rate(atlas_cache_lookups_total[5m])) * 60", "timeseries", 0, 20, 12, 8, unit="reqpm"),
        panel(13, "Переходы с Redis на память", "increase(atlas_cache_degradations_total[1h])", "timeseries", 12, 20, 12, 8, description="Рост означает сбой Redis, после которого включился локальный кэш."),
    ],
)

data = dashboard(
    "atlas-data",
    "02 · Качество и происхождение данных",
    "Полнота признаков, актуальность источников, импутация и запасные способы расчёта.",
    [
        panel(1, "Сбор данных работает", "atlas_data_collection_success", "stat", 0, 0, 4, 4, thresholds=[(None, "red"), (1, "green")]),
        panel(2, "Синтетические данные", "atlas_synthetic_demo_data_present", "stat", 4, 0, 4, 4, description="1 — индекс использует демопризнаки; полнота не означает достоверность значений.", thresholds=[(None, "green"), (1, "yellow")]),
        panel(3, "Оценённых территорий", "sum(atlas_scored_territories)", "stat", 8, 0, 4, 4),
        panel(4, "Полнота признаков", "100 * avg(atlas_region_feature_coverage_ratio)", "stat", 12, 0, 4, 4, unit="percent", decimals=1, description="Доля заполненных значений; при демозагрузке 100% не означает достоверность признаков."),
        panel(5, "Возраст сбора", "time() - atlas_data_last_collection_timestamp_seconds", "stat", 16, 0, 4, 4, unit="s"),
        panel(6, "Ревизия данных", "atlas_data_revision", "stat", 20, 0, 4, 4),
        panel(7, "Полнота по субъектам", "100 * atlas_region_feature_coverage_ratio{region=~\"$region\"}", "bargauge", 0, 4, 12, 10, unit="percent", description="Средняя доля заполнения признаков в каждом регионе."),
        panel(8, "Полнота признаков", "100 * atlas_feature_coverage_ratio{region=~\"$region\",feature=~\"$feature\"}", "bargauge", 12, 4, 12, 10, unit="percent", description="Собственное или унаследованное значение; пропуски остаются видимыми."),
        panel(9, "Территории по субъекту и кластеру", "atlas_scored_territories{region=~\"$region\"}", "bargauge", 0, 14, 12, 9),
        panel(10, "Импутированные значения ETL", "atlas_etl_imputed_feature_values{feature=~\"$feature\"}", "bargauge", 12, 14, 12, 9, description="Только значения с явным флагом imputed в таблице feature_value."),
        panel(11, "Пеший режим использовал авто", "atlas_foot_to_car_fallback_values{feature=~\"$feature\"}", "bargauge", 0, 23, 12, 9, description="Число территорий, где пеший профиль взял автомобильное время для признака."),
        panel(12, "Возраст последнего источника", "(time() - atlas_feature_latest_as_of_timestamp_seconds{region=~\"$region\",feature=~\"$feature\"}) / 86400 and atlas_feature_latest_as_of_timestamp_seconds{region=~\"$region\",feature=~\"$feature\"} > 0", "bargauge", 12, 23, 12, 9, unit="d", description="Дни с даты as_of; демопризнаки имеют условную дату 1900-01-01, которую нельзя считать датой измерения."),
        panel(13, "Последний успешный ETL", "time() - atlas_etl_last_success_timestamp_seconds > 0", "bargauge", 0, 32, 12, 8, unit="s", description="Секунды с завершения последнего успешного запуска каждого вида ETL."),
        panel(14, "Ошибки ETL", "atlas_etl_failed_runs", "bargauge", 12, 32, 12, 8, description="Накопленное число неудачных запусков по типу ETL."),
        panel(15, "POI / официальный счётчик", "100 * atlas_poi_official_coverage_ratio{region=~\"$region\"}", "bargauge", 0, 40, 12, 9, unit="percent", description="Соотношение количества POI на карте и официального счётчика; выше 100% возможно из-за различий методики."),
        panel(16, "Учтённые POI", "atlas_poi_mapped_count{region=~\"$region\"}", "bargauge", 12, 40, 12, 9, description="Количество POI в базе по региону и категории."),
    ],
    [
        query_variable("region", "Субъект", "label_values(atlas_region_feature_coverage_ratio, region)"),
        query_variable("feature", "Признак", "label_values(atlas_etl_imputed_feature_values, feature)"),
    ],
)

scenario = dashboard(
    "atlas-scenarios",
    "03 · Сценарии, маршрутизация и журналы",
    "Прогоны what-if, доступность OSRM, аналитические замены и журналы API.",
    [
        panel(1, "Успешные сценарии за час", "sum(increase(atlas_whatif_runs_total{outcome=\"success\"}[1h])) or vector(0)", "stat", 0, 0, 6, 4),
        panel(2, "Неудачные сценарии за час", "sum(increase(atlas_whatif_runs_total{outcome=\"failure\"}[1h])) or vector(0)", "stat", 6, 0, 6, 4),
        panel(3, "Доступность OSRM", "atlas_osrm_available", "stat", 12, 0, 6, 4, description="0 также означает, что соответствующий OSRM не настроен.", thresholds=[(None, "red"), (1, "green")]),
        panel(4, "Аналитические замены за час", "sum(increase(atlas_whatif_analytical_fallbacks_total[1h])) or vector(0)", "stat", 18, 0, 6, 4),
        panel(5, "Сценарии по коду и методу", "sum by (scenario, method, outcome) (rate(atlas_whatif_runs_total[5m])) * 60", "timeseries", 0, 4, 12, 8, unit="reqpm"),
        panel(6, "p95 сценария", "histogram_quantile(0.95, sum by (le, scenario) (rate(atlas_whatif_duration_seconds_bucket[5m])))", "timeseries", 12, 4, 12, 8, unit="s"),
        panel(7, "Запросы к OSRM", "sum by (mode, outcome) (rate(atlas_osrm_requests_total[5m])) * 60", "timeseries", 0, 12, 12, 8, unit="reqpm"),
        panel(8, "p95 OSRM", "histogram_quantile(0.95, sum by (le, mode) (rate(atlas_osrm_request_duration_seconds_bucket[5m])))", "timeseries", 12, 12, 12, 8, unit="s"),
        panel(9, "Причины аналитической замены", "sum by (mode, reason) (increase(atlas_whatif_analytical_fallbacks_total[1h]))", "bargauge", 0, 20, 12, 7),
        panel(10, "Ошибки и предупреждения API", "{job=\"atlas\",service=\"api\"} | json | level=~\"error|warning\"", "logs", 12, 20, 12, 7, datasource=LOKI),
        panel(11, "Все журналы API", "{job=\"atlas\",service=\"api\"} | json", "logs", 0, 27, 24, 12, datasource=LOKI, description="Структурированные журналы FastAPI, включая шаблон маршрута и длительность запроса."),
        panel(12, "Затронуто территорий за сценарий", "sum by (scenario) (rate(atlas_whatif_affected_territories_sum[1h])) / clamp_min(sum by (scenario) (rate(atlas_whatif_affected_territories_count[1h])), 0.00001)", "timeseries", 0, 39, 12, 8, description="Среднее число затронутых территорий по успешно завершённым прогонам."),
        panel(13, "Средняя дельта индекса", "sum by (scenario) (rate(atlas_whatif_average_delta_points_sum[1h])) / clamp_min(sum by (scenario) (rate(atlas_whatif_average_delta_points_count[1h])), 0.00001)", "timeseries", 12, 39, 12, 8, description="Среднее изменение баллов на затронутую территорию в успешных сценариях."),
    ],
)


partners = dashboard(
    "atlas-partners",
    "04 · API для организаций",
    "Фактический спрос на выгрузки, география запросов, объём данных, скорость и нагрузка на PostgreSQL.",
    [
        panel(1, "Обращения к /v1 в минуту", "60 * (sum(rate(atlas_partner_api_requests_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}[5m])) or vector(0))", "stat", 0, 0, 4, 4, unit="reqpm", decimals=1, description="Все ответы /v1, включая проверку ключа, каталог и ошибки. Средняя частота за последние пять минут."),
        panel(2, "Выгрузки с запуска", "sum(atlas_partner_exports_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}) or vector(0)", "stat", 4, 0, 4, 4, decimals=0, description="Успешно сформированные таблицы с последнего запуска контейнера API."),
        panel(3, "Строки с запуска", "sum(atlas_partner_export_rows_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}) or vector(0)", "stat", 8, 0, 4, 4, decimals=0, description="Сумма строк в успешных выгрузках с запуска API; это не число строк в БД."),
        panel(4, "Объём с запуска", "sum(atlas_partner_export_bytes_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}) or vector(0)", "stat", 12, 0, 4, 4, unit="bytes", description="Байты сформированных ответов до HTTP-сжатия с запуска API."),
        panel(5, "p95 формирования", "histogram_quantile(0.95, sum by (le) (rate(atlas_partner_export_generation_duration_seconds_bucket{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}[5m])))", "stat", 16, 0, 4, 4, unit="s", decimals=2, description="Время построения и сериализации успешной выгрузки после проверки ключа; требуется фактический трафик."),
        panel(6, "Лимит 429 с запуска", "sum(atlas_partner_api_requests_total{status=\"429\",dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}) or vector(0)", "stat", 20, 0, 4, 4, decimals=0, thresholds=[(None, "green"), (1, "yellow")], description="Число запросов, отклонённых лимитом ключа с запуска API."),
        panel(7, "Успешные выгрузки по наборам", "60 * sum by (dataset) (rate(atlas_partner_exports_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}[5m]))", "timeseries", 0, 4, 12, 8, unit="reqpm", description="Текущий спрос на каждый набор данных."),
        panel(8, "Ответы /v1 по статусу", "60 * sum by (status) (rate(atlas_partner_api_requests_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}[5m]))", "timeseries", 12, 4, 12, 8, unit="reqpm", description="2xx, 4xx, 5xx и 429; ошибки авторизации также входят в статистику."),
        panel(9, "Запрошенные субъекты с запуска", "sum by (region) (atlas_partner_exports_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\",region=~\"RU-.*\"})", "bargauge", 0, 12, 8, 10, decimals=0, description="Успешные выгрузки с явным параметром region с запуска API. Запросы всего округа и только по району не включены."),
        panel(10, "Наборы с запуска", "sum by (dataset) (atlas_partner_exports_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"})", "bargauge", 8, 12, 8, 10, decimals=0, description="Сколько раз успешно выгружали каждый набор с запуска API."),
        panel(11, "Данные по наборам с запуска", "sum by (dataset) (atlas_partner_export_bytes_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"})", "bargauge", 16, 12, 8, 10, unit="bytes", description="Объём до сжатия с запуска API; показывает самые тяжёлые наборы."),
        panel(12, "p95 формирования по наборам", "histogram_quantile(0.95, sum by (le, dataset) (rate(atlas_partner_export_generation_duration_seconds_bucket{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}[5m])))", "timeseries", 0, 22, 12, 8, unit="s", description="Время работы backend над успешной выгрузкой; не включает авторизацию и пересылку ответа."),
        panel(13, "Объём выгрузок по наборам", "sum by (dataset) (rate(atlas_partner_export_bytes_total{dataset=~\"$dataset\",format=~\"$format\",region=~\"$region\"}[5m]))", "timeseries", 12, 22, 12, 8, unit="Bps", description="Байты сформированных ответов в секунду до сжатия."),
        panel(14, "SQL / мин для /v1", "60 * (sum(rate(atlas_db_statements_total{scope=\"partner\"}[5m])) or vector(0))", "stat", 0, 30, 5, 4, unit="reqpm", decimals=1, description="Реальные SQL-операции, выполненные внутри запросов /v1, включая проверку ключа."),
        panel(15, "SQL на обращение /v1", "(sum(rate(atlas_db_statements_total{scope=\"partner\"}[5m])) or vector(0)) / clamp_min((sum(rate(atlas_partner_api_requests_total[5m])) or vector(0)), 0.001)", "stat", 5, 30, 5, 4, decimals=1, description="Среднее число SQL-операций на одно завершённое обращение /v1 за пять минут. Пока запрос выполняется, числитель может опережать счётчик ответов."),
        panel(16, "p95 SQL для /v1", "histogram_quantile(0.95, sum by (le) (rate(atlas_db_statement_duration_seconds_bucket{scope=\"partner\"}[5m])))", "stat", 10, 30, 5, 4, unit="s", decimals=3, description="Только выполнение SQL курсором; ожидание соединения и обработка строк не входят."),
        panel(17, "Соединения с БД заняты", "atlas_db_connections_in_use", "stat", 15, 30, 5, 4, description="Все занятые соединения рабочих процессов API, включая публичные запросы и фоновые задачи."),
        panel(18, "Ошибки SQL /v1 с запуска", "sum(atlas_db_statements_total{scope=\"partner\",outcome=\"error\"}) or vector(0)", "stat", 20, 30, 4, 4, decimals=0, thresholds=[(None, "green"), (1, "red")]),
        panel(19, "SQL-операции в минуту", "60 * sum by (scope, outcome) (rate(atlas_db_statements_total[5m]))", "timeseries", 0, 34, 12, 8, unit="reqpm", description="partner — запросы /v1; other — остальные HTTP-маршруты и фоновые задачи. Это SQL-операции, а не обращения к API."),
        panel(20, "p95 SQL по источнику", "histogram_quantile(0.95, sum by (le, scope) (rate(atlas_db_statement_duration_seconds_bucket[5m])))", "timeseries", 12, 34, 12, 8, unit="s", description="Время выполнения одного SQL-выражения для /v1 и остальной работы API."),
        panel(21, "Журнал обращений организаций", "{job=\"atlas\",service=\"api\"} | json | message=\"http_request\" | dataset!=\"\" | dataset=~\"$dataset\" | export_format=~\"$format\" | requested_region=~\"$region\"", "logs", 0, 42, 12, 12, datasource=LOKI, description="Структурированный журнал: набор, регион фильтра, статус, длительность, id организации и ключа, строки и байты. Секретный ключ и почта не пишутся."),
        panel(22, "Ошибки корпоративного API", "{job=\"atlas\",service=\"api\"} | json | message=\"http_request\" | dataset!=\"\" | status=~\"4..|5..\"", "logs", 12, 42, 12, 12, datasource=LOKI, description="Ошибки 4xx/5xx; поле key_id есть только после успешного распознавания ключа."),
    ],
    [
        query_variable("dataset", "Набор", "label_values(atlas_partner_api_requests_total, dataset)"),
        query_variable("format", "Формат", "label_values(atlas_partner_api_requests_total, format)"),
        query_variable("region", "Фильтр региона", "label_values(atlas_partner_api_requests_total, region)"),
    ],
)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for filename, content in (("service.json", service), ("data.json", data), ("scenarios.json", scenario),
                              ("partners.json", partners)):
        (ROOT / filename).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
