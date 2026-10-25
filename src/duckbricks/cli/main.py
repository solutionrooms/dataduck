"""`dbk`: the Duckbricks CLI. Command shapes follow the Databricks CLI (SPEC section 11).

Every command talks to the API (`DBK_HOST`, `DBK_TOKEN` or a `~/.dbk/config` profile).
Output is a Rich table by default; `-o json` prints JSON.
"""

from __future__ import annotations

import json
import sys
import time
import webbrowser
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from duckbricks import ops
from duckbricks.bundles.resolve import BundleError
from duckbricks.client import ApiCallError, DuckbricksClient

console = Console()
err = Console(stderr=True)

app = typer.Typer(
    no_args_is_help=True,
    help="Duckbricks: a local, Databricks-shaped platform.",
    pretty_exceptions_show_locals=False,
)
bundle_app = typer.Typer(no_args_is_help=True, help="Declarative Automation Bundles.")
jobs_app = typer.Typer(no_args_is_help=True, help="Jobs.")
runs_app = typer.Typer(no_args_is_help=True, help="Job runs.")
catalogs_app = typer.Typer(no_args_is_help=True, help="Unity Catalog catalogs.")
schemas_app = typer.Typer(no_args_is_help=True, help="Schemas.")
tables_app = typer.Typer(no_args_is_help=True, help="Tables.")
warehouses_app = typer.Typer(no_args_is_help=True, help="SQL warehouses.")
pipelines_app = typer.Typer(no_args_is_help=True, help="Pipelines.")
for sub, name in (
    (bundle_app, "bundle"),
    (jobs_app, "jobs"),
    (runs_app, "runs"),
    (catalogs_app, "catalogs"),
    (schemas_app, "schemas"),
    (tables_app, "tables"),
    (warehouses_app, "warehouses"),
    (pipelines_app, "pipelines"),
):
    app.add_typer(sub, name=name)


class Output(StrEnum):
    text = "text"
    json = "json"


OutputOpt = Annotated[Output, typer.Option("--output", "-o", help="text or json")]
TargetOpt = Annotated[str | None, typer.Option("--target", "-t", help="Bundle target")]
VarOpt = Annotated[
    list[str] | None, typer.Option("--var", help="Variable override, name=value (repeatable)")
]
PathOpt = Annotated[
    Path | None, typer.Option("--path", "-p", help="Bundle directory (default: current)")
]


def client() -> DuckbricksClient:
    return DuckbricksClient(user_agent="dbk-cli")


def pairs(values: list[str] | None, what: str) -> dict[str, str]:
    out = {}
    for item in values or []:
        if "=" not in item:
            raise typer.BadParameter(f"{what} must look like name=value, got '{item}'")
        key, value = item.split("=", 1)
        out[key.strip()] = value
    return out


def emit(data: Any, output: Output, columns: list[str] | None = None, title: str = "") -> None:
    """Print JSON, or a table of `columns` from a list of dicts, or key/value rows."""
    if output == Output.json:
        console.print_json(json.dumps(data, default=str))
        return
    if isinstance(data, list):
        table = Table(title=title or None, show_lines=False)
        cols = columns or (list(data[0]) if data else [])
        for col in cols:
            table.add_column(col)
        for row in data:
            table.add_row(*[_cell(row.get(c)) for c in cols])
        console.print(table)
        if not data:
            console.print("[dim](none)[/dim]")
    elif isinstance(data, dict):
        table = Table(show_header=False, title=title or None)
        table.add_column("key", style="bold")
        table.add_column("value")
        for key, value in data.items():
            table.add_row(str(key), _cell(value))
        console.print(table)
    else:
        console.print(data)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def fail(message: str, code: int = 1) -> None:
    err.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code)


def ms(value: Any) -> str:
    if not value:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(value) / 1000))


# --- status -----------------------------------------------------------------------------------


