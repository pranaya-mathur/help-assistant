# -----------------------------------------------------------------------------
# Builder — compile/install Python deps (needs build tools for some wheels)
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# -----------------------------------------------------------------------------
# Runtime — slim production image (non-root, healthcheck, entrypoint)
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /install /usr/local

# Application code (see .dockerignore — secrets and local data excluded)
COPY --chown=appuser:appuser . .

RUN chmod +x scripts/docker_entrypoint.sh \
    && mkdir -p data/sessions data/embeddings/chroma data/chunks data/indexes data/formatted \
    && chown -R appuser:appuser /app

USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8080 \
    API_RELOAD=false \
    APP_ENV=production \
    AUTO_INGEST_ON_START=true \
    UVICORN_WORKERS=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/api/v1/health || exit 1

ENTRYPOINT ["/app/scripts/docker_entrypoint.sh"]
