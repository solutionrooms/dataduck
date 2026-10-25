"""Bundles (deploy, destroy, summary), Pipelines API 2.0, lineage and dbt check."""

from __future__ import annotations

import base64
import io
import re
import tarfile
import time
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from duckbricks.api.deps import control, unity_catalog, workspace
from duckbricks.core import db, deployments, jobs, pipelines, runs
from duckbricks.core.config import get_settings
from duckbricks.core.errors import InvalidParameter, NotFound
from duckbricks.core.lineage import column_lineage, table_lineage

router = APIRouter(tags=["Bundles, pipelines, lineage"])


# --- bundles ---------------------------------------------------------------------------------


class DeployBody(BaseModel):
    config: dict[str, Any]
    files_tar_gz_b64: str = ""


class DestroyBody(BaseModel):
    bundle_name: str
    target: str


@router.post("/api/duckbricks/bundles/deploy")
def deploy(body: DeployBody) -> dict[str, Any]:
    config = body.config
    if not (config.get("bundle") or {}).get("name") or not config["bundle"].get("target"):
        raise InvalidParameter("config.bundle.name and config.bundle.target are required")
    report = deployments.deploy(config, body.files_tar_gz_b64, unity_catalog())
    return report.to_json()


@router.post("/api/duckbricks/bundles/destroy")
def destroy(body: DestroyBody) -> dict[str, Any]:
    return deployments.destroy(body.bundle_name, body.target, unity_catalog()).to_json()


@router.get("/api/duckbricks/bundles")
def list_deployments(bundle_name: str | None = None, target: str | None = None) -> dict[str, Any]:
    with control() as conn:
        return {"deployments": deployments.summary(conn, bundle_name, target)}


# --- pipelines -------------------------------------------------------------------------------


class PipelineSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    catalog: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    libraries: list[dict[str, Any]] = Field(default_factory=list)


class StartUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")
    full_refresh: bool = False


def _spec(body: PipelineSpec) -> dict[str, Any]:
    return body.model_dump(by_alias=True, exclude_none=True)


@router.post("/api/2.0/pipelines")
def create_pipeline(body: PipelineSpec) -> dict[str, Any]:
    with control() as conn:
        pipeline_id = pipelines.create_pipeline(conn, _spec(body), get_settings().user_name)
    return {"pipeline_id": pipeline_id}


@router.get("/api/2.0/pipelines")
def list_pipelines() -> dict[str, Any]:
    with control() as conn:
        return {"statuses": pipelines.list_pipelines(conn)}


@router.get("/api/2.0/pipelines/{pipeline_id}")
def get_pipeline(pipeline_id: str) -> dict[str, Any]:
    with control() as conn:
        return pipelines.pipeline_json(conn, pipelines.get_row(conn, pipeline_id))


@router.put("/api/2.0/pipelines/{pipeline_id}")
def edit_pipeline(pipeline_id: str, body: PipelineSpec) -> dict[str, Any]:
    with control() as conn:
        pipelines.edit_pipeline(conn, pipeline_id, _spec(body))
    return {}


@router.delete("/api/2.0/pipelines/{pipeline_id}")
def delete_pipeline(pipeline_id: str) -> dict[str, Any]:
    with control() as conn:
        pipelines.get_row(conn, pipeline_id)
        pipelines.delete_pipeline(conn, pipeline_id)
    return {}


@router.post("/api/2.0/pipelines/{pipeline_id}/updates")
def start_update(pipeline_id: str, body: StartUpdate) -> dict[str, Any]:
    with control() as conn:
        row = pipelines.get_row(conn, pipeline_id)
        update_id = pipelines.create_update(conn, pipeline_id, body.full_refresh)
        settings, _ = jobs.validate_settings(
            {
                "name": f"Pipeline update: {row['name']}",
                "tasks": [
                    {
                        "task_key": "pipeline_update",
                        "pipeline_task": {
                            "pipeline_id": pipeline_id,
                            "full_refresh": body.full_refresh,
                            "_update_id": update_id,
                        },
                    }
                ],
            }
        )
        run_id = runs.submit_run(conn, settings, f"Pipeline update: {row['name']}")
        pipelines.add_event(
            conn, pipeline_id, update_id, "INFO", "update_progress", f"Queued as run {run_id}"
        )
    return {"update_id": update_id, "request_id": update_id, "run_id": run_id}


@router.post("/api/2.0/pipelines/{pipeline_id}/stop")
def stop_pipeline(pipeline_id: str) -> dict[str, Any]:
    with control() as conn:
        for update in pipelines.latest_updates(conn, pipeline_id, limit=1):
            if update["state"] in pipelines.ACTIVE_UPDATE_STATES and update["task_run_id"]:
                task = runs.task_run_row(conn, update["task_run_id"])
                runs.cancel_run(conn, task["job_run_id"])
    return {}


@router.get("/api/2.0/pipelines/{pipeline_id}/updates")
def list_updates(pipeline_id: str, max_results: int = 25) -> dict[str, Any]:
    with control() as conn:
        pipelines.get_row(conn, pipeline_id)
        return {"updates": pipelines.latest_updates(conn, pipeline_id, max_results)}


@router.get("/api/2.0/pipelines/{pipeline_id}/updates/{update_id}")
def get_update(pipeline_id: str, update_id: str) -> dict[str, Any]:
    with control() as conn:
        return {"update": pipelines.get_update(conn, pipeline_id, update_id)}


