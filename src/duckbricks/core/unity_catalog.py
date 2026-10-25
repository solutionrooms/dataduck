"""Unity Catalog API subset over DuckDB's catalog functions on an attached session."""

from __future__ import annotations

import contextlib
from typing import Any

from duckbricks.core.catalogs import (
    MODE_OPTION,
    MODES,
    CatalogError,
    CatalogInfo,
    create_catalog,
    list_catalogs,
    update_catalog,
)
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.errors import AlreadyExists, ApiError, InvalidParameter, NotFound
from duckbricks.core.session import sql_identifier, sql_literal
from duckbricks.core.sql_engine import SqlEngine
from duckbricks.core.statements import type_name
from duckbricks.core.system_catalog import SYSTEM_CATALOG

HIDDEN_SCHEMAS = ("pg_catalog",)
NAME_RULE = "use letters, digits and underscores, starting with a letter or underscore"


def _ms(value: Any) -> int | None:
    return int(value.timestamp() * 1000) if value is not None else None


def _check_name(name: str, what: str) -> None:
    if (
        not name
        or not (name[0].isalpha() or name[0] == "_")
        or not all(c.isalnum() or c == "_" for c in name)
    ):
        raise InvalidParameter(f"Invalid {what} name '{name}': {NAME_RULE}")


