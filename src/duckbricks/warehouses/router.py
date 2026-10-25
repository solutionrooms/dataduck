"""Route Statement Execution requests to a warehouse, starting it if it is stopped.

Databricks starts a stopped warehouse when a statement targets it; so do we. An unknown
warehouse_id is an error. Statements without a warehouse_id run in the API process.
"""

from __future__ import annotations

from typing import Any

from duckbricks.core import db
from duckbricks.core.config import get_settings
from duckbricks.core.statements import QueryResult, WarehouseRunner
from duckbricks.warehouses import lifecycle
from duckbricks.warehouses.client import run_on_warehouse


def warehouse_router(warehouse_id: str) -> WarehouseRunner | None:
    settings = get_settings()
    engine = db.get_engine(settings.control_url)
    with engine.begin() as conn:
        row = lifecycle.get_row(conn, warehouse_id)
        if row["state"] != "RUNNING" or not row["port"]:
            row = lifecycle.start(conn, warehouse_id, settings)
        else:
            lifecycle.touch(conn, warehouse_id)
        port = int(row["port"])

    def run(
        sql: str, params: dict[str, Any], row_limit: int, catalog: str | None, schema: str | None
    ) -> QueryResult:
        return run_on_warehouse(port, sql, params, row_limit, catalog, schema, settings)

    return run