@app.command()
def status(output: OutputOpt = Output.text) -> None:
    """Health, versions, catalogs, workers, warehouses and version mismatches."""
    data = client().get("/api/duckbricks/status")
    if output == Output.json:
        emit(data, output)
        return
    colour = "green" if data["status"] == "ok" else "yellow"
    console.print(
        f"[bold]Duckbricks[/bold] [{colour}]{data['status']}[/{colour}]  user {data['user']}"
    )
    console.print(f"DuckDB {data['versions']['duckdb']}  UI {data['links']['ui']}")
    emit(
        [{**w, "heartbeat_at": ms(w["heartbeat_at"])} for w in data["workers"]],
        output,
        ["worker_id", "alive", "slots", "dbt_version", "dbt_distribution", "dbt_duckdb_version"],
        "Workers",
    )
    emit(data["catalogs"], output, ["name", "comment", "data_path"], "Catalogs")
    emit(data["warehouses"], output, ["id", "name", "state"], "Warehouses")
    queue = data["queue"]
    console.print(f"Queue: {queue['pending_tasks']} pending, {queue['running_tasks']} running")
    for mismatch in data["version_mismatches"]:
        console.print(f"[yellow]Version mismatch:[/yellow] {mismatch}")


# --- bundle -----------------------------------------------------------------------------------


@bundle_app.command("init")
def bundle_init(
    name: Annotated[str, typer.Argument(help="Directory to create")],
    template: Annotated[str, typer.Option(help="Example to copy")] = "shop_demo",
) -> None:
    """Scaffold a bundle from examples/bundles/<template>."""
    import shutil

    from duckbricks.core.config import REPO_ROOT

    source = REPO_ROOT / "examples" / "bundles" / template
    if not source.exists():
        available = ", ".join(p.name for p in (REPO_ROOT / "examples" / "bundles").iterdir())
        fail(f"No template '{template}'. Available: {available}")
    target = Path(name)
    if target.exists():
        fail(f"{target} already exists")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("target", "logs", "__pycache__"))
    config = target / "databricks.yml"
    config.write_text(config.read_text().replace(f"name: {template}", f"name: {target.name}", 1))
    console.print(f"Created bundle [bold]{target.name}[/bold] in {target}")


@bundle_app.command("validate")
def bundle_validate(
    target: TargetOpt = None,
    var: VarOpt = None,
    path: PathOpt = None,
    output: OutputOpt = Output.text,
) -> None:
    """Resolve and validate databricks.yml against the bundle schema and Duckbricks support."""
    result = ops.bundle_validate(client(), path, target, pairs(var, "--var"))
    if output == Output.json:
        data = result.summary()
        if result.bundle is not None:
            data["config"] = result.bundle.config
        emit(data, output)
    else:
        summary = result.summary()
        if result.bundle is not None:
            console.print(
                f"Name: [bold]{summary['name']}[/bold]  Target: {summary['target']}  "
                f"Mode: {summary['mode']}\nRoot: {summary['root_path']}"
            )
            for rtype, keys in summary["resources"].items():
                console.print(f"  {rtype}: {', '.join(keys)}")
        for warning in result.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")
        for error in result.errors:
            console.print(f"[red]Error:[/red] {error}")
        console.print(
            "[green]Validation OK![/green]" if result.ok else "[red]Validation failed[/red]"
        )
    if not result.ok:
        raise typer.Exit(1)


@bundle_app.command("deploy")
def bundle_deploy(
    target: TargetOpt = None,
    var: VarOpt = None,
    path: PathOpt = None,
    output: OutputOpt = Output.text,
) -> None:
    """Upload files and create, update or delete the bundle's resources."""
    report = ops.bundle_deploy(client(), path, target, pairs(var, "--var"))
    if output == Output.json:
        emit(report, output)
        return
    console.print(
        f"Deployed [bold]{report['bundle_name']}[/bold] to target {report['target']} "
        f"({report['files']} files)"
    )
    for label in ("created", "updated", "deleted", "unchanged"):
        if report.get(label):
            console.print(f"  {label}: {', '.join(report[label])}")
    for warning in report.get("warnings", []):
        console.print(f"[yellow]Warning:[/yellow] {warning}")


@bundle_app.command("run")
def bundle_run(
    job_key: Annotated[str, typer.Argument(help="Job resource key")],
    target: TargetOpt = None,
    path: PathOpt = None,
    no_wait: Annotated[bool, typer.Option("--no-wait", help="Return the run id at once")] = False,
    param: Annotated[
        list[str] | None, typer.Option("--param", help="Job parameter name=value (repeatable)")
    ] = None,
    output: OutputOpt = Output.text,
) -> None:
    """Trigger a bundle job and stream task states until it finishes."""
    c = client()
    job_id = ops.bundle_job_id(c, job_key, path, target)
    run_id = ops.run_now(c, job_id, pairs(param, "--param"))
    if no_wait:
        emit({"job_id": job_id, "run_id": run_id}, output)
        return
    run = follow_run(c, run_id, quiet=output == Output.json)
    if output == Output.json:
        emit(run, output)
    if run["state"].get("result_state") != "SUCCESS":
        raise typer.Exit(1)


