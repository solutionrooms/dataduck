# Versions

Exact versions the platform is built and tested with. `dbk status` will print these (Phase 1) and
warn when the worker, warehouse and dbt disagree.

Verified 2026-10-25 on macOS arm64 (Docker Desktop, linux/arm64 images).

## Engine and lakehouse

| Component | Version | Where it is pinned | How verified |
|---|---|---|---|
| DuckDB (Python) | 2.0.0, source id `d54cbbe3df` | `pyproject.toml` (`duckdb==2.0.0`) | `pragma_version()` in the api image |
| DuckLake extension | `417a9117f5` | Installed at image build (core repository) | `duckdb_extensions()` |
| DuckLake format | 1.1 | Written by the extension | `ducklake_options('<catalog>')` |
| httpfs extension | `77392a7207` | Image build | `duckdb_extensions()` |
| postgres extension (`postgres_scanner`) | `c0f5a3c0c4` | Image build | `duckdb_extensions()` |

## Services

| Component | Version | Where it is pinned |
|---|---|---|
| Postgres | 18.6 (`postgres:18.6-alpine`) | `docker-compose.yml` |
| SeaweedFS | 4.48 (`chrislusf/seaweedfs:4.48`) | `docker-compose.yml` |
| Python | 3.12.15 in images (`python:3.12-slim-trixie`), 3.12.11 on the host | Dockerfiles, `.python-version` |
| uv | 0.9.1 | Dockerfiles (`ghcr.io/astral-sh/uv:0.9.1`) |

## Python libraries (from `uv.lock`)

| Library | Version |
|---|---|
| FastAPI | 0.162.0 |
| SQLAlchemy | 2.1.6 |
| Alembic | 1.20.1 |
| Pydantic | 2.14.1 |
| psycopg | 3.3.6 |
| SQLFrame (`spark`, ADR 0008) | 4.7.0 |
| sqlglot | 30.24.0 |
| MCP Python SDK (`MCPServer`; FastMCP was renamed in 2.x) | 2.3.0 |
| marimo (notebooks, ADR 0011) | 0.25.2 |
| Typer / Rich (CLI) | 0.27.3 / 15.0.0 |
| httpx | 0.28.1 |
| croniter | 6.2.4 |
| jsonschema | 4.26.0 |
| pandas / polars / pyarrow (notebooks extra) | 3.1.0 / 2.0.0 / 26.0.0 |

## Web UI (`ui/package-lock.json`)

| Library | Version |
|---|---|
| React | 19.3.1 |
| Vite | 8.3.5 |
| TypeScript | 7.0.2 |
| Node (build image) | `node:22-alpine` |

## Federation extensions (ADR 0013)

`unity_catalog`, `delta` (`delta_scan`, deletion vectors) and `azure` are baked into every image
with `ducklake`, `httpfs`, `postgres` and `quack` (checked 2026-10-25 on DuckDB 2.0.0).

- `delta_scan('abfss://...')` reads Unity Catalog managed tables with a scoped `azure` secret
  whose `CONNECTION_STRING` carries a vended user-delegation SAS (`BlobEndpoint`, `DfsEndpoint`,
  `SharedAccessSignature`). Deletion vectors are applied.
- `ATTACH 'catalog' AS x (TYPE unity_catalog, SECRET s, READ_ONLY true)` lists and reads tables,
  but does not apply vended Azure credentials itself: scans need the scoped `azure` secret.
  The `unity_catalog` secret takes `TOKEN`, `ENDPOINT`, `AWS_REGION` (any value works on Azure).
- In Debian images the `azure` extension's default transport fails with "Problem with the SSL CA
  cert"; `SET azure_transport_option_type = 'curl'` works.
- DuckLake: `CALL ducklake_expire_snapshots('cat', older_than => ...)` then
  `CALL ducklake_cleanup_old_files('cat', cleanup_all => true)` removes replaced files.
- `iceberg` (incl. `iceberg_to_ducklake`) also loads; unused. No `delta_sharing` extension exists.

