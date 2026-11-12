# AGENT.md — backend

FastAPI monolith of Pulse Score (атлас доступности территорий СКФО): index API, explanations,
comparison, what-if scenarios W1–W10, a Claude (Anthropic API) chat with RAG, ETL from ML outputs,
and the Prometheus/Grafana/Loki monitoring stack. Read the root [AGENT.md](../AGENT.md) first. The human
docs are [README.md](README.md) (Russian, more detail on ETL formats and monitoring).

## Stack

- Python ≥ 3.12 (Docker image `python:3.12-slim`; a newer local venv also works), FastAPI, Pydantic 2,
  pydantic-settings.
- SQLAlchemy 2 async + asyncpg, Alembic with hand-written migrations. PostgreSQL 16 with PostGIS,
  pgvector and pg_trgm (image built from `docker/db`, which also creates the `atlas_test` database).
- Redis for the shared JSON cache and per-IP rate limits. `REDIS_URL=` (empty) switches to an
  in-process cache.
- numpy (scoring), pyarrow (reading ML parquet), prometheus-client in multiprocess mode, httpx
  (OSRM), the `anthropic` SDK 2.x (chat). Optional FastEmbed (`pip install -e ".[embeddings]"`)
  for vector search in chat.

## Commands

Run from `backend/`.

| Task | Command |
|---|---|
| Full stack: db, redis, api, monitoring | `docker compose up -d --build` |
| Reference tables, territories, OSM objects and features, chat knowledge (idempotent) | `docker compose run --rm api python -m app.cli seed` |
| Only the OSM objects and features (removes leftover synthetic values) | `docker compose run --rm api python -m app.cli seed-osm` |
| Check the chat key, network and model (free) | `docker compose run --rm api python -m app.cli chat-check` |
| Only DB and Redis (for a local API or tests) | `docker compose up -d db redis` |
| Local install | `python -m venv .venv`, activate (`.venv\Scripts\Activate.ps1` or `source .venv/bin/activate`), `pip install -e ".[dev]"` |
| Local API | `python -m app.cli migrate`, then `uvicorn app.main:app --reload` |
| Unit tests: no DB, about 5 s | `python -m pytest tests/unit -q` |
| All tests | `python -m pytest tests -q` with `TEST_DATABASE_URL=postgresql+asyncpg://atlas:atlas@localhost:5432/atlas_test` |
| Regenerate the API contract | `python -m app.cli export-openapi` (writes `openapi.json`) |
| Regenerate Grafana dashboards | `python observability/build_dashboards.py`, then `docker compose restart grafana` |
| List CLI commands | `python -m app.cli --help` |

As of 2026-11-12 the 84 unit tests pass. The 26 integration tests need the database; the README
reports all 110 tests passing on 2026-11-10. `Makefile` targets
work only in Linux, macOS and WSL; on Windows run the underlying commands. No linter or formatter
is configured, but the code carries ruff-style markers (`# noqa: ANN001`, `# noqa: BLE001`):
annotate every function and justify each broad `except Exception` with a comment.

Ports (change them in `.env`): API 8000, PostgreSQL 5432, Redis 6379, Grafana 3001 (3000 belongs
to the web app), Prometheus 9090, Loki 3100.

## Layout