def follow_run(c: DuckbricksClient, run_id: int, quiet: bool = False) -> dict[str, Any]:
    seen: dict[str, str] = {}
    run = c.get("/api/2.2/jobs/runs/get", run_id=run_id)
    if not quiet:
        console.print(f"Run {run_id}: {run.get('run_page_url', '')}")
    while True:
        for task in run.get("tasks", []):
            state = task["state"]
            label = state.get("result_state") or state["life_cycle_state"]
            if seen.get(task["task_key"]) != label:
                seen[task["task_key"]] = label
                if not quiet:
                    colour = {"SUCCESS": "green", "RUNNING": "cyan"}.get(label, "yellow")
                    if label in ("FAILED", "TIMEDOUT", "UPSTREAM_FAILED", "INTERNAL_ERROR"):
                        colour = "red"
                    console.print(f"  {task['task_key']}: [{colour}]{label}[/{colour}]")
        if run["state"]["life_cycle_state"] in ops.TERMINAL:
            break
        time.sleep(1)
        run = c.get("/api/2.2/jobs/runs/get", run_id=run_id)
    result = run["state"].get("result_state")
    if not quiet:
        colour = "green" if result == "SUCCESS" else "red"
        console.print(
            f"Run {run_id} [{colour}]{result}[/{colour}] {run['state'].get('state_message', '')}"
        )
        for task_key, logs in ops.failed_task_logs(c, run, lines=30).items():
            console.print(f"[red]--- {task_key} (last 30 lines) ---[/red]\n{logs}")
    return run


@bundle_app.command("summary")
def bundle_summary(
    target: TargetOpt = None, path: PathOpt = None, output: OutputOpt = Output.text
) -> None:
    """Deployed resources with ids and links."""
    summary = ops.bundle_summary(client(), path, target)
    if output == Output.json:
        emit(summary, output)
        return
    console.print(
        f"[bold]{summary['bundle_name']}[/bold] target {summary['target']} "
        f"({summary['mode'] or 'default'}), deployed {ms(summary['deployed_at'])}\n"
        f"Root: {summary['root_path']}"
    )
    emit(summary["resources"], output, ["resource_type", "resource_key", "resource_id", "url"])


@bundle_app.command("destroy")
def bundle_destroy(
    target: TargetOpt = None,
    path: PathOpt = None,
    auto_approve: Annotated[bool, typer.Option("--auto-approve", help="Skip confirmation")] = False,
    output: OutputOpt = Output.text,
) -> None:
    """Delete the target's resources and files."""
    c = client()
    if not auto_approve:
        name, tgt = ops.bundle_identity(c, path, target)
        if not sys.stdin.isatty():
            fail("destroy needs --auto-approve when not run interactively")
        typer.confirm(f"Destroy every resource of bundle {name}, target {tgt}?", abort=True)
    report = ops.bundle_destroy(c, path, target)
    if output == Output.json:
        emit(report, output)
    else:
        console.print(
            f"Destroyed {report['bundle_name']} ({report['target']}): "
            f"{', '.join(report.get('deleted', [])) or 'nothing deployed'}"
        )


@bundle_app.command("schema")
def bundle_schema() -> None:
    """Print the bundle JSON schema used for validation."""
    from duckbricks.bundles.schema import SCHEMA_PATH

    sys.stdout.write(SCHEMA_PATH.read_text())


# --- jobs and runs ----------------------------------------------------------------------------


