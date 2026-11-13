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

Run `docker compose` from the repository root (one `docker-compose.yml` for the whole app) and
`python` from `backend/`.

| Task | Command |
|---|---|
| Whole app: Caddy, web, api, db, redis, monitoring (the root `.env` holds the site login, passwords, ports) | `docker compose up -d --build` |
| Publish the four images to Docker Hub (on a PC or in CI, never on the server) | `docker login -u kekishev`, then `docker compose --profile vpn build` and `docker compose --profile vpn push` |
| Run on the server from Docker Hub images | `docker compose pull`, then `docker compose up -d --no-build` |
| Reference tables, territories, OSM objects and features, chat knowledge (idempotent) | `docker compose run --rm api python -m app.cli seed` |
| Only the OSM objects and features (removes leftover synthetic values) | `docker compose run --rm api python -m app.cli seed-osm` |
| Ambulance travel times to all settlements (after `seed`; ~20 s, peak ~2.5 GB RAM) | `docker compose run --rm api python -m app.cli load-ambulance` |
| Add missing SRTM tiles to `data/srtm` (on the host: Docker mounts it read-only) | `python -m app.cli fetch-srtm` |
| Organisations, API keys, payment orders of the /v1 data API (team only) | `python -m app.cli org-add`, `key-issue`, `order-create`, … (`--help`) |
| Check the chat key, network and model (free) | `docker compose run --rm api python -m app.cli chat-check` |
| Chat through the VPN tunnel (AmneziaWG sidecar, needs `backend/secrets/awg0.conf`) | `COMPOSE_PROFILES=vpn` and `LLM_PROXY_URL=http://vpn-egress:8888` in the root `.env`, `docker compose up -d --build`, then `docker compose exec api python -m app.cli egress-check` |
| Only DB and Redis (for a local API or tests) | `docker compose up -d db redis` |
| Local install | `python -m venv .venv`, activate (`.venv\Scripts\Activate.ps1` or `source .venv/bin/activate`), `pip install -e ".[dev]"` |
| Local API | `python -m app.cli migrate`, then `uvicorn app.main:app --reload` |
| Unit tests: no DB, about 5 s | `python -m pytest tests/unit -q` |
| All tests | `python -m pytest tests -q` with `TEST_DATABASE_URL=postgresql+asyncpg://atlas:atlas@localhost:5432/atlas_test` |
| Regenerate the API contract | `python -m app.cli export-openapi` (writes `openapi.json`) |
| Regenerate Grafana dashboards | `python observability/build_dashboards.py`, then `docker compose restart grafana` |
| List CLI commands | `python -m app.cli --help` |

As of 2026-11-13 the 112 unit tests and the 49 integration tests (they need the database) pass.
`Makefile` targets
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
  ambulance/      ambulance travel times: config.py (YAML), graph.py (ML road graph), speeds.py (OSM max
                  speed, built-up areas, slope and height factors), dem.py (SRTM tiles), pipeline.py
                  (one Dijkstra from all bases, route sums), service.py + texts.py (API answers)
  partners/       /v1 data API for organisations: keys.py (key format, hash, letters), store.py (organisations,
                  keys, the request check), datasets.py (exports built on the web app's services),
                  tables.py (CSV for programs / for Excel, JSON)
  payments/       paid access, approved by hand: service.py (orders, invoice letters, order-confirm, extending
                  keys, the audit trail)
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
config/           model.default.yaml (fallback copy of the ML model), ambulance.default.yaml
data/srtm/        35 SRTM 3″ tiles under the road graph (committed, ~41 MB; README.md = provenance)
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
- **Ambulance travel times** (`app/ambulance/`, table `ambulance_time`) are computed only by
  `load-ambulance`: graph + OSM maximum speed per road + SRTM slope and height, one Dijkstra from
  all bases (OSM ambulance stations *and every hospital*, `bases.categories`). The run does not bump
  the data revision (the index does not use it); the service keys its cache by the latest `ambulance`
  run. Model keys of `config/ambulance.default.yaml` are hashed into the run (`stale` in answers when
  they change); norm, thresholds, dispatch time and the error band are applied when answering. The
  pipeline checks itself (route sums must equal the Dijkstra times). Real data only: tests run it on
  the committed graph and SRTM tiles, never on toy graphs or made-up tiles.
- **Data API for organisations** (`/v1`, `app/partners/`) is an external, paid contract: change its
  columns and parameters only additively. Exports reuse the web app's services (scoring, `load_values`,
  the ambulance service, what-if evaluation), never a second formula; integration tests compare them
  with the regular endpoints. Keys: `pulse_` + 43 random characters, only the SHA-256 hash is stored,
  shown once by `key-issue`; read only from `Authorization: Bearer` or `X-API-Key`, never from the URL;
  never log or print a key. Rate limits are per key (`api_key()` in `api/deps.py`).
