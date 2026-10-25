# 0011. Notebooks are marimo over the workspace volume

- Status: accepted
- Date: 2026-10-25

## Context

SPEC section 6 calls for notebooks with the platform's catalogs available, runnable as job tasks.

## Decision

- The `notebooks` container runs `marimo edit --headless` over the workspace volume on
  127.0.0.1:2718, protected by `DBK_NOTEBOOKS_TOKEN`.
- Notebooks import `from duckbricks.runtime import con, spark, dbutils`. The runtime asks the
  Unity Catalog API for the catalogs to attach, so the notebooks container never reads the
  control database.
- `notebook_task` runs a marimo notebook (or a Databricks-exported `.py` source file) as a script
  in the worker; `base_parameters` and job parameters become `dbutils.widgets`. In Databricks
  source files, `# MAGIC` cells (`%sql`, `%md`) are comments and do not run.
- A new user home gets `Getting started.py`.

## Consequences

- `.ipynb` and Databricks `.dbc` notebooks are not supported; export them as source.
- marimo SQL cells need pandas or polars; both ship in the images.
