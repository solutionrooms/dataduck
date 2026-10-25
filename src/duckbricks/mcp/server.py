"""The Duckbricks MCP server (`dbk-mcp`): a thin, stateless client of the API (SPEC section 10).

Claude Code runs it over stdio. Read-only tools and write tools are separate so they can be
permissioned separately (`.claude/settings.json`). Configuration: `DBK_HOST`, `DBK_TOKEN`.
The only local file access is reading bundle (and dbt project) directories.
"""

from __future__ import annotations

import base64
import io
import json
import tarfile
import time
from functools import cache
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from duckbricks import ops
from duckbricks.bundles.resolve import BundleError
from duckbricks.client import ApiCallError, DuckbricksClient

INSTRUCTIONS = """\
Duckbricks is a local, Databricks-shaped data platform on DuckDB and DuckLake.
Catalogs (dev, prod, workspace) hold schemas and tables; `sys` holds read-only system tables
(sys.jobs.*, sys.lineage.*, sys.query.history, sys.compute.*, sys.pipelines.*).
SQL is DuckDB SQL. Jobs, pipelines and schemas are usually defined in bundles
(databricks.yml). Working loop: bundle_validate -> dbt_check -> bundle_deploy -> job_run_now
-> run_get -> sql_query.
"""

server = MCPServer(name="duckbricks", instructions=INSTRUCTIONS, log_level="WARNING")
READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)
MAX_ROWS = 1000


@cache
def api() -> DuckbricksClient:
    return DuckbricksClient(user_agent="dbk-mcp")