```
app/
  main.py         create_app(): GZip, CORS, X-Process-Time and metrics middlewares, error
                  handlers, /metrics, routers. The lifespan builds the Container.
  container.py    process-wide services; warmup() pre-scores 3 clusters x 5 presets
  core/           config.py (Settings), errors.py (ApiError family), ratelimit.py,
                  logging.py (JSON logs), metrics.py (Prometheus + data-quality collector)
  db/             models.py (schema source of truth), base.py (constraint naming), session.py
  api/deps.py     get_container, get_session, profile_query, normalize_cluster, rate_limit
  api/routes/     one router per area: territories, index, explain, compare, whatif, profiles,
                  reference (/features, /clusters, /regions, /data/coverage), knowledge, chat, health
  api/schemas.py  request/response models = the OpenAPI contract used by web/lib/api.ts
  modelcfg.py     loads and validates the ML-owned model YAML
  profiles/       profile contract v1, codec of the `p` URL parameter, preset resolver
  scoring/        contract.py (IndexEngine protocol), reference.py (built-in engine),
                  matrix.py (feature matrix, DataRevision, MatrixStore), service.py
                  (ScoringService, ClusterScores, ScenarioOverlay), snapshots.py, registry.py
  explain/        waterfall against the cluster's typical territory; texts.py = Russian phrasing
  compare/        2–3 territories of one cluster
  whatif/         catalog.py (W1–W10 params + examples), engine.py (handlers -> overrides ->
                  re-score), osrm.py, geo.py (haversine, analytical travel time)
  chat/           llm.py (Claude: request shape, caching, refusal fallbacks, error codes,
                  free check), service.py (tool loop, SSE, history cleanup), tools.py
                  (get_index, compare_territories, run_scenario), grounding.py, retriever.py
                  (hybrid full-text + pgvector), catalog.py, control_questions.yaml
  etl/            loaders (territories, features, poi, travel_times, stats, od_matrix, knowledge,
                  demo), helpers (io, keys, names), runs.py (etl_run), coverage.py
  cli.py          python -m app.cli <command>
  cache.py        MemoryCache / RedisCache with one async API
migrations/       Alembic (async env.py); versions/ are written by hand
config/           model.default.yaml (fallback copy of the ML model)
seeds/            regions.csv, municipality_regions.csv, cluster_overrides.csv, official_poi_counts.csv
observability/    Prometheus, Loki, Alloy, Grafana provisioning; build_dashboards.py
tests/            unit/ (no I/O) and integration/ (real PostGIS, real ML territories)
openapi.json      generated by export-openapi, committed
```

## How a request works

- `session: AsyncSession = Depends(get_session)` yields a session and rolls it back on error.
  Nothing is committed for you: a route that writes calls `await session.commit()`.
- `c = get_container(request)` gives settings, `cfg` (model config), `scoring`, `profiles`,
  `cache`, `rate_limiter`, `osrm`, `whatif` and `chat`.
- Profile in GET routes: `profile: Profile = Depends(profile_query)` (query `profile=<preset>` or
  `p=<encoded>`). In POST bodies: `c.profiles.resolve(body.profile)` (preset id, `p` string or object).
- Scoring: `cs = await c.scoring.score_cluster(session, cluster_code, profile, overlay=None)`
  returns `ClusterScores` (`scores`, `matrix.values`, `matrix.provenance`, `rank_of()`,
  `feature_stats()`, `total_stats()`). The engine runs in a worker thread.
- Snapshots: `await persist_snapshot(...)` with `model_version_id=await c.model_version_id()`.
- Rate limits: most routers declare `dependencies=[rate_limit("default", "rate_limit_per_minute")]`
  at router level. Some routes set their own: `POST /whatif/run` uses bucket `whatif`, `POST /chat`
  uses `chat`, `/knowledge/*` uses `knowledge`. `GET /whatif/scenarios` and `/healthz` have no limit.

## Invariants — do not break

- **Index scope.** The index is computed for settlements only, inside their comparability cluster
  (A/B/V, Cyrillic А/Б/В accepted by `normalize_cluster`). Districts and regions are aggregates
  (`GET /index?group_by=municipality|region`). Normalization is fitted on the cluster's base matrix,
  so a what-if never moves territories it does not touch.
- **IndexEngine contract** (`scoring/contract.py`): deterministic; `total` is 0..100 and equals the
  sum of component contributions; subindices are 0..100 or None; one `ComponentScore` per requested
  feature, in request order. `tests/unit/test_engine_contract.py` checks it.
- **Waterfall** (`explain/service.py`): `baseline + Σ delta = total` after rounding
  (`round_preserving_sum`). Pluses and minuses are the top 3 deltas each way.
- **Snapshots** (`index_result` + `component`) are immutable and unique per (territory,
  profile_hash, scenario_run_id, model_version_id, data_revision). Custom profiles are never stored,
  only their hash.
- **Data revision** = id of the last successful `etl_run` with `bumps_revision`. Caches and
  snapshots are keyed by it (picked up within about 3 s). Every data-changing loader must go through
  `start_run` / `finish_run`, or caches go stale.
