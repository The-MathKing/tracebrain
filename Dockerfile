# TraceBrain: one container serving the API (/api/*) and the built web UI (/) on $PORT.
# Build:  docker build -t tracebrain .
# Run:    docker run -p 8000:8000 -e FALKORDB_URL=redis://host:6379 -e OPENROUTER_API_KEY=... tracebrain

# ---- stage 1: build the frontend -------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- stage 2: python runtime -----------------------------------------------
FROM python:3.12-slim
# EXTRAS=lite bundles embedded falkordblite (only for running without FALKORDB_URL; needs glibc >= 2.39).
ARG EXTRAS=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TRACEBRAIN_WEB_DIST=/app/web/dist \
    TRACEBRAIN_SNAPSHOT_PATH=/app/deploy/snapshot/org_falkordb.json.gz \
    FALKORDB_LITE_PATH=/app/data/falkordb.db \
    PORT=8000
WORKDIR /app

COPY backend/ /app/backend/
RUN pip install "./backend${EXTRAS:+[$EXTRAS]}" && rm -rf /app/backend/build

COPY deploy/snapshot/ /app/deploy/snapshot/
COPY --from=web /web/dist /app/web/dist

RUN useradd --create-home --uid 10001 app && mkdir -p /app/data && chown -R app /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; r=urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health',timeout=4); sys.exit(0 if r.status==200 else 1)"

CMD ["sh", "-c", "exec uvicorn tracebrain.api:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
