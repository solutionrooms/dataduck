# 0008. `spark` in task code and notebooks is SQLFrame over DuckDB

- Status: accepted
- Date: 2026-10-25

## Context

SPEC section 6 promises a best-effort `spark` object for Python tasks and notebooks.

## Options

1. DuckDB's experimental `duckdb.experimental.spark`.
2. SQLFrame (`sqlframe.duckdb.DuckDBSession`), which translates the PySpark API to SQL via sqlglot.

Checked against DuckDB 2.0.0: the experimental API has no `DataFrame.agg`, no `writer.mode`, and
cannot take an existing connection, so it cannot see the platform's attached catalogs.
SQLFrame 4.7.0 accepts `DuckDBSession(conn=con)` and covers `table`, `sql`, `groupBy/agg`,
`write.saveAsTable`, `toPandas`.

## Decision

SQLFrame. `duckbricks.runtime.spark` wraps the session's `con`. SQLFrame's `spark.sql` only
resolves columns of tables it has already seen, so the runtime registers every qualified table a
query names before running it.

## Consequences

- Typical PySpark DataFrame code runs; RDDs, UDF-heavy code and Spark configs do not.
- `toPandas` needs pandas (installed with the notebooks extra in every image).
