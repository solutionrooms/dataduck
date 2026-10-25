# One image definition, several targets: api, worker, warehouse, notebooks.

FROM python:3.12-slim-trixie AS base
COPY --from=ghcr.io/astral-sh/uv:0.9.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra notebooks --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
RUN uv sync --frozen --no-dev --extra notebooks
# Bake DuckDB extensions into the image so sessions start offline.
RUN python -c "import duckdb; c = duckdb.connect(); [c.install_extension(e) for e in ('ducklake', 'httpfs', 'postgres', 'quack')]"

# --- web UI -----------------------------------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

# --- api: control plane, scheduler loop, web UI -----------------------------------------
FROM base AS api
COPY --from=ui /ui/dist ./ui/dist
COPY examples ./examples
EXPOSE 8080
CMD ["uvicorn", "duckbricks.api.app:app", "--host", "0.0.0.0", "--port", "8080"]

# --- warehouse: Quack servers, one process per running warehouse ------------------------
FROM base AS warehouse
EXPOSE 9400
CMD ["python", "-m", "duckbricks.warehouses.agent"]

# --- notebooks: marimo over the workspace volume ----------------------------------------
FROM base AS notebooks
EXPOSE 2718
CMD ["python", "-m", "duckbricks.notebooks.server"]

# --- worker: task executors and dbt -----------------------------------------------------
FROM base AS worker
# dbt v2: one static binary, checksum-pinned. DBT_DISTRIBUTION picks the build (ADR 0006):
#   full: the dbt distribution (static analysis, column lineage), from dbt Labs' CDN
#   oss:  dbt-oss, from the GitHub release
ARG DBT_DISTRIBUTION=full
ARG DBT_FULL_VERSION=2.0.9
ARG DBT_FULL_SHA256_AARCH64=a1a2ad5cd08f81654ed40aab7dcd6130582a1b5e1d73fd9624a41bc3c4097ac5
ARG DBT_FULL_SHA256_X86_64=cc57e1f6a40d9d62008d2242c03fc8fbe5cca289c957abf784f9a6267bc4b0ef
ARG DBT_OSS_VERSION=2.0.8
ARG DBT_OSS_SHA256_AARCH64=08adc0abdcf8c077237ea3dfc78815a949557879144ee1def5c447bc2d2912cd
ARG DBT_OSS_SHA256_X86_64=5ae21d84f6d3b96f1e4f3ba7e0265c78bee804f6c812c4c00494487fd402abbe
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$DBT_DISTRIBUTION/$arch" in \
      full/aarch64) sha="$DBT_FULL_SHA256_AARCH64" ;; \
      full/x86_64) sha="$DBT_FULL_SHA256_X86_64" ;; \
      oss/aarch64) sha="$DBT_OSS_SHA256_AARCH64" ;; \
      oss/x86_64) sha="$DBT_OSS_SHA256_X86_64" ;; \
      *) echo "unsupported DBT_DISTRIBUTION/arch: $DBT_DISTRIBUTION/$arch"; exit 1 ;; \
    esac; \
    if [ "$DBT_DISTRIBUTION" = full ]; then \
      url="https://public.cdn.getdbt.com/fs/cli/fs-v${DBT_FULL_VERSION}-${arch}-unknown-linux-gnu.tar.gz"; \
    else \
      url="https://github.com/dbt-labs/dbt/releases/download/v${DBT_OSS_VERSION}/dbt-core-${DBT_OSS_VERSION}-${arch}-unknown-linux-gnu.tar.gz"; \
    fi; \
    mkdir -p /tmp/dbt; \
    curl -fsSLo /tmp/dbt.tar.gz "$url"; \
    echo "${sha}  /tmp/dbt.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/dbt.tar.gz -C /tmp/dbt; \
    install -Dm755 "$(find /tmp/dbt -type f -name dbt | head -1)" /usr/local/bin/dbt; \
    rm -rf /tmp/dbt.tar.gz /tmp/dbt; \
    dbt --version
ENV DBK_DBT_DISTRIBUTION=${DBT_DISTRIBUTION}

# dbt's bundled DuckDB driver (1.5.4) cannot open DuckLake 1.1 catalogs. Install the system
# DuckDB driver at the platform's DuckDB version; dbt prefers it over the bundled one (ADR 0005).
ARG DBC_VERSION=0.3.1
ARG DUCKDB_DRIVER_VERSION=2.0.0
RUN uv tool run --from "dbc==${DBC_VERSION}" dbc install "duckdb=${DUCKDB_DRIVER_VERSION}" --level system
ENV XDG_CACHE_HOME=/var/cache/dbt
CMD ["python", "-m", "duckbricks.worker.main"]
