"""Imported by task code and notebooks: `from duckbricks.runtime import con, spark, dbutils`.

- `con`: a DuckDB session from the platform's session factory (every catalog attached).
- `spark`: a SQLFrame `DuckDBSession` over `con`, so PySpark-style code runs on DuckDB (ADR 0008).
- `dbutils`: a shim with `widgets`, `jobs.taskValues` and `notebook.exit`.

Inside a job run, the worker passes the run context in `DBK_TASK_CONTEXT`. Outside a run (local
development), the same names work with defaults: `dbutils.widgets.get` raises for unknown
widgets and `taskValues.get` returns `debugValue`.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb

__all__ = ["con", "context", "dbutils", "spark"]


@lru_cache
def context() -> dict[str, Any]:
    raw = os.environ.get("DBK_TASK_CONTEXT")
    return json.loads(raw) if raw else {}


@lru_cache
def _con() -> duckdb.DuckDBPyConnection:
    from duckbricks.core.session import open_session

    ctx = context()
    return open_session(
        ctx.get("node_type"),
        default_catalog=ctx.get("catalog") or "workspace",
        default_schema=ctx.get("schema"),
    )


@lru_cache
def _spark() -> Any:
    from sqlframe.duckdb import DuckDBSession

    session = DuckDBSession(conn=_con())
    original_sql = session.sql

    def sql(sqlQuery: Any, dialect: Any = None, qualify: bool = True) -> Any:
        if qualify and isinstance(sqlQuery, str):
            _register_tables(session, sqlQuery, dialect)
        return original_sql(sqlQuery, dialect=dialect, qualify=qualify)

    session.sql = sql
    return session


def _register_tables(session: Any, query: str, dialect: Any) -> None:
    """Teach SQLFrame the columns of qualified tables a query names.

    `spark.sql` resolves columns only for tables SQLFrame has already seen (through
    `spark.table`), so `SELECT x FROM dev.gold.t` would fail on a fresh session.
    """
    import contextlib

    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(query, read=dialect or session.input_dialect)
    except sqlglot.errors.ParseError:
        return
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not table.db or table.name in ctes:
            continue
        name = exp.table_(table.name, db=table.db, catalog=table.catalog or None)
        with contextlib.suppress(Exception):
            session.catalog.add_table(name)


class _Api:
    def __init__(self) -> None:
        from duckbricks.core.config import get_settings

        settings = get_settings()
        self.host = os.environ.get("DBK_HOST", settings.host).rstrip("/")
        self.token = os.environ.get("DBK_TOKEN", settings.token.get_secret_value())

    def call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        import httpx

        response = httpx.request(
            method,
            f"{self.host}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30,
            **kwargs,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} failed: {response.text}")
        result: dict[str, Any] = response.json()
        return result


class _Widgets:
    def __init__(self) -> None:
        self._defaults: dict[str, str] = {}

    def _values(self) -> dict[str, str]:
        return {**self._defaults, **context().get("widgets", {})}

    def get(self, name: str) -> str:
        values = self._values()
        if name not in values:
            raise ValueError(f"InputWidgetNotDefined: No input widget named {name} is defined")
        return str(values[name])

    def getAll(self) -> dict[str, str]:
        return self._values()

    def text(self, name: str, defaultValue: str = "", label: str | None = None) -> None:
        self._defaults[name] = defaultValue

    def dropdown(
        self,
        name: str,
        defaultValue: str,
        choices: list[str],
        label: str | None = None,
    ) -> None:
        self._defaults[name] = defaultValue

    def removeAll(self) -> None:
        self._defaults.clear()


class _TaskValues:
    def set(self, key: str, value: Any) -> None:
        ctx = context()
        if not ctx.get("task_run_id"):
            return  # outside a job run, like Databricks
        json.dumps(value)  # must be JSON-serialisable
        _Api().call(
            "POST",
            "/api/duckbricks/task-values",
            json={"run_id": ctx["task_run_id"], "key": key, "value": value},
        )

    def get(
        self,
        taskKey: str,
        key: str,
        default: Any = None,
        debugValue: Any = None,
    ) -> Any:
        ctx = context()
        if not ctx.get("job_run_id"):
            if debugValue is None:
                raise TypeError("Must pass debugValue when calling get outside of a job context")
            return debugValue
        try:
            return _Api().call(
                "GET",
                "/api/duckbricks/task-values",
                params={"run_id": ctx["job_run_id"], "task_key": taskKey, "key": key},
            )["value"]
        except RuntimeError:
            if default is not None:
                return default
            raise ValueError(f"Task value '{key}' of task '{taskKey}' is not set") from None


class _Jobs:
    taskValues = _TaskValues()


class _Notebook:
    def exit(self, value: Any) -> None:
        outcome = os.environ.get("DBK_TASK_OUTCOME")
        if outcome:
            Path(outcome).write_text(json.dumps({"notebook_output": {"result": str(value)}}))
        raise SystemExit(0)


class DBUtils:
    widgets = _Widgets()
    jobs = _Jobs()
    notebook = _Notebook()

    def help(self) -> str:
        return (
            "Duckbricks dbutils shim: widgets.get/text/getAll, jobs.taskValues.set/get, "
            "notebook.exit"
        )


dbutils = DBUtils()


def __getattr__(name: str) -> Any:
    if name == "con":
        return _con()
    if name == "spark":
        return _spark()
    raise AttributeError(f"module 'duckbricks.runtime' has no attribute {name!r}")