@router.get("/api/2.0/pipelines/{pipeline_id}/events")
def list_events(
    pipeline_id: str, max_results: int = 100, update_id: str | None = None
) -> dict[str, Any]:
    with control() as conn:
        pipelines.get_row(conn, pipeline_id)
        return {"events": pipelines.list_events(conn, pipeline_id, max_results, update_id)}


# --- lineage ---------------------------------------------------------------------------------


def _table_info(name: str) -> dict[str, Any]:
    parts = name.split(".")
    info: dict[str, Any] = {"name": parts[-1], "table_type": "TABLE"}
    if len(parts) == 3:
        info.update(catalog_name=parts[0], schema_name=parts[1])
    return {"tableInfo": info}


@router.get("/api/2.0/lineage-tracking/table-lineage")
def databricks_table_lineage(
    table_name: str, include_entity_lineage: bool = False
) -> dict[str, Any]:
    engine = db.get_engine(get_settings().control_url)
    result = table_lineage(engine, table_name, max_depth=1)
    return {
        "upstreams": [_table_info(u["name"]) for u in result["upstreams"]],
        "downstreams": [_table_info(d["name"]) for d in result["downstreams"]],
    }


@router.get("/api/2.0/lineage-tracking/column-lineage")
def databricks_column_lineage(table_name: str, column_name: str) -> dict[str, Any]:
    engine = db.get_engine(get_settings().control_url)
    result = column_lineage(engine, table_name, column_name, max_depth=1)

    def item(entry: dict[str, Any]) -> dict[str, Any]:
        catalog, schema, table = ([*entry["table"].split("."), "", "", ""])[:3]
        return {
            "name": entry["column"],
            "catalog_name": catalog,
            "schema_name": schema,
            "table_name": table,
        }

    return {
        "upstream_cols": [item(u) for u in result["upstreams"]],
        "downstream_cols": [item(d) for d in result["downstreams"]],
    }


@router.get("/api/duckbricks/lineage/table")
def duckbricks_table_lineage(name: str, max_depth: int = 10) -> dict[str, Any]:
    return table_lineage(db.get_engine(get_settings().control_url), name, max_depth)


@router.get("/api/duckbricks/lineage/column")
def duckbricks_column_lineage(table: str, column: str, max_depth: int = 10) -> dict[str, Any]:
    return column_lineage(db.get_engine(get_settings().control_url), table, column, max_depth)


# --- dbt check -------------------------------------------------------------------------------


class DbtCheck(BaseModel):
    project_directory: str | None = None
    files_tar_gz_b64: str | None = None
    catalog: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    timeout_seconds: int = 300


DIAGNOSTIC = re.compile(r"\[(error|warning)\]\s*(.*)", re.IGNORECASE)


@router.post("/api/duckbricks/dbt/compile")
def dbt_compile(body: DbtCheck) -> dict[str, Any]:
    """Strict static-analysis compile on the worker; returns diagnostics (MCP `dbt_check`)."""
    ws = workspace()
    project = body.project_directory
    if body.files_tar_gz_b64:
        project = f"/Users/{get_settings().user_name}/.dbk/dbt_check/{uuid.uuid4().hex[:12]}"
        target = ws.local(project)
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(
            fileobj=io.BytesIO(base64.b64decode(body.files_tar_gz_b64)), mode="r:gz"
        ) as tar:
            tar.extractall(target, filter="data")
    if not project:
        raise InvalidParameter("project_directory or files_tar_gz_b64 is required")
    ws.get_status(project)
    import os

    distribution = os.environ.get("DBK_DBT_DISTRIBUTION")
    with control() as conn:
        workers = conn.execute(db.workers.select()).mappings().all()
        distribution = next(
            (w["dbt_distribution"] for w in workers if w["dbt_distribution"]), distribution
        )
        command = (
            "dbt compile --static-analysis strict"
            if (distribution or "full") == "full"
            else "dbt compile"
        )
        settings, _ = jobs.validate_settings(
            {
                "name": "dbt check",
                "tasks": [
                    {
                        "task_key": "dbt_check",
                        "timeout_seconds": body.timeout_seconds,
                        "dbt_task": {
                            "project_directory": project,
                            "commands": [command],
                            "catalog": body.catalog or "dev",
                            "schema": body.schema_ or "default",
                            "_check": True,
                        },
                    }
                ],
            }
        )
        run_id = runs.submit_run(conn, settings, "dbt check")
    deadline = time.monotonic() + body.timeout_seconds + 30
    while time.monotonic() < deadline:
        with control() as conn:
            run = runs.get_run_row(conn, run_id)
            if run["life_cycle_state"] in runs.TERMINAL:
                task = (
                    conn.execute(db.task_runs.select().where(db.task_runs.c.job_run_id == run_id))
                    .mappings()
                    .first()
                )
                break
        time.sleep(1)
    else:
        raise NotFound(f"dbt check run {run_id} did not finish in time")
    logs, _ = runs.read_log_tail(task["log_path"] if task else None, 256 * 1024)
    errors, warnings = [], []
    for line in logs.splitlines():
        match = DIAGNOSTIC.search(line)
        if match:
            (errors if match.group(1).lower() == "error" else warnings).append(
                match.group(2).strip()
            )
    ok = run["result_state"] == "SUCCESS"
    if not ok and not errors:
        errors.append((task or {}).get("error") or run["state_message"] or "dbt compile failed")
    return {
        "ok": ok,
        "run_id": run_id,
        "command": command,
        "project_directory": project,
        "errors": errors,
        "warnings": warnings,
        "log_tail": "\n".join(logs.splitlines()[-60:]),
    }
