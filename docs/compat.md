# Databricks compatibility

What Duckbricks accepts, ignores or rejects compared with Databricks. Grouped by area; newest
entries at the top of each table. "Lenient" means accepted with a warning and no effect.

## Catalogs and namespaces

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| `system` catalog | `sys` (read-only views over the control database) | Different name | DuckDB reserves `system` (ADR 0002, ADR 0007) |
| Default catalog `main` | Default catalog `workspace`; `main` is rejected | Different | DuckDB reserves `main` as a database name (ADR 0002) |
| Default schema `default` | Every catalog has `default`; DuckLake's `main` schema also exists (hidden when empty) | Same + extra | ADR 0001 #5 |
| Catalog names | Lowercase letters, digits, `_`; max 63 characters | Stricter | Names double as DuckDB aliases and Postgres schema names |
| `DESCRIBE HISTORY` | `GET /api/2.1/unity-catalog/tables/{name}/history` (DuckLake snapshots) | Extension | |

## SQL statements and warehouses

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| SQL dialect | DuckDB SQL; `dbk sql transpile --from databricks` helps port | Different | SPEC non-goal |
| `format` | `JSON_ARRAY` only; `ARROW_STREAM`/`CSV` rejected | Subset | |
| `disposition` | `INLINE` only; `EXTERNAL_LINKS` rejected | Subset | |
| `:name` parameters with `type` | Supported; cast by type | Same | |
| Statement without `warehouse_id` | Runs on the API's engine | Extension | ADR 0001 #13 |
| `read_only: true` request field | Runs on `READ_ONLY` attachments | Extension | MCP `sql_query` |
| Stopped warehouse targeted by a statement | Starts it, then runs | Same | ADR 0010 |
| Statement naming `sys` with a `warehouse_id` | Runs on the API's read-only engine | Different | Warehouses do not attach `sys` (ADR 0010) |
| `cluster_size` | Maps to node types (dbk.small / medium / large) | Different | ADR 0010 |
| `enable_photon`, `enable_serverless_compute`, `min/max_num_clusters`, `spot_instance_policy`, `warehouse_type`, `channel`, `tags` | Stored, no effect | Lenient | Single machine |
| JDBC/ODBC | Not available; Quack on `quack:127.0.0.1:<port>` | Different | |

## Compute

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| `new_cluster`, `job_clusters`, `existing_cluster_id` | `node_type_id` picks memory and threads; other cluster fields ignored | Lenient | One DuckDB per task |
| Unknown `node_type_id` | Mapped to `dbk.medium` with a warning | Lenient | SPEC section 6 |
| `spark` in Python | SQLFrame over DuckDB | Best effort | ADR 0008 |

## Jobs

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| `trigger.file_arrival` | s3:// prefixes; first poll is a baseline; honours `min_time_between_triggers_seconds`, `wait_after_last_change_seconds` | Same | 15 s poll |
| `trigger.table_update` | Ignored with a warning | Lenient | |
| `python_wheel_task`, `spark_jar_task`, `spark_submit_task`, `for_each_task`, `dashboard_task`, `power_bi_task`, ... | Rejected at create and at bundle validate | Rejected | Cannot run |
| `sql_task.query`, `.dashboard`, `.alert` | Rejected; use `sql_task.file` | Rejected | |
| `sql_task.warehouse_id` | Ignored; SQL tasks run on the worker | Lenient | |
| `email_notifications`, `webhook_notifications`, `notification_settings`, `health` | Ignored with a warning | Lenient | Single user, local |
| `run_if`, `max_retries`, `min_retry_interval_millis`, `retry_on_timeout`, `timeout_seconds`, `disabled` | Supported | Same | |
| Dynamic value references (`{{job.parameters.x}}`, `{{tasks.k.values.v}}`, `{{job.start_time.iso_date}}`, ...) | Supported | Same | |
| Run `state` and `status` | Both returned | Same | ADR 0001 #7 |
| Queueing at `max_concurrent_runs` | Trigger skipped and logged, never queued | Different | SPEC section 7, ADR 0001 #7 |
| `runs/get-output` | Takes a task run id | Same | ADR 0001 #8 |
| `sql_task` parameter markers | `:name` (converted to DuckDB `$name`) | Same | ADR 0001 #9 |
| Quartz day-of-week 1-7 (SUN=1) | Converted to cron 0-6; year field must be `*` | Same | ADR 0001 #6 |

## Bundles

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| `resources.jobs`, `resources.schemas`, `resources.pipelines` | Supported | Same | SPEC section 8 |
| Other resource types (dashboards, apps, model serving, volumes, ...) | Ignored with a warning | Lenient | |
| `artifacts`, `python`, `scripts`, `run_as`, `permissions`, `experimental` | Ignored with a warning | Lenient | |
| `workspace.host`, `profile`, `auth_type` | Ignored; Duckbricks uses `DBK_HOST` | Lenient | |
| `variables.*.lookup` | Error | Rejected | No workspace objects to look up |
| `mode: development` | Prefix `[dev <short_name>] ` on jobs and pipelines, schedules and triggers paused, `development: true` on pipelines | Same | |
| `mode: development` schema names | Not prefixed (`dev_<user>_`); use a dev catalog instead | Different | Catalog per target in the examples |
| `presets.name_prefix`, `trigger_pause_status`, `jobs_max_concurrent_runs` | Supported | Same | |
| Interpolated values | Given their schema type for known integer and boolean fields | Same | Like the Databricks CLI |
| `${resources.<type>.<key>.id}` | Resolved by the API at deploy | Same | |
| Deployment state | In the control database (`bundle_resources`), not in the workspace | Different | |
| Deployed jobs | Carry `deployment.kind = BUNDLE`, `edit_mode = UI_LOCKED` | Same | |

