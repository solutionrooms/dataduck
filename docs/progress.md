# Progress

## 2026-10-25: Phases 1-4 and the web UI (branch `platform`)

The owner asked for a full working platform: workspace features, DuckDB compute, pipelines, jobs
and schedules compatible with bundles, dbt, and MCP-native control from Claude Code.

**Done**

- Phase 1: control DB schema (Alembic 0002), Unity Catalog API subset, Statement Execution API,
  Jobs API 2.2 with the worker queue (retries, run_if, cancel, timeouts, task values, dynamic
  values), Quartz schedules, `dbk` CLI, `dbk-mcp` server.
- Phase 2: bundle resolution and validation against the bundle schema, idempotent deploy and
  destroy, dbt task (full distribution 2.0.9 by default, ADR 0006), `dbt_check`, the shop_demo
  example, seed script and `make e2e`.
- Phase 3: file arrival triggers, SQL warehouses over Quack with auto-start and auto-stop
  (ADR 0010), marimo notebooks with a starter notebook (ADR 0011), `dbk sql transpile`,
  project skill `.claude/skills/duckbricks-bundle`.
- Phase 4: lineage from dbt column lineage and pipelines, `sys` system tables (ADR 0007),
  SQL pipelines with expectations and incremental streaming tables (ADR 0012), the web_events
  example.
- Web UI in the Databricks layout (ADR 0009) and the Workspace API.
- Warehouses and notebooks no longer read the control database (only api and worker do).

**Acceptance**

| Check | Result |
|---|---|
| `make check` | Pass: ruff, format, mypy (core, bundles, warehouses, mcp, ops), 110 unit and golden tests |
| `make test-integration` | Pass: 14 tests (jobs, session, warehouses, notebooks service) |
| `make e2e` | Pass: shop_demo validate → deploy → run (SUCCESS) → 90 gold rows → redeploy without duplicates → rerun without double-loading → lineage → destroy; web_events pipeline with expectations and incremental reload |
| MCP over stdio | 22 tools and 2 prompts listed; read tools, read-only rejection and destroy confirmation checked |
| File arrival | Baseline then a new file fired a run (trigger FILE_ARRIVAL) |

**In progress**

- Databricks mirror (cache a Databricks schema in a local DuckLake catalog): options discussed
  with the owner; waiting on which access paths their workspace allows.

**Next**

1. Mirror: probe UC credential vending and the SQL warehouse path against one table, then build
   a `DATABRICKS` connection + foreign catalog with `mode: mirror`.
2. UI unit tests; a Playwright smoke test in `make e2e`.
3. Phase 5 (stretch): wire compatibility with the real Databricks CLI.

**Blockers**

None.

## 2026-10-25: Phase 0 complete

**Done**

- Repo scaffold: uv project (Python 3.12), ruff, pytest, mypy (strict on `core` and `bundles`),
  Makefile (`up`, `down`, `check`, `test-integration`, `spike-a`, `spike-b`, `schema`), CLAUDE.md,
  VERSIONS.md.
- Compose stack: Postgres 18.6 (`control` and `lake` databases), SeaweedFS 4.48 with `lakehouse`
  and `landing` buckets created at start-up, API with `/health` and a token-protected
  `/api/duckbricks/status`. All ports bind to 127.0.0.1.
- Session factory (`duckbricks.core.session.open_session`) and catalog registry with Alembic
  migration 0001. Bootstrap creates `workspace`, `dev` and `prod` (ADR 0002: `main` is reserved
  in DuckDB).
- Spike A passes: two processes write different tables in one catalog concurrently and read each
  other's; concurrent appends to one shared table also succeed (ADR 0004).
- Spike B passes: dbt-oss 2.0.8 builds a model into the Postgres-backed DuckLake on SeaweedFS via
  `catalogs.yml`, using the system DuckDB 2.0.0 driver (ADR 0005). No fallback needed.
- `schemas/bundle_config_schema.json` generated with Databricks CLI v1.12.1.
- Quack server and client syntax verified locally (VERSIONS.md).

**Phase 0 acceptance**

| Criterion | Result |
|---|---|
| `make up` is healthy | Pass, including from empty volumes (bootstrap created all three catalogs) |
| `make check` is green | Pass: ruff, ruff format, mypy, 28 unit tests |
| Both spikes pass or have an ADR | Both pass; findings in ADRs 0003, 0004, 0005 |
| `VERSIONS.md` is filled in | Pass |

Integration tests (`make test-integration`, 4 tests) also pass against the stack.

**Next: Phase 1**

1. Control DB schema for jobs, job runs, task runs, task values and logs (Alembic 0002).
2. Unity Catalog API subset and Statement Execution API (in-process, ADR 0001 #13).
3. Jobs API 2.2 subset, task-run queue, worker with `WORKER_SLOTS`, heartbeat and reaper.
4. `dbk` CLI and the MCP server.

**Open decisions**

- dbt distribution for column lineage and the strict `dbt_check` (ADR 0005): decide when Phase 2
  starts.
- Internal alias for the `system` catalog (ADR 0002): decide when Phase 4 starts.

**Blockers**

None.
