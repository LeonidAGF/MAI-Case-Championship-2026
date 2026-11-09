FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (layer cache): read them from pyproject.toml.
COPY pyproject.toml ./
RUN python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); open('/tmp/requirements.txt', 'w').write('\n'.join(d['project']['dependencies']))" \
    && pip install -r /tmp/requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
COPY seeds ./seeds
COPY app ./app

RUN useradd --create-home --uid 1000 atlas
USER atlas

EXPOSE 8000
ENV WEB_CONCURRENCY=2
# Migrations are idempotent; then serve. Seed separately: python -m app.cli seed
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY} --proxy-headers"]
