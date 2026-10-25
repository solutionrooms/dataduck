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
  pipelines, workspace, ui)

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