- **Payments** (`app/payments/`) have no provider: a team member confirms an order with `order-confirm
  --received <amount>`, which must equal the order's; confirming twice or a canceled order is refused;
  every step goes to `payment_event`. Money is `Decimal`/`Numeric(12, 2)`, never float.
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
previous one, currently `"0003_organisations_payments"`). Use the full constraint names the naming convention in
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
- `load-ambulance` reads `GRAPH_DIR` (`graph/*.parquet`, the columns in the root contract table), the
  SRTM tiles of `SRTM_DIR` (`data/srtm`) and the `poi` and `territory` tables, and writes
  `ambulance_time`. It is CPU work in a worker thread (~20 s, peak ~2.5 GB), never done per request. The model
  computes its own speeds; the ML columns `speed_kmh` and `generalized_time_s` are not used.
- Only `../data`, `../geo-embeddings/data`, `../graph` and `./data/srtm` are mounted into the API
  container (read-only). `../ml_results`
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
- `tests/integration/test_real_ambulance.py` loads `osm_poi.csv` and runs `load-ambulance` on the real
  graph and `data/srtm` (~20 s); it is skipped when the graph is only a Git LFS pointer. Its first test
  expects `503 ambulance_not_computed`, so it must run before the module fixture.
- `tests/integration/test_real_v1.py` runs the /v1 API on the real data (after `test_real_data.py`) and the
  manual order flow (create, wrong amount refused, confirm once, first key, cancel).
- Without `TEST_DATABASE_URL` the integration tests are **skipped**, not failed. Check the skip count
  before calling DB code tested.
- The fixtures wipe the test database. Never point `TEST_DATABASE_URL` at the main `atlas` database.
- `Settings` reads `backend/.env` in tests too, and it may hold a real Anthropic key. The fixture forces
  `anthropic_api_key=None`, unit tests build `Settings(_env_file=None, ...)`, and chat tests inject a
  scripted fake client (`FakeClaude`). Keep that pattern for any new external service.

## Gotchas

- `backend/.env` contains the Anthropic key (`ANTHROPIC_API_KEY`). Don't print, copy or commit it.
  Organisation API keys (`pulse_…`) are secrets too, and so is `backend/secrets/awg0.conf` (the VPN
  private key for the `vpn` compose profile).
- `LLM_PROXY_URL` is used by the Anthropic client only (`app/chat/llm.py`). Don't route other traffic
  through it. The tunnel is for demonstrating VPN egress: the server and the VPS must both be in
  countries where Anthropic's API is available.
- The chat model is `LLM_MODEL` (default `claude-sonnet-5-5`) at `LLM_EFFORT` (default `medium`);
  `claude-haiku-5-5` is faster and cheaper and gets no refusal fallback. Requests are not streamed
  (the answer is checked for invented numbers before the user sees it), so keep `LLM_MAX_TOKENS` ≤ 20000.
- `chat-eval` calls the paid Anthropic API: run it only when the user explicitly asks. `chat-check`
  is free (it only reads the model's description).
- `docker compose restart` doesn't reload `.env`. Use `docker compose up -d api`.
- Compose reads the root `.env` (ports, passwords, `IMAGE_TAG`, `COMPOSE_PROFILES`, `LLM_PROXY_URL`,
  `CORS_ORIGINS`). `backend/.env` is the api's `env_file`, and the compose `environment` overrides it
  (`DATABASE_URL`, `REDIS_URL`, the data folders, `TRUST_FORWARDED_FOR`, `LLM_PROXY_URL`, `CORS_ORIGINS`).
- In Docker the API sits behind Caddy under `/api`: Caddy strips the prefix and
  `UVICORN_ROOT_PATH=/api` adds it back. Use http://localhost/api/docs; `http://localhost:8000/docs`
  doesn't load its schema, while other direct `:8000` requests (`/healthz`, `/metrics`) work.
- The territory table holds all three levels. `is_scored` is true only for settlements; `parent_id`
  goes settlement → municipality → region; every level has `region_code` (RU-DA, RU-IN, RU-KB,
  RU-KC, RU-SE, RU-CE, RU-STA).
- The `db/models.py` docstring mentions `tests/integration/test_migrations.py`; the check actually
  lives in `test_api.py::test_migration_matches_models`.
- The browser can reach the API only from origins listed in `CORS_ORIGINS` (comma-separated, default
  `https://cson.ru,http://localhost:3000`): the root `.env` in Docker, `backend/.env` without Docker. The
  site itself calls `/api` on its own origin and needs no entry.
