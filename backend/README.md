# Атлас доступности территорий СКФО — бэкенд (FastAPI-монолит)

Кейс А2. Реализованы все задачи бэкенда из итогового отчёта v3.0 (роли BE1 и BE2): API,
схема БД с миграциями, ETL, расчётный конвейер индекса, сравнение, what-if W1–W10,
объяснения и чат-ассистент. ML-часть не затронута: модель подключается через контракт
(см. «Интеграция с ML»), файлы ML в `../data`, `../graph`, `../clast`, `../scripts` только читаются.

| Этап | BE1 | BE2 |
|---|---|---|
| 0 | Скелет FastAPI, `/healthz`, конфиги, БД | Модель territory (Pydantic + SQLAlchemy), миграция схемы раздела 7 |
| 1 | Геометрии в PostGIS, привязка POI к территориям | Парсеры CSV (ЕМИСС / БДМО «Точно» / ФНС / матрица «Точно»), нормализация названий, OKTMO |
| 2–3 | `GET /index`, кеш, кластерные фильтры | `GET /explain`, генератор текстов |
| 4–6 | `POST /compare`, `POST /whatif/run` | `POST /chat` (SSE), function calling |
| 7–8 | Контракты, единые ошибки, лимиты | Контрольные вопросы чата, проверка «ни одной выдуманной цифры» |

## Быстрый старт

### Frontend
```bash
cd web
npm install      - зависимости (если нет)
npm run dev		 - запуск
```

### Backend

Мониторинг Prometheus, Grafana и Loki запускается вместе с API; [инструкция и панели](observability/README.md).

```bash
cd backend
cp .env.example .env                      # порты и настройки
docker compose up -d --build              # db (PostgreSQL 16 + PostGIS 3 + pgvector) + redis + api
docker compose run --rm api python -m app.cli seed            # справочники + 7 субъектов, 144 района, 3403 НП
docker compose run --rm api python -m app.cli seed-demo-features   # ТОЛЬКО для разработки фронта (синтетика!)
# Swagger: http://localhost:8000/docs   OpenAPI-контракт: openapi.json
```

Миграции применяются при старте контейнера (`alembic upgrade head`). Всё идемпотентно:
`seed` можно запускать повторно.

Локально без Docker для API (нужен Python ≥ 3.12 и запущенные db/redis):

```bash
python -m venv .venv && .venv/Scripts/activate        # Windows; Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m app.cli migrate && python -m app.cli seed
uvicorn app.main:app --reload
```

> Синтетические признаки помечены `source='synthetic-demo'`, `/healthz` пишет
> `feature_data: synthetic-demo (NOT real data)`, а чат обязан предупредить пользователя.
> Перед защитой: `python -m app.cli clear-demo-features` и загрузка реальных признаков ML.

## Устройство

```
app/
  main.py            приложение, CORS, GZip, X-Process-Time, обработчики ошибок
  container.py       сервисы процесса (движок индекса, кеши, OSRM, чат) + прогрев
  core/              настройки (.env), единый формат ошибок, rate limit, логирование
  db/                ORM-модели (раздел 7 + служебные таблицы), сессии
  api/routes/        эндпоинты; api/schemas.py — типизированный контракт для фронта
  profiles/          контракт профиля 9.2, кодек параметра p, пресеты
  modelcfg.py        загрузка/валидация YAML модели (контракт с ML)
  scoring/           contract.py (интерфейс движка для ML), reference.py (формула 4.3),
                     matrix.py (матрица признаков: наследование, режим авто/пешком),
                     service.py (кеши, what-if overlay), snapshots.py (index_result + component)
  explain/           водопад относительно «типичной территории кластера», тексты причин
  compare/           сравнение 2–3 территорий внутри кластера
  whatif/            каталог W1–W10, движок сценариев, клиент OSRM
  chat/              оркестратор GigaChat, 3 функции, RAG, проверка чисел, 10 контрольных вопросов
  etl/               загрузчики: территории, признаки, POI, матрица времени, статистика, «Точно», база знаний, полнота
  cli.py             `python -m app.cli --help`
migrations/          Alembic (асинхронный), миграция написана вручную и сверяется с моделями в тестах
config/model.default.yaml   резервная копия модели из таблиц 2–5 отчёта (основная — у ML)
seeds/               субъекты, район→субъект (144 шт.), ручные правки кластеров, официальные счётчики POI
```

