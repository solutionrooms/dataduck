"""Route Statement Execution requests to a warehouse (filled in with the warehouse client)."""

from __future__ import annotations

from duckbricks.core.statements import WarehouseRunner


def warehouse_router(warehouse_id: str) -> WarehouseRunner | None:
    return None
