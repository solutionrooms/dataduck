# Duckbricks: agent rules

## What this is
A local, single-user, Databricks-shaped platform on DuckDB 2.0, DuckLake and dbt v2.
SPEC.md is the source of truth. Read the relevant section before starting a task.

## How to work
- Work phase by phase (SPEC.md section 13). Finish a phase's acceptance criteria before starting the next.
- Run `make check` before every commit and keep it green. One logical change per commit.
- At the end of each session, update docs/progress.md: done, in progress, next, blockers.
- Record decisions in docs/adr/NNNN-title.md: context, options, decision, consequences.
- Track every Databricks field or behaviour we accept, ignore or reject in docs/compat.md.

## Fidelity
- Mirror Databricks API paths, field names and enum values exactly. When unsure how Databricks behaves, check the public docs and copy it.
- Be lenient: a valid but unsupported Databricks field produces a warning, never a crash.
- A task type we cannot run is rejected at deploy with a clear message.

## Verify, don't guess
- DuckDB 2.0, Quack, DuckLake, dbt v2 and the MCP SDK changed recently. Check current docs before using any syntax or option you are not certain of. Record versions in VERSIONS.md.
- If a dependency cannot do what SPEC.md assumes, stop, write an ADR with options, and use the fallback the spec names. Do not build large workarounds.

## Architecture rules
- API-first: the CLI, MCP server, scheduler and notebook helper only talk to the API.
- Only api and worker touch the control database.
- All DuckDB connections come from duckbricks.core.session.open_session. Never call duckdb.connect elsewhere.
- Read-only paths use READ_ONLY attachments, not keyword filtering.
- Keep object storage, run dispatch, scheduling and warehouse lifecycle behind small interfaces (one module each), so a second deployment target can replace Docker Compose later.

## Code
- Python 3.12, uv, ruff, type hints everywhere. Pydantic models for every API payload.
- Unit tests for pure logic: bundle resolution, interpolation, Quartz conversion.
- Golden-file tests for bundle resolution: input YAML to resolved JSON.
- Integration tests run against Docker Compose (pytest -m integration). End-to-end via `make e2e`.

## Safety
- No secrets in the repo. Use .env; commit only .env.example.
- Bind every port to 127.0.0.1.
- Do not delete Docker volumes or object storage buckets unless the task says so.