Таблицы: `territory`, `cluster`, `feature`, `feature_value`, `profile`, `index_result`,
`component`, `scenario`, `scenario_run`, `knowledge_chunk` (раздел 7) и служебные `subindex`,
`model_version` («версия модели в БД»), `etl_run` (ревизии данных и отчёты загрузок),
`poi`, `travel_time` (для what-if W1–W5, W9).

## Интеграция с ML (бэкенд в модель не вмешивается)

1. **Конфиг модели** — YAML со схемой `config/model.default.yaml` (субиндексы, 30 признаков с
   направлением/весом/уровнем, параметры кластеров, пресеты). ML хранит свой файл и задаёт
   `MODEL_CONFIG_PATH`. Схема проверяется при старте; версия и хеш пишутся в `model_version`.
2. **Калькулятор** — класс с интерфейсом `app.scoring.contract.IndexEngine`
   (`name`, `version`, `score(ScoreRequest) -> {territory_id: TerritoryScore}`), подключается
   `SCORING_ENGINE=пакет.модуль:Класс`. Бэкенд сам собирает матрицу (наследование статистики
   района/субъекта, выбор режима car/foot по фильтру «Мобильность»), передаёт профиль и
   параметры кластера, сохраняет снимки и строит объяснения. Обязательные инварианты
   (детерминизм, сумма вкладов = total, диапазоны) проверяются `tests/unit/test_engine_contract.py`
   — их можно прогнать против движка ML. До подключения работает `reference.py` — формула 4.3
   + winsorize/квантильная нормировка 5.3, фильтры профиля в ней не интерпретируются.
3. **Признаки** — `python -m app.cli load-features file.parquet|csv`:
   long (`<ключ>, feature_code, raw_value[, mode, as_of, imputed]`) или wide (`<ключ>, F01, F01@foot, …`).
   Ключ: `settlement_osm_id` | `osm_id` | `municipality_osm_relation_id` | `region_code` |
   `external_id` | `territory_id`. `mode`: `car` | `foot` | `any`. История — по `as_of`.
4. **Матрица времени OSRM** — `load-travel-times` (`<ключ>, poi_id|poi_external_id, mode, seconds|minutes`),
   **POI** — `load-poi` (GeoJSON из osmium или таблица с lon/lat; категории по тегам OSM).
5. **OSRM для графового what-if** — `OSRM_CAR_URL`, `OSRM_FOOT_URL` (контейнеры MLOps).
   Без них и при сбое сценарии считаются аналитически, ответ содержит предупреждение.

## Данные и ETL

| Команда | Что делает |
|---|---|
| `seed` | `seed-reference` + `load-territories` (можно `--demo-features`) |
| `load-territories [--data-dir]` | субъекты (union полигонов), 144 района (WKB → PostGIS), 3403 НП рабочей выборки ML, кластеры А/Б/В |
| `load-stats FILE --feature F23 [--indicator …] [--year] [--learn-oktmo] [--dry-run]` | ЕМИСС / Росстат БДМО (выгрузки «Точно»: CSV UTF-8 `;`) / ФНС / «широкие» таблицы по годам → район/субъект |
| `load-od-matrix FILE [--out pairs.csv] [--to-features]` | матрица «Точно» город–город → нормализованные пары для ML (+ F14/F21 городов СКФО) |
| `load-features`, `load-poi`, `load-travel-times`, `load-knowledge` | см. выше; база знаний — md/txt/jsonl/csv |
| `coverage` | отчёт о полноте (также `GET /data/coverage`): заполненность признаков по субъектам, POI, сверка с `seeds/official_poi_counts.csv` |

Кластеры (4.1): A — население > 50 тыс. или столица субъекта; Б — город/посёлок или райцентр
(крупнейший НП муниципального района/округа); В — остальные сёла. Причина записана в
`territory.meta.cluster_reason`, ручные правки — `seeds/cluster_overrides.csv`.
Сопоставление статистики с районами: ОКТМО, затем субъект + нормализованное название + тип
(городской округ / район), затем защищённый префиксный матч; строки поселений отбрасываются.
Все загрузки пишутся в `etl_run`; новая ревизия данных автоматически сбрасывает кеши API (≤ 3 с).

## API

Полный контракт — `/docs` и `openapi.json`. Все 10 эндпоинтов таблицы 10 + помощники:

