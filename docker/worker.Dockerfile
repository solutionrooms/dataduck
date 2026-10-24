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

# dbt layer: added once Spike B settles the install method.