## dbt

| Component | Version | Where it is pinned | How verified |
|---|---|---|---|
| dbt (default build, `DBT_DISTRIBUTION=full`) | dbt 2.0.9 | `docker/Dockerfile` (`DBT_FULL_VERSION`, sha256 per arch) | `dbt --version` |
| dbt (alternative build, `DBT_DISTRIBUTION=oss`) | dbt-oss 2.0.8 | `docker/Dockerfile` (`DBT_OSS_VERSION`, sha256 per arch) | `dbt --version` |
| dbt's DuckDB driver | DuckDB 2.0.0 (system ADBC driver in `/etc/adbc/drivers`) | `docker/Dockerfile` (`DUCKDB_DRIVER_VERSION`) | `dbt show --inline "select version()"` returns `v2.0.0` |
| dbt's bundled DuckDB driver | 1.5.4 (not used: cannot open DuckLake 1.1) | dbt source | Spike B, ADR 0005 |
| Static analysis / column lineage | full build only | ADR 0006 | Spike B prints `dbt.column_lineage.parquet` rows |
| dbc (driver installer) | 0.3.1 | `docker/Dockerfile` (`DBC_VERSION`) | build-time only |

dbt and the platform run the same DuckDB (2.0.0), so `dbk status` has no mismatch to report.

## Quack (warehouses)

| Component | Version | How verified |
|---|---|---|
| quack extension | `f96420ed46` | `duckdb_extensions()` in the warehouse image |

Used by warehouses (ADR 0010), verified in the running stack:
`CALL quack_serve('quack:0.0.0.0:<port>', token => '...', allow_other_hostname => true,
disable_ssl => true)` serves in the background; `FROM quack_server_list()` lists servers;
`CALL quack_stop('<uri>')` stops one (`SELECT quack_stop(...)` fails: it is a table function).
Clients run `SELECT * FROM quack_query('quack:<host>:<port>', '<sql>', token => '...',
disable_ssl => true)`; DDL, DML and several statements (`USE dev.default; SELECT ...`) work, `USE`
does not persist across calls, column types are preserved.

Verified locally: `CALL quack_serve('quack:127.0.0.1:9494', token = '...')` serves over plain HTTP
on a local address (default port 9494); a client reads with
`FROM quack_query('quack:127.0.0.1:9494', 'SELECT ...', token = '...')` or
`ATTACH 'quack:127.0.0.1:9494' AS wh (TOKEN '...')`; a wrong token fails with
`Authentication failed`. Non-local addresses default to HTTPS with a self-signed certificate
(`disable_ssl`, `ssl_cert_file`, `ssl_key_file` options) per the
[Quack docs](https://duckdb.org/docs/2.0/quack/reference.html); not yet tried.

## Bundle schema

| Component | Version | Note |
|---|---|---|
| Databricks CLI | v1.12.1 | Generated `schemas/bundle_config_schema.json` with `databricks bundle schema`. Not a runtime dependency. |

## Syntax verified against these versions

S3 secret for SeaweedFS (path-style URLs, no SSL):

```sql
CREATE OR REPLACE TEMPORARY SECRET dbk_s3 (TYPE s3, KEY_ID '...', SECRET '...',
  ENDPOINT 'seaweedfs:8333', REGION 'us-east-1', URL_STYLE 'path', USE_SSL false);
```

DuckLake with Postgres metadata (one schema per catalog) and S3 data:

```sql
CREATE OR REPLACE TEMPORARY SECRET dbk_pg (TYPE postgres, HOST 'postgres', PORT 5432,
  USER '...', PASSWORD '...');
ATTACH IF NOT EXISTS 'ducklake:postgres:dbname=lake' AS "dev" (
  DATA_PATH 's3://lakehouse/dev/', METADATA_SCHEMA 'dev',
  METADATA_PARAMETERS MAP {'SECRET': 'dbk_pg'});
```

An unnamed postgres secret is not picked up by DuckLake's metadata connection; name it in
`METADATA_PARAMETERS`.
