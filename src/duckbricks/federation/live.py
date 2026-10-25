"""Live foreign catalogs: query Databricks tables in place (ADR 0013).

Before a statement runs, `prepare` finds the tables it names in live catalogs and returns the
statements that make them readable in the same session: load the extensions, attach each
remote catalog READ_ONLY with DuckDB's `unity_catalog` extension, and create one storage secret
per table from credentials Unity Catalog vends. Table metadata is cached for a few minutes and
credentials until shortly before they expire, so repeated queries make no extra calls.

Views, materialized views and streaming tables have no files to read; live mode rejects them
with a message (mirror them, or query their base tables).

Browsing a live catalog (schemas, tables, columns) goes to the remote Unity Catalog API, with
the remote catalog name rewritten to the local one.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import sqlglot
from sqlglot import exp

from duckbricks.core.catalogs import list_catalogs
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.session import sql_identifier, sql_literal
from duckbricks.federation.connections import get_connection
from duckbricks.federation.databricks import (
    FEDERATION_EXTENSIONS,
    DatabricksClient,
    DatabricksError,
    RemoteTable,
    StorageCredential,
)

METADATA_TTL_SECONDS = 300.0
LIVE_EXTENSIONS = ("unity_catalog", *FEDERATION_EXTENSIONS)


@dataclass(frozen=True)
class LiveCatalog:
    name: str  # local name
    remote: str  # remote catalog name
    host: str
    token: str = field(repr=False)
    warehouse_id: str | None = None

    @property
    def secret(self) -> str:
        return "dbx_uc_" + hashlib.sha1(f"{self.host}/{self.name}".encode()).hexdigest()[:12]


def live_catalogs(settings: Settings | None = None) -> list[LiveCatalog]:
    """Live foreign catalogs, from the control database (API and worker only)."""
    settings = settings or get_settings()
    out = []
    for info in list_catalogs(settings):
        if info.mode == "live" and info.connection_name:
            c = get_connection(info.connection_name, settings)
            out.append(
                LiveCatalog(
                    info.name,
                    info.remote_catalog or info.name,
                    c.host,
                    c.token(settings),
                    c.warehouse_id,
                )
            )
    return out


class _Cache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.clients: dict[tuple[str, str], DatabricksClient] = {}
        self.tables: dict[tuple[str, str], tuple[float, RemoteTable]] = {}
        self.credentials: dict[tuple[str, str], StorageCredential] = {}

    def client(self, c: LiveCatalog) -> DatabricksClient:
        key = (c.host, hashlib.sha1(c.token.encode()).hexdigest())
        with self.lock:
            if key not in self.clients:
                self.clients[key] = DatabricksClient(c.host, c.token, c.warehouse_id)
            return self.clients[key]

    def table(self, c: LiveCatalog, full_name: str) -> RemoteTable:
        key = (c.host, full_name.lower())
        with self.lock:
            hit = self.tables.get(key)
        if hit and time.monotonic() - hit[0] < METADATA_TTL_SECONDS:
            return hit[1]
        t = self.client(c).get_table(full_name)
        with self.lock:
            self.tables[key] = (time.monotonic(), t)
        return t

    def credential(self, c: LiveCatalog, t: RemoteTable) -> StorageCredential:
        key = (c.host, t.storage_location or t.full_name)
        with self.lock:
            hit = self.credentials.get(key)
        if hit and hit.fresh:
            return hit
        credential = self.client(c).vend(t)
        with self.lock:
            self.credentials[key] = credential
        return credential


_cache = _Cache()


def referenced_tables(
    sql: str, default_catalog: str | None = None, default_schema: str | None = None
) -> set[tuple[str, str, str]]:
    """(catalog, schema, table) candidates a statement may read. Two-part names are tried as
    schema.table in the default catalog and as catalog.table in the `default` schema."""
    try:
        trees = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.SqlglotError:
        return set()
    out: set[tuple[str, str, str]] = set()
    for tree in trees:
        if tree is None:
            continue
        ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        for table in tree.find_all(exp.Table):
            name, db, catalog = table.name, table.db, table.catalog
            if not name or not isinstance(table.this, exp.Identifier):
                continue  # table functions such as read_parquet(...)
            if catalog:
                out.add((catalog.lower(), db, name))
            elif db:
                if default_catalog:
                    out.add((default_catalog.lower(), db, name))
                out.add((db.lower(), "default", name))
            elif default_catalog and name.lower() not in ctes:
                out.add((default_catalog.lower(), default_schema or "default", name))
    return out


def prepare(
    sql: str,
    live: list[LiveCatalog],
    default_catalog: str | None = None,
    default_schema: str | None = None,
) -> list[str]:
    """Statements to run before `sql` so the live tables it names are readable."""
    by_name = {c.name.lower(): c for c in live}
    refs = sorted(
        r for r in referenced_tables(sql, default_catalog, default_schema) if r[0] in by_name
    )
    if not refs:
        return []
    out = [f"INSTALL {e}; LOAD {e}" for e in LIVE_EXTENSIONS]
    out.append("SET GLOBAL azure_transport_option_type = 'curl'")
    for name in sorted({r[0] for r in refs}):
        c = by_name[name]
        out.append(
            f"CREATE OR REPLACE TEMPORARY SECRET {c.secret} (TYPE unity_catalog, "
            f"TOKEN {sql_literal(c.token)}, ENDPOINT {sql_literal(c.host)}, AWS_REGION 'us-east-1')"
        )
        out.append(
            f"ATTACH IF NOT EXISTS {sql_literal(c.remote)} AS {sql_identifier(c.name)} "
            f"(TYPE unity_catalog, SECRET {c.secret}, READ_ONLY true)"
        )
    for catalog, schema, name in refs:
        c = by_name[catalog]
        try:
            t = _cache.table(c, f"{c.remote}.{schema}.{name}")
        except DatabricksError as exc:
            if exc.status == 404:
                continue  # a two-part guess that does not exist; DuckDB reports real misses
            raise
        if t.is_view:
            raise DatabricksError(
                f"{catalog}.{schema}.{name} is a {t.table_type.lower().replace('_', ' ')}: live "
                "mode reads table files directly, so it cannot read views. Mirror it, or query "
                "the tables it reads",
                "VIEW_NOT_SUPPORTED_LIVE",
            )
        out.append(_cache.credential(c, t).secret_sql)
    return out


def preparer(settings: Settings | None = None) -> Any:
    """A `prepare` bound to the current live catalogs (re-read every call; cheap)."""

    def run(sql: str, default_catalog: str | None, default_schema: str | None) -> list[str]:
        live = live_catalogs(settings)
        return prepare(sql, live, default_catalog, default_schema) if live else []

    return run


# --- browsing a live catalog ------------------------------------------------------------


def _rename(item: dict[str, Any], c: LiveCatalog) -> dict[str, Any]:
    item = dict(item)
    if "catalog_name" in item:
        item["catalog_name"] = c.name
    if item.get("full_name"):
        item["full_name"] = c.name + item["full_name"][len(c.remote) :]
    item.setdefault("properties", {})
    item["properties"] = {**(item["properties"] or {}), "duckbricks.remote_catalog": c.remote}
    return item


def browse_schemas(c: LiveCatalog) -> list[dict[str, Any]]:
    return [_rename(s, c) for s in _cache.client(c).list_schemas(c.remote)]


def browse_schema(c: LiveCatalog, schema: str) -> dict[str, Any]:
    body = _cache.client(c).request(
        "GET", f"/api/2.1/unity-catalog/schemas/{quote(f'{c.remote}.{schema}')}"
    )
    return _rename(body, c)


def browse_tables(c: LiveCatalog, schema: str) -> list[dict[str, Any]]:
    client = _cache.client(c)
    items = (
        client.request(
            "GET",
            "/api/2.1/unity-catalog/tables",
            params={"catalog_name": c.remote, "schema_name": schema, "max_results": 500},
        ).get("tables")
        or []
    )
    return [_rename(t, c) for t in items]


def browse_table(c: LiveCatalog, schema: str, name: str) -> dict[str, Any]:
    body = _cache.client(c).request(
        "GET", f"/api/2.1/unity-catalog/tables/{quote(f'{c.remote}.{schema}.{name}')}"
    )
    return _rename(body, c)


def find_live(name: str, settings: Settings | None = None) -> LiveCatalog | None:
    return next((c for c in live_catalogs(settings) if c.name == name), None)
