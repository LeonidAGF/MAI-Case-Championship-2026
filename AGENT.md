# AGENT.md — Pulse Score (атлас доступности территорий СКФО)

Pulse Score is a web service with a map of the North Caucasus Federal District (СКФО). For each
of 3,403 settlements it shows a 0–100 accessibility-of-life index with 7 subindices, compares
territories, runs "what if" scenarios (open a hospital, repair a road, …), estimates ambulance
travel times and answers questions in a chat. It is the team's solution to case A2 «Индекс
транспортной связанности» of MAI Case Championship 2026 (team Team_Epsilon). Product UI and human
docs are in Russian; code, comments and these agent docs are in English.

## Read next

| Working on | Read |
|---|---|
| ML: road graph, clusters, travel times, transport index, GraphSAGE (`scripts/`, `geo-embeddings/`, `graph/`, `clast/`, `data/`, `ml_results/`) | [scripts/AGENT.md](scripts/AGENT.md) |
| Backend: FastAPI, DB, ETL, what-if, chat, monitoring (`backend/`) | [backend/AGENT.md](backend/AGENT.md) |
| Frontend: Next.js, map, pages (`web/`) | [web/AGENT.md](web/AGENT.md) |

Human docs: [README.md](README.md) (whole product, Russian) and
[backend/README.md](backend/README.md) (backend details). `README_ML.md`, which the README links
to, does not exist yet.

## Repository map

```
backend/         FastAPI monolith + PostgreSQL/PostGIS/pgvector + Redis + Prometheus/Grafana/Loki
web/             Next.js 16 + React 19 + MapLibre + Tailwind 4 + zustand
scripts/         ML pipeline scripts (graph -> Leiden -> closeness/gravity -> transport index;
                 20-21: OpenStreetMap objects -> features F01–F17, F21)
geo-embeddings/  GraphSAGE embeddings pilot (scripts/, data/); data/ also holds the real OSM data
                 the backend loads: osm_poi.csv, osm_features.csv (+ reports)
graph/           committed road graph: nodes.parquet, edges.parquet (Git LFS, ~150 MB)
clast/           committed Leiden clustering of the road graph
data/            committed settlements, municipalities and graph features (read by the backend)
ml_results/      committed closeness, gravity and provisional transport index
docs/links       numbered list of sources (market, migration and population statistics)
```

## How the parts connect

```
OSM, statistics ──► ML (scripts/) ──► data/, graph/, clast/, ml_results/, geo-embeddings/data/
                                        │ committed files; backend `seed` reads data/ and
                                        │ geo-embeddings/data/ (OSM CSV), `load-ambulance` reads
                                        │ graph/; the backend never writes ML folders
                                        ▼
browser ◄── web (Next.js) ◄── HTTP/JSON ──► backend (FastAPI) ──► PostgreSQL + PostGIS + pgvector
                                              │                    Redis (cache, rate limits)
                                              ├── Anthropic API: Claude (chat, optional; can go
                                              │   through the AmneziaWG or Xray sidecar, LLM_PROXY_URL)
                                              └── OSRM (exact what-if, not connected yet)
```

- The frontend talks only to the backend API (`NEXT_PUBLIC_API_URL`, default `http://localhost:8000`).
  In Docker, Caddy (`deploy/Caddyfile`) is the only public entry: `/` goes to the frontend and `/api`
  to the backend, and the frontend image is built with `NEXT_PUBLIC_API_URL=/api`.
- The backend computes the index itself with a built-in reference engine. ML plugs its model in
  through a YAML config and an optional `IndexEngine` class, without backend code changes.
- The data is **real**: 18 of the 30 features (F01–F17, F21) are computed from OpenStreetMap on the
  ML road graph, for the 3,403 settlements (OSM snapshot 2026-11-11). F18–F20 and F22–F30 (Rosstat,
  ФНС, ЕМИСС statistics and climate) aren't in OSM and have no data yet, so the climate subindex and
  the attractiveness badge are empty and those features are left out of the index.

