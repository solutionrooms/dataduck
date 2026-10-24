# Duckbricks — Build Spec

2026-10-24 · Jon Scott

> Exported from https://claude.ai/artifact/4VnwAvfhGukfLhw9abHY94 (rev 36) on 2026-10-25. The doc is upstream; re-export if it changes.

## 1. Purpose and scope

Duckbricks is a single-user, local, Databricks-shaped data platform. It runs on DuckDB 2.0, DuckLake and dbt v2, and it can be operated end to end from Claude Code through MCP. It is a hobby and learning build: faithful to Databricks concepts and API shapes where that is cheap, pragmatic everywhere else.

The owner is an experienced Databricks practitioner (Unity Catalog, DLT, dbt, Asset Bundles). When Databricks behaviour is the question, copy what Databricks does and record it in `docs/compat.md`.

**Goals**

1. **One command.** `make up` (Docker Compose) brings the whole platform up on a laptop.
2. **Databricks-shaped API.** A REST subset of Jobs 2.2, Statement Execution, Workspace and Unity Catalog, using the same paths, field names and enum values.
3. **Bundle-compatible config.** A `databricks.yml` using the supported subset deploys and runs without YAML changes. Code may need dialect changes.
4. **DuckLake is the lakehouse.** A `catalog.schema.table` namespace shared by every compute process.
5. **dbt v2 is first-class.** dbt runs as a job task, and its column-level lineage is captured.
6. **MCP-native.** Every capability is reachable through an MCP server, so Claude Code can build, deploy, run and inspect.

**Non-goals for v1**

- Distributed execution or a Spark runtime. Each compute process is one DuckDB on one machine.
- Running Databricks SQL or PySpark unchanged. Task code is DuckDB SQL or Python; Spark compatibility is best effort.
- Wire compatibility with the real Databricks CLI. This is the Phase 5 stretch goal.
- Multi-user auth, grants, row or column security.
- Production hardening and high availability. Cloud targets (Cloudflare, MotherDuck) are deferred; the adapter rule in section 14 keeps them open.
- A custom web UI beyond notebooks and the DuckDB UI.

## 2. Concept mapping

Each Databricks concept maps to one Duckbricks component. Spark-only settings are accepted and ignored with a warning, never rejected.

| Databricks | Duckbricks | Notes |
|---|---|---|
| Workspace | File tree on a volume + Workspace API subset | Paths mirror `/Users/<user>/...` |
| Unity Catalog | Catalog registry in the control DB; each catalog is one DuckLake | `catalog.schema.table` namespace |
| Delta tables | DuckLake tables (Parquet on SeaweedFS, metadata in Postgres) | Snapshots and time travel come from DuckLake |
| System tables | `system` catalog: views over the control DB + lineage tables | Phase 4 |
| Job / all-purpose cluster | DuckDB process with a node type (memory, threads) | `num_workers`, `spark_version` ignored with a warning |
| SQL warehouse | Long-running DuckDB serving the Quack protocol | Phase 3; until then statements run in the API process |
| Statement Execution API | `POST /api/2.0/sql/statements` | Row-limited `JSON_ARRAY` results |
| Lakeflow Jobs | Jobs API 2.2 subset + Postgres run queue + worker | Same task model and states |
| Job schedules | Scheduler loop in the API, Quartz cron | Development mode pauses schedules |
| Lakeflow Declarative Pipelines | SQL-only subset: materialized views, streaming tables, expectations | Phase 4 |
| Asset Bundles | `dbk bundle` reading `databricks.yml` | Validated against the real bundle JSON schema |
| dbt task | dbt-oss v2 in the worker; `catalogs.yml` points at DuckLake | Lineage lands in `system` |
| Notebooks | marimo `.py` notebooks + `duckbricks` helper | Phase 3 |
| `dbutils` | Shim: `widgets.get`, `jobs.taskValues` | Phase 3 |
| Databricks CLI | `dbk` CLI with the same command shapes |  |
| Databricks MCP | Duckbricks MCP server | Thin client over the API |

## 3. Architecture

Duckbricks is API-first: one control-plane API owns all platform state, and the CLI, MCP server, scheduler and notebook helper are thin clients of it.

> Diagram: "Duckbricks architecture · clients, control plane, compute, shared state" lives in the source doc (not exported).

Clients only call the API. The API queues runs for the worker and sends SQL to warehouses, and every DuckDB process attaches the same DuckLake.

