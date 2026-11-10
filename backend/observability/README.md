# Мониторинг Атласа СКФО

Стек состоит из Prometheus для числовых метрик, Grafana для панелей, Loki для журналов и Alloy для чтения файлов журналов API. Всё запускается тем же `backend/docker-compose.yml`, что и API. Мониторинг рассчитан на локальный показ; его порты привязаны к `127.0.0.1`.

## Запуск в PowerShell

Из корня репозитория:

```powershell
if (-not (Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
docker compose -f backend/docker-compose.yml up -d --build
docker compose -f backend/docker-compose.yml ps
docker compose -f backend/docker-compose.yml run --rm api python -m app.cli seed
```

Команда `seed` загружает территории и справочники. Для демонстрации с разрешёнными синтетическими признаками выполните дополнительно:

```powershell
docker compose -f backend/docker-compose.yml run --rm api python -m app.cli seed-demo-features
docker compose -f backend/docker-compose.yml restart api
```

Демопризнаки создаются детерминированно из фиксированного seed, координат и кластера территории с псевдослучайными отклонениями. Они помечены `source='synthetic-demo'` и датой `1900-01-01`. После загрузки индикатор «Синтетические данные» показывает `1`, а правило `AtlasSyntheticDataPresent` активируется как напоминание о происхождении значений. Полнота в этом режиме означает наличие демозначения, а не проверку его достоверности. Возраст источников для этих значений не является возрастом реальных измерений. Если появится проверенный экспорт `F01…F30`, загрузите его отдельной командой `load-features` и удалите демопризнаки через `clear-demo-features`.

Если `backend/.env` уже существует, сохраните его и добавьте при необходимости переменные `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_PORT`, `PROMETHEUS_PORT`, `LOKI_PORT`, `METRICS_REFRESH_SECONDS`. Пароль по умолчанию предназначен только для локального показа.

Проверьте: [состояние API](http://localhost:8000/healthz), [метрики API](http://localhost:8000/metrics), [цели Prometheus](http://localhost:9090/targets), [готовность Loki](http://localhost:3100/ready). Цель `atlas-api` в Prometheus должна иметь состояние `UP`.

Правила на странице [Alerts Prometheus](http://localhost:9090/alerts) отмечают недоступность API, долю ошибок 5xx выше 5% при наличии трафика, устаревший сбор качества данных и наличие синтетических значений. Порог устаревания 900 секунд рассчитан на стандартный цикл сбора 300 секунд. Это локальные правила Prometheus без рассылки уведомлений.

Откройте [Grafana](http://localhost:3001), войдите с логином `admin` и паролем из `GRAFANA_ADMIN_PASSWORD` (`atlas-demo-change-me`, если переменная не изменена). Папка «Атлас СКФО» создаётся автоматически. Прямые ссылки:

- [01 · Сервис и скорость](http://localhost:3001/d/atlas-service)
- [02 · Качество и происхождение данных](http://localhost:3001/d/atlas-data)
- [03 · Сценарии, маршрутизация и журналы](http://localhost:3001/d/atlas-scenarios)

Чтобы появились запросы и журналы, откройте [каталог сценариев](http://localhost:8000/whatif/scenarios) и несколько страниц приложения. Для графиков сценариев выполните прогон в интерфейсе what-if. Частотные и p95 панели строятся по окну 5 минут: до первых запросов они могут быть пустыми. Показатели данных обновляются каждые `METRICS_REFRESH_SECONDS` секунд, по умолчанию 300. Для демонстрации можно выставить 30 перед запуском.

## Журналы и диагностика

В Grafana откройте **Explore**, выберите источник **Loki** и выполните запрос:

```logql
{job="atlas",service="api"} | json
```

Только ошибки и предупреждения:

```logql
{job="atlas",service="api"} | json | level=~"error|warning"
```

Для проверки передачи журналов полезен запрос `GET /whatif/scenarios`. Затем проверьте контейнеры:

```powershell
docker compose -f backend/docker-compose.yml logs --tail 100 api alloy loki prometheus grafana
```

API пишет JSON в stdout и в отдельный файл каждого воркера. Alloy читает общий том `api-logs` и передаёт новые строки в Loki. Файлы каждого воркера ротируются после 10 МБ и хранят до трёх резервных копий; при запуске API удаляются файлы старше семи дней. Loki настроен на семь дней хранения, Prometheus — на 15 дней. Prometheus объединяет метрики обоих воркеров через multiprocess-режим Python-клиента. Каталог промежуточных файлов очищается при старте контейнера API.

## Уход за конфигурацией

Файлы панелей в `grafana/dashboards` создаются из `build_dashboards.py`. После изменения выражений выполните:

```powershell
python backend/observability/build_dashboards.py
docker compose -f backend/docker-compose.yml restart grafana
```

Без Docker API можно запустить отдельно, но для сбора нескольких воркеров необходимо заранее задать `PROMETHEUS_MULTIPROC_DIR` и очистить этот каталог перед запуском Uvicorn. Для локальной разработки с одним процессом переменная не нужна.
