FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NYX_CONFIG=/dev/null

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY packages/nyx-harness ./packages/nyx-harness
COPY nyx ./nyx

RUN pip install --no-cache-dir uv \
    && uv sync --locked --no-dev --no-editable \
    && useradd --create-home --uid 10001 nyx \
    && mkdir -p /app/data/workspace \
    && ln -s /app/data/workspace /app/workspace \
    && chown -R nyx:nyx /app

USER nyx
EXPOSE 8000

CMD ["/app/.venv/bin/python", "-m", "nyx.main"]
