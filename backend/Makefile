# Shortcuts (Linux/macOS/WSL). On Windows run the commands from the README directly.
COMPOSE = docker compose
RUN_API = $(COMPOSE) run --rm api

.PHONY: up down logs seed seed-osm load-ml-results migrate test test-unit openapi coverage

up:            ## db + redis + api (migrations run on start)
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f api

migrate:
	$(RUN_API) alembic upgrade head

seed:          ## reference tables + territories + OSM objects and features + ML results (idempotent)
	$(RUN_API) python -m app.cli seed

seed-osm:      ## only the OSM objects and features (data/osm_poi.csv, data/osm_features.csv)
	$(RUN_API) python -m app.cli seed-osm

load-ml-results: ## only the ML gravity results (ml_results/settlement_gravity_observed.csv) for /markets
	$(RUN_API) python -m app.cli load-ml-results

coverage:
	$(RUN_API) python -m app.cli coverage

test-unit:
	python -m pytest tests/unit -q

test:          ## unit + integration (needs TEST_DATABASE_URL, e.g. the atlas_test DB of this compose)
	python -m pytest tests -q

openapi:
	python -m app.cli export-openapi --out openapi.json
