---
name: duckbricks-bundle
description: Build, deploy and debug Duckbricks bundles (databricks.yml) with the duckbricks MCP tools - medallion layout, supported task types, DuckDB SQL dialect, and the validate → dbt_check → deploy → run → query loop. Use when creating or changing a bundle, job, pipeline or dbt project on Duckbricks, or when a Duckbricks run fails.
---

# Duckbricks bundles

Duckbricks is a local, Databricks-shaped platform on DuckDB + DuckLake. Bundles use the
Databricks bundle format unchanged; the code inside them is DuckDB SQL and Python.

## Working loop

1. `bundle_validate(path, target)`: schema and semantic checks. Fix every error; read the warnings.
2. `dbt_check(project_directory=<local dbt dir>, catalog=...)` when the bundle has dbt.
3. `bundle_deploy(path, target)`: idempotent; reports created/updated/deleted.
4. `job_run_now(bundle_job_key=..., bundle_path=path, target=..., wait=true)`.
5. On failure, `run_get(run_id)` shows failed-task logs. Fix the source and go back to 1.
6. `sql_query` (read-only) to check the result tables. Use `sql_execute` only for deliberate DDL/DML.

Start a new bundle by copying `examples/bundles/shop_demo` (or `dbk bundle init <name>`).

## Layout and naming

```
databricks.yml            bundle, include, variables (catalog), targets dev/prod
resources/schemas.yml     bronze, silver, gold in ${var.catalog}
resources/<job>.job.yml   tasks; paths are relative to this file (../src/x.sql)
src/                      SQL files and Python tasks
dbt/                      dbt project (models/silver, models/gold)
```

- Catalogs: `dev` (target dev, `mode: development`) and `prod`. Schemas `bronze`, `silver`, `gold`.
- Development mode prefixes job names with `[dev <short_name>] ` and pauses schedules.
- Landing files live under `s3://landing/<dataset>/...`.

## Supported

| Task type | Notes |
|---|---|
| `sql_task.file` | DuckDB SQL file; `parameters` `catalog`/`schema` set the default namespace; `:name` markers bind other parameters. `query`, `dashboard`, `alert` are rejected. |
| `spark_python_task` | `from duckbricks.runtime import spark, con, dbutils`; `spark` is SQLFrame (PySpark API on DuckDB). |
| `notebook_task` | marimo `.py` notebooks or Databricks-exported `.py` source; `base_parameters` become widgets. |
| `dbt_task` | dbt v2 (full distribution by default). `catalog`/`schema` arrive as `DBK_CATALOG`/`DBK_SCHEMA`; set `+catalog_name: "{{ env_var('DBK_CATALOG', 'dev') }}"` in dbt_project.yml and use a `generate_schema_name` macro so `+schema: silver` means `silver`. |
| `pipeline_task` | Pipelines from `resources.pipelines` (SQL: materialized views, streaming tables over `read_files`, `CONSTRAINT ... EXPECT`). |
| `condition_task`, `run_job_task` | As on Databricks. |

Rejected at deploy: `python_wheel_task`, `spark_jar_task`, `spark_submit_task`, `for_each_task`, `dashboard_task` and the other Databricks-only types.

Also: `depends_on`, `run_if`, `max_retries`, `timeout_seconds`, `job parameters`, `{{job.parameters.x}}`,
`{{tasks.k.values.v}}` (via `dbutils.jobs.taskValues`), `schedule` (Quartz), `trigger.periodic`,
`trigger.file_arrival` (s3:// prefixes), `max_concurrent_runs`. Clusters, notifications and
permissions are accepted with a warning and ignored.

## DuckDB SQL notes (vs Databricks SQL)

- Read files with `read_csv('s3://...', filename = true)`, `read_parquet`, `read_json_auto`; not `read_files` (pipelines accept `read_files` and translate it).
- `CREATE OR REPLACE TABLE ... AS`, `INSERT INTO t BY NAME SELECT ...`, `QUALIFY`, `try_cast`, `strftime`, `date_trunc('day', ts)`.
- No `MERGE ... WHEN NOT MATCHED BY SOURCE`; use `INSERT ... ON CONFLICT` only on tables with keys. DuckLake tables have no primary keys, so dedupe with `QUALIFY row_number() ...`.
- Incremental file loads: keep `filename AS _source_file` and filter `WHERE filename NOT IN (SELECT _source_file FROM t)`.
- `dbk sql transpile --from databricks file.sql` converts Databricks SQL as a starting point.
- System tables: `sys.jobs.job_runs`, `sys.jobs.task_runs`, `sys.lineage.table_lineage`, `sys.lineage.column_lineage`, `sys.query.history`, `sys.compute.warehouses`, `sys.pipelines.events`.

## When a run fails

- `run_get` first: the failed task's log tail usually names the file, line and SQL error.
- dbt failures: rerun `dbt_check` on the local project; check `catalog_name` and source `database`.
- Python task failures: `spark.sql` needs fully qualified names (`dev.silver.orders`).
- "read-only mode" means you used `sql_query` for a write; use `sql_execute`.
- `UNAVAILABLE`: the stack is down; ask the user to run `make up`.
