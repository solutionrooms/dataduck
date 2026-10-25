"""In-process SQL for the API: two long-lived DuckDB sessions (read-write and read-only).

Each request gets its own cursor, so `USE` and interrupts are per request. Catalogs created
after start-up are attached on the next request.
"""

from __future__ import annotations

import threading
import time

import duckdb

from duckbricks.core.catalogs import registered_attachments
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.session import (
    DEFAULT_SCHEMA,
    attach_statement,
    open_session,
    sql_identifier,
)

REGISTRY_TTL_SECONDS = 5.0


class SqlEngine:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._lock = threading.Lock()
        self._sessions: dict[bool, duckdb.DuckDBPyConnection] = {}
        self._attached: dict[bool, set[str]] = {}
        self._synced_at: dict[bool, float] = {}

    def _session(self, read_only: bool) -> duckdb.DuckDBPyConnection:
        with self._lock:
            con = self._sessions.get(read_only)
            if con is None:
                attachments = registered_attachments(self.settings)
                con = open_session(
                    self.settings.api_node_type,
                    read_only=read_only,
                    catalogs=attachments,
                    settings=self.settings,
                    system_catalog=True,
                )
                self._sessions[read_only] = con
                self._attached[read_only] = {a.name for a in attachments}
                self._synced_at[read_only] = time.monotonic()
            elif time.monotonic() - self._synced_at[read_only] > REGISTRY_TTL_SECONDS:
                self._sync(con, read_only)
            return con

    def _sync(self, con: duckdb.DuckDBPyConnection, read_only: bool) -> None:
        current = registered_attachments(self.settings)
        for name in self._attached[read_only] - {a.name for a in current}:
            # No longer a DuckLake to attach: a foreign catalog switched to live mode.
            con.execute(f"DETACH DATABASE IF EXISTS {sql_identifier(name)}")
            self._attached[read_only].discard(name)
        for attachment in current:
            if attachment.name not in self._attached[read_only]:
                con.execute(attach_statement(attachment, self.settings, read_only))
                self._attached[read_only].add(attachment.name)
        self._synced_at[read_only] = time.monotonic()

    def refresh(self) -> None:
        """Attach newly registered catalogs now (after a create)."""
        with self._lock:
            for read_only, con in self._sessions.items():
                self._sync(con, read_only)

    def cursor(
        self, read_only: bool = False, catalog: str | None = None, schema: str | None = None
    ) -> duckdb.DuckDBPyConnection:
        cur = self._session(read_only).cursor()
        if catalog:
            cur.execute(f"USE {sql_identifier(catalog)}.{sql_identifier(schema or DEFAULT_SCHEMA)}")
        elif schema:
            cur.execute(f"USE {sql_identifier(schema)}")
        else:
            from duckbricks.core.catalogs import DEFAULT_CATALOG

            cur.execute(f"USE {sql_identifier(DEFAULT_CATALOG)}.{sql_identifier(DEFAULT_SCHEMA)}")
        return cur

    def close(self) -> None:
        with self._lock:
            for con in self._sessions.values():
                con.close()
            self._sessions.clear()


_engine: SqlEngine | None = None


def get_sql_engine() -> SqlEngine:
    global _engine
    if _engine is None:
        _engine = SqlEngine()
    return _engine