## Domain glossary

- **Territory levels**: region = субъект (7: RU-DA, RU-IN, RU-KB, RU-KC, RU-SE, RU-CE, RU-STA);
  municipality = муниципальный район или округ (144); settlement = населённый пункт (3,403 scored).
  The index is computed for settlements only; districts and regions are aggregates.
- **Comparability clusters А/Б/В** (API codes `A`/`B`/`V`; A=26, B=180, V=3197): city, town or
  district centre, village. Normalization and fair comparison happen inside a cluster. These are
  **not** the ML **Leiden transport clusters** (`cluster_id`, 1,078 road-network communities).
  Never mix the two.
- **Features F01–F30** → **subindices S1–S6** (медицина, образование, досуг, транспорт, работа,
  климат) → total 0–100. **S7** «привлекательность» is a separate badge for the realtor mode and is
  not part of the total.
- **Profile** (contract v1): weights for S1–S6 plus 9 filters, passed as `profile=<preset>` or
  `p=<base64url JSON>`. Presets: `family`, `young_specialist`, `pensioner`, `remote_worker`,
  `realtor`. Profiles live in the URL and localStorage; there are no user accounts.
- **What-if W1–W10**: scenarios that override feature values (open or close a hospital, school,
  college, rail stop or leisure object; road repair; bus route; airport; a district package).
- **Synthetic data**: feature values with `source='synthetic-demo'` and `as_of=1900-01-01`. Only the
  integration test database has them; `seed` deletes any left over from older versions, and every
  output still flags them if they appear.
- **Population**: known (from OSM) for only 1,545 of 3,403 settlements, about 8.55 M people of
  СКФО's ~10.3 M. Population-weighted numbers must state that coverage.

## Quick start

```bash
cp .env.example .env                  # compose settings: site address, login (BASIC_AUTH_*), passwords, ports
cp backend/.env.example backend/.env  # API settings; only if backend/.env does not exist yet
docker compose up -d --build          # caddy, web, api (migrations run on start), db, redis, monitoring
docker compose run --rm api python -m app.cli seed   # territories + real OSM features + chat knowledge
cd web && npm install && npm run dev  # optional: the frontend with hot reload on http://localhost:3000
```