@jobs_app.command("list")
def jobs_list(
    name: Annotated[str | None, typer.Option(help="Filter by exact name")] = None,
    limit: int = 25,
    output: OutputOpt = Output.text,
) -> None:
    data = client().get("/api/2.2/jobs/list", name=name, limit=limit)
    jobs = data.get("jobs", [])
    emit(
        [
            {
                "job_id": j["job_id"],
                "name": j["settings"].get("name"),
                "schedule": (j["settings"].get("schedule") or {}).get("quartz_cron_expression"),
                "paused": (j["settings"].get("schedule") or {}).get("pause_status"),
                "created": ms(j.get("created_time")),
            }
            for j in jobs
        ]
        if output == Output.text
        else data,
        output,
    )


@jobs_app.command("get")
def jobs_get(job_id: int, output: OutputOpt = Output.json) -> None:
    emit(client().get("/api/2.2/jobs/get", job_id=job_id), output)


@jobs_app.command("run-now")
def jobs_run_now(
    job_id: int,
    param: Annotated[list[str] | None, typer.Option("--param")] = None,
    no_wait: Annotated[bool, typer.Option("--no-wait")] = False,
    output: OutputOpt = Output.text,
) -> None:
    c = client()
    run_id = ops.run_now(c, job_id, pairs(param, "--param"))
    if no_wait:
        emit({"run_id": run_id}, output)
        return
    run = follow_run(c, run_id, quiet=output == Output.json)
    if output == Output.json:
        emit(run, output)
    if run["state"].get("result_state") != "SUCCESS":
        raise typer.Exit(1)


@jobs_app.command("delete")
def jobs_delete(job_id: int) -> None:
    client().post("/api/2.2/jobs/delete", {"job_id": job_id})
    console.print(f"Deleted job {job_id}")


@runs_app.command("list")
def runs_list(
    job_id: Annotated[int | None, typer.Option("--job-id")] = None,
    active_only: Annotated[bool, typer.Option("--active-only")] = False,
    limit: int = 25,
    output: OutputOpt = Output.text,
) -> None:
    data = client().get(
        "/api/2.2/jobs/runs/list", job_id=job_id, active_only=active_only, limit=limit
    )
    if output == Output.json:
        emit(data, output)
        return
    emit(
        [
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
        ],
        output,
    )


@runs_app.command("get")
def runs_get(run_id: int, output: OutputOpt = Output.text) -> None:
    run = client().get("/api/2.2/jobs/runs/get", run_id=run_id)
    if output == Output.json:
        emit(run, output)
        return
    state = run["state"]
    console.print(
        f"Run {run_id} ({run.get('run_name')}): {state['life_cycle_state']} "
        f"{state.get('result_state') or ''} {state.get('state_message') or ''}"
    )
    emit(
        [
            {
                "task_key": t["task_key"],
                "run_id": t["run_id"],
                "attempt": t.get("attempt_number", 0),
                "state": t["state"]["life_cycle_state"],
                "result": t["state"].get("result_state"),
                "message": t["state"].get("state_message"),
            }
            for t in run.get("tasks", [])
        ],
        output,
    )


@runs_app.command("cancel")
def runs_cancel(run_id: int) -> None:
    client().post("/api/2.2/jobs/runs/cancel", {"run_id": run_id})
    console.print(f"Cancellation requested for run {run_id}")


@runs_app.command("logs")
def runs_logs(
    run_id: int, task: Annotated[str | None, typer.Option("--task", help="Task key")] = None
) -> None:
    """Logs of a run's tasks (or of one task)."""
    c = client()
    run = c.get("/api/2.2/jobs/runs/get", run_id=run_id)
    tasks = run.get("tasks") or []
    if task:
        tasks = [t for t in tasks if t["task_key"] == task]
        if not tasks:
            fail(f"Run {run_id} has no task '{task}'")
    if not tasks:  # a task run id was given
        tasks = [{"task_key": str(run_id), "run_id": run_id}]
    for t in tasks:
        logs = c.get(f"/api/duckbricks/runs/{t['run_id']}/logs")["logs"]
        console.rule(f"{t['task_key']} (run {t['run_id']})")
        sys.stdout.write(logs if logs.endswith("\n") or not logs else logs + "\n")


# --- SQL ---------------------------------------------------------------------------------------


