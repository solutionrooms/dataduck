"""Connections to external systems, shaped like the Unity Catalog connections API.

Only `DATABRICKS` connections are supported. Options never hold a secret: the token comes from
the environment variable named by `duckbricks_token_env` (default `DATABRICKS_TOKEN`).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, insert, select

from duckbricks.core import db
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.errors import AlreadyExists, InvalidParameter, NotFound
from duckbricks.federation.databricks import DatabricksClient

SUPPORTED_TYPES = ("DATABRICKS",)
TOKEN_ENV_OPTION = "duckbricks_token_env"
WAREHOUSE_OPTION = "duckbricks_warehouse_id"
SECRET_OPTIONS = ("personalAccessToken", "client_secret", "password", "token")
KNOWN_OPTIONS = ("host", "httpPath", TOKEN_ENV_OPTION, WAREHOUSE_OPTION)
HTTP_PATH = re.compile(r"/sql/1\.0/warehouses/([0-9a-f]+)")


@dataclass(frozen=True)
class Connection:
    name: str
    connection_type: str
    options: dict[str, Any]
    comment: str | None
    created_at: datetime

    @property
    def host(self) -> str:
        return str(self.options.get("host") or "")

    @property
    def warehouse_id(self) -> str | None:
        if self.options.get(WAREHOUSE_OPTION):
            return str(self.options[WAREHOUSE_OPTION])
        match = HTTP_PATH.search(str(self.options.get("httpPath") or ""))
        return match.group(1) if match else None

    def token(self, settings: Settings | None = None) -> str:
        env = str(self.options.get(TOKEN_ENV_OPTION) or "DATABRICKS_TOKEN")
        value = os.environ.get(env, "")
        if not value and env == "DATABRICKS_TOKEN":
            value = (settings or get_settings()).databricks_token.get_secret_value()
        return value

    def client(self, settings: Settings | None = None) -> DatabricksClient:
        return DatabricksClient(self.host, self.token(settings), self.warehouse_id)


def normalise_options(
    options: dict[str, Any], settings: Settings
) -> tuple[dict[str, str], list[str]]:
    """Options to store, plus warnings. Secrets are dropped; host defaults to DATABRICKS_HOST."""
    warnings: list[str] = []
    out: dict[str, str] = {}
    for key, value in (options or {}).items():
        if key in SECRET_OPTIONS:
            warnings.append(
                f"options.{key} ignored: Duckbricks never stores secrets; set the token in .env "
                f"(the variable named by {TOKEN_ENV_OPTION}, default DATABRICKS_TOKEN)"
            )
        elif key in KNOWN_OPTIONS:
            out[key] = str(value)
        else:
            warnings.append(f"options.{key} is not used by Duckbricks")
            out[key] = str(value)
    out.setdefault("host", settings.databricks_host)
    if not out["host"]:
        raise InvalidParameter("A DATABRICKS connection needs options.host (or DATABRICKS_HOST)")
    out["host"] = out["host"].rstrip("/")
    if not out["host"].startswith("http"):
        out["host"] = "https://" + out["host"]
    if "httpPath" not in out and WAREHOUSE_OPTION not in out and settings.databricks_warehouse_id:
        out[WAREHOUSE_OPTION] = settings.databricks_warehouse_id
    return out, warnings


def _row_to_connection(row: Any) -> Connection:
    return Connection(**dict(row))


def list_connections(settings: Settings | None = None) -> list[Connection]:
    engine = db.get_engine((settings or get_settings()).control_url)
    with engine.connect() as conn:
        rows = conn.execute(select(db.connections).order_by(db.connections.c.name)).mappings()
        return [_row_to_connection(r) for r in rows]


def get_connection(name: str, settings: Settings | None = None) -> Connection:
    engine = db.get_engine((settings or get_settings()).control_url)
    with engine.connect() as conn:
        row = (
            conn.execute(select(db.connections).where(db.connections.c.name == name))
            .mappings()
            .first()
        )
    if row is None:
        raise NotFound(
            f"Connection '{name}' does not exist.", error_code="CONNECTION_DOES_NOT_EXIST"
        )
    return _row_to_connection(row)


def create_connection(
    name: str,
    connection_type: str,
    options: dict[str, Any],
    comment: str | None = None,
    settings: Settings | None = None,
) -> tuple[Connection, list[str]]:
    settings = settings or get_settings()
    if connection_type.upper() not in SUPPORTED_TYPES:
        raise InvalidParameter(
            f"connection_type {connection_type} is not supported: Duckbricks connects to "
            "Databricks workspaces only (DATABRICKS)"
        )
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]{0,254}$", name):
        raise InvalidParameter(f"Invalid connection name '{name}'")
    stored, warnings = normalise_options(options, settings)
    engine = db.get_engine(settings.control_url)
    with engine.begin() as conn:
        if conn.execute(select(db.connections.c.name).where(db.connections.c.name == name)).first():
            raise AlreadyExists(f"Connection '{name}' already exists.")
        conn.execute(
            insert(db.connections).values(
                name=name, connection_type=connection_type.upper(), options=stored, comment=comment
            )
        )
    return get_connection(name, settings), warnings


def delete_connection(name: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    engine = db.get_engine(settings.control_url)
    with engine.begin() as conn:
        used = conn.execute(
            select(db.catalogs.c.name).where(db.catalogs.c.connection_name == name)
        ).first()
        if used:
            raise InvalidParameter(f"Connection '{name}' is used by catalog '{used[0]}'")
        if not conn.execute(delete(db.connections).where(db.connections.c.name == name)).rowcount:
            raise NotFound(
                f"Connection '{name}' does not exist.", error_code="CONNECTION_DOES_NOT_EXIST"
            )


def connection_json(c: Connection, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "name": c.name,
        "full_name": c.name,
        "connection_type": c.connection_type,
        "options": c.options,
        "comment": c.comment,
        "owner": settings.user_name,
        "created_at": int(c.created_at.timestamp() * 1000),
        "created_by": settings.user_name,
        "read_only": True,
        "credential_type": "BEARER_TOKEN",
        "url": c.host,
        "properties": {"duckbricks.token_set": str(bool(c.token(settings))).lower()},
    }