def call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Turn API and bundle errors into tool errors with a likely fix."""
    try:
        return fn(*args, **kwargs)
    except ApiCallError as exc:
        hint = {
            "UNAVAILABLE": "Start the stack with `make up` in the Duckbricks repo.",
            "RESOURCE_DOES_NOT_EXIST": "Check the name or id; list the parent to see what exists.",
            "PERMISSION_DENIED": "sql_query is read-only; use sql_execute for DDL and DML.",
            "INVALID_PARAMETER_VALUE": "Fix the argument named in the message and retry.",
            "UNAUTHENTICATED": "Set DBK_TOKEN to the token in the Duckbricks .env file.",
        }.get(exc.error_code, "")
        message = f"{exc.error_code}: {exc.message}" + (f"\nLikely fix: {hint}" if hint else "")
        raise ToolError(message) from None
    except BundleError as exc:
        raise ToolError(f"Bundle error: {exc}") from None


# --- formatting -------------------------------------------------------------------------------


def cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, default=str)
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= 120 else text[:117] + "..."


def md_table(
    rows: list[dict[str, Any]], columns: list[str], cap: int = 50, total: int | None = None
) -> str:
    if not rows:
        return "(none)"
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows[:cap]:
        lines.append("| " + " | ".join(cell(row.get(c)) for c in columns) + " |")
    more = (total if total is not None else len(rows)) - min(len(rows), cap)
    if more > 0:
        lines.append(f"\n_truncated, {more} more_")
    return "\n".join(lines)


def ms(value: Any) -> str:
    if not value:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(value) / 1000))


def result_table(result: dict[str, Any]) -> str:
    state = result["status"]["state"]
    if state != "SUCCEEDED":
        message = result["status"].get("error", {}).get("message", "statement failed")
        if "read-only mode" in message:
            message += "\nLikely fix: sql_query is read-only; use sql_execute for DDL and DML."
        raise ToolError(f"{state}: {message}")
    columns = [c["name"] for c in result.get("manifest", {}).get("schema", {}).get("columns", [])]
    if not columns:
        return "OK (no result set)"
    rows = [dict(zip(columns, r, strict=True)) for r in result["result"]["data_array"]]
    out = md_table(rows, columns, cap=len(rows))
    note = f"\n\n{len(rows)} row(s)"
    if result["manifest"].get("truncated"):
        note += " (row limit reached; more rows exist)"
    return out + note


# --- read tools -------------------------------------------------------------------------------


@server.tool(annotations=READ)
def platform_status() -> str:
    """Health, versions, catalogs, workers, warehouses, queue and version mismatches."""
    data = call(api().get, "/api/duckbricks/status")
    lines = [
        f"**Status:** {data['status']}  **User:** {data['user']}  "
        f"**DuckDB:** {data['versions']['duckdb']}  **UI:** {data['links']['ui']}",
        f"**Queue:** {data['queue']['pending_tasks']} pending, "
        f"{data['queue']['running_tasks']} running",
        "",
        "**Catalogs**",
        md_table(data["catalogs"], ["name", "comment"]),
        "",
        "**Workers**",
        md_table(
            [w for w in data["workers"] if w["alive"]],
            ["worker_id", "slots", "dbt_version", "dbt_distribution", "dbt_duckdb_version"],
        ),
        "",
        "**Warehouses**",
        md_table(data["warehouses"], ["id", "name", "state"]),
    ]
    if data["version_mismatches"]:
        lines += ["", "**Version mismatches**", *[f"- {m}" for m in data["version_mismatches"]]]
    return "\n".join(lines)


@server.tool(annotations=READ)
def catalog_browse(
    catalog: str | None = None,
    schema: str | None = None,
    name_filter: str | None = None,
    limit: int = 100,
) -> str:
    """List catalogs; or the schemas of `catalog`; or the tables of `catalog`.`schema`.

    `name_filter` keeps names containing that text (case-insensitive).
    """
    needle = (name_filter or "").lower()
    if catalog and schema:
        data = call(
            api().get, "/api/2.1/unity-catalog/tables", catalog_name=catalog, schema_name=schema
        )
        rows = data.get("tables", [])
        columns = ["full_name", "table_type", "comment"]
    elif catalog:
        rows = call(api().get, "/api/2.1/unity-catalog/schemas", catalog_name=catalog).get(
            "schemas", []
        )
        columns = ["full_name", "comment"]
    else:
        rows = call(api().get, "/api/2.1/unity-catalog/catalogs").get("catalogs", [])
        columns = ["name", "catalog_type", "comment"]
    if needle:
        rows = [r for r in rows if needle in str(r.get("name", "")).lower()]
    return md_table(rows, columns, cap=max(1, min(limit, 500)))


@server.tool(annotations=READ)
def table_describe(full_name: str, sample_rows: int = 0) -> str:
    """Columns, types, row count and latest snapshot of `catalog.schema.table`.

    `sample_rows` (0-20) adds a sample of the data.
    """
    info = call(api().get, f"/api/2.1/unity-catalog/tables/{full_name}")
    count = call(api().rows, f"SELECT count(*) AS n FROM {full_name}", read_only=True)
    lines = [
        f"**{info['full_name']}** ({info.get('table_type')}), {count[0]['n']} row(s)",
        info.get("comment") or "",
        md_table(info.get("columns", []), ["name", "type_text", "nullable", "comment"], cap=200),
    ]
    try:
        history = api().get(f"/api/2.1/unity-catalog/tables/{full_name}/history").get("history", [])
    except ApiCallError:
        history = []
    if history:
        latest = history[0]
        lines.append(
            f"\nLatest snapshot: version {latest.get('version')} at {ms(latest.get('timestamp'))}"
            f" ({latest.get('operation', '')}); {len(history)} snapshot(s) in history"
        )
    if sample_rows > 0:
        result = call(
            api().sql,
            f"SELECT * FROM {full_name}",
            read_only=True,
            row_limit=min(sample_rows, 20),
        )
        lines += ["", "**Sample**", result_table(result)]
    return "\n".join(line for line in lines if line is not None)


@server.tool(annotations=READ)
def sql_query(
    statement: str, catalog: str | None = None, schema: str | None = None, row_limit: int = 100
) -> str:
    """Run read-only DuckDB SQL (SELECT, DESCRIBE, SHOW, EXPLAIN). Default 100 rows, max 1000.

    Writes are rejected; use sql_execute. `catalog`/`schema` set the default namespace.
    """
    result = call(
        api().sql,
        statement,
        catalog=catalog,
        schema=schema,
        read_only=True,
        row_limit=max(1, min(row_limit, MAX_ROWS)),
        wait_seconds=60,
    )
    return result_table(result)


@server.tool(annotations=READ)
def jobs_list(name_filter: str | None = None, limit: int = 50) -> str:
    """Job definitions: id, name, schedule, task count."""
    data = call(api().get, "/api/2.2/jobs/list", limit=100)
    jobs = data.get("jobs", [])
    if name_filter:
        jobs = [j for j in jobs if name_filter.lower() in j["settings"].get("name", "").lower()]
    rows = [
        {
            "job_id": j["job_id"],
            "name": j["settings"].get("name"),
            "tasks": len(j["settings"].get("tasks", [])),
            "schedule": (j["settings"].get("schedule") or {}).get("quartz_cron_expression"),
            "paused": (j["settings"].get("schedule") or {}).get("pause_status"),
            "bundle": (j["settings"].get("deployment") or {}).get("metadata_file_path", ""),
        }
        for j in jobs
    ]
    return md_table(rows, ["job_id", "name", "tasks", "schedule", "paused"], cap=limit)


@server.tool(annotations=READ)
def job_get(job_id: int) -> str:
    """A job's full settings as JSON."""
    return json.dumps(call(api().get, "/api/2.2/jobs/get", job_id=job_id), indent=1)


