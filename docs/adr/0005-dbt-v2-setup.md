# 0005. dbt v2: dbt-oss with the system DuckDB 2.0 driver; no fallback needed

- Status: accepted
- Date: 2026-10-25

## Context

SPEC.md section 9 runs dbt v2's open-source distribution (dbt-oss) against the platform's
DuckLake through `catalogs.yml`, with dbt-duckdb v1 as the fallback. Research flagged three risks:
dbt v2 bundles an older DuckDB, dbt-oss lacks the static-analysis engine, and
[dbt-labs/dbt#16486](https://github.com/dbt-labs/dbt/issues/16486) (open) reports DuckLake writes
with Postgres metadata that "succeed silently but are never committed".

## Findings (Spike B, dbt-oss 2.0.8, linux/arm64)

| Question | Result |
|---|---|
| Install | Single static binary from the GitHub release (`dbt-core-2.0.8-<arch>-unknown-linux-gnu.tar.gz`), checksum-pinned in `docker/worker.Dockerfile`. Reports `dbt-oss 2.0.8`. |
| Bundled DuckDB driver (1.5.4) | Fails: `DuckLake catalog version mismatch: catalog version is 1.1, but the extension requires version 1.0`. |
| System DuckDB driver | `dbc install "duckdb=2.0.0" --level system` (dbc 0.3.1) puts it in `/etc/adbc/drivers`; dbt prefers it. `dbt show --inline "select version()"` returns `v2.0.0`. |
| `catalogs.yml` with Postgres metadata | Works: `metadata_path: "postgres:dbname=lake"`, `metadata_schema: dev`, `data_path: "s3://lakehouse/dev/"`. |
| Postgres credentials | libpq env vars (`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`); `catalogs.yml` DuckLake entries take no secret option. |
| S3 credentials | Profile `secrets:` entry of `type: s3` with endpoint, `url_style: path`, `use_ssl: false`. |
| Targeting a catalog | Models set `catalog_name: dev`. Setting `database: dev` fails with dbt1005 ("set `catalog_name: dev` explicitly"). Sources set `database: dev`. |
| `use_catalogs_v2` | On by default in v2; only settable in `dbt_project.yml` `flags:`. Nothing to do. |
| #16486 silent non-commit | Did not reproduce. A separate read-only platform session reads the model's rows (50 rows, revenue matches the source) and its Parquet file under `s3://lakehouse/dev/`. |
| `--static-analysis strict` | dbt-oss warns: "this distribution of dbt OSS does not include the static analysis engine". Info-schema Parquet files are still written. |

## Decision

Use dbt-oss 2.0.8 with the system DuckDB driver pinned to the platform's DuckDB version (2.0.0).
`catalogs.yml` is the integration path. The dbt-duckdb fallback is not needed.

## Consequences

- Version alignment holds by construction: dbt and the platform run the same DuckDB. Bumping
  `duckdb` in `pyproject.toml` means bumping `DUCKDB_DRIVER_VERSION` in the worker image; Spike B
  fails if they differ.
- The Phase 2 executor generates `profiles.yml` and `catalogs.yml` per run and passes Postgres
  credentials to the dbt subprocess as libpq env vars.
- **Open, Phase 2 and 4:** column-level lineage and the strict `dbt_check` need the static-analysis
  engine, which only the full `dbt` distribution has (free to use, proprietary licence;
  `dbt system upgrade-distribution`). Options when Phase 2 starts: switch the worker to `dbt`, or
  keep dbt-oss and limit `dbt_check` to parse and compile errors and drop column lineage.
- DuckLake data files are named after dbt's `<model>__dbt_tmp` table, because dbt builds a temp
  table and renames it. Harmless.
