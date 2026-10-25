# 0012. Lakeflow Declarative Pipelines: a SQL subset, incremental by file

- Status: accepted
- Date: 2026-10-25

## Context

SPEC Phase 4 adds `resources.pipelines` and `pipeline_task`. Databricks pipelines run on Spark
Structured Streaming; DuckDB has no streaming engine.

## Decision

- SQL sources only: `CREATE [OR REFRESH] [PRIVATE|TEMPORARY] MATERIALIZED VIEW`,
  `CREATE [OR REFRESH] STREAMING TABLE ... FROM STREAM read_files(...)`, `CREATE [TEMPORARY] [LIVE]
  VIEW`, `CONSTRAINT ... EXPECT (...) [ON VIOLATION DROP ROW | FAIL UPDATE]`, `COMMENT`, `LIVE.`
  prefixes. Python sources are rejected with a clear message.
- An update runs in a fresh process: it parses the library files, orders datasets by their
  references and builds them in order. Materialized views are recomputed. Streaming tables read only files not seen
  before (tracked per pipeline in the control database); a full refresh replaces them.
  `read_files` becomes `read_csv`/`read_json_auto`/`read_parquet` over an explicit file list, and
  `_metadata.file_path` becomes `filename`.
- Expectations are counted per update and reported in `flow_progress` events
  (`data_quality.expectations`, `dropped_records`), as in the Databricks event log.
- Pipelines API 2.0 subset: create, get, list, edit, delete, start update, stop, list updates,
  events. Updates run as job runs on the worker.

## Consequences

- `STREAM(table)` reads of another streaming table recompute rather than read incrementally.
- `APPLY CHANGES`, `AUTO CDC`, flows and sinks are not supported.
