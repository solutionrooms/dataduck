# 0007. `sys` is the system catalog; lineage lives in the control database

- Status: accepted (resolves the open question in ADR 0002)
- Date: 2026-10-25

## Context

Databricks exposes system tables under `system`. DuckDB reserves `system` (ADR 0002), so the
platform needs another name. Lineage (Phase 4) needs a home that the API, the UI and SQL can all read.

## Options

1. A DuckLake catalog named `system_tables` that a job refreshes from the control database.
2. A read-only in-memory catalog of views over the control database, attached per session.
3. Expose system tables only through the API.

## Decision

Option 2, named `sys`. `open_session(system_catalog=True)` attaches the control database
through the postgres extension with `READ_ONLY` and creates views in an in-memory `sys` catalog:
`sys.jobs.{jobs, job_runs, task_runs}`, `sys.lineage.{table_lineage, column_lineage}`,
`sys.query.history`, `sys.compute.{warehouses, workers}`, `sys.pipelines.{pipelines, updates,
events}`. Only the API's SQL engine attaches it.

Lineage edges are rows in the control database (`table_lineage`, `column_lineage`), written by
the worker after dbt runs (from `dbt.column_lineage.parquet`, full dbt build) and pipeline updates.

## Consequences

- System tables are always current; no refresh job.
- Warehouses do not attach `sys` (they must not read the control database, ADR 0010); statements
  that name `sys` run on the API's read-only engine even with a `warehouse_id`.
- Queries say `sys.` where Databricks says `system.`; `dbk sql transpile` does not rewrite it.
