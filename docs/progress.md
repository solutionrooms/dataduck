# Progress

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
