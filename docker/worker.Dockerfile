FROM python:3.12-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.9.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
RUN uv sync --frozen --no-dev
RUN python -c "import duckdb; c = duckdb.connect(); [c.install_extension(e) for e in ('ducklake', 'httpfs', 'postgres')]"

# dbt v2, open-source distribution: one static binary from the GitHub release, checksum-pinned.
ARG DBT_VERSION=2.0.8
ARG DBT_SHA256_AARCH64=08adc0abdcf8c077237ea3dfc78815a949557879144ee1def5c447bc2d2912cd
ARG DBT_SHA256_X86_64=5ae21d84f6d3b96f1e4f3ba7e0265c78bee804f6c812c4c00494487fd402abbe
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
      aarch64) sha="$DBT_SHA256_AARCH64" ;; \
      x86_64) sha="$DBT_SHA256_X86_64" ;; \
      *) echo "unsupported architecture: $arch"; exit 1 ;; \
    esac; \
    name="dbt-core-${DBT_VERSION}-${arch}-unknown-linux-gnu"; \
    curl -fsSLo /tmp/dbt.tar.gz "https://github.com/dbt-labs/dbt/releases/download/v${DBT_VERSION}/${name}.tar.gz"; \
    echo "${sha}  /tmp/dbt.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/dbt.tar.gz -C /tmp; \
    install -Dm755 "/tmp/${name}/dbt" /usr/local/bin/dbt; \
    rm -rf /tmp/dbt.tar.gz "/tmp/${name}"; \
    dbt --version

# dbt's bundled DuckDB driver (1.5.4) cannot open DuckLake 1.1 catalogs. Install the system
# DuckDB driver at the platform's DuckDB version; dbt prefers it over the bundled one (ADR 0005).
ARG DBC_VERSION=0.3.1
ARG DUCKDB_DRIVER_VERSION=2.0.0
RUN uv tool run --from "dbc==${DBC_VERSION}" dbc install "duckdb=${DUCKDB_DRIVER_VERSION}" --level system
ENV XDG_CACHE_HOME=/var/cache/dbt
