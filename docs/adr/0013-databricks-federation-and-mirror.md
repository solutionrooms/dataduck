# 0013. Databricks federation and mirror: vended credentials, DuckDB reads, DuckLake copies

- Status: proposed (spike done)
- Date: 2026-10-25

## Context

The owner wants two things from a real Databricks workspace:

1. **Live:** query Databricks tables directly from Duckbricks.
2. **Mirror:** copy chosen schemas into Duckbricks for offline, faster use. Refreshed nightly,
   with a near-real-time option.

The workspace is on Azure with a Unity Catalog metastore that has external data access turned
on. The spike used one production schema: 130 managed Delta tables and 3 views, 46.8 GB of zstd
Parquet in 887 files. Every table has deletion vectors. None has change data feed, UniForm, row
filters or column masks. The three largest tables hold 25.4 GB; 88 tables are under 10 MB.

## Spike results

| Path | Result |
|---|---|
| UC credential vending (`POST /api/2.1/unity-catalog/temporary-table-credentials`, `READ`) | Needs `EXTERNAL USE SCHEMA` on the schema, which `ALL PRIVILEGES` does not include. Then it returns a 60-minute user-delegation SAS for the table's `abfss://` location |
| DuckDB `unity_catalog` extension, `ATTACH ... (TYPE unity_catalog, READ_ONLY true)` on its own | Lists tables, but scans fail with HTTP 401 from Azure storage: the extension does not apply vended Azure credentials (its docs only cover `AWS_REGION`) |
| Vended SAS → `azure` secret (`CONNECTION_STRING` with `BlobEndpoint`, `DfsEndpoint`, `SharedAccessSignature`; `SCOPE` = table location) → `delta_scan('abfss://...')` | Works. Deletion vectors are applied: counts match the SQL warehouse (34,861 and 16,455,830 rows) |
| The same scoped secret + the extension attach | Works: counts match, `AT (VERSION => n)` works, `READ_ONLY` blocks `INSERT`. Views are listed as tables but cannot be scanned (no storage location) |
| Mirror copy of a 1.34 GB table to local Parquet | 86 s, about 16 MB/s of source Parquet; 1.40 GB locally |
| Live queries on an 11.15 GB, 274.6 M-row table | `count(*)` 6.9 s; min/max of one date column 12.1 s; 30-day filter 4.8 s |
| SQL warehouse, Statement Execution API, `EXTERNAL_LINKS` + `ARROW_STREAM` | Works without the grant; serverless 2X-Small cold start about 10 s. Arrow is about 8.5x the Parquet size (6.0 MB against 0.7 MB), so it does not suit bulk copies |
| Iceberg REST (UniForm), Delta Sharing | Not tried: no table has UniForm, and Delta Sharing needs a share set up on the provider side per schema |

## Options

1. **Vended credentials + DuckDB reads** (extension attach for metadata, our own scoped Azure
   secrets for storage). No Databricks compute; reads compressed Parquet; needs the grant.
2. **Extension attach alone.** Fails on Azure today. Revisit when the extension applies Azure
   credentials itself; option 1 then shrinks to one `ATTACH`.
3. **SQL warehouse only.** Costs DBUs and moves about 8.5x the bytes. A full copy of the spike
   schema would move roughly 400 GB.
4. **Iceberg REST through UniForm.** Means changing production tables.
5. **Delta Sharing.** A share per schema on the provider side; no DuckDB extension exists.

## Decision

Option 1 for tables. Option 3 only for views and for tables the grant does not cover.

- **Connection.** A UC connection of type `DATABRICKS`
  (`POST /api/2.1/unity-catalog/connections`) holds `host` and `warehouse_id`. The token comes
  from `.env` (`DATABRICKS_TOKEN`); OAuth machine-to-machine comes later. The token stays in
  the API process.
- **Foreign catalog.** `POST /api/2.1/unity-catalog/catalogs` with `connection_name` and
  `options.catalog` (the remote name). The local name defaults to the remote name, so
  three-part names match Databricks and the same SQL and dbt models run in both. Duckbricks adds
  `options.duckbricks_mode` (`live` or `mirror`) and `options.duckbricks_mirror` (schemas and
  tables to include or exclude). Both are extension fields, recorded in compat.md.
- **Live mode.** The catalog is attached with the `unity_catalog` extension, `READ_ONLY`. Before
  a statement, the attaching process asks the API for credentials for the tables it references
  (`POST /api/duckbricks/connections/{name}/credentials`). The API vends them, caches each one
  until five minutes before it expires, and returns it. The process then creates a scoped
  `azure` secret per table location. SAS tokens live in memory only and are never written to
  the control database or logs. Views run on the SQL warehouse.
- **Mirror mode.** The catalog is a DuckLake catalog in the existing metadata database and
  object store. Users and warehouses attach it `READ_ONLY`; only the sync, which runs in the
  worker, writes. For each table, a sync compares the remote Delta version with the last synced
  version:
  - unchanged: skip it;
  - changed: in one DuckLake transaction, `CREATE OR REPLACE TABLE ... AS SELECT * FROM
    delta_scan(...)`, so readers see the old or the new copy, never half, and DuckLake keeps
    the old snapshot for time travel;
  - later: append only the new files when the Delta log shows only additions, and apply change
    data feed with `MERGE` where it is enabled.
  - Views are copied as tables through the SQL warehouse.
  Sync state lives in `sys.mirror.tables` and `sys.mirror.syncs`.
- **Refresh.** A nightly schedule per catalog (default 02:00, Quartz). The near-real-time option
  is a per-table poll interval: a cheap metadata check, with only changed tables copied. Live
  mode is always the way to see the very latest data.
- **Offline.** Mirror mode needs no network. Live mode fails with a clear "workspace
  unreachable" message.
- **Control.** `dbk mirror` (status, sync, mode), matching MCP tools, and freshness in the UI
  catalog page.

## Consequences

- Copying production data onto a laptop is a governance decision for the data owner. Mirror
  only the schemas that are needed, keep the disk encrypted, and prefer a service principal
  with narrow grants over a personal token.
- The spike schema is 46.8 GB. The first full sync takes about 50 minutes at the measured rate.
  Nightly syncs copy only tables whose version changed; a table rewritten daily is copied whole
  until the append-only and change-data-feed paths exist.
- Each schema needs `EXTERNAL USE SCHEMA`, granted explicitly.
- We depend on undocumented behaviour: the extension's scans pick up a scoped `azure` secret.
  An extension upgrade that handles Azure vending itself would remove that code.
- Tables with row filters or column masks cannot be vended. They would go through the SQL
  warehouse, which applies the masks for the token's identity.
