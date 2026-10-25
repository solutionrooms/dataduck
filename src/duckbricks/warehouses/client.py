"""Run a statement on a running warehouse over Quack (API side)."""

from __future__ import annotations

import datetime as dt
import decimal
import re
import threading
from typing import Any

import duckdb

from duckbricks.core.config import Settings, get_settings
from duckbricks.core.session import open_session, sql_identifier, sql_literal
from duckbricks.core.statements import QueryResult, to_json_value

_local = threading.local()
_MARKER = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _client(settings: Settings) -> duckdb.DuckDBPyConnection:
    """One Quack client connection per thread: no catalogs, just the extension."""
    con = getattr(_local, "con", None)
    if con is None:
        con = open_session(settings.api_node_type, catalogs=[], settings=settings)
        con.install_extension("quack")
        con.load_extension("quack")
        _local.con = con
    return con


def literal(value: Any) -> str:
    """A DuckDB literal for a bound parameter (Quack takes SQL text, not bind values)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) + "::DOUBLE"
    if isinstance(value, decimal.Decimal):
        return f"{format(value, 'f')}::DECIMAL(38, {max(-value.as_tuple().exponent, 0)})"  # type: ignore[operator]
    if isinstance(value, dt.datetime):
        return f"TIMESTAMP {sql_literal(value.isoformat(sep=' '))}"
    if isinstance(value, dt.date):
        return f"DATE {sql_literal(value.isoformat())}"
    return sql_literal(str(value))


def inline_parameters(sql: str, params: dict[str, Any]) -> str:
    """Replace `$name` markers with literals, outside quotes and comments."""
    if not params:
        return sql
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            end = i + 1
            while end < n and not (sql[end] == ch and sql[end + 1 : end + 2] != ch):
                end += 2 if sql[end] == ch else 1
            out.append(sql[i : end + 1])
            i = end + 1
        elif sql.startswith("--", i) or sql.startswith("/*", i):
            close = "\n" if sql.startswith("--", i) else "*/"
            end = sql.find(close, i + 2)
            end = n if end < 0 else end + len(close)
            out.append(sql[i:end])
            i = end
        elif (match := _MARKER.match(sql, i)) and match.group(1) in params:
            out.append(literal(params[match.group(1)]))
            i = match.end()
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def run_on_warehouse(
    port: int,
    sql: str,
    params: dict[str, Any],
    row_limit: int,
    catalog: str | None,
    schema: str | None,
    settings: Settings | None = None,
) -> QueryResult:
    settings = settings or get_settings()
    text = inline_parameters(sql, params)
    if catalog:
        target = sql_identifier(catalog) + (f".{sql_identifier(schema)}" if schema else "")
        text = f"USE {target}; {text}"
    elif schema:
        text = f"USE {sql_identifier(schema)}; {text}"
    uri = f"quack:{settings.warehouse_host}:{port}"
    con = _client(settings)
    relation = con.execute(
        "SELECT * FROM quack_query(?, ?, token => ?, disable_ssl => true)",
        [uri, text, settings.warehouse_token.get_secret_value()],
    )
    if relation.description is None:
        return QueryResult(columns=[], rows=[], truncated=False)
    columns = [(d[0], str(d[1])) for d in relation.description]
    fetched = relation.fetchmany(row_limit + 1)
    rows = [[to_json_value(v) for v in row] for row in fetched[:row_limit]]
    return QueryResult(columns=columns, rows=rows, truncated=len(fetched) > row_limit)
