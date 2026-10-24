"""Control database: table definitions (SQLAlchemy Core) and the engine.

Only the api and worker processes use this module. Schema changes go through Alembic.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Column, DateTime, Engine, MetaData, Table, Text, create_engine, func

metadata = MetaData()

catalogs = Table(
    "catalogs",
    metadata,
    Column("name", Text, primary_key=True),
    Column("comment", Text),
    Column("metadata_schema", Text, nullable=False, unique=True),
    Column("data_path", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


@lru_cache
def get_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)
