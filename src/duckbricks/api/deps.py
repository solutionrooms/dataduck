"""Shared API objects: one SQL engine, statement service and catalog service per process."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection

from duckbricks.core import db
from duckbricks.core.config import get_settings
from duckbricks.core.sql_engine import get_sql_engine
from duckbricks.core.statements import StatementService
from duckbricks.core.unity_catalog import UnityCatalog
from duckbricks.core.workspace import Workspace


@contextmanager
def control() -> Iterator[Connection]:
    """A control-database transaction."""
    with db.get_engine(get_settings().control_url).begin() as conn:
        yield conn


@lru_cache
def statement_service() -> StatementService:
    from duckbricks.federation.live import preparer
    from duckbricks.warehouses.router import warehouse_router

    return StatementService(get_sql_engine(), router=warehouse_router, prelude=preparer())


@lru_cache
def unity_catalog() -> UnityCatalog:
    return UnityCatalog(get_sql_engine())


@lru_cache
def workspace() -> Workspace:
    return Workspace()


def source_of(user_agent: str | None, explicit: str | None = None) -> str:
    """Query-history source: UI, MCP, CLI, or API."""
    if explicit:
        return explicit.upper()
    agent = (user_agent or "").lower()
    for marker, source in (("duckbricks-ui", "UI"), ("dbk-mcp", "MCP"), ("dbk-cli", "CLI")):
        if marker in agent:
            return source
    return "API"