## Pipelines

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| SQL materialized views, streaming tables, views, expectations | Supported | Same | ADR 0012 |
| Python sources (`import dlt`) | Rejected with a message | Rejected | ADR 0012 |
| `APPLY CHANGES` / `AUTO CDC`, flows, sinks | Not supported | Rejected | |
| `read_files` options | `format`, `header`, `sep`/`delimiter`; others ignored with a warning | Subset | |
| `serverless`, `photon`, `clusters`, `channel`, `edition`, `continuous` | Ignored | Lenient | Updates are triggered |
| `configuration` | Logged, not applied | Lenient | |

## dbt

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| `dbt_task` on a SQL warehouse | dbt v2 in the worker (full distribution 2.0.9 by default, dbt-oss as a build arg), DuckLake via `catalogs.yml` | Different | SPEC section 9, ADR 0005, ADR 0006 |
| `dbt_task.catalog` / `schema` | Passed as `DBK_CATALOG` / `DBK_SCHEMA` and the profile's default schema | Different | Use `+catalog_name: "{{ env_var('DBK_CATALOG') }}"` |
| `dbt_task.warehouse_id` | Ignored | Lenient | |
| Model `database:` naming a catalog | Use `catalog_name:`; `database:` naming a catalogs.yml entry fails (dbt1005) | Different | dbt v2 catalogs |
| Column lineage | From dbt static analysis (full build) into `sys.lineage.column_lineage` | Same idea | ADR 0006, ADR 0007 |

## Workspace and notebooks

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| Workspace API `list`, `get-status`, `mkdirs`, `import` (`AUTO`, `SOURCE`, `RAW`), `export`, `delete` | Supported | Same | ADR 0009 |
| `import` formats `DBC`, `JUPYTER`, `HTML` | Rejected | Rejected | |
| Delete | Moves to a trash (restore or purge via `/api/duckbricks/workspace/trash`) | Different | Like the Databricks UI |
| Notebooks | marimo; Databricks `.py` source runs as Python, `# MAGIC` cells skipped | Different | ADR 0011 |
| `dbutils` | `widgets`, `jobs.taskValues`, `notebook.exit` only | Subset | |

## Connections and foreign catalogs (ADR 0013)

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| `connection_type` | `DATABRICKS` only; others rejected | Subset | Federation targets a Databricks workspace |
| Connection secrets (`personalAccessToken`, `client_secret`, ...) | Ignored with a warning; the token comes from the env var in `options.duckbricks_token_env` (default `DATABRICKS_TOKEN`) | Different | No secrets in the control database |
| `options.httpPath` | Gives the SQL warehouse id (also `options.duckbricks_warehouse_id`) | Same + extra | |
| Foreign catalog `options.catalog` | The remote catalog (defaults to the local name) | Same | |
| `options.duckbricks_mode` | `live` (default) or `mirror` | Extension | ADR 0013 |
| `PATCH /catalogs/{name}` `options` | Merged into the existing options | Different | Databricks replaces the map |
| Live mode: tables | Read from storage with vended credentials; needs `EXTERNAL USE SCHEMA` | Same idea | No Databricks compute |
| Live mode: views, materialized views, streaming tables | Rejected with a message | Rejected | No files to read; mirror them instead |
| Live mode in notebooks (`spark`, `mo.sql`) and dbt | Not available; mirror mode works everywhere | Subset | Live tables are prepared per statement by the API, warehouses and SQL tasks |
| Writes to a foreign catalog | Rejected (`READ_ONLY` attachment) | Same | |
| Mirror config, status, syncs | `/api/duckbricks/mirrors/...` | Extension | |
| Mirrored views | Copied through the SQL warehouse (Arrow) | Extension | About 8.5x the Parquet size on the wire |
| Storage clouds | Azure (ADLS) only | Subset | Only Azure has been tested |
| Table history of a live table | Empty | Different | Snapshots are DuckLake's |

## Engine notes

| Topic | Note |
|---|---|
| DuckDB 2.0 Python API | `DuckDBPyRelation.fetchone()` is gone; use `con.execute(sql).fetchone()` or `rel.fetchall()` |
| DuckLake data inlining | Inserts under 10 rows are inlined into the Postgres metadata until flushed (`ducklake_default_data_inlining_row_limit`) |
| Reserved words | DuckDB 2.0 reserves `days` (and other interval units) as column aliases; quote them |
| Azure in containers | The `azure` extension needs `azure_transport_option_type = 'curl'` on Debian images (CA bundle path) |
