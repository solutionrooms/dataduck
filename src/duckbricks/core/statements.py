"""Statement Execution API: run SQL in-process or on a warehouse, Databricks response shapes.

Statement state lives in this process's memory (ADR 0001 #13); the `statements` table keeps
the query history. Results use the `JSON_ARRAY` format with `INLINE` disposition only.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import decimal
import json
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import duckdb
from sqlalchemy import insert, update

from duckbricks.core import db
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.errors import InvalidParameter, NotFound
from duckbricks.core.sql_engine import SqlEngine

DEFAULT_ROW_LIMIT = 1_000
MAX_ROW_LIMIT = 10_000
KEEP_RESULTS = 200
PARAM_MARKER = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")

TYPE_NAMES = {
    "VARCHAR": "STRING",
    "INTEGER": "INT",
    "BIGINT": "LONG",
    "HUGEINT": "DECIMAL",
    "SMALLINT": "SHORT",
    "TINYINT": "BYTE",
    "UINTEGER": "LONG",
    "UBIGINT": "DECIMAL",
    "USMALLINT": "INT",
    "UTINYINT": "SHORT",
    "DOUBLE": "DOUBLE",
    "FLOAT": "FLOAT",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP_NTZ",
    "TIMESTAMP WITH TIME ZONE": "TIMESTAMP",
    "BLOB": "BINARY",
    "INTERVAL": "INTERVAL",
    "UUID": "STRING",
    "JSON": "STRING",
}


def type_name(type_text: str) -> str:
    upper = type_text.upper()
    if upper.startswith("DECIMAL"):
        return "DECIMAL"
    if upper.endswith("[]") or upper.startswith("LIST"):
        return "ARRAY"
    if upper.startswith("STRUCT"):
        return "STRUCT"
    if upper.startswith("MAP"):
        return "MAP"
    return TYPE_NAMES.get(upper, "STRING")


def to_json_value(value: Any) -> str | None:
    """JSON_ARRAY results carry every value as a string (or null), as Databricks does."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, default=str)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    return str(value)