@server.tool(annotations=READ)
def runs_list(job_id: int | None = None, active_only: bool = False, limit: int = 20) -> str:
    """Recent runs, newest first."""
    data = call(
        api().get,
        "/api/2.2/jobs/runs/list",
        job_id=job_id,
        active_only=active_only,
        limit=max(1, min(limit, 100)),
    )
    rows = [
        {
            "run_id": r["run_id"],
            "job_id": r.get("job_id"),
            "name": r.get("run_name"),
            "state": r["state"]["life_cycle_state"],
            "result": r["state"].get("result_state"),
            "trigger": r.get("trigger"),
            "start": ms(r.get("start_time")),
        }
        for r in data.get("runs", [])
    ]
    note = "\n\n_more runs exist; raise limit or filter by job_id_" if data.get("has_more") else ""
    return (
        md_table(rows, ["run_id", "job_id", "name", "state", "result", "trigger", "start"]) + note
    )


@server.tool(annotations=READ)
def run_get(run_id: int, log_lines: int = 200) -> str:
    """A run's state and task states, with the last log lines of each failed task."""
    run = call(api().get, "/api/2.2/jobs/runs/get", run_id=run_id)
    state = run["state"]
    lines = [
        f"**Run {run_id}** ({run.get('run_name')}): {state['life_cycle_state']} "
        f"{state.get('result_state') or ''}",
        state.get("state_message") or "",
        run.get("run_page_url", ""),
        "",
        md_table(
            [
                {
                    "task_key": t["task_key"],
                    "task_run_id": t["run_id"],
                    "attempt": t.get("attempt_number", 0),
                    "state": t["state"]["life_cycle_state"],
                    "result": t["state"].get("result_state"),
                    "message": t["state"].get("state_message"),
                }
                for t in run.get("tasks", [])
            ],
            ["task_key", "task_run_id", "attempt", "state", "result", "message"],
        ),
    ]
    for task_key, logs in ops.failed_task_logs(
        api(), run, lines=max(1, min(log_lines, 500))
    ).items():
        lines += ["", f"**Log of failed task {task_key}** (last lines)", "```", logs, "```"]
    return "\n".join(lines)


@server.tool(annotations=READ)
def bundle_validate(
    path: str = ".", target: str | None = None, variables: dict[str, str] | None = None
) -> str:
    """Resolve and validate a local bundle directory (databricks.yml) for a target.

    Returns the summary, warnings and errors. Nothing is deployed.
    """
    result = call(ops.bundle_validate, api(), path, target, variables)
    summary = result.summary()
    lines = [f"**Validation {'OK' if result.ok else 'FAILED'}**"]
    if result.bundle is not None:
        lines.append(
            f"Bundle {summary['name']}, target {summary['target']} (mode {summary['mode']}), "
            f"root {summary['root_path']}"
        )
        for rtype, keys in summary["resources"].items():
            lines.append(f"- {rtype}: {', '.join(keys)}")
    lines += [f"- error: {e}" for e in result.errors]
    lines += [f"- warning: {w}" for w in result.warnings]
    return "\n".join(lines)


def _tar_directory(directory: Path) -> str:
    buffer = io.BytesIO()
    skip = {"target", "logs", "dbt_packages", ".venv", "__pycache__", ".git"}
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(directory.rglob("*")):
            rel = path.relative_to(directory)
            if path.is_file() and not skip.intersection(rel.parts):
                tar.add(path, arcname=rel.as_posix(), recursive=False)
    return base64.b64encode(buffer.getvalue()).decode()


@server.tool(annotations=READ)
def dbt_check(
    project_directory: str,
    catalog: str = "dev",
    schema: str = "default",
    timeout_seconds: int = 300,
) -> str:
    """Strict static-analysis compile of a dbt project on the worker; returns diagnostics.

    `project_directory` is a local directory containing dbt_project.yml (it is uploaded) or a
    workspace path such as /Users/<you>/.bundle/<bundle>/dev/files/dbt.
    """
    local = Path(project_directory).expanduser()
    body: dict[str, Any] = {
        "catalog": catalog,
        "schema": schema,
        "timeout_seconds": timeout_seconds,
    }
    if (local / "dbt_project.yml").exists():
        body["files_tar_gz_b64"] = _tar_directory(local.resolve())
    else:
        body["project_directory"] = project_directory
    result = call(api().post, "/api/duckbricks/dbt/compile", body)
    lines = [f"**dbt check {'passed' if result['ok'] else 'FAILED'}** (`{result['command']}`)"]
    lines += [f"- error: {e}" for e in result["errors"][:50]]
    lines += [f"- warning: {w}" for w in result["warnings"][:50]]
    if not result["ok"]:
        lines += ["", "```", result["log_tail"][-4000:], "```"]
    return "\n".join(lines)