@app.command(context_settings={"allow_extra_args": False})
def sql(
    statement: Annotated[list[str], typer.Argument(help="SQL, or `transpile <file>`")],
    catalog: Annotated[str | None, typer.Option("--catalog", "-c")] = None,
    schema: Annotated[str | None, typer.Option("--schema", "-s")] = None,
    warehouse: Annotated[str | None, typer.Option("--warehouse", "-w")] = None,
    read_only: Annotated[bool, typer.Option("--read-only")] = False,
    limit: Annotated[int, typer.Option("--limit")] = 1000,
    source_dialect: Annotated[
        str, typer.Option("--from", help="transpile: source dialect")
    ] = "databricks",
    output: OutputOpt = Output.text,
) -> None:
    """Run SQL: `dbk sql "select 1"`. Port SQL: `dbk sql transpile --from databricks f.sql`."""
    if statement and statement[0] == "transpile":
        if len(statement) != 2:
            fail("usage: dbk sql transpile --from databricks <file>")
        transpile(Path(statement[1]), source_dialect)
        return
    text = " ".join(statement)
    if text == "-":
        text = sys.stdin.read()
    result = client().sql(
        text,
        catalog=catalog,
        schema=schema,
        warehouse_id=warehouse,
        read_only=read_only,
        row_limit=limit,
    )
    state = result["status"]["state"]
    if state != "SUCCEEDED":
        error = result["status"].get("error", {})
        fail(f"{state}: {error.get('message', '')}")
    if output == Output.json:
        emit(result, output)
        return
    columns = [c["name"] for c in result.get("manifest", {}).get("schema", {}).get("columns", [])]
    rows = result.get("result", {}).get("data_array", [])
    if not columns:
        console.print("OK")
        return
    table = Table()
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*["NULL" if v is None else str(v) for v in row])
    console.print(table)
    truncated = " (truncated)" if result["manifest"].get("truncated") else ""
    console.print(f"[dim]{len(rows)} row(s){truncated}[/dim]")


def transpile(path: Path, source: str) -> None:
    import sqlglot

    try:
        statements = sqlglot.transpile(path.read_text(), read=source, write="duckdb", pretty=True)
    except sqlglot.errors.ParseError as exc:
        fail(f"Cannot parse {path} as {source} SQL: {exc}")
    sys.stdout.write(";\n\n".join(statements) + ";\n")


# --- catalog ------------------------------------------------------------------------------------


@catalogs_app.command("list")
def catalogs_list(output: OutputOpt = Output.text) -> None:
    data = client().get("/api/2.1/unity-catalog/catalogs")
    emit(
        data if output == Output.json else data.get("catalogs", []),
        output,
        ["name", "catalog_type", "comment", "storage_root"],
    )


@catalogs_app.command("create")
def catalogs_create(
    name: str,
    comment: Annotated[str | None, typer.Option()] = None,
    output: OutputOpt = Output.text,
) -> None:
    body = {"name": name, **({"comment": comment} if comment else {})}
    emit(client().post("/api/2.1/unity-catalog/catalogs", body), output)


@schemas_app.command("list")
def schemas_list(catalog: str, output: OutputOpt = Output.text) -> None:
    data = client().get("/api/2.1/unity-catalog/schemas", catalog_name=catalog)
    emit(
        data if output == Output.json else data.get("schemas", []), output, ["full_name", "comment"]
    )


@schemas_app.command("create")
def schemas_create(
    full_name: Annotated[str, typer.Argument(help="catalog.schema")],
    comment: Annotated[str | None, typer.Option()] = None,
    output: OutputOpt = Output.text,
) -> None:
    if full_name.count(".") != 1:
        fail("Give the schema as catalog.schema")
    catalog, name = full_name.split(".")
    body = {"catalog_name": catalog, "name": name, **({"comment": comment} if comment else {})}
    emit(client().post("/api/2.1/unity-catalog/schemas", body), output)


@schemas_app.command("delete")
def schemas_delete(full_name: str, force: Annotated[bool, typer.Option("--force")] = False) -> None:
    client().delete(f"/api/2.1/unity-catalog/schemas/{full_name}", force=str(force).lower())
    console.print(f"Deleted schema {full_name}")


@tables_app.command("list")
def tables_list(
    schema: Annotated[str, typer.Argument(help="catalog.schema")], output: OutputOpt = Output.text
) -> None:
    if schema.count(".") != 1:
        fail("Give the schema as catalog.schema")
    catalog, name = schema.split(".")
    data = client().get("/api/2.1/unity-catalog/tables", catalog_name=catalog, schema_name=name)
    emit(
        data if output == Output.json else data.get("tables", []),
        output,
        ["full_name", "table_type", "comment"],
    )


