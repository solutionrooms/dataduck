"""SQL Warehouses API (`/api/2.0/sql/warehouses`)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from duckbricks.api.deps import control
from duckbricks.core.config import get_settings
from duckbricks.warehouses import lifecycle

router = APIRouter(prefix="/api/2.0/sql/warehouses", tags=["SQL Warehouses"])


class WarehouseBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None = None
    cluster_size: str | None = None
    auto_stop_mins: int | None = None


def _body(body: WarehouseBody) -> dict[str, Any]:
    return body.model_dump(exclude_unset=True)


@router.get("")
def list_warehouses() -> dict[str, Any]:
    with control() as conn:
        lifecycle.reconcile(conn)
        return {"warehouses": [lifecycle.warehouse_json(r) for r in lifecycle.list_rows(conn)]}


@router.post("")
def create_warehouse(body: WarehouseBody) -> dict[str, Any]:
    with control() as conn:
        warehouse_id, warnings = lifecycle.create(conn, _body(body), get_settings().user_name)
    out: dict[str, Any] = {"id": warehouse_id}
    if warnings:
        out["warnings"] = warnings
    return out


@router.get("/{warehouse_id}")
def get_warehouse(warehouse_id: str) -> dict[str, Any]:
    with control() as conn:
        return lifecycle.warehouse_json(lifecycle.get_row(conn, warehouse_id))


@router.post("/{warehouse_id}/edit")
def edit_warehouse(warehouse_id: str, body: WarehouseBody) -> dict[str, Any]:
    with control() as conn:
        warnings = lifecycle.edit(conn, warehouse_id, _body(body))
    return {"warnings": warnings} if warnings else {}


@router.delete("/{warehouse_id}")
def delete_warehouse(warehouse_id: str) -> dict[str, Any]:
    with control() as conn:
        lifecycle.delete(conn, warehouse_id)
    return {}


@router.post("/{warehouse_id}/start")
def start_warehouse(warehouse_id: str) -> dict[str, Any]:
    with control() as conn:
        lifecycle.start(conn, warehouse_id)
    return {}


@router.post("/{warehouse_id}/stop")
def stop_warehouse(warehouse_id: str) -> dict[str, Any]:
    with control() as conn:
        lifecycle.stop(conn, warehouse_id)
    return {}