@server.tool(annotations=READ)
def lineage_get(table: str, column: str | None = None, max_depth: int = 3) -> str:
    """Upstream and downstream lineage of `catalog.schema.table` (or of one column)."""
    if column:
        data = call(
            api().get,
            "/api/duckbricks/lineage/column",
            table=table,
            column=column,
            max_depth=max_depth,
        )
        fmt = ["table", "column", "depth", "evolution"]
    else:
        data = call(api().get, "/api/duckbricks/lineage/table", name=table, max_depth=max_depth)
        fmt = ["name", "depth", "source"]
    subject = f"{table}.{column}" if column else table
    return "\n".join(
        [
            f"**Lineage of {subject}**",
            "",
            "**Upstream**",
            md_table(data.get("upstreams", []), fmt),
            "",
            "**Downstream**",
            md_table(data.get("downstreams", []), fmt),
        ]
    )


@server.tool(annotations=READ)
def pipelines_list() -> str:
    """Pipelines with their state and latest update."""
    data = call(api().get, "/api/2.0/pipelines")
    rows = [
        {
            "pipeline_id": p["pipeline_id"],
            "name": p["name"],
            "state": p.get("state"),
            "latest_update": (p.get("latest_updates") or [{}])[0].get("state"),
        }
        for p in data.get("statuses", [])
    ]
    return md_table(rows, ["pipeline_id", "name", "state", "latest_update"])


# --- write tools ------------------------------------------------------------------------------


@server.tool(annotations=WRITE)
def sql_execute(
    statement: str,
    catalog: str,
    schema: str | None = None,
    warehouse_id: str | None = None,
    row_limit: int = 100,
) -> str:
    """Run DDL or DML (or any SQL) with `catalog` as the default namespace.

    Several statements separated by `;` run in order. `warehouse_id` runs it on a warehouse.
    """
    result = call(
        api().sql,
        statement,
        catalog=catalog,
        schema=schema,
        warehouse_id=warehouse_id,
        row_limit=max(1, min(row_limit, MAX_ROWS)),
    )
    return result_table(result)


@server.tool(annotations=WRITE)
def catalog_create(name: str, comment: str | None = None) -> str:
    """Create a catalog (a DuckLake catalog with its own storage prefix)."""
    body = {"name": name, **({"comment": comment} if comment else {})}
    data = call(api().post, "/api/2.1/unity-catalog/catalogs", body)
    return f"Created catalog {data['name']} at {data.get('storage_root')}"


@server.tool(annotations=WRITE)
def schema_create(catalog: str, name: str, comment: str | None = None) -> str:
    """Create a schema in a catalog."""
    body = {"catalog_name": catalog, "name": name, **({"comment": comment} if comment else {})}
    data = call(api().post, "/api/2.1/unity-catalog/schemas", body)
    return f"Created schema {data['full_name']}"


@server.tool(annotations=WRITE)
def job_run_now(
    job_id: int | None = None,
    bundle_job_key: str | None = None,
    bundle_path: str = ".",
    target: str | None = None,
    job_parameters: dict[str, str] | None = None,
    wait: bool = True,
    timeout_seconds: int = 900,
) -> str:
    """Trigger a job by `job_id`, or by `bundle_job_key` of the bundle at `bundle_path`/`target`.

    With `wait`, returns the final run state (and failed-task logs); otherwise the run id.
    """
    client = api()
    if job_id is None:
        if not bundle_job_key:
            raise ToolError("Give job_id, or bundle_job_key (with bundle_path and target)")
        job_id = call(ops.bundle_job_id, client, bundle_job_key, bundle_path, target)
    run_id = call(ops.run_now, client, job_id, job_parameters)
    if not wait:
        return f"Started run {run_id} of job {job_id}. Check it with run_get({run_id})."
    call(client.wait_for_run, run_id, timeout=timeout_seconds)
    return run_get(run_id, log_lines=200)


