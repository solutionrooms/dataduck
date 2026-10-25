"""SQL warehouse lifecycle (API side): create, edit, start, stop, auto-stop.

Warehouses are rows in the control database; a running warehouse is a process in the
`warehouse` container, started and stopped through its agent. Ports 9500-9509.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import uuid
from typing import Any

import httpx
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection

from duckbricks.core import db
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.errors import AlreadyExists, InvalidParameter, InvalidState, NotFound

PORTS = range(9500, 9510)
# Databricks cluster sizes to node types (memory and threads per warehouse process).
CLUSTER_SIZES = {
    "2X-Small": "dbk.small",
    "X-Small": "dbk.small",
    "Small": "dbk.medium",
    "Medium": "dbk.medium",
    "Large": "dbk.large",
    "X-Large": "dbk.large",
    "2X-Large": "dbk.large",
    "3X-Large": "dbk.large",
    "4X-Large": "dbk.large",
}
DEFAULT_SIZE = "Small"
DEFAULT_AUTO_STOP = 10
# Accepted and stored but without effect (see docs/compat.md).
IGNORED_FIELDS = (
    "min_num_clusters",
    "max_num_clusters",
    "enable_photon",
    "enable_serverless_compute",
    "spot_instance_policy",
    "warehouse_type",
    "channel",
    "tags",
    "instance_profile_arn",
)


class AgentError(InvalidState):
    pass


def _agent(settings: Settings, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    token = settings.warehouse_token.get_secret_value()
    try:
        response = httpx.request(
            method,
            settings.warehouse_agent_url.rstrip("/") + path,
            headers={"Authorization": f"Bearer {token}"},
            timeout=90,
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise AgentError(
            f"The warehouse service is not reachable at {settings.warehouse_agent_url}: {exc}. "
            "Start it with `make up`."
        ) from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise AgentError(f"Warehouse service error: {detail}")
    return dict(response.json())


def validate(body: dict[str, Any], partial: bool = False) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    values: dict[str, Any] = {}
    if "name" in body or not partial:
        name = (body.get("name") or "").strip()
        if not name:
            raise InvalidParameter("name is required")
        values["name"] = name
    if "cluster_size" in body or not partial:
        size = body.get("cluster_size") or DEFAULT_SIZE
        if size not in CLUSTER_SIZES:
            raise InvalidParameter(
                f"cluster_size must be one of {', '.join(CLUSTER_SIZES)}; got '{size}'"
            )
        values["cluster_size"] = size
    if "auto_stop_mins" in body or not partial:
        mins = body.get("auto_stop_mins", DEFAULT_AUTO_STOP)
        if mins is None:
            mins = DEFAULT_AUTO_STOP
        if not isinstance(mins, int) or (mins != 0 and mins < 1):
            raise InvalidParameter("auto_stop_mins must be 0 (never) or a positive integer")
        values["auto_stop_mins"] = mins
    settings = {k: body[k] for k in IGNORED_FIELDS if k in body}
    for key in settings:
        warnings.append(f"Field '{key}' is accepted but has no effect in Duckbricks")
    values["settings"] = settings
    return values, warnings


def warehouse_json(row: Any, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    stored = dict(row["settings"] or {})
    node_type = CLUSTER_SIZES.get(row["cluster_size"], "dbk.medium")
    out: dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "cluster_size": row["cluster_size"],
        "auto_stop_mins": row["auto_stop_mins"],
        "state": row["state"],
        "creator_name": row["creator_user_name"],
        "min_num_clusters": stored.get("min_num_clusters", 1),
        "max_num_clusters": stored.get("max_num_clusters", 1),
        "num_clusters": 1 if row["state"] == "RUNNING" else 0,
        "num_active_sessions": 0,
        "enable_photon": False,
        "enable_serverless_compute": False,
        "warehouse_type": "PRO",
        "spot_instance_policy": stored.get("spot_instance_policy", "COST_OPTIMIZED"),
        "tags": stored.get("tags", {"custom_tags": []}),
        "health": {"status": "HEALTHY" if row["state"] in ("RUNNING", "STOPPED") else "DEGRADED"},
        "jdbc_url": "",
        "odbc_params": {"hostname": "127.0.0.1", "port": row["port"] or 0, "protocol": "quack"},
        "node_type_id": node_type,
        "last_activity_at": row["last_activity_at"].isoformat()
        if row["last_activity_at"]
        else None,
    }
    if row["state"] == "RUNNING" and row["port"]:
        out["quack_uri"] = f"quack:127.0.0.1:{row['port']}"
    return out


def get_row(conn: Connection, warehouse_id: str) -> Any:
    row = (
        conn.execute(select(db.warehouses).where(db.warehouses.c.id == warehouse_id))
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound(f"Warehouse {warehouse_id} does not exist")
    return row


def find_by_name(conn: Connection, name: str) -> Any:
    return (
        conn.execute(select(db.warehouses).where(db.warehouses.c.name == name)).mappings().first()
    )


def list_rows(conn: Connection) -> list[Any]:
    return list(conn.execute(select(db.warehouses).order_by(db.warehouses.c.name)).mappings())


def create(conn: Connection, body: dict[str, Any], user: str) -> tuple[str, list[str]]:
    values, warnings = validate(body)
    if find_by_name(conn, values["name"]):
        raise AlreadyExists(f"A warehouse named '{values['name']}' already exists")
    warehouse_id = uuid.uuid4().hex[:16]
    conn.execute(
        insert(db.warehouses).values(
            id=warehouse_id, state="STOPPED", creator_user_name=user, **values
        )
    )
    return warehouse_id, warnings


def edit(conn: Connection, warehouse_id: str, body: dict[str, Any]) -> list[str]:
    row = get_row(conn, warehouse_id)
    values, warnings = validate(body, partial=True)
    if "name" in values and values["name"] != row["name"]:
        other = find_by_name(conn, values["name"])
        if other and other["id"] != warehouse_id:
            raise AlreadyExists(f"A warehouse named '{values['name']}' already exists")
    values["settings"] = {**dict(row["settings"] or {}), **values["settings"]}
    conn.execute(update(db.warehouses).where(db.warehouses.c.id == warehouse_id).values(**values))
    return warnings


def delete(conn: Connection, warehouse_id: str, settings: Settings | None = None) -> None:
    row = get_row(conn, warehouse_id)
    if row["state"] in ("RUNNING", "STARTING"):
        stop(conn, warehouse_id, settings)
    conn.execute(db.warehouses.delete().where(db.warehouses.c.id == warehouse_id))


def _free_port(conn: Connection) -> int:
    used = {
        r[0]
        for r in conn.execute(
            select(db.warehouses.c.port).where(
                db.warehouses.c.state.in_(("RUNNING", "STARTING")),
                db.warehouses.c.port.is_not(None),
            )
        )
    }
    for port in PORTS:
        if port not in used:
            return port
    raise InvalidState(f"All {len(PORTS)} warehouse ports are in use; stop a warehouse first")


def start(conn: Connection, warehouse_id: str, settings: Settings | None = None) -> Any:
    """Start a warehouse and wait until it accepts queries. Idempotent."""
    settings = settings or get_settings()
    row = (
        conn.execute(
            select(db.warehouses).where(db.warehouses.c.id == warehouse_id).with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound(f"Warehouse {warehouse_id} does not exist")
    port = row["port"] if row["state"] == "RUNNING" and row["port"] else _free_port(conn)
    result = _agent(
        settings,
        "POST",
        f"/warehouses/{warehouse_id}/start",
        json={"port": port, "node_type": CLUSTER_SIZES.get(row["cluster_size"])},
    )
    conn.execute(
        update(db.warehouses)
        .where(db.warehouses.c.id == warehouse_id)
        .values(state="RUNNING", port=result["port"], last_activity_at=func.now())
    )
    return get_row(conn, warehouse_id)


def stop(conn: Connection, warehouse_id: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    get_row(conn, warehouse_id)
    _agent(settings, "POST", f"/warehouses/{warehouse_id}/stop")
    conn.execute(
        update(db.warehouses)
        .where(db.warehouses.c.id == warehouse_id)
        .values(state="STOPPED", port=None)
    )


def touch(conn: Connection, warehouse_id: str) -> None:
    conn.execute(
        update(db.warehouses)
        .where(db.warehouses.c.id == warehouse_id)
        .values(last_activity_at=func.now())
    )


def reconcile(conn: Connection, settings: Settings | None = None) -> None:
    """Mark warehouses STOPPED when the agent no longer runs them (e.g. after a restart)."""
    settings = settings or get_settings()
    try:
        alive = _agent(settings, "GET", "/warehouses")["warehouses"]
    except AgentError:
        return
    for row in list_rows(conn):
        if row["state"] == "RUNNING" and row["id"] not in alive:
            conn.execute(
                update(db.warehouses)
                .where(db.warehouses.c.id == row["id"])
                .values(state="STOPPED", port=None)
            )


def auto_stop_idle() -> None:
    """Scheduler step: stop running warehouses idle for longer than auto_stop_mins."""
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    with db.get_engine(settings.control_url).begin() as conn:
        reconcile(conn, settings)
        for row in list_rows(conn):
            if row["state"] != "RUNNING" or not row["auto_stop_mins"]:
                continue
            last = row["last_activity_at"] or row["created_at"]
            if now - last >= dt.timedelta(minutes=row["auto_stop_mins"]):
                with contextlib.suppress(AgentError):
                    stop(conn, row["id"], settings)
