# PostgreSQL 16 + pgvector (official pgvector image, Debian bookworm) + PostGIS 3 from PGDG.
# Note: postgis/postgis:16-3.4 is Debian bullseye, whose PGDG apt repo is gone (404),
# so pgvector cannot be added there; this direction (pgvector image + PostGIS) works.
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-16-postgis-3 \
        postgresql-16-postgis-3-scripts \
    && rm -rf /var/lib/apt/lists/*

# Runs once on an empty data directory.
COPY init/ /docker-entrypoint-initdb.d/
