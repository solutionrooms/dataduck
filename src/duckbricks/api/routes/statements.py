"""Statement Execution API: /api/2.0/sql/statements, plus query history."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from duckbricks.api.deps import control, source_of, statement_service
from duckbricks.core import db

router = APIRouter(tags=["SQL"])


class StatementParameter(BaseModel):
    name: str
    value: str | None = None
    type: str | None = None


class ExecuteStatement(BaseModel):
    model_config = ConfigDict(extra="allow")
    statement: str
    warehouse_id: str | None = None
    catalog: str | None = None
    schema_: str | None = None
    parameters: list[StatementParameter] | None = None
    wait_timeout: str | None = None
    on_wait_timeout: str | None = None
    row_limit: int | None = None
    format: str | None = None
    disposition: str | None = None
    # Duckbricks extensions: run with every catalog attached READ_ONLY; label the caller.
    read_only: bool = False
    client: str | None = None


@router.post("/api/2.0/sql/statements")
@router.post("/api/2.0/sql/statements/")
def execute(body: dict[str, Any], user_agent: str | None = Header(default=None)) -> dict[str, Any]:
    request = ExecuteStatement.model_validate({**body, "schema_": body.get("schema")})
    payload = request.model_dump(exclude_none=True)
    payload["schema"] = payload.pop("schema_", None)
    if request.parameters:
        payload["parameters"] = [p.model_dump() for p in request.parameters]
    return statement_service().execute(payload, source_of(user_agent, request.client))


@router.get("/api/2.0/sql/statements/{statement_id}")
def get_statement(statement_id: str) -> dict[str, Any]:
    return statement_service().get(statement_id)


@router.get("/api/2.0/sql/statements/{statement_id}/result/chunks/{chunk_index}")
def get_chunk(statement_id: str, chunk_index: int) -> dict[str, Any]:
    response = statement_service().get(statement_id)
    return response.get("result", {"chunk_index": chunk_index, "data_array": []})


@router.post("/api/2.0/sql/statements/{statement_id}/cancel")
def cancel(statement_id: str) -> dict[str, Any]:
    statement_service().cancel(statement_id)
    return {}


def _ms(value: datetime | None) -> int | None:
    return int(value.timestamp() * 1000) if value else None


@router.get("/api/2.0/sql/history/queries")
def query_history(
    max_results: int = 100,
    warehouse_id: str | None = None,
    status: str | None = None,
    start_time_from: int | None = None,
) -> dict[str, Any]:
    st = db.statements
    query = select(st).order_by(st.c.start_time.desc()).limit(min(max_results, 1000))
    if warehouse_id:
        query = query.where(st.c.warehouse_id == warehouse_id)
    if status:
        query = query.where(st.c.status == status)
    if start_time_from:
        query = query.where(st.c.start_time >= datetime.fromtimestamp(start_time_from / 1000, UTC))
    with control() as conn:
        rows = conn.execute(query).mappings().all()
    return {
        "res": [
            {
                "query_id": r["statement_id"],
                "query_text": r["statement_text"],
                "status": {"SUCCEEDED": "FINISHED"}.get(r["status"], r["status"]),
                "statement_status": r["status"],
                "warehouse_id": r["warehouse_id"],
                "catalog": r["catalog"],
                "schema": r["schema"],
                "client_application": r["source"],
                "error_message": r["error"],
                "rows_produced": r["row_count"],
                "query_start_time_ms": _ms(r["start_time"]),
                "query_end_time_ms": _ms(r["end_time"]),
                "duration": (
                    _ms(r["end_time"]) - _ms(r["start_time"])  # type: ignore[operator]
                    if r["end_time"]
                    else None
                ),
            }
            for r in rows
        ],
        "has_next_page": False,
    }