def replace_markers(sql: str, names: set[str] | None = None) -> str:
    """`:name` -> `$name` outside quotes and comments; only `names` when given."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            end = i + 1
            while end < n:
                if sql[end] == ch:
                    if end + 1 < n and sql[end + 1] == ch:
                        end += 2
                        continue
                    break
                end += 1
            out.append(sql[i : end + 1])
            i = end + 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end < 0 else end
            out.append(sql[i:end])
            i = end
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(sql[i:end])
            i = end
        elif ch == ":" and (
            i == 0 or not (sql[i - 1] == ":" or sql[i - 1].isalnum() or sql[i - 1] == "_")
        ):
            match = PARAM_MARKER.match(sql, i)
            if match and (names is None or match.group(1) in names) and not sql.startswith("::", i):
                out.append(f"${match.group(1)}")
                i = match.end()
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def convert_parameters(
    statement: str, parameters: list[dict[str, Any]] | None
) -> tuple[str, dict[str, Any]]:
    """Databricks `:name` markers become DuckDB `$name`; values are cast by their `type`."""
    if not parameters:
        return statement, {}
    values: dict[str, Any] = {}
    for p in parameters:
        name = p.get("name")
        if not name:
            raise InvalidParameter("Every statement parameter needs a name")
        values[name] = _cast(p.get("value"), str(p.get("type") or "STRING").upper())
    return replace_markers(statement, set(values)), values


def _cast(value: Any, kind: str) -> Any:
    if value is None:
        return None
    try:
        if kind in ("INT", "BIGINT", "LONG", "SHORT", "SMALLINT", "TINYINT", "BYTE"):
            return int(value)
        if kind in ("DOUBLE", "FLOAT"):
            return float(value)
        if kind.startswith("DECIMAL"):
            return decimal.Decimal(str(value))
        if kind == "BOOLEAN":
            return str(value).lower() == "true"
        if kind == "DATE":
            return dt.date.fromisoformat(str(value))
        if kind.startswith("TIMESTAMP"):
            return dt.datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise InvalidParameter(f"Parameter value '{value}' is not a valid {kind}") from exc
    return str(value)


def parse_wait_timeout(value: str | None) -> float:
    if value is None:
        return 10.0
    match = re.fullmatch(r"(\d+)s", value.strip())
    if not match:
        raise InvalidParameter("wait_timeout must look like '10s'")
    seconds = int(match.group(1))
    if seconds != 0 and not 5 <= seconds <= 50:
        raise InvalidParameter("wait_timeout must be 0s or between 5s and 50s")
    return float(seconds)


@dataclass
class QueryResult:
    columns: list[tuple[str, str]]  # (name, type_text)
    rows: list[list[str | None]]
    truncated: bool


@dataclass
class StatementRun:
    statement_id: str
    request: dict[str, Any]
    state: str = "PENDING"
    result: QueryResult | None = None
    error: dict[str, str] | None = None
    cursor: duckdb.DuckDBPyConnection | None = None
    cancel_hook: Callable[[], None] | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def response(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "statement_id": self.statement_id,
            "status": {"state": self.state},
        }
        if self.error:
            out["status"]["error"] = self.error
        if self.state == "SUCCEEDED" and self.result is not None:
            result = self.result
            out["manifest"] = {
                "format": "JSON_ARRAY",
                "schema": {
                    "column_count": len(result.columns),
                    "columns": [
                        {
                            "name": name,
                            "type_text": type_text,
                            "type_name": type_name(type_text),
                            "position": i,
                        }
                        for i, (name, type_text) in enumerate(result.columns)
                    ],
                },
                "total_chunk_count": 1,
                "chunks": [{"chunk_index": 0, "row_offset": 0, "row_count": len(result.rows)}],
                "total_row_count": len(result.rows),
                "truncated": result.truncated,
            }
            out["result"] = {
                "chunk_index": 0,
                "row_offset": 0,
                "row_count": len(result.rows),
                "data_array": result.rows,
            }
        return out


def run_query(
    cur: duckdb.DuckDBPyConnection, sql: str, params: dict[str, Any], row_limit: int
) -> QueryResult:
    relation = cur.execute(sql, params) if params else cur.execute(sql)
    if relation.description is None:
        return QueryResult(columns=[], rows=[], truncated=False)
    columns = [(d[0], str(d[1])) for d in relation.description]
    fetched = relation.fetchmany(row_limit + 1)
    rows = [[to_json_value(v) for v in row] for row in fetched[:row_limit]]
    return QueryResult(columns=columns, rows=rows, truncated=len(fetched) > row_limit)


# A warehouse router returns a callable that runs (sql, params, row_limit) on a warehouse,
# plus a cancel hook, or None to run in-process.
WarehouseRunner = Callable[[str, dict[str, Any], int, str | None, str | None], QueryResult]
WarehouseRouter = Callable[[str], WarehouseRunner | None]


class StatementService:
    def __init__(
        self,
        engine: SqlEngine,
        settings: Settings | None = None,
        router: WarehouseRouter | None = None,
    ):
        self.engine = engine
        self.settings = settings or get_settings()
        self.router = router
        self._runs: OrderedDict[str, StatementRun] = OrderedDict()
        self._lock = threading.Lock()

    def _remember(self, run: StatementRun) -> None:
        with self._lock:
            self._runs[run.statement_id] = run
            while len(self._runs) > KEEP_RESULTS:
                self._runs.popitem(last=False)

    def execute(self, request: dict[str, Any], source: str = "API") -> dict[str, Any]:
        statement = (request.get("statement") or "").strip()
        if not statement:
            raise InvalidParameter("statement is required")
        row_limit = int(request.get("row_limit") or DEFAULT_ROW_LIMIT)
        if row_limit < 1:
            raise InvalidParameter("row_limit must be positive")
        request = {**request, "row_limit": min(row_limit, MAX_ROW_LIMIT)}
        if (request.get("format") or "JSON_ARRAY") != "JSON_ARRAY":
            raise InvalidParameter("Only format JSON_ARRAY is supported")
        if (request.get("disposition") or "INLINE") != "INLINE":
            raise InvalidParameter("Only disposition INLINE is supported")
        wait = parse_wait_timeout(request.get("wait_timeout"))
        on_timeout = request.get("on_wait_timeout") or "CONTINUE"
        run = StatementRun(statement_id=str(uuid.uuid4()), request=request)
        self._remember(run)
        with db.get_engine(self.settings.control_url).begin() as conn:
            conn.execute(
                insert(db.statements).values(
                    statement_id=run.statement_id,
                    statement_text=statement,
                    warehouse_id=request.get("warehouse_id"),
                    catalog=request.get("catalog"),
                    schema=request.get("schema"),
                    source=source,
                    status="RUNNING",
                )
            )
        thread = threading.Thread(target=self._run, args=(run,), daemon=True)
        run.state = "RUNNING"
        thread.start()
        if wait and run.done.wait(wait):
            return run.response()
        if wait and on_timeout == "CANCEL":
            self._cancel(run)
            run.done.wait(5)
        return run.response()

    def _run(self, run: StatementRun) -> None:
        request = run.request
        sql, params = convert_parameters(request["statement"], request.get("parameters"))
        row_limit = int(request["row_limit"])
        read_only = bool(request.get("read_only"))
        try:
            runner = None
            if request.get("warehouse_id") and not read_only and self.router is not None:
                runner = self.router(str(request["warehouse_id"]))
            if runner is not None:
                run.result = runner(
                    sql, params, row_limit, request.get("catalog"), request.get("schema")
                )
            else:
                cur = self.engine.cursor(read_only, request.get("catalog"), request.get("schema"))
                run.cursor = cur
                try:
                    run.result = run_query(cur, sql, params, row_limit)
                finally:
                    cur.close()
            if run.state != "CANCELED":
                run.state = "SUCCEEDED"
        except duckdb.InterruptException:
            run.state = "CANCELED"
        except Exception as exc:  # reported to the caller
            if run.state != "CANCELED":
                run.state = "FAILED"
                run.error = {"error_code": _error_code(exc), "message": str(exc)}
        finally:
            run.cursor = None
            self._record(run)
            run.done.set()

    def _record(self, run: StatementRun) -> None:
        rows = len(run.result.rows) if run.result else None
        with db.get_engine(self.settings.control_url).begin() as conn:
            conn.execute(
                update(db.statements)
                .where(db.statements.c.statement_id == run.statement_id)
                .values(
                    status=run.state,
                    error=run.error["message"] if run.error else None,
                    row_count=rows,
                    end_time=dt.datetime.now(dt.UTC),
                )
            )

    def get(self, statement_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(statement_id)
        if run is None:
            raise NotFound(
                f"Statement {statement_id} not found (results are kept for the most recent "
                f"{KEEP_RESULTS} statements of this API process)."
            )
        return run.response()

    def cancel(self, statement_id: str) -> None:
        with self._lock:
            run = self._runs.get(statement_id)
        if run is None:
            raise NotFound(f"Statement {statement_id} not found")
        self._cancel(run)

    def _cancel(self, run: StatementRun) -> None:
        if run.done.is_set():
            return
        run.state = "CANCELED"
        if run.cursor is not None:
            with contextlib.suppress(duckdb.Error):
                run.cursor.interrupt()
        if run.cancel_hook is not None:
            run.cancel_hook()


def _error_code(exc: Exception) -> str:
    name = type(exc).__name__
    if name in ("CatalogException", "BinderException"):
        return "NOT_FOUND" if "does not exist" in str(exc) else "BAD_REQUEST"
    if name == "ParserException":
        return "BAD_REQUEST"
    if name in ("PermissionException",) or "read-only" in str(exc) or "READ_ONLY" in str(exc):
        return "PERMISSION_DENIED"
    if name == "InvalidInputException" and "attached in read-only mode" in str(exc):
        return "PERMISSION_DENIED"
    return "BAD_REQUEST"