The site is at http://localhost (Caddy with the login from the root `.env`; Swagger at
`/api/docs`). The API also listens on http://localhost:8000 for local use (health at `/healthz`),
and Grafana on http://localhost:3001. Images are built on a PC (`docker compose --profile vpn --profile
xray build` and `push`) or by CI (`.github/workflows/ci-cd.yml`, which also tests the backend) and published
to Docker Hub as `kekishev/case-championship-{backend,frontend,db,vpn-egress,xray-egress}`. The server never builds:
it runs `docker compose pull` and `docker compose up -d --no-build`.

## Rules for every change

- **ML artifacts are read-only** unless the task is ML work: `data/`, `graph/`, `clast/`,
  `ml_results/`, `geo-embeddings/data/`. The backend only reads `data/`, the OSM CSV files in
  `geo-embeddings/data/` and, in `load-ambulance`, the road graph in `graph/`.
- **Secrets.** `backend/.env` holds the Anthropic API key. Never print it, copy it into other files or
  commit it. Organisation API keys (`pulse_…`) are secrets too: the database keeps only their hashes. `backend/secrets/awg0.conf` and `backend/secrets/xray.vpn` (VPN egress) hold the tunnel keys and are gitignored too. `.env*` is gitignored, but `backend/.env.example` is tracked, so keep it secret-free.
- **Paid calls.** `python -m app.cli chat-eval` and anything else that calls the Anthropic API costs
  money. Run them only when the user asks. `chat-check` is free.
- **Real data only.** The product loads real data; synthetic values exist only in the integration
  test database. Missing values stay missing (left out of the index, or imputed with the cluster
  median and flagged), never invented. Keep the synthetic-data detection working (`/healthz`, chat
  warning, metrics, `/data/coverage`).
- **No OpenStreetMap at runtime.** OSM is read once by `scripts/20_fetch_osm_poi.py`, and the results
  are committed as CSV in `geo-embeddings/data/`. The backend only reads those files during `seed`.
  Elevation works the same way: the SRTM tiles (3″) are committed in `backend/data/srtm/` and read
  only by `load-ambulance`; `fetch-srtm` downloads missing ones. Nothing external at request time.
- **Texts.** UI strings and API error messages are Russian. User-facing text never shows feature,
  subindex or scenario codes such as F01, S3 or W2 (backend tests check this).
- **Large files.** Anything over ~50 MB goes through Git LFS (`.gitattributes`).
- **Run the checks** for the parts you touched: backend `python -m pytest tests/unit -q` (plus
  integration tests when DB code changes), web `npm run build` and `npm run lint`. Web lint already
  fails on 30 existing errors, so compare against that baseline. Details are in each part's
  AGENT.md.

## Contracts that cross team boundaries

Change both sides in the same branch, or coordinate with the owner:

| Contract | Producer | Consumer |
|---|---|---|
| Data API for organisations (`/v1/*` routes, dataset columns; paid, external) | `backend/app/api/routes/v1.py`, `backend/app/partners/datasets.py` | organisations' own programs: change only additively |
| HTTP API (`backend/openapi.json`, regenerate with `python -m app.cli export-openapi`) | `backend/app/api/schemas.py`, `backend/app/api/routes/` | `web/lib/api.ts` (hand-written TS types) |
| Profile v1 (`v`, `preset`, `weights` S1–S6 summing to 100, 9 `filters`) | `backend/app/profiles/contract.py` | `web/types/profile.ts`, `web/lib/profile.ts` |
| Model config YAML + `IndexEngine` | ML (`MODEL_CONFIG_PATH`, `SCORING_ENGINE`) | `backend/app/modelcfg.py`, `backend/app/scoring/contract.py` |
| Territory parquet schemas | ML (`data/*.parquet`) | `backend/app/etl/territories.py` |
| OSM objects and features (`osm_poi.csv`, `osm_features.csv`) | ML (`scripts/20-21`, `geo-embeddings/data/`) | `backend/app/cli.py` (`seed`, `seed-osm`), `app/etl/poi.py`, `app/etl/features.py` |
| Road graph (`graph/nodes.parquet`: `node_id, lon, lat`, sorted; `graph/edges.parquet`: `u, v, length_m, highway, maxspeed, bridge, tunnel, access`) | ML (`graph/`, Git LFS) | `backend/app/ambulance/graph.py` (`load-ambulance`) |

## Git

- `main` is the integration branch. Work arrives through GitHub PRs (repo
  `LeonidAGF/MAI-Case-Championship-2026`) from area branches: `backend`, `web`, `ml`,
  `observability`, `docs`, `geo-embedd`. Don't commit to `main` directly.
- Backend commits mostly use Conventional Commits (`feat(backend): …`, `docs: …`); other areas have
  used free-form messages, including Russian ones.

## Known repo-wide gaps (as of 2026-11-12)

- 12 of 30 features have no data (F18–F20, F22–F30: statistics and climate are not in OSM). Only
  car times are computed, so profiles without a car fall back to them (flagged). OSM coverage is
  uneven in villages. OSRM is not connected (what-if times for new objects are analytical
  estimates); betweenness isn't computed; validation so far is manual only.
- The older ML pipeline steps can't be re-run from a fresh clone: several steps and all
  intermediate inputs are missing (steps 20–21 can, see [scripts/AGENT.md](scripts/AGENT.md)).
- The backend doesn't use `ml_results/` (the transport index and gravity) or the GraphSAGE files yet.
- `README_ML.md` is referenced but missing.
- Development happens on Windows (PowerShell) as well as Unix shells: `Makefile` targets work only
  in Linux, macOS and WSL.
