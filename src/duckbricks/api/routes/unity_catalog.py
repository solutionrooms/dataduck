"""Unity Catalog API subset: /api/2.1/unity-catalog/{catalogs,schemas,tables}."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from duckbricks.api.deps import unity_catalog
from duckbricks.core.errors import InvalidParameter

router = APIRouter(prefix="/api/2.1/unity-catalog", tags=["Unity Catalog"])


class CreateCatalog(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    comment: str | None = None
    # Foreign catalogs (ADR 0013): a connection plus options.catalog (the remote name) and
    # options.duckbricks_mode (live or mirror).
    connection_name: str | None = None
    options: dict[str, Any] | None = None


class UpdateCatalog(BaseModel):
    model_config = ConfigDict(extra="allow")
    comment: str | None = None
    options: dict[str, Any] | None = None


class CreateSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    catalog_name: str
    comment: str | None = None


@router.get("/catalogs")
def list_catalogs() -> dict[str, Any]:
    return {"catalogs": unity_catalog().list_catalogs()}


@router.post("/catalogs")
def create_catalog(body: CreateCatalog) -> dict[str, Any]:
    return unity_catalog().create_catalog(
        body.name, body.comment, body.connection_name, body.options
    )


@router.get("/catalogs/{name}")
def get_catalog(name: str) -> dict[str, Any]:
    return unity_catalog().get_catalog(name)


@router.patch("/catalogs/{name}")
def update_catalog(name: str, body: UpdateCatalog) -> dict[str, Any]:
    return unity_catalog().update_catalog(name, body.comment, body.options)


@router.get("/schemas")
def list_schemas(catalog_name: str) -> dict[str, Any]:
    return {"schemas": unity_catalog().list_schemas(catalog_name)}


@router.post("/schemas")
def create_schema(body: CreateSchema) -> dict[str, Any]:
    return unity_catalog().create_schema(body.catalog_name, body.name, body.comment)


@router.get("/schemas/{full_name}")
def get_schema(full_name: str) -> dict[str, Any]:
    return unity_catalog().get_schema(full_name)


@router.delete("/schemas/{full_name}")
def delete_schema(full_name: str, force: bool = False) -> dict[str, Any]:
    unity_catalog().delete_schema(full_name, force)
    return {}


@router.get("/tables")
def list_tables(
    catalog_name: str, schema_name: str, name_pattern: str | None = None
) -> dict[str, Any]:
    return {"tables": unity_catalog().list_tables(catalog_name, schema_name, name_pattern)}


@router.get("/tables/{full_name}")
def get_table(full_name: str) -> dict[str, Any]:
    return unity_catalog().get_table(full_name)


@router.get("/tables/{full_name}/history")
def table_history(full_name: str, limit: int = 50) -> dict[str, Any]:
    """Duckbricks extension: DuckLake snapshots that touched the table."""
    if full_name.count(".") != 2:
        raise InvalidParameter("Table name must be catalog.schema.table")
    return {"history": unity_catalog().table_history(full_name, limit)}
