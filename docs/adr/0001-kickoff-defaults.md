# 0001. Kickoff decisions (defaults accepted)

- Status: accepted
- Date: 2026-10-25

## Context

The kickoff review of SPEC.md (Phases 0 and 1) raised fifteen points that were wrong, ambiguous or
blocked. The owner accepted the recommended default for every one ("use defaults").

## Decision

| # | Topic | Decision |
|---|---|---|
| 1 | Docker | Owner starts Docker Desktop; no workaround. |
| 2 | Repo name | Repo and folder are `dataduck`; the Python package stays `duckbricks`. |
| 3 | Post-2026 facts | Verified against current docs and empirically; recorded in VERSIONS.md. |
| 4 | `system` catalog | DuckDB reserves `system`. Phase 4 stores it under another name and the Unity Catalog API presents it as `system`. See ADR 0002. |
| 5 | Default schema | Every catalog gets a `default` schema (Databricks' default), and sessions `USE <catalog>.default`. DuckLake's own `main` schema also exists. |
| 6 | Quartz cron | Remap day-of-week numbers (Quartz 1-7, SUN=1 to cron 0-6, SUN=0); reject a year field unless it is `*`. |
| 7 | Run state | Runs return both `state` (life_cycle_state/result_state) and `status`. Triggers at `max_concurrent_runs` are skipped, not queued; logged in compat.md. |
| 8 | `runs/get-output` | Takes a task run id, as in Databricks. |
| 9 | `sql_task` parameters | DuckDB `$name` markers; multi-statement files are split and each statement binds only the parameters it uses. |
| 10 | Queue granularity | The queue holds task runs. Finishing a task enqueues its ready dependents in the same transaction. |
| 11 | Worker concurrency | `WORKER_SLOTS` setting, default 4. |
| 12 | Workspace API | Phase 2, alongside bundle deploy: `list`, `get-status`, `import`, `export`, `mkdirs`, `delete`. |
| 13 | Statement state | Phase 1 keeps statement state in the API process's memory; supports `on_wait_timeout` `CONTINUE`/`CANCEL` and `INLINE` disposition only. |
| 14 | Addresses | Hosts and ports come from `DBK_*` settings; containers override them in Compose. Spikes run in containers on the Compose network. |
| 15 | Auth | One static bearer token (`DBK_TOKEN`) checked by the API; `/health` is open. |

## Consequences

Each decision is reflected in code or in docs/compat.md as the relevant phase lands.
