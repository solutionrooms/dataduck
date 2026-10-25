"""Pipelines API storage: pipeline specs, updates and the event log (Pipelines API 2.0 shapes)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, delete, func, insert, select, update

from duckbricks.core import db
from duckbricks.core.errors import InvalidParameter, NotFound

ACTIVE_UPDATE_STATES = ("QUEUED", "CREATED", "WAITING_FOR_RESOURCES", "INITIALIZING", "RUNNING")


def _ms(value: datetime | None) -> int | None:
    return int(value.timestamp() * 1000) if value else None


def validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    out = dict(spec)
    if not out.get("name"):
        raise InvalidParameter("Pipeline name is required")
    if not out.get("catalog"):
        raise InvalidParameter("Pipeline catalog is required (Unity Catalog pipelines only)")
    out.setdefault("schema", out.get("target") or "default")
    if not out.get("libraries"):
        raise InvalidParameter("Pipeline libraries are required")
    for field in ("pipeline_id", "id"):
        out.pop(field, None)
    return out


def create_pipeline(conn: Connection, spec: dict[str, Any], user: str) -> str:
    spec = validate_spec(spec)
    pipeline_id = str(uuid.uuid4())
    conn.execute(
        insert(db.pipelines).values(
            pipeline_id=pipeline_id, name=spec["name"], spec=spec, creator_user_name=user
        )
    )
    add_event(conn, pipeline_id, None, "INFO", "create_update", f"Pipeline {spec['name']} created")
    return pipeline_id


def edit_pipeline(conn: Connection, pipeline_id: str, spec: dict[str, Any]) -> None:
    spec = validate_spec(spec)
    result = conn.execute(
        update(db.pipelines)
        .where(db.pipelines.c.pipeline_id == pipeline_id)
        .values(name=spec["name"], spec=spec)
    )
    if result.rowcount == 0:
        raise NotFound(f"Pipeline {pipeline_id} does not exist.")


def delete_pipeline(conn: Connection, pipeline_id: str) -> None:
    conn.execute(delete(db.pipelines).where(db.pipelines.c.pipeline_id == pipeline_id))
    conn.execute(delete(db.pipeline_events).where(db.pipeline_events.c.pipeline_id == pipeline_id))
    conn.execute(delete(db.pipeline_files).where(db.pipeline_files.c.pipeline_id == pipeline_id))


def exists(conn: Connection, pipeline_id: str) -> bool:
    row = conn.execute(
        select(db.pipelines.c.pipeline_id).where(db.pipelines.c.pipeline_id == pipeline_id)
    ).first()
    return row is not None


def get_row(conn: Connection, pipeline_id: str) -> dict[str, Any]:
    row = (
        conn.execute(select(db.pipelines).where(db.pipelines.c.pipeline_id == pipeline_id))
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound(f"Pipeline {pipeline_id} does not exist.")
    return dict(row)


def _update_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "update_id": row["update_id"],
        "pipeline_id": row["pipeline_id"],
        "state": row["state"],
        "cause": row["cause"],
        "full_refresh": row["full_refresh"],
        "creation_time": _ms(row["creation_time"]),
        "end_time": _ms(row["end_time"]),
        "message": row["message"],
        "task_run_id": row["task_run_id"],
    }


def latest_updates(conn: Connection, pipeline_id: str, limit: int = 5) -> list[dict[str, Any]]:
    pu = db.pipeline_updates
    rows = conn.execute(
        select(pu)
        .where(pu.c.pipeline_id == pipeline_id)
        .order_by(pu.c.creation_time.desc())
        .limit(limit)
    ).mappings()
    return [_update_json(dict(r)) for r in rows]


def pipeline_json(conn: Connection, row: dict[str, Any]) -> dict[str, Any]:
    updates = latest_updates(conn, row["pipeline_id"])
    running = bool(updates) and updates[0]["state"] in ACTIVE_UPDATE_STATES
    return {
        "pipeline_id": row["pipeline_id"],
        "name": row["name"],
        "spec": {**row["spec"], "id": row["pipeline_id"]},
        "state": "RUNNING" if running else "IDLE",
        "creator_user_name": row["creator_user_name"],
        "run_as_user_name": row["creator_user_name"],
        "latest_updates": [
            {"update_id": u["update_id"], "state": u["state"], "creation_time": u["creation_time"]}
            for u in updates
        ],
        "last_modified": _ms(row["created_at"]),
    }


def list_pipelines(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(select(db.pipelines).order_by(db.pipelines.c.name)).mappings().all()
    out = []
    for row in rows:
        full = pipeline_json(conn, dict(row))
        out.append(
            {
                k: full[k]
                for k in ("pipeline_id", "name", "state", "creator_user_name", "latest_updates")
            }
        )
    return out


def create_update(
    conn: Connection, pipeline_id: str, full_refresh: bool, cause: str = "API_CALL"
) -> str:
    get_row(conn, pipeline_id)
    pu = db.pipeline_updates
    active = conn.execute(
        select(func.count())
        .select_from(pu)
        .where(pu.c.pipeline_id == pipeline_id, pu.c.state.in_(ACTIVE_UPDATE_STATES))
    ).scalar_one()
    if active:
        raise InvalidParameter(
            f"Pipeline {pipeline_id} already has an active update", error_code="INVALID_STATE"
        )
    update_id = str(uuid.uuid4())
    conn.execute(
        insert(pu).values(
            update_id=update_id,
            pipeline_id=pipeline_id,
            state="QUEUED",
            cause=cause,
            full_refresh=full_refresh,
        )
    )
    add_event(
        conn,
        pipeline_id,
        update_id,
        "INFO",
        "create_update",
        f"Update {update_id[:6]} started by {cause}" + (" (full refresh)" if full_refresh else ""),
    )
    return update_id


def set_update(conn: Connection, update_id: str, **values: Any) -> None:
    conn.execute(
        update(db.pipeline_updates)
        .where(db.pipeline_updates.c.update_id == update_id)
        .values(**values)
    )


def get_update(conn: Connection, pipeline_id: str, update_id: str) -> dict[str, Any]:
    pu = db.pipeline_updates
    row = (
        conn.execute(select(pu).where(pu.c.pipeline_id == pipeline_id, pu.c.update_id == update_id))
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound(f"Update {update_id} of pipeline {pipeline_id} does not exist.")
    return _update_json(dict(row))


def add_event(
    conn: Connection,
    pipeline_id: str,
    update_id: str | None,
    level: str,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        insert(db.pipeline_events).values(
            pipeline_id=pipeline_id,
            update_id=update_id,
            level=level,
            event_type=event_type,
            message=message,
            details=details or {},
        )
    )


def list_events(
    conn: Connection, pipeline_id: str, max_results: int = 100, update_id: str | None = None
) -> list[dict[str, Any]]:
    pe = db.pipeline_events
    query = select(pe).where(pe.c.pipeline_id == pipeline_id)
    if update_id:
        query = query.where(pe.c.update_id == update_id)
    rows = conn.execute(query.order_by(pe.c.id.desc()).limit(max_results)).mappings()
    return [
        {
            "id": str(r["id"]),
            "timestamp": r["timestamp"].isoformat(),
            "level": r["level"],
            "event_type": r["event_type"],
            "message": r["message"],
            "details": r["details"],
            "origin": {"pipeline_id": pipeline_id, "update_id": r["update_id"]},
        }
        for r in rows
    ]


def processed_files(conn: Connection, pipeline_id: str) -> dict[str, list[str]]:
    pf = db.pipeline_files
    out: dict[str, list[str]] = {}
    for table, path in conn.execute(
        select(pf.c.table_name, pf.c.file_path).where(pf.c.pipeline_id == pipeline_id)
    ):
        out.setdefault(table, []).append(path)
    return out


def record_files(
    conn: Connection, pipeline_id: str, files: dict[str, list[str]], reset: list[str]
) -> None:
    pf = db.pipeline_files
    for table in reset:
        conn.execute(delete(pf).where(pf.c.pipeline_id == pipeline_id, pf.c.table_name == table))
    rows = [
        {"pipeline_id": pipeline_id, "table_name": t, "file_path": p}
        for t, paths in files.items()
        for p in paths
    ]
    if rows:
        conn.execute(insert(pf), rows)