`GET /territories` (фильтры level/cluster/region/parent_id/q/bbox, пагинация) ·
`GET /territories/geojson` (слои карты, `with_index`) · `GET /territories/{id}` ·
`GET /index?territory_id=…|cluster=…` (`sort`, `region`, `group_by=municipality|region` для хлороплета,
`scenario_run_id`) · `GET /explain/{id}` · `POST /compare` · `GET /whatif/scenarios` (для формы: `target`
point|municipality, `kinds` с подписями, рабочий `example` — `municipality_id` берётся из базы) ·
`POST /whatif/run` · `GET /whatif/runs/{id}` · `GET /profiles/presets` · `POST /profiles/encode` ·
`GET /profiles/decode` · `GET /features` · `GET /clusters` · `GET /regions` · `GET /data/coverage` ·
`POST /chat` (SSE) · `GET /healthz`.

* **Профиль**: `profile=<id пресета>` или `p=<сериализованный профиль>` (в POST — строкой или объектом).
  `p` = base64url(JSON) или base64url(deflate-raw(JSON)); в браузере:
  `new CompressionStream('deflate-raw')`. Пример из отчёта `?p=eyJ2IjoxLCJwcm9maWxlIjoicGVuc2lvbmVyIn0`
  тоже принимается. Частичный профиль дополняется пресетом. Неизвестная версия →
  `422 profile_version_unsupported` (фронт сбрасывает на пресет).
* **Ошибки**: всегда `{"code", "message", "details?"}`; коды: `validation_error`, `not_found`,
  `territory_not_scored`, `cluster_mismatch`, `profile_*`, `scenario_*`, `poi_not_found`,
  `rate_limited` (+ `Retry-After`), `chat_disabled`.
* **Водопад** (`/explain`): `baseline` — типичная территория кластера (сумма медианных вкладов),
  шаги `delta` — отклонение каждого признака; `baseline + Σdelta = total` (округление
  сохраняет сумму). Плюсы/минусы — топ-3 по `delta`, тексты без кодов признаков.
* **Снимки**: `index_result` + `component` неизменяемы и дедуплицируются по
  (территория, профиль, сценарий, версия модели, ревизия данных). Кастомные профили в БД
  не хранятся — только хеш расчётной части.
* **Лимиты**: `RATE_LIMIT_*` (по IP, Redis), `p` ≤ 2000 символов, сравнение 2–3 территории,
  `max_page_size`, чат ≤ 30 сообщений по 4000 символов.

## What-if W1–W10

Сценарий → поправки `feature_value` (хранятся в `scenario_run.overrides`) → обычный конвейер.
Нормировка остаётся подогнанной по базовым данным, поэтому незатронутые территории не
меняются. W1/W3/W4/W5/W9 — время до новой точки (OSRM `table` или аналитически: расстояние ×
1,3 / 50 км/ч, пешком 4,5 км/ч, не меньше 3 мин); W2 — по матрице `travel_time` до следующего
объекта, иначе отношение расстояний до следующего объекта из `poi`. Если слоя POI рядом нет
(`load-poi` не запускали), объект считается находящимся в указанной точке: НП зависел от него, когда
точка не дальше его текущего ближайшего объекта (×1,25 + 2 мин), а новое время — самый дешёвый
путь через соседний НП, который обслуживает другой объект (время до соседа + его значение
признака); ответ содержит предупреждение. W6/W8 — масштабирование времени; W7 — F19 → 0, F17 → время
до остановки; W10 — пакет W1+W3+W7: без `steps` автоплан ставит ФАП и школу в сёла с худшей
доступностью района. Ответ: до/после, дельты субиндексов, победители/проигравшие, текст объяснения.
Параметры каждого сценария строго проверяются (лишнее поле → `422 scenario_params_invalid`,
в `details` — поле и причина); пример каждого сценария из `GET /whatif/scenarios` запускается как есть.

## Чат (фича №2)

GigaChat (REST API Сбера, `app/chat/gigachat.py`) с тремя функциями `get_index`,
`compare_territories`, `run_scenario` (принимают названия НП). Числа о конкретных территориях
берутся из функций; ответ проверяется (`done.grounding`), неподтверждённые числа не выдаются
пользователю. Нет ключа или сбой LLM → событие `error`, остальной API работает.
События SSE: `meta`, `delta`, `tool_call`, `tool_result`, `citations`, `error`, `done`.