@tables_app.command("describe")
def tables_describe(
    full_name: Annotated[str, typer.Argument(help="catalog.schema.table")],
    output: OutputOpt = Output.text,
) -> None:
    data = client().get(f"/api/2.1/unity-catalog/tables/{full_name}")
    if output == Output.json:
        emit(data, output)
        return
    console.print(f"[bold]{data['full_name']}[/bold] ({data.get('table_type')})")
    emit(data.get("columns", []), output, ["name", "type_text", "nullable", "comment"])


# --- warehouses and pipelines -------------------------------------------------------------------


@warehouses_app.command("list")
def warehouses_list(output: OutputOpt = Output.text) -> None:
    data = client().get("/api/2.0/sql/warehouses")
    emit(
        data if output == Output.json else data.get("warehouses", []),
        output,
        ["id", "name", "state", "cluster_size", "auto_stop_mins", "quack_uri"],
    )


@warehouses_app.command("create")
def warehouses_create(
    name: str,
    cluster_size: Annotated[str, typer.Option("--cluster-size")] = "Small",
    auto_stop_mins: Annotated[int, typer.Option("--auto-stop-mins")] = 10,
    output: OutputOpt = Output.text,
) -> None:
    body = {"name": name, "cluster_size": cluster_size, "auto_stop_mins": auto_stop_mins}
    emit(client().post("/api/2.0/sql/warehouses", body), output)


@warehouses_app.command("start")
def warehouses_start(warehouse_id: str) -> None:
    client().post(f"/api/2.0/sql/warehouses/{warehouse_id}/start")
    console.print(f"Warehouse {warehouse_id} running")


@warehouses_app.command("stop")
def warehouses_stop(warehouse_id: str) -> None:
    client().post(f"/api/2.0/sql/warehouses/{warehouse_id}/stop")
    console.print(f"Warehouse {warehouse_id} stopped")


@pipelines_app.command("list")
def pipelines_list(output: OutputOpt = Output.text) -> None:
    data = client().get("/api/2.0/pipelines")
    emit(
        data if output == Output.json else data.get("statuses", []),
        output,
        ["pipeline_id", "name", "state"],
    )


@pipelines_app.command("start-update")
def pipelines_start_update(
    pipeline_id: str,
    full_refresh: Annotated[bool, typer.Option("--full-refresh")] = False,
    output: OutputOpt = Output.text,
) -> None:
    emit(
        client().post(f"/api/2.0/pipelines/{pipeline_id}/updates", {"full_refresh": full_refresh}),
        output,
    )


@pipelines_app.command("events")
def pipelines_events(pipeline_id: str, limit: int = 50, output: OutputOpt = Output.text) -> None:
    data = client().get(f"/api/2.0/pipelines/{pipeline_id}/events", max_results=limit)
    emit(
        data if output == Output.json else data.get("events", []),
        output,
        ["timestamp", "level", "event_type", "message"],
    )


# --- UI -----------------------------------------------------------------------------------------


@app.command()
def ui(
    duckdb_ui: Annotated[
        bool, typer.Option("--duckdb", help="Start the DuckDB UI with every catalog attached")
    ] = False,
) -> None:
    """Open the Duckbricks web UI (or the DuckDB UI with --duckdb)."""
    c = client()
    if not duckdb_ui:
        url = c.get("/api/duckbricks/status")["links"]["ui"]
        console.print(f"Opening {url}")
        webbrowser.open(url)
        return
    from duckbricks.core.session import CatalogAttachment, open_session

    catalogs = [
        CatalogAttachment(
            name=item["name"],
            metadata_schema=item["properties"]["duckbricks.metadata_schema"],
            data_path=item["storage_root"],
        )
        for item in c.get("/api/2.1/unity-catalog/catalogs")["catalogs"]
        if item.get("properties", {}).get("duckbricks.metadata_schema")
    ]
    con = open_session(catalogs=catalogs)
    con.execute("CALL start_ui()")
    console.print("DuckDB UI running at http://localhost:4213 (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        con.execute("CALL stop_ui_server()")


def main() -> None:
    try:
        app()
    except (ApiCallError, BundleError) as exc:
        err.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
