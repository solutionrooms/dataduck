# 0010. SQL warehouses: one container, one DuckDB process per warehouse, Quack without TLS

- Status: accepted
- Date: 2026-10-25

## Context

SPEC section 6 maps SQL warehouses to long-running DuckDB processes serving Quack. The rules
also say only the API and the worker read the control database, and every port binds to 127.0.0.1.

## Decision

- The `warehouse` container runs an agent (`duckbricks.warehouses.agent`, port 9400 on the
  Compose network only) that starts and stops `duckbricks.warehouses.server` processes. Each
  warehouse serves Quack on one of ports 9500-9509, published on 127.0.0.1.
- Quack runs with `disable_ssl => true` and `allow_other_hostname => true`: traffic stays on the
  private Compose network or 127.0.0.1, and Quack's token authenticates every call.
- The API passes the catalog registry when it starts a warehouse and issues
  `ATTACH IF NOT EXISTS` for each catalog before each statement, so catalogs created later are
  visible. Warehouses never read the control database and do not attach `sys` (ADR 0007).
- Statements with a `warehouse_id` run on that warehouse via `quack_query`; parameters are
  inlined as typed literals because Quack takes SQL text. A stopped warehouse starts on demand,
  as on Databricks. Auto-stop runs on the scheduler loop.
- `cluster_size` maps to node types (2X-Small/X-Small → dbk.small, Small/Medium → dbk.medium,
  Large and up → dbk.large). Photon, serverless, scaling and channel fields are stored and
  ignored with a warning. A stopped "Starter Warehouse" exists after first boot.

## Consequences

- At most ten running warehouses.
- Results come back through the API (JSON_ARRAY, INLINE), so large results are capped by
  `row_limit` like in-process statements.
- Clients that speak Quack can connect to `quack:127.0.0.1:<port>` directly with the warehouse token.
