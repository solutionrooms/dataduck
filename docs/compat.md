# Databricks compatibility

What Duckbricks accepts, ignores or rejects compared with Databricks. Grouped by area; newest
entries at the top of each table.

## Catalogs and namespaces

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| Default catalog `main` | Default catalog `workspace`; `main` is rejected | Different | DuckDB reserves `main` as a database name (ADR 0002) |
| `system` catalog | Reserved; arrives in Phase 4 under an internal alias | Deferred | DuckDB reserves `system` (ADR 0002) |
| Default schema `default` | Every catalog has `default`; DuckLake's `main` schema also exists | Same + extra | ADR 0001 #5 |
| Catalog names | Lowercase letters, digits, `_`; max 63 characters | Stricter | Names double as DuckDB aliases and Postgres schema names |

## Compute

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| Unknown `node_type_id` | Mapped to `dbk.medium` with a warning | Lenient | SPEC.md section 6 |

## Jobs (planned, Phase 1)

| Databricks | Duckbricks | Kind | Why |
|---|---|---|---|
| Run `state` and `status` | Both returned | Same | ADR 0001 #7 |
| Queueing at `max_concurrent_runs` | Trigger skipped and logged, never queued | Different | SPEC.md section 7, ADR 0001 #7 |
| `runs/get-output` | Takes a task run id | Same | ADR 0001 #8 |
| `sql_task` parameter markers | DuckDB `$name` | Different | ADR 0001 #9 |
| Quartz day-of-week 1-7 (SUN=1) | Converted to cron 0-6; year field must be `*` | Same | ADR 0001 #6 |

## Engine notes

| Topic | Note |
|---|---|
| DuckDB 2.0 Python API | `DuckDBPyRelation.fetchone()` is gone; use `con.execute(sql).fetchone()` or `rel.fetchall()` |
| DuckLake data inlining | Inserts under 10 rows are inlined into the Postgres metadata until flushed (`ducklake_default_data_inlining_row_limit`) |