| Component | Runs as | Responsibility | Talks to |
|---|---|---|---|
| `postgres` | Container | Two databases: `control` (platform state, run queue) and `lake` (DuckLake metadata) |  |
| `seaweedfs` | Container | S3-compatible storage. Buckets: `lakehouse` (table data), `landing` (raw files) |  |
| `api` | Container | FastAPI control plane: REST API, bundle apply, scheduler loop, catalog reads, statement execution | postgres, seaweedfs, warehouse |
| `worker` | Container | Claims task runs from the queue and runs each task in a fresh subprocess with its own DuckDB session. Holds the dbt-oss binary | postgres, seaweedfs |
| `warehouse` | Container, Phase 3 | Long-running DuckDB serving Quack, catalogs attached | postgres, seaweedfs |
| `notebooks` | Container, Phase 3 | marimo server over the workspace volume | api, warehouse |
| `dbk` CLI | Host | Resolves and validates bundles locally, then calls the API | api |
| MCP server | Host, stdio | Tools for Claude Code | api |

**Rules**

- Only `api` and `worker` read or write the `control` database.
- Every DuckDB connection comes from one session factory (`duckbricks.core.session.open_session`), so extensions, secrets and catalog attachments are identical everywhere.
- All ports bind to `127.0.0.1`. Credentials live in `.env`; only `.env.example` is committed.

## 4. Tech stack and repo layout

A Python 3.12 monorepo managed with uv: one package, several entry points.

