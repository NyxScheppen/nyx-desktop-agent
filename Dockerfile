FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NYX_CONFIG=/dev/null

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY deploy/railway/nyx.config.yaml ./nyx.config.yaml
COPY packages/nyx-harness ./packages/nyx-harness
COPY nyx ./nyx
COPY scripts/migrate_sqlite_to_postgres.py ./scripts/

RUN pip install --no-cache-dir uv \
    && uv sync --locked --no-dev --no-editable \
    && useradd --create-home --uid 10001 nyx \
    && mkdir -p /app/data/workspace \
    && ln -s /app/data/workspace /app/workspace \
    && chown -R nyx:nyx /app

USER root
EXPOSE 8000

CMD ["sh", "-c", "chown -R nyx:nyx /app/data && exec su -s /bin/sh nyx -c '/app/.venv/bin/python -m nyx.main'"]