1. Ключ авторизации (base64 от `client_id:client_secret` из личного кабинета developers.sber.ru)
   положите в `backend/.env`: `GIGACHAT_CREDENTIALS=...`, `GIGACHAT_SCOPE=GIGACHAT_API_PERS`
   (юрлица: `GIGACHAT_API_B2B` / `GIGACHAT_API_CORP`). Файл `.env` в git не попадает.
2. Модель — `GIGACHAT_MODEL` (по умолчанию `GigaChat-3-Pro`; сильнее — `GigaChat-3-Ultra`).
3. Сертификаты: серверы GigaChat подписаны Russian Trusted Root CA (Минцифры), которого нет в
   certifi и Linux-образах. Корневой и промежуточный сертификаты лежат в
   [`config/certs`](config/certs/README.md) и подключаются только к клиенту GigaChat, поверх
   системного хранилища — ничего ставить не нужно ни локально, ни в Docker.
4. Проверка без расхода токенов (OAuth + список моделей):
   `python -m app.cli chat-check` (в Docker: `docker compose run --rm api python -m app.cli chat-check`).
RAG: гибридный поиск по русскому полнотекстовому индексу PostgreSQL и точный косинусный
поиск pgvector по локальным мультиязычным эмбеддингам FastEmbed. Команда `seed` обновляет
справочник методики из активного `MODEL_CONFIG_PATH` и индексирует его. В Docker модель
загружается при сборке образа; на хосте установите `pip install -e .[embeddings]`.
Контрольные вопросы: `python -m app.cli chat-eval --yes`
(платно — вызывает API).

### Проверка базы знаний и RAG

Из каталога `backend` запустите `docker compose up -d --build --wait`, затем
`docker compose run --rm api python -m app.cli seed --demo-features`. Повторный `seed`
заменяет встроенный справочник без дубликатов. Если справочники и территории уже загружены,
достаточно `docker compose run --rm api python -m app.cli seed-knowledge`.

Откройте `http://localhost:8000/knowledge/status`: поля `chunks` и `embedded` должны быть
положительными. Проверьте `http://localhost:8000/knowledge/search?q=больница&limit=5`:
ответ содержит найденные фрагменты, `retrieval_methods` (`vector` и/или `full_text`) и
`source_url`. Ссылка `/knowledge/chunks/{id}` открывает полный исходный фрагмент. Эти
маршруты работают без ключа LLM. В `/docs` доступны их параметры.

Свои проверенные материалы добавляются командой
`docker compose run --rm api python -m app.cli load-knowledge /data/файл.jsonl --source team-data --replace --embed`.
Формат JSONL: по объекту на строку, обязательное поле `text`, необязательные `title`,
`source_url`, `external_id` или `territory` с `region`. Файл должен находиться в
смонтированном каталоге `data`; в режиме Docker это `/data`. Указывайте источник и ссылку
на оригинал, если материал проверен. Точные текущие баллы и минуты бот получает из
расчётных инструментов, а не из текстов базы знаний.

Для беседы добавьте `GIGACHAT_CREDENTIALS` в `backend/.env`, перезапустите API и откройте чат в веб-приложении.
Источники появляются под ответом. Без ключа можно проверить поиск через `/knowledge/search`,
а ответы самого бота — нельзя. Результаты `seed --demo-features` синтетические; чат предупреждает об этом.

## Тесты

```bash
pytest tests/unit -q          # без БД
pytest tests -q               # + интеграционные: TEST_DATABASE_URL (база atlas_test создаётся compose)
```

Интеграционные тесты пересоздают схему (`downgrade base → upgrade head`), проверяют
`alembic check` (миграция ↔ модели), грузят реальные территории ML и прогоняют API, ETL и
чат (на подставном клиенте LLM, без сети).

## Ограничения и открытые вопросы

* В таблице 2 отчёта веса пресета «Риелтор / инвестор» дают в сумме 90, а не 100: при загрузке
  они пропорционально приводятся к 100 с предупреждением в логе — нужно решение ML/лида.
* Райцентры определяются эвристикой (крупнейший НП района); ошибки правятся в
  `seeds/cluster_overrides.csv`. У районов из данных ML нет ОКТМО — они заполняются при
  загрузке статистики с `--learn-oktmo`; дамп ГАР ФИАС не разбирается.
* Аналитический what-if — оценка по прямой с коэффициентом извилистости; точные времена —
  при подключённом OSRM. Сезонность, пробки, качество услуг, цены жилья индекс не учитывает.
* `index` и снимки считаются для населённых пунктов; районы и субъекты — агрегаты (`group_by`).