| Concern | Choice | Note |
|---|---|---|
| Language and packaging | Python 3.12+, uv | `uv run` everywhere |
| API | FastAPI + Pydantic v2 | Payload models use Databricks field names |
| Control DB | SQLAlchemy 2 Core + Alembic | Migrations from day one |
| Engine | DuckDB 2.0 LTS (Python) | Extensions: `ducklake`, `httpfs`, `postgres`; Quack for warehouses |
| Lakehouse | DuckLake 1.x | Metadata in Postgres, data on SeaweedFS via its S3 API (MinIO's open-source edition is no longer maintained) |
| Transformations | dbt v2, open-source distribution (dbt-oss) | Built-in DuckDB adapter; pins its own DuckDB build |
| Scheduling | croniter + a Quartz-to-cron converter |  |
| CLI | Typer + Rich | Entry point `dbk` |
| MCP | Official MCP Python SDK, stdio transport | Entry point `dbk-mcp` |
| Notebooks | marimo | Phase 3 |
| Bundle schema | Output of `databricks bundle schema` | Vendored in `schemas/` |
| Quality | ruff (lint + format), pytest, mypy on `core` and `bundles` | `make check` |
| Containers | Docker Compose |  |

**Version rule.** `VERSIONS.md` records the exact DuckDB version, DuckLake format version, dbt build and the DuckDB version it embeds, and the Databricks CLI version used to generate the schema. `dbk status` prints these and warns when worker, warehouse and dbt disagree.

```text
duckbricks/
├── CLAUDE.md                 # agent rules (section 14)
├── SPEC.md                   # this document
├── VERSIONS.md
├── Makefile                  # up, down, check, e2e
├── docker-compose.yml
├── .env.example
├── .mcp.json                 # registers the MCP server for Claude Code
├── .claude/settings.json     # tool permissions
├── pyproject.toml
├── src/duckbricks/
│   ├── core/                 # config, models, session factory, catalog registry
│   ├── bundles/              # load, merge, interpolate, validate (CLI + MCP)
│   ├── api/                  # FastAPI app, one router per Databricks API family
│   ├── scheduler/
│   ├── worker/               # queue claim, heartbeat, task executors
│   ├── runtime/              # imported by task code: con, spark, dbutils shim
│   ├── cli/
│   └── mcp/
├── migrations/               # Alembic
├── docker/                   # Dockerfiles: api, worker, warehouse
├── config/node_types.yml
├── schemas/bundle_config_schema.json
├── examples/bundles/shop_demo/
├── scripts/seed_landing.py
├── docs/adr/  docs/compat.md  docs/progress.md
└── tests/unit/  tests/integration/  tests/e2e/
```

## 5. Storage and catalogs

Every catalog is a DuckLake with metadata in Postgres and data under `s3://lakehouse/<catalog>/` on SeaweedFS. Any DuckDB process can attach it, and concurrent writers coordinate through Postgres.

**Catalog registry.** Table `catalogs` in the control DB: name, comment, metadata location, data path, created_at. Bootstrap creates `main`, `dev` and `prod`. The name `system` is reserved and read-only.

**Metadata isolation.** One Postgres schema per catalog inside the `lake` database. If DuckLake's attach options make that awkward, use one database per catalog. Verify, then record the choice in an ADR.

**Session factory.** `open_session(node_type, read_only=False, default_catalog=None, default_schema=None)`:

1. Connect with `memory_limit` and `threads` taken from the node type.
2. Load `ducklake`, `httpfs` and `postgres`.
3. Create the S3 secret for SeaweedFS: endpoint, path-style URLs, no SSL on the compose network.
4. `ATTACH` every registered catalog under its own name, `READ_ONLY` when requested.
5. `USE` the default catalog and schema.

Nothing else in the codebase calls `duckdb.connect`.

**Unity Catalog API subset.** Implemented over DuckDB's `information_schema` on an attached session.

- Read: `GET /api/2.1/unity-catalog/catalogs`, `/schemas?catalog_name=`, `/tables?catalog_name=&schema_name=`, `/tables/{full_name}` (columns and types).
- Write: `POST /api/2.1/unity-catalog/catalogs` and `/schemas`. Bundle `resources.schemas` uses the same code path.

**`system` catalog (Phase 4).** `system.jobs.*` are read-only views over the control DB through DuckDB's `postgres` extension. `system.lineage.table_lineage` and `system.lineage.column_lineage` are DuckLake tables loaded from dbt output.

**Write conflicts.** Two writers committing to the same table can conflict. Use DuckLake's built-in commit-retry settings if they exist; otherwise retry the statement in the executor up to 3 times with backoff, then fail the task with a message naming the table.

## 6. Compute and SQL execution

A cluster is a DuckDB process with a node type. Job tasks get a fresh one per task run; SQL warehouses are long-running ones served over Quack.

**Node types.** Defined in `config/node_types.yml`, for example:

| Node type | memory_limit | threads |
|---|---|---|
| `dbk.small` | 2 GB | 2 |
| `dbk.medium` | 4 GB | 4 |
| `dbk.large` | 8 GB | 8 |

An unknown Databricks `node_type_id` maps to `dbk.medium` with a warning.

**Job compute.** `job_clusters[].new_cluster` and task-level `new_cluster` resolve to a node type. `num_workers`, `autoscale`, `spark_version`, `spark_conf` and cloud attributes are accepted and ignored with a warning. `existing_cluster_id` runs on job compute with a warning.

**Isolation.** Phase 1 runs each task as a subprocess inside the worker container. Container-per-task through the Docker SDK is an optional Phase 3 upgrade behind a config flag.

**Statement Execution API.**

- `POST /api/2.0/sql/statements`: `statement`, `warehouse_id`, `catalog`, `schema`, `parameters`, `wait_timeout`, `row_limit`.
- `GET /api/2.0/sql/statements/{id}` and `POST .../cancel`.
- Results in `JSON_ARRAY` format with `manifest.schema`. Default 1,000 rows, hard cap 10,000.
- Phase 1 executes in a session inside the API process. Phase 3 routes to the named warehouse.

**Warehouses (Phase 3).** `/api/2.0/sql/warehouses`: list, get, create, start, stop.

- Each warehouse is a container running DuckDB with the Quack extension serving, catalogs attached through the session factory, and a token from `.env`.
- Clients attach with `ATTACH 'quack:<host>' AS wh` or call `quack_query`. Check the Quack docs for the exact server start, auth and TLS options before writing this.
- Auto-stop after `auto_stop_mins` idle.
- All Quack access sits behind one client module, so execution can fall back to in-process sessions if Quack misbehaves.

**Read-only enforcement.** Read-only callers, such as the MCP `sql_query` tool, get a session with every catalog attached `READ_ONLY`. Never rely on keyword filtering. If a warehouse cannot guarantee read-only, run read-only queries in-process.

## 7. Jobs, runs and scheduling

Jobs follow the Databricks Jobs API 2.2 shapes. Runs are queued in Postgres, claimed by workers with `SELECT ... FOR UPDATE SKIP LOCKED`, and executed task by task in dependency order.

**Endpoints.** `/api/2.2/jobs/` `create`, `reset`, `get`, `list`, `delete`, `run-now`; `/api/2.2/jobs/runs/` `get`, `list`, `cancel`, `get-output`.

**Task types**

| Task type | Phase | Behaviour |
|---|---|---|
| `sql_task` | 1 | `file.path` only: a multi-statement `.sql` file run on job compute; Phase 3 runs it on `warehouse_id` when set. `parameters` bind as named parameters |
| `spark_python_task` | 1 | Runs `python_file` in a subprocess with `parameters` as argv. `duckbricks.runtime` exposes `con`; `spark` arrives in Phase 2 |
| `dbt_task` | 2 | See section 9 |
| `run_job_task` | 2 | Triggers another job, waits, takes its result |
| `notebook_task` | 3 | Runs a marimo `.py` notebook as a script; `base_parameters` readable through `dbutils.widgets` |
| `condition_task` | 3 | Compares task values; downstream tasks branch on `outcome` |
| `pipeline_task` | 4 | Triggers a declarative pipeline update |
| Anything else |  | Rejected at deploy with a clear message, because it cannot run |

**Run semantics**

- `depends_on` builds a DAG. `run_if` supports `ALL_SUCCESS` (default) and `ALL_DONE` in Phase 1, the rest later.
- States: `state.life_cycle_state` (`PENDING`, `RUNNING`, `TERMINATING`, `TERMINATED`, `SKIPPED`, `INTERNAL_ERROR`) and `state.result_state` (`SUCCESS`, `FAILED`, `TIMEDOUT`, `CANCELED`, `UPSTREAM_FAILED`).
- Per task: `max_retries`, `min_retry_interval_millis`, `retry_on_timeout`, `timeout_seconds`.
- Job `parameters` with defaults, overridable at `run-now`. Dynamic references: `{{job.id}}`, `{{job.run_id}}`, `{{job.parameters.<name>}}`, `{{task.name}}`, `{{job.start_time.iso_date}}`.
- Task values (`dbutils.jobs.taskValues.set` and `get`) stored per run in the control DB, Phase 3.
- `max_concurrent_runs` defaults to 1. A trigger at the limit is skipped and logged; no queueing in v1.
- Logs: stdout and stderr per task run go to `/data/logs/<run_id>/<task_key>.log`. `runs/get-output` returns the last 64 KB plus `error` and `error_trace`.
- Workers heartbeat every 10 s. A reaper marks runs `INTERNAL_ERROR` when the heartbeat is over 60 s stale. Cancel sets a flag; the executor kills the subprocess.

**Scheduling**

- `schedule.quartz_cron_expression`, `timezone_id`, `pause_status`. Convert Quartz (seconds field, `?`) to five-field cron. Reject non-zero seconds and Quartz-only syntax (`L`, `W`, `#`) with a clear message.
- The scheduler loop runs in the API every 15 s. Missed runs are not backfilled, matching Databricks.
- `trigger.periodic` in Phase 3. `trigger.file_arrival` in Phase 4, polling an object-store prefix.
- Bundles deployed in `mode: development` get paused schedules and triggers.

## 8. Bundles

`dbk bundle` resolves a `databricks.yml` locally the way the Databricks CLI does and validates it against the real bundle JSON schema. It then sends the resolved config and source files to the API, which applies them idempotently. Databricks renamed Asset Bundles to Declarative Automation Bundles in March 2026; the file format is the same.

**Supported subset (v1)**

- `bundle.name`, `include` (globs), `sync.include` / `sync.exclude`, `workspace.root_path`.
- `variables`: `default`, `description`. `lookup` is an error.
- `targets`: `default`, `mode`, `variables`, `workspace`, `presets.name_prefix`, per-target `resources` overrides.
- `resources.jobs`: tasks, `job_clusters`, `schedule`, `trigger`, `parameters`, `max_concurrent_runs`, `tags`. Notifications are accepted and ignored with a warning.
- `resources.schemas`: `catalog_name`, `name`, `comment`.
- `resources.pipelines` in Phase 4.
- Anything else that is valid per the schema is a warning ("unsupported, ignored"). Anything invalid per the schema is an error.

**Interpolation.** `${var.*}`, `${bundle.name}`, `${bundle.target}`, `${workspace.current_user.userName}`, `${workspace.current_user.short_name}`, `${workspace.root_path}`, `${workspace.file_path}`. `${resources.jobs.<key>.id}` resolves server-side at apply time. Variables are overridable with `--var name=value` and `BUNDLE_VAR_<name>`.

**Modes.**

- `development`: resource names prefixed `[dev <short_name>] `, schedules and triggers paused, default root path `~/.bundle/${bundle.name}/${bundle.target}`.
- `production`: no prefix, schedules as written.

**Validation pipeline.**

1. JSON-schema check against `schemas/bundle_config_schema.json`, generated with `databricks bundle schema`. Install the Databricks CLI only to generate this file; it is not a runtime dependency. Record its version in `VERSIONS.md`.
2. Semantic checks: supported task types, acyclic dependencies, referenced files exist, every variable resolved.
3. Output: the resolved config (`dbk bundle validate -o json`) and a list of warnings.

**Deploy.** `POST /api/duckbricks/bundles/deploy` takes the resolved config plus a tar.gz of synced files.

- Files replace `<root_path>/files` in the workspace volume.
- Resources are upserted, keyed by (bundle name, target, resource key). A redeploy updates in place and never duplicates. Resources removed from the config are deleted.
- Deployment state (resource key, id, config hash, deployed_at) goes in `bundle_deployments`.
- Everything happens in one transaction; any failure rolls back.
- The response lists what was created, updated and deleted.

**Other verbs.**

- `destroy` deletes the target's resources and files. It needs `--auto-approve` in the CLI and `confirm=true` in MCP.
- `run <job_key>` triggers `run-now` and streams task states until terminal; `--no-wait` returns the run id.
- `summary` lists deployed resources with ids.

**Code compatibility.** YAML ports from Databricks unchanged; code generally does not. SQL files are DuckDB SQL. Phase 3 adds `dbk sql transpile --from databricks` using sqlglot as a porting aid.

## 9. dbt integration

dbt runs as `dbt_task` inside the worker, using the open-source dbt v2 distribution (dbt-oss). It ships a built-in DuckDB adapter and attaches DuckLake through `catalogs.yml`.

**Image.** The worker image installs the dbt-oss v2 binary. dbt downloads and caches its DuckDB driver on first run; bake that into the image at build time so runs are offline and reproducible.

**Task fields.** `project_directory`, `commands` (each starting with `dbt`), `profiles_directory` (optional), `catalog`, `schema`. `warehouse_id` is ignored.

**Generated config.** For each run, the executor writes into a temp dir:

- `profiles.yml`: `type: duckdb`, a scratch local database file, and whatever settings dbt v2 needs for S3 credentials and Postgres access.
- `catalogs.yml`: one `ducklake` entry per registered catalog, pointing at the same Postgres metadata and `s3://lakehouse/<catalog>/` path the session factory uses.
- The `use_catalogs_v2` flag. If the project's `dbt_project.yml` lacks it, set it by flag or env if dbt allows; otherwise fail with a clear message. Verify which works.
- The task's `catalog` and `schema` become the target defaults.

**Fallback.** If dbt v2 cannot attach a Postgres-backed DuckLake with S3 credentials, write an ADR and fall back to dbt-duckdb (Python, v1) with the profile `attach` block. Select it with `dbt.engine: v2` or `v1` in config. Do not build a large workaround.

**Lineage (Phase 4).** After each successful `dbt_task`, run `dbt compile --generate-info-schema --static-analysis strict`. Load `target/info_schema/v1/*.parquet` (models and column lineage) into `system.lineage`. Keep the run's dbt artifacts in its log directory.

**Static analysis as a check.** `POST /api/duckbricks/dbt/compile` runs a strict static-analysis compile on the worker for a workspace project path and returns diagnostics. MCP exposes it as `dbt_check`.

**Version alignment.** dbt v2 pins its own DuckDB build. At worker start, record dbt's DuckDB version (for example with `dbt show --inline "select version()"`) and the DuckLake format version. Compare them with the session factory's DuckDB and surface mismatches in `dbk status`. Pin the platform's DuckDB to dbt's where possible.

## 10. MCP server and Claude Code

The MCP server is a thin, stateless client of the API. Claude Code runs it over stdio. Read-only and mutating tools are separate, so they can be permissioned separately.

**Implementation.** Official MCP Python SDK, module `duckbricks.mcp`, entry point `dbk-mcp`. Configured from env `DBK_HOST` and `DBK_TOKEN`. It has no database or file access, except reading local bundle directories for validate and deploy through `duckbricks.bundles`. Registered in the repo's `.mcp.json` at project scope.

| Tool | Kind | Phase | Purpose |
|---|---|---|---|
| `platform_status` | Read | 1 | Health, versions, catalogs, workers, warehouses, version mismatches |
| `catalog_browse` | Read | 1 | Catalogs, schemas, tables, with an optional name filter |
| `table_describe` | Read | 1 | Columns, types, row count, latest snapshot; optional sample of up to 20 rows |
| `sql_query` | Read | 1 | Read-only SQL. Default 100 rows, max 1,000, 60 s timeout |
| `jobs_list`, `job_get` | Read | 1 | Job definitions |
| `runs_list`, `run_get` | Read | 1 | Task states, errors, last 200 log lines of each failed task |
| `bundle_validate` | Read | 2 | Resolve and validate a local bundle; returns warnings and a summary |
| `dbt_check` | Read | 2 | Strict static-analysis compile; returns diagnostics |
| `lineage_get` | Read | 4 | Upstream and downstream for a table or column |
| `sql_execute` | Write | 1 | DDL and DML on a named catalog |
| `catalog_create`, `schema_create` | Write | 1 | Create catalogs and schemas |
| `job_run_now` | Write | 1 | Trigger by job id or bundle job key; optional wait |
| `run_cancel` | Write | 1 | Cancel a run |
| `bundle_deploy` | Write | 2 | Deploy a local bundle to a target |
| `bundle_destroy` | Write | 2 | Requires `confirm=true` |
| `warehouse_start`, `warehouse_stop` | Write | 3 | Warehouse lifecycle |

**Output rules**

- Compact: markdown tables or small JSON. Every list is capped or paginated, with an explicit "truncated, N more" line.
- Errors say what failed and the likely fix. Stack traces only with `verbose=true`.
- Never return secrets or connection strings.
- Deploy and other writes report what changed.

**Claude Code setup**

- `.claude/settings.json` allows every read tool and asks before every write tool.
- Phase 3 adds a project skill, `.claude/skills/duckbricks-bundle/SKILL.md`. It covers bundle layout, supported task types, the medallion naming used here, DuckDB SQL dialect notes, and the working loop: `bundle_validate` → `dbt_check` → `bundle_deploy` → `job_run_now` → `run_get` → `sql_query`.
- Optional MCP prompts: `new_medallion_bundle`, `investigate_failed_run`.

## 11. CLI

`dbk` mirrors the Databricks CLI's command shapes, so muscle memory carries over. Output is Rich tables by default and `-o json` everywhere. Configuration comes from `DBK_HOST` (default `http://127.0.0.1:8080`) and `DBK_TOKEN`, or profiles in `~/.dbk/config`.

```text
dbk status
dbk bundle init shop                       # scaffold from examples/
dbk bundle validate [-t dev] [-o json]
dbk bundle deploy   [-t dev] [--var k=v]
dbk bundle run <job_key> [-t dev] [--no-wait] [--param k=v]
dbk bundle summary  [-t dev]
dbk bundle destroy  [-t dev] --auto-approve
dbk jobs list | get <id> | run-now <id>
dbk runs list [--job-id <id>] | get <run_id> | cancel <run_id>
dbk runs logs <run_id> [--task <key>]
dbk sql "<statement>" [--catalog c --schema s] [--warehouse <id>]
dbk catalogs list | create <name>
dbk schemas list <catalog> | create <catalog>.<schema>
dbk tables list <catalog>.<schema> | describe <catalog>.<schema>.<table>
dbk warehouses list | start <id> | stop <id>     # Phase 3
dbk sql transpile --from databricks <file>      # Phase 3
dbk ui                                          # DuckDB UI with every catalog attached
```

## 12. Example bundle and end-to-end test

`examples/bundles/shop_demo` is both the demo and the acceptance test. One job ingests raw CSVs to bronze, builds silver and gold with dbt, then checks quality.

**Seed data.** `scripts/seed_landing.py` writes deterministic synthetic data with a fixed random seed: customers, products, and 90 days of orders, with a few dirty rows. Files land as daily CSVs under `s3://landing/shop/orders/dt=YYYY-MM-DD/`.

**Bundle**

- Targets: `dev` (default, `mode: development`, catalog `dev`) and `prod` (catalog `prod`), via a `catalog` variable.
- `resources.schemas`: `bronze`, `silver`, `gold`.
- Job `shop_daily`, scheduled daily at 06:00 Europe/London (paused in dev):
  1. `ingest_bronze` (`sql_task`): appends new landing files to `bronze.orders_raw` with file name and load timestamp, skipping files already loaded.
  2. `transform` (`dbt_task`, after 1): `dbt build` produces `silver.orders`, `silver.customers` and `gold.daily_revenue`, with unique, not_null and accepted_values tests.
  3. `quality_checks` (`spark_python_task`, after 2): asserts gold has rows and that total revenue matches silver within 0.01.

**`make e2e`**

1. `make up`, then run the seed script.
2. `dbk bundle validate -t dev`, then `dbk bundle deploy -t dev`.
3. `dbk bundle run shop_daily -t dev` and assert `SUCCESS`.
4. Query `dev.gold.daily_revenue` and assert expected row count.
5. Redeploy and assert no duplicate jobs.
6. Run again and assert bronze did not double-load.
7. `dbk bundle destroy -t dev --auto-approve`.

## 13. Delivery phases

Five phases, each ending in a demo-able system with `make check` green. Do not start a phase until the previous phase's acceptance criteria pass. Phase 0 spikes the riskiest dependencies first.

### Phase 0: Scaffold and spikes

- [x] Repo layout, uv project, ruff, pytest, mypy, Makefile, `CLAUDE.md`, `VERSIONS.md`, `docs/progress.md`
- [x] Compose: postgres, seaweedfs (buckets created at start-up), api with a health endpoint
- [x] Session factory and catalog bootstrap (`main`, `dev`, `prod`) — `workspace` replaces `main` (docs/adr/0002)
- [x] Spike A: two separate processes write different tables in the same DuckLake catalog at the same time, and each reads the other's table
- [x] Spike B: dbt-oss v2 builds one model into a Postgres-backed DuckLake on SeaweedFS. If it cannot, write the ADR and choose the fallback now
- [x] Generate `schemas/bundle_config_schema.json`

**Acceptance:** `make up` is healthy, `make check` is green, both spikes pass or have an ADR, and `VERSIONS.md` is filled in.

### Phase 1: Catalog, SQL, jobs core, MCP

- [ ] Control DB schema and Alembic migrations
- [ ] Unity Catalog API subset; Statement Execution API (in-process)
- [ ] Jobs API 2.2 subset, queue, worker, heartbeat, reaper; `sql_task` and `spark_python_task` (`con` only); retries, timeouts, cancel, logs
- [ ] `dbk`: `status`, `sql`, `catalogs`, `schemas`, `tables`, `jobs`, `runs`
- [ ] MCP server with every Phase 1 tool

**Acceptance:** a two-task job created from JSON runs to `SUCCESS`. A failing first task gives `FAILED` with error text and `UPSTREAM_FAILED` downstream. Two jobs writing different tables concurrently both succeed. From Claude Code, `catalog_browse`, `sql_query` and `job_run_now` work through MCP.

### Phase 2: Bundles, dbt, example

- [ ] Bundle resolve and validate; deploy, destroy, run, summary; deployment state
- [ ] `dbt_task` with dbt-oss v2 and `catalogs.yml` (or the documented fallback); `dbt_check` endpoint
- [ ] `spark` object in the runtime (decision in section 16)
- [ ] `run_job_task`
- [ ] Example bundle, seed script, `make e2e`
- [ ] MCP: `bundle_validate`, `bundle_deploy`, `bundle_destroy`, `dbt_check`

**Acceptance:** `make e2e` passes. Redeploy is idempotent. An unsupported field gives a warning, not a failure. Claude Code takes the example from validate to a queried gold table using only MCP tools.

### Phase 3: Schedules, warehouses, notebooks

- [ ] Scheduler: Quartz conversion, time zones, development-mode pause, `trigger.periodic`
- [ ] Warehouses over Quack: start, stop, auto-stop; Statement Execution routed to warehouses
- [ ] marimo service and `duckbricks.connect()` helper; `notebook_task`; `dbutils` shim; `condition_task`
- [ ] Optional container-per-task isolation
- [ ] Project skill `duckbricks-bundle`; `dbk sql transpile`

**Acceptance:** a schedule fires within one scheduler tick of its time. An idle warehouse stops after `auto_stop_mins`. A notebook queries gold through a warehouse. A `notebook_task` passes a parameter in and a task value downstream.

### Phase 4: Lineage, system tables, pipelines

- [ ] `system` catalog: jobs and runs views, dbt lineage tables; lineage API and `lineage_get`
- [ ] Declarative pipelines SQL subset: `CREATE OR REFRESH MATERIALIZED VIEW` (full refresh), `STREAMING TABLE` from files (incremental by file), `CONSTRAINT ... EXPECT ... ON VIOLATION DROP ROW` or `FAIL UPDATE`; `resources.pipelines`; `pipeline_task`
- [ ] `trigger.file_arrival`

**Acceptance:** `lineage_get` on a gold column returns its silver and bronze sources. A pipeline with an expectation drops bad rows and reports the count.

### Phase 5 (stretch): Wire compatibility

- [ ] Point the real Databricks CLI at `http://127.0.0.1:8080` and run `bundle deploy` with the direct deployment engine and `--debug`. Implement each endpoint it calls until deploy and run succeed.
- [ ] Work from public API docs and the CLI's debug output against Duckbricks itself. Do not record traffic from a real company workspace.

**Acceptance:** the unmodified example bundle deploys and runs with the real Databricks CLI.

## 14. CLAUDE.md

Copy the block below into `CLAUDE.md` at the repo root. These are the agent's standing rules.

```markdown
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
```

## 15. Kickoff prompt

Start Claude Code in an empty `duckbricks/` directory that contains this spec as `SPEC.md` and section 14 as `CLAUDE.md`, then paste:

```text
Read SPEC.md and CLAUDE.md in full. Then:

1. Summarise the Phase 0 and Phase 1 scope in ten bullets. List anything in the spec
   you think is wrong, ambiguous or blocked by a dependency. Wait for my answers.

2. Verify current syntax and record versions in VERSIONS.md for:
   - DuckLake ATTACH with Postgres metadata and S3 data
   - DuckDB S3 secrets for SeaweedFS (endpoint, path-style URLs, no SSL)
   - the Quack extension, server and client side
   - dbt v2 (dbt-oss) install, the DuckDB adapter, and catalogs.yml for DuckLake
   Write an ADR for anything that differs from the spec.

3. Generate schemas/bundle_config_schema.json with `databricks bundle schema`
   and record the CLI version.

4. Build Phase 0 to its acceptance criteria, including both spikes.
   Commit as you go, keep `make check` green, update docs/progress.md,
   then stop and report what passed, what failed, and any ADRs written.
```

For later sessions, a shorter prompt is enough: `Read docs/progress.md, then continue with the next unchecked item in SPEC.md section 13.`

## 16. Open decisions and risks

Three decisions are left to the agent, each settled with an ADR. Five risks are known up front.

**Decisions**

| Decision | Options | How to decide |
|---|---|---|
| `spark` object in task code | SQLFrame on the DuckDB session, or DuckDB's experimental Spark API | Run the example's Python task through both; pick the one with fewer gaps |
| Catalog metadata isolation | Postgres schema per catalog, or database per catalog | Whichever DuckLake's attach options support cleanly |
| Task isolation | Subprocess (default), or container per task | Keep subprocess unless memory limits prove unreliable |

**Risks**

| Risk | Mitigation |
|---|---|
| dbt v2 may not attach a Postgres-backed DuckLake with S3 credentials | Spike B in Phase 0; fallback to dbt-duckdb v1 |
| DuckDB version skew between dbt's pinned build and the platform | `dbk status` check; pin the platform to dbt's DuckDB version |
| DuckLake write conflicts under concurrent jobs | Retries with clear errors; example tasks write distinct tables |
| Quack is new (released May 2026) | Warehouses are Phase 3, behind one client module with in-process fallback |
| Databricks SQL and PySpark won't run unchanged | Documented in `docs/compat.md`; `dbk sql transpile` helper |

## References for the agent

- [DuckDB Now Ships inside dbt v2](https://duckdb.org/2026/09/22/dbt-fusion) (dbt v2 DuckDB adapter, `catalogs.yml`, info-schema Parquet, column lineage)
- [Quack remote protocol](https://duckdb.org/docs/current/quack/overview.html)
- [DuckLake](https://ducklake.select) and [DuckLake v1.0 announcement](https://ducklake.select/2026/04/13/ducklake-10/)
- [DuckDB Spark API](https://duckdb.org/docs/current/clients/python/spark_api)
- [Bundle configuration reference](https://learn.microsoft.com/en-in/azure/databricks/dev-tools/bundles/settings)
