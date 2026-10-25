"""Connections (Unity Catalog API shape) and the Databricks mirror (Duckbricks extension).

/api/2.1/unity-catalog/connections   list, create, get, delete
/api/duckbricks/mirrors              mirror config, status, syncs (ADR 0013)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from duckbricks.core import db
from duckbricks.core.catalogs import list_catalogs
from duckbricks.core.config import get_settings
from duckbricks.core.errors import NotFound
from duckbricks.federation import connections as conns
from duckbricks.federation import mirror

router = APIRouter(tags=["Federation"])


class CreateConnection(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    connection_type: str
    options: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = None


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tables: list[str] = Field(default_factory=list, description="schema.table globs; empty = all")
    full_refresh: bool = False


def _engine() -> Any:
    return db.get_engine(get_settings().control_url)


@router.get("/api/2.1/unity-catalog/connections")
def list_connections() -> dict[str, Any]:
    return {"connections": [conns.connection_json(c) for c in conns.list_connections()]}


@router.post("/api/2.1/unity-catalog/connections")
def create_connection(body: CreateConnection) -> dict[str, Any]:
    connection, warnings = conns.create_connection(
        body.name, body.connection_type, body.options, body.comment
    )
    out = conns.connection_json(connection)
    if warnings:
        out["warnings"] = warnings
    return out


@router.get("/api/2.1/unity-catalog/connections/{name}")
def get_connection(name: str) -> dict[str, Any]:
    return conns.connection_json(conns.get_connection(name))


@router.delete("/api/2.1/unity-catalog/connections/{name}")
def delete_connection(name: str) -> dict[str, Any]:
    conns.delete_connection(name)
    return {}


def _mirror_summary(name: str) -> dict[str, Any]:
    info = mirror.require_mirror_catalog(name)
    with _engine().connect() as conn:
        config = mirror.load_config(conn, name)
        tables = [
            {
                **{k: v for k, v in row.items() if k != "catalog"},
                "synced_at": row["synced_at"].isoformat() if row["synced_at"] else None,
                "checked_at": row["checked_at"].isoformat() if row["checked_at"] else None,
            }
            for row in conn.execute(
                select(db.mirror_tables)
                .where(db.mirror_tables.c.catalog == name)
                .order_by(db.mirror_tables.c.schema_name, db.mirror_tables.c.table_name)
            ).mappings()
        ]
        syncs = [
            mirror.sync_json(r)
            for r in conn.execute(
                select(db.mirror_syncs)
                .where(db.mirror_syncs.c.catalog == name)
                .order_by(db.mirror_syncs.c.sync_id.desc())
                .limit(10)
            ).mappings()
        ]
    return {
        "catalog": name,
        "mode": info.mode,
        "connection_name": info.connection_name,
        "remote_catalog": info.remote_catalog,
        "config": config.model_dump(mode="json") if config else None,
        "tables": tables,
        "syncs": syncs,
    }


@router.get("/api/duckbricks/mirrors")
def list_mirrors() -> dict[str, Any]:
    return {"mirrors": [_mirror_summary(i.name) for i in list_catalogs() if i.is_foreign]}


@router.get("/api/duckbricks/mirrors/{catalog}")
def get_mirror(catalog: str) -> dict[str, Any]:
    return _mirror_summary(catalog)


@router.put("/api/duckbricks/mirrors/{catalog}")
def put_mirror(catalog: str, body: mirror.MirrorConfig) -> dict[str, Any]:
    mirror.require_mirror_catalog(catalog)
    with _engine().begin() as conn:
        mirror.save_config(conn, catalog, body)
    return _mirror_summary(catalog)


@router.post("/api/duckbricks/mirrors/{catalog}/sync")
def sync_mirror(catalog: str, body: SyncRequest | None = None) -> dict[str, Any]:
    info = mirror.require_mirror_catalog(catalog)
    body = body or SyncRequest()
    with _engine().begin() as conn:
        if mirror.load_config(conn, catalog) is None:
            raise NotFound(
                f"No mirror config for '{catalog}': PUT /api/duckbricks/mirrors/{catalog}"
            )
        sync_id, created = mirror.enqueue(
            conn, catalog, "MANUAL", body.tables or None, body.full_refresh
        )
    out: dict[str, Any] = {"sync_id": sync_id, "queued": created}
    if info.mode != "mirror":
        out["warnings"] = [
            f"'{catalog}' is in live mode: the copy is made, but queries keep going to Databricks "
            f"until options.duckbricks_mode is mirror"
        ]
    return out


@router.get("/api/duckbricks/mirrors/{catalog}/syncs")
def list_syncs(catalog: str, limit: int = 20) -> dict[str, Any]:
    mirror.require_mirror_catalog(catalog)
    with _engine().connect() as conn:
        rows = conn.execute(
            select(db.mirror_syncs)
            .where(db.mirror_syncs.c.catalog == catalog)
            .order_by(db.mirror_syncs.c.sync_id.desc())
            .limit(min(max(limit, 1), 200))
        ).mappings()
        return {"syncs": [mirror.sync_json(r) for r in rows]}


@router.get("/api/duckbricks/mirror-syncs/{sync_id}")
def get_sync(sync_id: int) -> dict[str, Any]:
    with _engine().connect() as conn:
        row = (
            conn.execute(select(db.mirror_syncs).where(db.mirror_syncs.c.sync_id == sync_id))
            .mappings()
            .first()
        )
    if row is None:
        raise NotFound(f"Mirror sync {sync_id} does not exist")
    return mirror.sync_json(row)