- **Inheritance and mode.** Municipal and regional statistics (F18–F23, F27–F30) are inherited by
  settlements, and provenance (`inherited_from`, `value_mode`) is kept. Travel-time features resolve
  the mode as car → any → foot, or foot → any → car (`profile.mode`).
- **Error envelope** `{"code", "message", "details?"}`. Raise `NotFoundError`, `ValidationFailed`,
  `ConflictError`, `ServiceUnavailable` or `RateLimited` with a stable snake_case `code` and a
  Russian message. Don't return ad-hoc error dicts. The frontend shows `message` and the
  `details[].loc/msg` of 422s.
- **User-facing texts** are Russian and never contain feature, subindex or scenario codes. Tests
  grep for `\b[FSW]\d{1,2}\b`; use `explain/texts.py` helpers (`fact_text`, `fmt_num`, `plural`).
- **Real data only.** The product loads real values only (OSM features via `seed`). The synthetic
  generator lives in `tests/synthetic.py` and feeds only the integration test database. The marker
  (`source='synthetic-demo'`, `as_of=1900-01-01`) is still detected by `/healthz`, chat, metrics
  and `/data/coverage`, so leftovers from older versions are never shown as real; `seed` deletes
  them. Any new output derived from feature values must keep that check.
- **Missing data stays missing.** A feature with no values in a cluster is left out of the index
  (`normalized` is None, its text says «признак не участвует в расчёте»); a territory missing one
  value gets the cluster median, flagged `imputed`. Never fill gaps with invented numbers.
- **Chat grounding.** Every number the model states about a territory must come from tool results.
  `grounding.py` hides unverified numbers, and the synthetic-data warning is added by code, not by
  the prompt. New capabilities go into `chat/tools.py` as tools, not as facts in the prompt.
- **The model belongs to ML.** `config/model.default.yaml` is a fallback copy of report tables 2–5;
  don't tune weights there. ML points `MODEL_CONFIG_PATH` at their YAML and may plug in
  `SCORING_ENGINE=module:Class`. `modelcfg.py` uses `extra="forbid"`: an unknown key fails startup.

## How to

