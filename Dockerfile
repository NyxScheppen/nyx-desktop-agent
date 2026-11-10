FROM python:3.11-slim AS embedding-builder

ARG HF_HUB_OFFLINE=0
ENV HF_HUB_OFFLINE=${HF_HUB_OFFLINE}

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY nyx ./nyx
COPY scripts/__init__.py scripts/export_embedding.py ./scripts/
RUN --mount=type=cache,target=/root/.cache/uv \
    pip install --no-cache-dir uv \
    && uv sync --locked --no-dev --extra embedding-export --no-editable
RUN --mount=type=cache,target=/root/.cache/huggingface \
    uv run --no-sync python -m scripts.export_embedding --output /embedding-model

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NYX_CONFIG=/dev/null \
    HF_HOME=/app/models

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY nyx ./nyx
COPY scripts/migrate_sqlite_to_postgres.py ./scripts/
COPY --from=embedding-builder /embedding-model /app/models/hub/models--BAAI--bge-small-zh-v1.5/nyx-onnx-fp32

RUN --mount=type=cache,target=/root/.cache/uv \
    pip install --no-cache-dir uv \
    && uv sync --locked --no-dev --no-editable \
    && useradd --create-home --uid 10001 nyx \
    && mkdir -p /app/data/workspace \
    && ln -s /app/data/workspace /app/workspace \
    && chown -R nyx:nyx /app

COPY deploy/railway/nyx.config.yaml ./nyx.config.yaml

USER root
EXPOSE 8000

CMD ["sh", "-c", "chown -R nyx:nyx /app/data && exec su -s /bin/sh nyx -c '/app/.venv/bin/python -m nyx.main'"]
