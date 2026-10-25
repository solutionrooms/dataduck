"""Duckbricks platform endpoints: status, current user, compute, task values, run logs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from duckbricks.api.deps import control
from duckbricks.core import db, runs
from duckbricks.core.catalogs import list_catalogs
from duckbricks.core.config import get_settings
from duckbricks.core.node_types import get_node_types
from duckbricks.core.sql_engine import get_sql_engine

router = APIRouter(tags=["Duckbricks"])
WORKER_ALIVE = timedelta(seconds=60)


def _ms(value: datetime | None) -> int | None:
    return int(value.timestamp() * 1000) if value else None


def platform_versions() -> dict[str, Any]:
    cur = get_sql_engine().cursor(read_only=True)
    try:
        extensions = dict(
            cur.execute(
                "SELECT extension_name, extension_version FROM duckdb_extensions() "
                "WHERE loaded AND extension_name IN ('ducklake', 'httpfs', 'postgres_scanner', "
                "'quack')"
            ).fetchall()
        )
    finally:
        cur.close()
    return {"duckdb": duckdb.__version__, "extensions": extensions}


@router.get("/api/duckbricks/status")
def status() -> dict[str, Any]:
    settings = get_settings()
    versions = platform_versions()
    now = datetime.now(UTC)
    with control() as conn:
        workers = (
            conn.execute(select(db.workers).order_by(db.workers.c.started_at)).mappings().all()
        )
        warehouses = conn.execute(select(db.warehouses)).mappings().all()
        queued = conn.execute(
            select(db.task_runs.c.run_id).where(db.task_runs.c.life_cycle_state == "PENDING")
        ).all()
        running = conn.execute(
            select(db.task_runs.c.run_id).where(db.task_runs.c.life_cycle_state == "RUNNING")
        ).all()
    alive = [w for w in workers if now - w["heartbeat_at"] < WORKER_ALIVE]
    mismatches = []
    platform = versions["duckdb"].lstrip("v")
    for w in alive:
        if w["duckdb_version"].lstrip("v") != platform:
            mismatches.append(
                f"worker {w['worker_id']} runs DuckDB {w['duckdb_version']}, API runs {platform}"
            )
        dbt_duckdb = (w["dbt_duckdb_version"] or "").lstrip("v")
        if dbt_duckdb and dbt_duckdb != platform:
            mismatches.append(
                f"dbt on worker {w['worker_id']} uses DuckDB {w['dbt_duckdb_version']}, "
                f"platform uses {platform}"
            )
    return {
        "status": "ok" if alive else "degraded",
        "user": settings.user_name,
        "versions": versions,
        "catalogs": [
            {"name": c.name, "comment": c.comment, "data_path": c.data_path}
            for c in list_catalogs()
        ],
        "workers": [
            {
                "worker_id": w["worker_id"],
                "alive": w in alive,
                "slots": w["slots"],
                "duckdb_version": w["duckdb_version"],
                "dbt_version": w["dbt_version"],
                "dbt_distribution": w["dbt_distribution"],
                "dbt_duckdb_version": w["dbt_duckdb_version"],
                "heartbeat_at": _ms(w["heartbeat_at"]),
            }
            for w in workers
            if now - w["heartbeat_at"] < timedelta(hours=1)
        ],
        "warehouses": [{"id": w["id"], "name": w["name"], "state": w["state"]} for w in warehouses],
        "queue": {"pending_tasks": len(queued), "running_tasks": len(running)},
        "version_mismatches": mismatches,
        "links": {"ui": settings.public_url, "notebooks": settings.notebooks_url},
    }


@router.get("/api/2.0/preview/scim/v2/Me")
def me() -> dict[str, Any]:
    settings = get_settings()
    return {
        "id": "1",
        "userName": settings.user_name,
        "displayName": settings.user_name.split("@")[0],
        "active": True,
        "emails": [{"value": settings.user_name, "primary": True}],
        "groups": [{"display": "admins"}],
    }


@router.get("/api/2.1/clusters/list-node-types")
@router.get("/api/2.0/clusters/list-node-types")
def list_node_types() -> dict[str, Any]:
    catalog = get_node_types()
    return {
        "node_types": [
            {
                "node_type_id": nt.name,
                "description": f"DuckDB, {nt.memory_limit} memory, {nt.threads} threads",
                "memory_limit": nt.memory_limit,
                "num_cores": nt.threads,
                "is_default": nt.name == catalog.default,
            }
            for nt in catalog.node_types.values()
        ]
    }


@router.get("/api/2.1/clusters/list")
@router.get("/api/2.0/clusters/list")
def list_clusters() -> dict[str, Any]:
    """No all-purpose clusters: job compute is per task run, SQL runs on warehouses."""
    return {"clusters": []}


class TaskValue(BaseModel):
    run_id: int  # the task run id
    key: str
    value: Any


@router.post("/api/duckbricks/task-values")
def set_task_value(body: TaskValue) -> dict[str, Any]:
    with control() as conn:
        runs.set_task_value(conn, body.run_id, body.key, body.value)
    return {}


@router.get("/api/duckbricks/task-values")
def get_task_value(run_id: int, task_key: str, key: str) -> dict[str, Any]:
    """`run_id` is the job run id."""
    with control() as conn:
        return {"value": runs.get_task_value(conn, run_id, task_key, key)}


@router.get("/api/duckbricks/runs/{task_run_id}/logs")
def run_logs(task_run_id: int, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
    with control() as conn:
        row = runs.task_run_row(conn, task_run_id)
    logs, truncated = runs.read_log_tail(row["log_path"], max_bytes)
    return {"run_id": task_run_id, "logs": logs, "truncated": truncated}
