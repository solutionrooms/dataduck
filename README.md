# Duckbricks

A local, single-user, Databricks-shaped data platform on DuckDB 2.0, DuckLake and dbt v2.
Databricks API paths, bundle files (`databricks.yml`) and CLI shapes, running on your laptop.
See [SPEC.md](SPEC.md) for the design and [docs/compat.md](docs/compat.md) for what differs.

| Databricks | Duckbricks |
|---|---|
| Workspace (files, notebooks, trash, favorites) | Web UI + Workspace API over a local volume; marimo notebooks |
| Unity Catalog | DuckLake catalogs (`dev`, `prod`, `workspace`) on Postgres + SeaweedFS (S3); `sys` system tables |
| SQL warehouses | DuckDB processes serving Quack, started on demand |
| Jobs, schedules, file arrival triggers | Jobs API 2.2, Quartz schedules, s3:// file arrival |
| Lakeflow Declarative Pipelines | SQL materialized views, streaming tables (incremental by file), expectations |
| Declarative Automation Bundles | `dbk bundle validate / deploy / run / destroy` with the real bundle schema |
| dbt on Databricks | dbt v2 (full distribution by default) in the worker, column lineage |
| Databricks MCP / AI tooling | `dbk-mcp` for Claude Code, plus a project skill |
| Lakehouse Federation (Databricks) | Foreign catalogs over a real workspace: query live, or mirror locally for offline use |

## Quick start

Needs Docker and [uv](https://docs.astral.sh/uv/).

```sh
make up            # builds and starts postgres, seaweedfs, api, worker, warehouse, notebooks
make seed          # demo data in s3://landing/shop/
cd examples/bundles/shop_demo
uv run dbk bundle deploy
uv run dbk bundle run shop_daily
uv run dbk sql "select * from dev.gold.daily_revenue order by order_date desc limit 5"
```

- Web UI: http://127.0.0.1:8080 (sign in with `DBK_TOKEN` from `.env`)
- Notebooks: http://127.0.0.1:2718 (password `DBK_NOTEBOOKS_TOKEN` from `.env`)
- CLI: `uv run dbk --help` (status, bundle, jobs, runs, sql, catalogs, schemas, tables, warehouses,
  pipelines, workspace, connections, mirror, ui)

## A real Databricks workspace (ADR 0013)

Put `DATABRICKS_HOST`, `DATABRICKS_TOKEN` and `DATABRICKS_WAREHOUSE_ID` in `.env`. The token needs
`EXTERNAL USE SCHEMA` on each schema you read, and the metastore needs external data access.

```sh
uv run dbk connections create databricks_prod
uv run dbk catalogs create uc_live --connection databricks_prod --remote-catalog main_prod --mode live
uv run dbk sql "select count(*) from uc_live.sales.orders"      # reads Databricks storage directly

uv run dbk catalogs create main_prod --connection databricks_prod --mode mirror
uv run dbk mirror config main_prod -f mirror.yml   # schemas, exclude, filters, schedule
uv run dbk mirror sync main_prod                   # copies changed tables; nightly by default
uv run dbk mirror status main_prod
```

`mirror.yml`:

```yaml
schemas: [sales]
exclude: [sales.big_history_snapshot]
filters:   # DuckDB predicate over `source`; scalar subqueries are evaluated first
  sales.journal: "period > (SELECT CAST(max(CAST(period AS INTEGER)) - 1000 AS VARCHAR) FROM source)"
include_views: false
schedule: {quartz_cron_expression: "0 0 2 * * ?", timezone_id: Europe/London}
```

A mirror keeps one copy of each table by default (`keep_history_hours: 0` expires old DuckLake
snapshots after each sync) and is read-only. Switch a catalog with
`dbk catalogs set-mode main_prod live|mirror`.

## Claude Code

Open the repo in Claude Code. `.mcp.json` starts the `duckbricks` MCP server; read tools are
allowed and write tools ask first (`.claude/settings.json`). The `duckbricks-bundle` skill teaches
the loop: `bundle_validate` → `dbt_check` → `bundle_deploy` → `job_run_now` → `run_get` →
`sql_query`. Try: "Create a bundle that ingests s3://landing/shop/events into bronze and builds a
daily funnel in gold."

## Development

```sh
make check         # ruff, format, mypy, unit + golden-file tests
make test-integration
make e2e           # SPEC section 12 acceptance test (deploys and destroys the dev target)
```

Decisions are in [docs/adr](docs/adr), versions in [VERSIONS.md](VERSIONS.md), progress in
[docs/progress.md](docs/progress.md).
