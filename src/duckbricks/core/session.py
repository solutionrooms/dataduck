"""The one place that opens DuckDB connections.

Every DuckDB process (API, worker tasks, warehouses, spikes) gets its connection from
`open_session`, so extensions, secrets and catalog attachments are identical everywhere.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import duckdb

from duckbricks.core.config import Settings, get_settings
from duckbricks.core.node_types import NodeType, get_node_types

EXTENSIONS = ("ducklake", "httpfs", "postgres")
PG_SECRET = "dbk_pg"
S3_SECRET = "dbk_s3"
DEFAULT_SCHEMA = "default"


@dataclass(frozen=True)
class CatalogAttachment:
    """What a session needs to attach one catalog."""

    name: str
    metadata_schema: str
    data_path: str
    # Always attached READ_ONLY (a foreign catalog's mirror: only the mirror sync writes it).
    read_only: bool = False


def attachment_from_catalog_json(item: Mapping[str, Any]) -> CatalogAttachment | None:
    """The attachment for a catalog as the Unity Catalog API returns it, or None when the
    catalog has no DuckLake to attach (`sys`, or a foreign catalog in live mode)."""
    props = item.get("properties") or {}
    if not props.get("duckbricks.metadata_schema"):
        return None
    return CatalogAttachment(
        name=item["name"],
        metadata_schema=props["duckbricks.metadata_schema"],
        data_path=item["storage_root"],
        read_only=props.get("duckbricks.read_only") == "true",
    )


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def secret_statements(settings: Settings) -> list[str]:
    """Temporary secrets for SeaweedFS (S3 API) and the DuckLake metadata database."""
    s3 = (
        f"CREATE OR REPLACE TEMPORARY SECRET {S3_SECRET} (TYPE s3, "
        f"KEY_ID {sql_literal(settings.s3_access_key.get_secret_value())}, "
        f"SECRET {sql_literal(settings.s3_secret_key.get_secret_value())}, "
        f"ENDPOINT {sql_literal(settings.s3_endpoint)}, "
        f"REGION {sql_literal(settings.s3_region)}, "
        f"URL_STYLE 'path', USE_SSL {str(settings.s3_use_ssl).lower()})"
    )
    pg = (
        f"CREATE OR REPLACE TEMPORARY SECRET {PG_SECRET} (TYPE postgres, "
        f"HOST {sql_literal(settings.pg_host)}, PORT {settings.pg_port}, "
        f"USER {sql_literal(settings.pg_user)}, "
        f"PASSWORD {sql_literal(settings.pg_password.get_secret_value())})"
    )
    return [s3, pg]


def attach_statement(catalog: CatalogAttachment, settings: Settings, read_only: bool) -> str:
    """ATTACH for one DuckLake catalog: metadata in its own Postgres schema, data on S3."""
    options = [
        f"DATA_PATH {sql_literal(catalog.data_path)}",
        f"METADATA_SCHEMA {sql_literal(catalog.metadata_schema)}",
        f"METADATA_PARAMETERS MAP {{'SECRET': {sql_literal(PG_SECRET)}}}",
    ]
    if read_only or catalog.read_only:
        options.append("READ_ONLY")
    target = sql_literal(f"ducklake:postgres:dbname={settings.lake_db}")
    return f"ATTACH IF NOT EXISTS {target} AS {sql_identifier(catalog.name)} ({', '.join(options)})"


def open_session(
    node_type: str | NodeType | None = None,
    read_only: bool = False,
    default_catalog: str | None = None,
    default_schema: str | None = None,
    *,
    catalogs: Sequence[CatalogAttachment] | None = None,
    settings: Settings | None = None,
    system_catalog: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with every registered catalog attached.

    `catalogs` defaults to the registry in the control database; pass a list to attach
    a specific set (bootstrap does this before the registry row exists).
    `system_catalog` adds the read-only `sys` catalog (views over the control database).
    """
    settings = settings or get_settings()
    if not isinstance(node_type, NodeType):
        node_type, _ = get_node_types().resolve(node_type)
    if catalogs is None:
        from duckbricks.core.catalogs import registered_attachments

        catalogs = registered_attachments(settings)

    con = duckdb.connect(
        config={"memory_limit": node_type.memory_limit, "threads": node_type.threads}
    )
    try:
        for extension in EXTENSIONS:
            con.install_extension(extension)
            con.load_extension(extension)
        for statement in secret_statements(settings):
            con.execute(statement)
        for catalog in catalogs:
            con.execute(attach_statement(catalog, settings, read_only))
        if system_catalog:
            from duckbricks.core.system_catalog import attach_system_catalog

            attach_system_catalog(con, settings)
        if default_catalog is not None:
            schema = default_schema or DEFAULT_SCHEMA
            con.execute(f"USE {sql_identifier(default_catalog)}.{sql_identifier(schema)}")
    except Exception:
        con.close()
        raise
    return con
