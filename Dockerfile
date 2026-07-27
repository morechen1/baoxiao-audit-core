FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app
RUN pip install "uv==0.11.32"
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN uv sync --frozen --no-install-project \
    && uv sync --frozen

FROM base AS runtime

COPY alembic.ini ./
COPY migrations ./migrations
COPY data/samples ./data/samples
RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p data/raw data/parsed data/review_batches data/review_results \
    && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test

RUN uv sync --frozen --extra dev
COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests
COPY scripts ./scripts
CMD ["pytest"]
