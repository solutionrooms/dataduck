"""Catalog registry: which DuckLake catalogs exist and where they live."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update

from duckbricks.core import db
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.session import (
    DEFAULT_SCHEMA,
    CatalogAttachment,
    open_session,
    sql_identifier,
)

# Catalogs every platform starts with. Databricks' `main` is a reserved attach name in
# DuckDB, so the default catalog is `workspace` (ADR 0001).
BOOTSTRAP_CATALOGS = ("workspace", "dev", "prod")
DEFAULT_CATALOG = "workspace"

# DuckDB reserves main/system/temp/memory as database names; Postgres reserves the
# schema names we would use for metadata. `system` is also kept for Phase 4.
RESERVED_NAMES = frozenset({"main", "system", "temp", "memory", "public", "information_schema"})
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class CatalogError(ValueError):
    pass


MANAGED = "MANAGED_CATALOG"
FOREIGN = "FOREIGN_CATALOG"
MODE_OPTION = "duckbricks_mode"  # foreign catalogs: live (query Databricks) or mirror (local copy)
MODES = ("live", "mirror")


@dataclass(frozen=True)
class CatalogInfo:
    name: str
    comment: str | None
    metadata_schema: str
    data_path: str
    created_at: datetime
    catalog_type: str = MANAGED
    connection_name: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def is_foreign(self) -> bool:
        return self.catalog_type == FOREIGN

    @property
    def mode(self) -> str | None:
        """`live` or `mirror` for a foreign catalog; None for a managed one."""
        return str(self.options.get(MODE_OPTION) or "live") if self.is_foreign else None

    @property
    def remote_catalog(self) -> str | None:
        return str(self.options.get("catalog") or self.name) if self.is_foreign else None

    def attachment(self) -> CatalogAttachment:
        return CatalogAttachment(
            self.name, self.metadata_schema, self.data_path, read_only=self.is_foreign
        )


def validate_catalog_name(name: str) -> None:
    if not NAME_PATTERN.match(name):
        raise CatalogError(
            f"Invalid catalog name '{name}': use lowercase letters, digits and underscores, "
            "starting with a letter (max 63 characters)"
        )
    if name in RESERVED_NAMES or name.startswith("pg_"):
        raise CatalogError(f"Catalog name '{name}' is reserved")


def list_catalogs(settings: Settings | None = None) -> list[CatalogInfo]:
    settings = settings or get_settings()
    engine = db.get_engine(settings.control_url)
    with engine.connect() as conn:
        rows = conn.execute(select(db.catalogs).order_by(db.catalogs.c.name)).mappings().all()
    return [CatalogInfo(**dict(row)) for row in rows]


def registered_attachments(settings: Settings | None = None) -> list[CatalogAttachment]:
    """DuckLake catalogs every session attaches. A foreign catalog's mirror is attached
    read-only in mirror mode; in live mode the name belongs to the remote catalog (ADR 0013)."""
    return [info.attachment() for info in list_catalogs(settings) if info.mode != "live"]


def get_catalog_info(name: str, settings: Settings | None = None) -> CatalogInfo | None:
    return next((i for i in list_catalogs(settings) if i.name == name), None)


def update_catalog(
    name: str,
    comment: str | None = None,
    options: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> CatalogInfo:
    settings = settings or get_settings()
    values: dict[str, Any] = {}
    if comment is not None:
        values["comment"] = comment
    if options is not None:
        values["options"] = options
    if values:
        with db.get_engine(settings.control_url).begin() as conn:
            conn.execute(update(db.catalogs).where(db.catalogs.c.name == name).values(**values))
    info = get_catalog_info(name, settings)
    if info is None:
        raise CatalogError(f"Catalog '{name}' does not exist")
    return info


def create_catalog(
    name: str,
    comment: str | None = None,
    settings: Settings | None = None,
    *,
    connection_name: str | None = None,
    options: dict[str, Any] | None = None,
) -> CatalogInfo:
    """Register a catalog and initialise its DuckLake (metadata schema + `default` schema).

    The registry row and the DuckLake initialisation succeed or fail together: the row is
    written in a transaction that commits only after the DuckLake attach works. A catalog with
    a `connection_name` is a foreign catalog; its DuckLake holds the mirror.
    """
    settings = settings or get_settings()
    validate_catalog_name(name)
    attachment = CatalogAttachment(name, metadata_schema=name, data_path=settings.data_path(name))
    engine = db.get_engine(settings.control_url)
    with engine.begin() as conn:
        exists = conn.execute(select(db.catalogs.c.name).where(db.catalogs.c.name == name))
        if exists.first() is not None:
            raise CatalogError(f"Catalog '{name}' already exists")
        conn.execute(
            insert(db.catalogs).values(
                name=name,
                comment=comment,
                metadata_schema=attachment.metadata_schema,
                data_path=attachment.data_path,
                catalog_type=FOREIGN if connection_name else MANAGED,
                connection_name=connection_name,
                options=options or {},
            )
        )
        initialise_ducklake(attachment, settings)
    return next(info for info in list_catalogs(settings) if info.name == name)


def initialise_ducklake(attachment: CatalogAttachment, settings: Settings) -> None:
    """First attach creates the DuckLake metadata; then add the `default` schema."""
    con = open_session("dbk.small", catalogs=[attachment], settings=settings)
    try:
        con.execute(
            f"CREATE SCHEMA IF NOT EXISTS {sql_identifier(attachment.name)}."
            f"{sql_identifier(DEFAULT_SCHEMA)}"
        )
    finally:
        con.close()


def ensure_bootstrap_catalogs(settings: Settings | None = None) -> list[str]:
    """Create any missing bootstrap catalogs. Returns the names created."""
    settings = settings or get_settings()
    existing = {info.name for info in list_catalogs(settings)}
    created = []
    for name in BOOTSTRAP_CATALOGS:
        if name not in existing:
            create_catalog(name, comment="Created at bootstrap", settings=settings)
            created.append(name)
    return created