class UnityCatalog:
    def __init__(self, engine: SqlEngine, settings: Settings | None = None):
        self.engine = engine
        self.settings = settings or get_settings()

    # --- catalogs ----------------------------------------------------------------------

    def _catalog_json(self, info: CatalogInfo) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": info.name,
            "full_name": info.name,
            "comment": info.comment,
            "owner": self.settings.user_name,
            "catalog_type": info.catalog_type,
            "storage_root": info.data_path,
            "storage_location": info.data_path,
            "created_at": _ms(info.created_at),
            "created_by": self.settings.user_name,
            "isolation_mode": "OPEN",
            "properties": {"duckbricks.metadata_schema": info.metadata_schema},
        }
        if info.is_foreign:
            out["connection_name"] = info.connection_name
            out["options"] = info.options
            if info.mode == "live":
                # Nothing local to attach: the name belongs to the remote catalog.
                out["properties"] = {"duckbricks.mode": "live"}
            else:
                out["properties"].update(
                    {"duckbricks.mode": "mirror", "duckbricks.read_only": "true"}
                )
        return out

    def _system_json(self) -> dict[str, Any]:
        return {
            "name": SYSTEM_CATALOG,
            "full_name": SYSTEM_CATALOG,
            "comment": "System tables (read-only views over the control database)",
            "owner": "System user",
            "catalog_type": "SYSTEM_CATALOG",
            "isolation_mode": "OPEN",
        }

    def list_catalogs(self) -> list[dict[str, Any]]:
        infos = [self._catalog_json(i) for i in list_catalogs(self.settings)]
        return [*infos, self._system_json()]

    def get_catalog(self, name: str) -> dict[str, Any]:
        if name == SYSTEM_CATALOG:
            return self._system_json()
        for info in list_catalogs(self.settings):
            if info.name == name:
                return self._catalog_json(info)
        raise NotFound(f"Catalog '{name}' does not exist.", error_code="CATALOG_DOES_NOT_EXIST")

    def create_catalog(
        self,
        name: str,
        comment: str | None,
        connection_name: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name == SYSTEM_CATALOG:
            raise InvalidParameter(f"Catalog name '{name}' is reserved")
        if any(i.name == name for i in list_catalogs(self.settings)):
            raise AlreadyExists(f"Catalog '{name}' already exists.")
        if connection_name:
            from duckbricks.federation.connections import get_connection

            get_connection(connection_name, self.settings)
            options = {str(k): str(v) for k, v in (options or {}).items()}
            options.setdefault("catalog", name)
            options.setdefault(MODE_OPTION, "live")
            self._check_mode(options)
        elif options:
            raise InvalidParameter("options apply to foreign catalogs (with connection_name) only")
        try:
            info = create_catalog(
                name, comment, self.settings, connection_name=connection_name, options=options
            )
        except ValueError as exc:
            raise InvalidParameter(str(exc)) from exc
        self.engine.refresh()
        return self._catalog_json(info)

    @staticmethod
    def _check_mode(options: dict[str, Any]) -> None:
        if options.get(MODE_OPTION) not in MODES:
            raise InvalidParameter(f"options.{MODE_OPTION} must be one of {', '.join(MODES)}")

    def update_catalog(
        self, name: str, comment: str | None, options: dict[str, Any] | None
    ) -> dict[str, Any]:
        """PATCH a catalog. Foreign catalog options are merged (Databricks replaces them)."""
        if name == SYSTEM_CATALOG:
            raise InvalidParameter("The system catalog cannot be changed")
        info = next((i for i in list_catalogs(self.settings) if i.name == name), None)
        if info is None:
            raise NotFound(f"Catalog '{name}' does not exist.", error_code="CATALOG_DOES_NOT_EXIST")
        merged = None
        if options is not None:
            if not info.is_foreign:
                raise InvalidParameter("options apply to foreign catalogs only")
            merged = {**info.options, **{str(k): str(v) for k, v in options.items()}}
            self._check_mode(merged)
        try:
            updated = update_catalog(name, comment, merged, self.settings)
        except CatalogError as exc:
            raise InvalidParameter(str(exc)) from exc
        if updated.mode != info.mode:
            self._mode_changed(name)
        return self._catalog_json(updated)

    def _mode_changed(self, name: str) -> None:
        """Re-attach the name everywhere: the API engine now, warehouses by restarting them."""
        from duckbricks.core import db
        from duckbricks.warehouses import lifecycle

        self.engine.refresh()
        with db.get_engine(self.settings.control_url).begin() as conn:
            for row in lifecycle.list_rows(conn):
                if row["state"] in ("RUNNING", "STARTING"):
                    lifecycle.stop(conn, row["id"], self.settings)

    # --- schemas -----------------------------------------------------------------------

    def _require_catalog(self, catalog: str) -> None:
        self.get_catalog(catalog)

    def _live(self, catalog: str) -> Any:
        """The live foreign catalog of this name, if it is one (ADR 0013)."""
        from duckbricks.federation.live import find_live

        return find_live(catalog, self.settings)

    @staticmethod
    def _remote(call: Any) -> Any:
        """Run a call against the remote workspace; report its errors as API errors."""
        from duckbricks.federation.databricks import DatabricksError

        try:
            return call()
        except DatabricksError as exc:
            status = exc.status if exc.status in (400, 403, 404) else 502
            raise ApiError(str(exc), exc.error_code, status) from None

    def list_schemas(self, catalog: str) -> list[dict[str, Any]]:
        self._require_catalog(catalog)
        if live := self._live(catalog):
            from duckbricks.federation.live import browse_schemas

            return list(self._remote(lambda: browse_schemas(live)))
        cur = self.engine.cursor(read_only=True)
        try:
            rows = cur.execute(
                """
                SELECT s.schema_name, s.comment,
                       (SELECT count(*) FROM duckdb_tables() t
                         WHERE t.database_name = s.database_name
                           AND t.schema_name = s.schema_name)
                     + (SELECT count(*) FROM duckdb_views() v
                         WHERE v.database_name = s.database_name
                           AND v.schema_name = s.schema_name AND NOT v.internal) AS objects
                FROM duckdb_schemas() s
                WHERE s.database_name = $catalog AND NOT s.internal
                ORDER BY s.schema_name
                """,
                {"catalog": catalog},
            ).fetchall()
        finally:
            cur.close()
        out = []
        for name, comment, objects in rows:
            if name in HIDDEN_SCHEMAS or (name == "main" and not objects):
                continue
            out.append(self._schema_json(catalog, name, comment))
        return out

    def _schema_json(self, catalog: str, name: str, comment: str | None) -> dict[str, Any]:
        return {
            "name": name,
            "catalog_name": catalog,
            "full_name": f"{catalog}.{name}",
            "comment": comment,
            "owner": self.settings.user_name,
            "catalog_type": "SYSTEM_CATALOG" if catalog == SYSTEM_CATALOG else "MANAGED_CATALOG",
        }

    def get_schema(self, full_name: str) -> dict[str, Any]:
        catalog, _, schema = full_name.partition(".")
        for item in self.list_schemas(catalog):
            if item["name"] == schema:
                return item
        raise NotFound(f"Schema '{full_name}' does not exist.", error_code="SCHEMA_DOES_NOT_EXIST")

    def create_schema(self, catalog: str, name: str, comment: str | None) -> dict[str, Any]:
        if catalog == SYSTEM_CATALOG:
            raise InvalidParameter("The sys catalog is read-only")
        self._require_catalog(catalog)
        _check_name(name, "schema")
        if any(s["name"] == name for s in self.list_schemas(catalog)):
            raise AlreadyExists(f"Schema '{catalog}.{name}' already exists.")
        target = f"{sql_identifier(catalog)}.{sql_identifier(name)}"
        cur = self.engine.cursor()
        try:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {target}")
            if comment:
                # Comments are best effort on DuckLake.
                with contextlib.suppress(Exception):
                    cur.execute(f"COMMENT ON SCHEMA {target} IS {sql_literal(comment)}")
        finally:
            cur.close()
        return self._schema_json(catalog, name, comment)

    def ensure_schema(self, catalog: str, name: str, comment: str | None) -> bool:
        """Create a schema if it is missing. Returns True when it was created."""
        try:
            self.get_schema(f"{catalog}.{name}")
            return False
        except NotFound:
            self.create_schema(catalog, name, comment)
            return True

    def delete_schema(self, full_name: str, force: bool = False) -> None:
        catalog, _, schema = full_name.partition(".")
        if catalog == SYSTEM_CATALOG:
            raise InvalidParameter("The sys catalog is read-only")
        self.get_schema(full_name)
        cur = self.engine.cursor()
        try:
            cascade = " CASCADE" if force else ""
            cur.execute(f"DROP SCHEMA {sql_identifier(catalog)}.{sql_identifier(schema)}{cascade}")
        except Exception as exc:
            if "depend" in str(exc).lower() or "not empty" in str(exc).lower():
                raise InvalidParameter(
                    f"Schema '{full_name}' is not empty. Use force=true to delete it and its "
                    "tables.",
                    error_code="SCHEMA_NOT_EMPTY",
                ) from exc
            raise
        finally:
            cur.close()

    # --- tables ------------------------------------------------------------------------

    def list_tables(
        self, catalog: str, schema: str, name_pattern: str | None = None
    ) -> list[dict[str, Any]]:
        if live := self._live(catalog):
            from duckbricks.federation.live import browse_tables

            tables = self._remote(lambda: browse_tables(live, schema))
            return [
                t for t in tables if not name_pattern or name_pattern.lower() in t["name"].lower()
            ]
        self.get_schema(f"{catalog}.{schema}")
        cur = self.engine.cursor(read_only=True)
        try:
            rows = cur.execute(
                """
                SELECT table_name, 'MANAGED' AS table_type, comment, estimated_size, column_count
                FROM duckdb_tables() WHERE database_name = $c AND schema_name = $s
                UNION ALL
                SELECT view_name, 'VIEW', comment, NULL, column_count
                FROM duckdb_views() WHERE database_name = $c AND schema_name = $s AND NOT internal
                ORDER BY 1
                """,
                {"c": catalog, "s": schema},
            ).fetchall()
        finally:
            cur.close()
        out = []
        for name, kind, comment, size, column_count in rows:
            if name_pattern and name_pattern.lower() not in name.lower():
                continue
            out.append(
                {
                    "name": name,
                    "catalog_name": catalog,
                    "schema_name": schema,
                    "full_name": f"{catalog}.{schema}.{name}",
                    "table_type": kind,
                    "data_source_format": "DUCKLAKE" if kind == "MANAGED" else None,
                    "comment": comment,
                    "owner": self.settings.user_name,
                    "properties": {
                        k: v
                        for k, v in {
                            "duckbricks.estimated_rows": size,
                            "duckbricks.column_count": column_count,
                        }.items()
                        if v is not None
                    },
                }
            )
        return out

    def get_table(self, full_name: str) -> dict[str, Any]:
        parts = full_name.split(".")
        if len(parts) != 3:
            raise InvalidParameter(f"Table name '{full_name}' must be catalog.schema.table")
        catalog, schema, table = parts
        if live := self._live(catalog):
            from duckbricks.federation.live import browse_table

            return dict(self._remote(lambda: browse_table(live, schema, table)))
        matches = [t for t in self.list_tables(catalog, schema) if t["name"] == table]
        if not matches:
            raise NotFound(
                f"Table '{full_name}' does not exist.", error_code="TABLE_DOES_NOT_EXIST"
            )
        info = matches[0]
        cur = self.engine.cursor(read_only=True)
        try:
            cols = cur.execute(
                """
                SELECT column_name, data_type, column_index, is_nullable, comment
                FROM duckdb_columns()
                WHERE database_name = $c AND schema_name = $s AND table_name = $t
                ORDER BY column_index
                """,
                {"c": catalog, "s": schema, "t": table},
            ).fetchall()
            if info["table_type"] == "VIEW":
                definition = cur.execute(
                    "SELECT sql FROM duckdb_views() WHERE database_name = $c AND schema_name = $s "
                    "AND view_name = $t",
                    {"c": catalog, "s": schema, "t": table},
                ).fetchone()
                if definition:
                    info["view_definition"] = definition[0]
        finally:
            cur.close()
        info["columns"] = [
            {
                "name": name,
                "type_text": str(dtype).lower(),
                "type_name": type_name(str(dtype)),
                "position": int(index) - 1,
                "nullable": bool(nullable),
                "comment": comment,
            }
            for name, dtype, index, nullable, comment in cols
        ]
        if info["table_type"] == "MANAGED" and catalog != SYSTEM_CATALOG:
            info["storage_location"] = f"{self.settings.data_path(catalog)}{schema}/{table}/"
        return info

    def table_history(self, full_name: str, limit: int = 50) -> list[dict[str, Any]]:
        if self._live(full_name.split(".")[0]):
            return []  # snapshots are DuckLake's; a live table's history stays in Databricks
        """DuckLake snapshots that changed a table (Duckbricks extension, like DESCRIBE HISTORY)."""
        catalog, schema, table = full_name.split(".")
        if catalog == SYSTEM_CATALOG:
            return []
        cur = self.engine.cursor(read_only=True)
        try:
            rows = cur.execute(
                f"SELECT snapshot_id, snapshot_time, schema_version, changes "
                f"FROM ducklake_snapshots({sql_literal(catalog)}) "
                f"ORDER BY snapshot_id DESC LIMIT 2000"
            ).fetchall()
        finally:
            cur.close()
        qualified = {f"{schema}.{table}", table}
        out = []
        for snapshot_id, snapshot_time, schema_version, changes in rows:
            touched = {
                k: v for k, v in (changes or {}).items() if any(str(x) in qualified for x in v)
            }
            if touched:
                out.append(
                    {
                        "version": snapshot_id,
                        "timestamp": _ms(snapshot_time),
                        "schema_version": schema_version,
                        "operation": ", ".join(sorted(touched)),
                    }
                )
            if len(out) >= limit:
                break
        return out