**Add an endpoint.** Put the route in the matching `api/routes/<area>.py` (a new module needs an
`APIRouter(tags=[...])`, a line in `main.py`'s router tuple and a `TAGS` entry). Request and response
models go in `api/schemas.py`; request bodies use `ConfigDict(extra="forbid")`. Keep logic in a
service module, not in the route. Add `responses=ERROR_RESPONSES`, a rate limit and an integration
test. Then run `export-openapi` and update `web/lib/api.ts`.

**SQL.** Routes and services use SQLAlchemy Core `text()` with bound parameters (`:name`). The ORM
models define the schema. Only fixed, whitelisted fragments may be formatted into SQL (see
`territories._filters`), never request values.

**Change the schema.** Edit `db/models.py` and add a hand-written migration
`migrations/versions/YYYYMMDD_NNNN_<slug>.py` (revision `"NNNN_<slug>"`, `down_revision` = the
previous one, currently `"0001_initial"`). Use the full constraint names the naming convention in
`db/base.py` produces: `CheckConstraint(..., name="level")` in a model is `ck_territory_level` in a
migration. Geometry columns use `spatial_index=False` plus an explicit GiST `Index`. The integration
suite runs `downgrade base → upgrade head → alembic check → downgrade → upgrade`, so the migration
must match the models exactly and downgrade cleanly.

**Add a setting.** Add a field to `core/config.py` (the env var is the upper-cased name) and
document it in `.env.example`, which is tracked and must stay secret-free.

**Add a loader.** Put it in `etl/` and wire it in `cli.py` (subparser plus `ASYNC_COMMANDS`). Run it
through `_in_transaction(kind, source, fn)`, which records failures in `etl_run`. Use idempotent
upserts (`ON CONFLICT`) and return a JSON-serializable stats dict.

**Add a what-if scenario.** Add a params model (`extra="forbid"`) and a `ScenarioDef` with a
runnable `example` to `whatif/catalog.py`, and a handler to `whatif/engine.py` `HANDLERS`. A handler
only writes overrides through `ctx.ws.set(...)`. `test_every_catalog_example_runs` posts every
example and expects affected > 0 and monotonic deltas.

**Add a metric.** Define it in `core/metrics.py`. Dashboards are generated, so change
`observability/build_dashboards.py` and rerun it; never hand-edit
`observability/grafana/dashboards/*.json`.

## Data and ML integration

- `load-territories` (part of `seed`) reads `DATA_DIR` (default `../data`; `/data` inside Docker):
  `municipalities.parquet`, `settlements_working.parquet`, `settlements.parquet`, plus `seeds/*.csv`.
  It creates 7 regions, 144 municipalities and 3,403 settlements, and assigns clusters A=26, B=180,
  V=3197 (rules in `etl/territories.py`, overrides in `seeds/cluster_overrides.csv`). Graph snap
  info from ML lands in `territory.meta`.
- `seed` (and `seed-osm`) loads the real OSM data from `OSM_DATA_DIR` (default `../geo-embeddings/data`,
  `/osm` in Docker): `osm_poi.csv` into `poi` and `osm_features.csv` into `feature_value`. Both files
  are produced once by `scripts/20_fetch_osm_poi.py` and `scripts/21_calculate_osm_features.py`;
  the backend never calls OpenStreetMap. Other features F01–F30 come in through `load-features`
  (long or wide parquet/CSV; keys and formats are in README) or `load-stats`.
- `travel_time` is empty unless `load-travel-times` runs, so W2 (closing a hospital) uses the OSM
  `poi` layer with the analytical estimate. OSRM is not connected, so W1–W5 and W9 use the
  analytical estimate (straight line × 1.3, 50 km/h by car or 4.5 km/h on foot) and add a warning.
- Only `../data` and `../geo-embeddings/data` are mounted into the API container. `../ml_results`
  and `../clast` are not visible inside Docker and are not used by the backend yet.
- Population is known for only 1,545 of 3,403 settlements, so anything weighted by population must
  use known population only and report coverage.

## Tests

- `pytest-asyncio` in auto mode with a session-scoped loop. Unit tests must not touch the DB or the
  network.
- Integration tests carry `pytestmark = pytest.mark.integration` and use the session fixtures
  `migrated` → `seeded` → `container` → `client` (httpx over ASGI). The database holds real ML
  territories plus synthetic test features (`tests/synthetic.py`: every feature present, so all
  mechanics are covered). Find settlements by name with `territory_id(client, "Ахты")`.
- All integration tests share one database per session, including scenario runs created by other
  tests. Keep assertions order-independent.
- Without `TEST_DATABASE_URL` the integration tests are **skipped**, not failed. Check the skip count
  before calling DB code tested.
- The fixtures wipe the test database. Never point `TEST_DATABASE_URL` at the main `atlas` database.
- `Settings` reads `backend/.env` in tests too, and it may hold a real Anthropic key. The fixture forces
  `anthropic_api_key=None`, unit tests build `Settings(_env_file=None, ...)`, and chat tests inject a
  scripted fake client (`FakeClaude`). Keep that pattern for any new external service.

## Gotchas

- `backend/.env` contains the Anthropic key (`ANTHROPIC_API_KEY`). Don't print, copy or commit it.
- The chat model is `LLM_MODEL` (default `claude-sonnet-5-5`) at `LLM_EFFORT` (default `medium`);
  `claude-haiku-5-5` is faster and cheaper and gets no refusal fallback. Requests are not streamed
  (the answer is checked for invented numbers before the user sees it), so keep `LLM_MAX_TOKENS` ≤ 20000.
- `chat-eval` calls the paid Anthropic API: run it only when the user explicitly asks. `chat-check`
  is free (it only reads the model's description).
- `docker compose restart` doesn't reload `.env`. Use `docker compose up -d api`.
- The territory table holds all three levels. `is_scored` is true only for settlements; `parent_id`
  goes settlement → municipality → region; every level has `region_code` (RU-DA, RU-IN, RU-KB,
  RU-KC, RU-SE, RU-CE, RU-STA).
- The `db/models.py` docstring mentions `tests/integration/test_migrations.py`; the check actually
  lives in `test_api.py::test_migration_matches_models`.
- The browser can reach the API only from origins listed in `CORS_ORIGINS` (default
  `http://localhost:3000`).
