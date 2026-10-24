#!/usr/bin/env bash
# Spike B: dbt v2 (dbt-oss) builds one model into the Postgres-backed DuckLake on SeaweedFS.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

export PGHOST="$DBK_PG_HOST" PGPORT="$DBK_PG_PORT" PGUSER="$DBK_PG_USER" PGPASSWORD="$DBK_PG_PASSWORD"
work="$(mktemp -d)"
cp -R "$here/project/." "$work/"
cd "$work"
export DBT_PROFILES_DIR="$work"

python "$here/check.py" seed
dbt --version
driver="$(dbt show --inline "select version() as v" 2>&1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
echo "dbt DuckDB driver: ${driver:-unknown}"
dbt run
python "$here/check.py" verify "${driver:-unknown}"