@server.tool(annotations=DESTRUCTIVE)
def run_cancel(run_id: int) -> str:
    """Cancel a job run (and its running tasks)."""
    call(api().post, "/api/2.2/jobs/runs/cancel", {"run_id": run_id})
    return f"Cancellation requested for run {run_id}"


@server.tool(annotations=WRITE)
def bundle_deploy(
    path: str = ".", target: str | None = None, variables: dict[str, str] | None = None
) -> str:
    """Validate and deploy a local bundle to a target; reports what was created/updated/deleted."""
    report = call(ops.bundle_deploy, api(), path, target, variables)
    lines = [
        f"Deployed **{report['bundle_name']}** to **{report['target']}** ({report['files']} files)"
    ]
    for label in ("created", "updated", "deleted"):
        if report.get(label):
            lines.append(f"- {label}: {', '.join(report[label])}")
    if report.get("unchanged"):
        lines.append(f"- unchanged: {len(report['unchanged'])} resource(s)")
    lines += [f"- warning: {w}" for w in report.get("warnings", [])]
    return "\n".join(lines)


@server.tool(annotations=DESTRUCTIVE)
def bundle_destroy(path: str = ".", target: str | None = None, confirm: bool = False) -> str:
    """Delete every resource and file the bundle deployed to the target. Needs confirm=true."""
    if not confirm:
        raise ToolError(
            "bundle_destroy deletes the target's jobs, pipelines, schemas and files. "
            "Call again with confirm=true to proceed."
        )
    report = call(ops.bundle_destroy, api(), path, target)
    return (
        f"Destroyed {report['bundle_name']} ({report['target']}): "
        f"{', '.join(report.get('deleted', [])) or 'nothing was deployed'}"
    )


@server.tool(annotations=WRITE)
def warehouse_start(warehouse_id: str) -> str:
    """Start a SQL warehouse (a DuckDB process serving Quack)."""
    call(api().post, f"/api/2.0/sql/warehouses/{warehouse_id}/start")
    info = call(api().get, f"/api/2.0/sql/warehouses/{warehouse_id}")
    return f"Warehouse {info['name']} is {info['state']} ({info.get('quack_uri', '')})"


@server.tool(annotations=WRITE)
def warehouse_stop(warehouse_id: str) -> str:
    """Stop a SQL warehouse."""
    call(api().post, f"/api/2.0/sql/warehouses/{warehouse_id}/stop")
    return f"Warehouse {warehouse_id} stopped"


@server.tool(annotations=WRITE)
def pipeline_start_update(pipeline_id: str, full_refresh: bool = False, wait: bool = True) -> str:
    """Start a pipeline update; with `wait`, return its events when it finishes."""
    started = call(
        api().post, f"/api/2.0/pipelines/{pipeline_id}/updates", {"full_refresh": full_refresh}
    )
    if not wait:
        return f"Started update {started['update_id']} (run {started['run_id']})"
    call(api().wait_for_run, int(started["run_id"]), timeout=900)
    update = call(api().get, f"/api/2.0/pipelines/{pipeline_id}/updates/{started['update_id']}")
    events = call(
        api().get,
        f"/api/2.0/pipelines/{pipeline_id}/events",
        update_id=started["update_id"],
        max_results=50,
    ).get("events", [])
    return "\n".join(
        [
            f"Update {started['update_id']}: **{update['update']['state']}**",
            md_table(events, ["timestamp", "level", "message"], cap=50),
        ]
    )


# --- prompts ----------------------------------------------------------------------------------


@server.prompt()
def new_medallion_bundle(name: str, source: str = "s3://landing/<name>/") -> str:
    """Scaffold a new bronze/silver/gold bundle and take it to a queried gold table."""
    return (
        f"Create a Duckbricks bundle named {name} that ingests files from {source} into bronze, "
        "builds silver and gold with dbt, and checks quality. Follow the duckbricks-bundle "
        "skill: copy examples/bundles/shop_demo as the starting layout, adapt names, then "
        "bundle_validate, dbt_check the dbt project, bundle_deploy, job_run_now with wait, "
        "and finally sql_query the gold table to show the result."
    )


@server.prompt()
def investigate_failed_run(run_id: int) -> str:
    """Diagnose a failed run and propose a fix."""
    return (
        f"Investigate Duckbricks run {run_id}: call run_get({run_id}) and read the failed task "
        "logs. Inspect the tables and SQL involved with table_describe and sql_query. Explain "
        "the root cause, propose the smallest fix to the bundle source, and offer to redeploy "
        "and re-run."
    )


def main() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    server.run("stdio")


if __name__ == "__main__":
    main()
