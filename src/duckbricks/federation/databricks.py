"""A small client for one Databricks workspace: Unity Catalog metadata, credential vending and
the Statement Execution API (for views). Never logs or returns the token or vended secrets."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from duckbricks.core.session import sql_identifier, sql_literal

# Vended credentials are reused until this many seconds before they expire.
EXPIRY_MARGIN_SECONDS = 300
WAREHOUSE_ONLY_TYPES = frozenset({"VIEW", "MATERIALIZED_VIEW", "STREAMING_TABLE", "METRIC_VIEW"})


FEDERATION_EXTENSIONS = ("delta", "azure")


def enable_federation(con: Any) -> None:
    """Load what reading Databricks storage needs into a session.

    The azure extension's default transport cannot find the CA bundle on Debian images ("Problem
    with the SSL CA cert"); its curl transport can.
    """
    for extension in FEDERATION_EXTENSIONS:
        con.install_extension(extension)
        con.load_extension(extension)
    con.execute("SET azure_transport_option_type = 'curl'")


class DatabricksError(RuntimeError):
    def __init__(self, message: str, error_code: str = "DATABRICKS_ERROR", status: int = 0):
        super().__init__(message)
        self.error_code = error_code
        self.status = status


@dataclass(frozen=True)
class RemoteTable:
    """A table or view in the remote Unity Catalog."""

    catalog: str
    schema: str
    name: str
    table_type: str  # MANAGED, EXTERNAL, VIEW, MATERIALIZED_VIEW, STREAMING_TABLE, ...
    data_source_format: str | None = None
    table_id: str | None = None
    storage_location: str | None = None
    columns: list[dict[str, Any]] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    comment: str | None = None

    @property
    def key(self) -> str:
        """`schema.table`, the name the mirror config uses."""
        return f"{self.schema}.{self.name}"

    @property
    def full_name(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.name}"

    @property
    def is_view(self) -> bool:
        """Read through the SQL warehouse rather than from storage: views, materialized views,
        streaming tables, and anything without Delta files (vending covers Delta tables only)."""
        return (
            self.table_type in WAREHOUSE_ONLY_TYPES
            or not self.storage_location
            or (self.data_source_format or "DELTA") != "DELTA"
        )

    @property
    def version(self) -> int | None:
        """The Delta version Unity Catalog last recorded for the table, if any."""
        value = self.properties.get("delta.lastUpdateVersion")
        return int(value) if value not in (None, "") else None

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> RemoteTable:
        return cls(
            catalog=item["catalog_name"],
            schema=item["schema_name"],
            name=item["name"],
            table_type=item.get("table_type") or "UNKNOWN",
            data_source_format=item.get("data_source_format"),
            table_id=item.get("table_id"),
            storage_location=item.get("storage_location"),
            columns=item.get("columns") or [],
            properties=item.get("properties") or {},
            comment=item.get("comment"),
        )


@dataclass(frozen=True)
class StorageCredential:
    """Short-lived read access to one table's files."""

    location: str
    secret_sql: str  # CREATE OR REPLACE TEMPORARY SECRET ..., scoped to `location`
    expires_at: float  # epoch seconds

    @property
    def fresh(self) -> bool:
        return self.expires_at - time.time() > EXPIRY_MARGIN_SECONDS


def secret_name(location: str) -> str:
    """A stable DuckDB secret name per storage location."""
    import hashlib

    return "dbx_" + hashlib.sha1(location.encode()).hexdigest()[:16]


def storage_secret_sql(location: str, vended: dict[str, Any]) -> str:
    """A DuckDB secret for a vended credential, scoped to the table's location.

    Azure: the user-delegation SAS goes in a connection string with both endpoints, so the
    delta extension (object_store) and DuckDB's azure extension can both use it.
    """
    sas = (vended.get("azure_user_delegation_sas") or {}).get("sas_token")
    if not sas:
        kinds = sorted(k for k in vended if k not in ("expiration_time", "url"))
        raise DatabricksError(
            f"Unsupported storage credential {kinds}: only Azure (ADLS) is supported so far",
            "UNSUPPORTED_STORAGE",
        )
    host = urlparse(location).hostname or ""
    account = host.split(".")[0]
    connection_string = (
        f"DefaultEndpointsProtocol=https;AccountName={account};"
        f"BlobEndpoint=https://{account}.blob.core.windows.net;"
        f"DfsEndpoint=https://{account}.dfs.core.windows.net;SharedAccessSignature={sas}"
    )
    return (
        f"CREATE OR REPLACE TEMPORARY SECRET {sql_identifier(secret_name(location))} "
        f"(TYPE azure, CONNECTION_STRING {sql_literal(connection_string)}, "
        f"SCOPE {sql_literal(location.rstrip('/') + '/')})"
    )


class DatabricksClient:
    def __init__(
        self, host: str, token: str, warehouse_id: str | None = None, timeout: float = 60.0
    ):
        if not host or not token:
            raise DatabricksError(
                "The Databricks connection needs a host and a token (DATABRICKS_HOST and "
                "DATABRICKS_TOKEN in .env)",
                "MISSING_CREDENTIALS",
            )
        self.host = host.rstrip("/")
        if not self.host.startswith("http"):
            self.host = "https://" + self.host
        self._token = token
        self.warehouse_id = warehouse_id or None
        self._http = httpx.Client(
            base_url=self.host,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._http.request(method, path, json=json, params=params)
        except httpx.TransportError as exc:
            raise DatabricksError(
                f"Databricks workspace {self.host} is unreachable: {self._redact(str(exc))}",
                "WORKSPACE_UNREACHABLE",
            ) from None
        try:
            body: dict[str, Any] = response.json() if response.content else {}
        except ValueError:
            body = {"message": response.text[:300]}
        if response.status_code >= 400:
            raise DatabricksError(
                self._redact(str(body.get("message") or response.reason_phrase)),
                str(body.get("error_code") or f"HTTP_{response.status_code}"),
                response.status_code,
            )
        return body

    # --- Unity Catalog ------------------------------------------------------------------

    def _paged(self, path: str, key: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        token = None
        while True:
            page = {**params, "page_token": token} if token else params
            body = self.request("GET", path, params=page)
            items.extend(body.get(key) or [])
            token = body.get("next_page_token")
            if not token:
                return items

    def list_schemas(self, catalog: str) -> list[dict[str, Any]]:
        return self._paged(
            "/api/2.1/unity-catalog/schemas",
            "schemas",
            {"catalog_name": catalog, "max_results": 1000},
        )

    def list_tables(self, catalog: str, schema: str) -> list[RemoteTable]:
        items = self._paged(
            "/api/2.1/unity-catalog/tables",
            "tables",
            {"catalog_name": catalog, "schema_name": schema, "max_results": 500},
        )
        return [RemoteTable.from_json(i) for i in items]

    def get_table(self, full_name: str) -> RemoteTable:
        body = self.request("GET", f"/api/2.1/unity-catalog/tables/{quote(full_name)}")
        return RemoteTable.from_json(body)

    def vend(self, table: RemoteTable) -> StorageCredential:
        """Read credentials for one table (needs EXTERNAL USE SCHEMA on its schema)."""
        if not table.table_id or not table.storage_location:
            raise DatabricksError(f"{table.full_name} has no storage to read", "NOT_A_TABLE")
        body = self.request(
            "POST",
            "/api/2.1/unity-catalog/temporary-table-credentials",
            json={"table_id": table.table_id, "operation": "READ"},
        )
        return StorageCredential(
            location=table.storage_location,
            secret_sql=storage_secret_sql(table.storage_location, body),
            expires_at=float(body.get("expiration_time") or 0) / 1000 or time.time() + 3600,
        )

    # --- SQL warehouse (views) ----------------------------------------------------------

    def statement(self, sql: str, **options: Any) -> dict[str, Any]:
        """Run one statement on the connection's SQL warehouse and wait for it."""
        if not self.warehouse_id:
            raise DatabricksError(
                "This needs a SQL warehouse: set DATABRICKS_WAREHOUSE_ID or the connection's "
                "httpPath",
                "NO_WAREHOUSE",
            )
        body = self.request(
            "POST",
            "/api/2.0/sql/statements",
            json={
                "warehouse_id": self.warehouse_id,
                "statement": sql,
                "wait_timeout": "30s",
                "on_wait_timeout": "CONTINUE",
                **options,
            },
        )
        while (body.get("status") or {}).get("state") in ("PENDING", "RUNNING"):
            time.sleep(2)
            body = self.request("GET", f"/api/2.0/sql/statements/{body['statement_id']}")
        status = body.get("status") or {}
        if status.get("state") != "SUCCEEDED":
            error = status.get("error") or {}
            raise DatabricksError(
                self._redact(str(error.get("message") or status.get("state"))),
                str(error.get("error_code") or "STATEMENT_FAILED"),
            )
        return body

    def arrow_tables(self, sql: str) -> Iterator[Any]:
        """Stream a query's result as pyarrow Tables, one per result chunk."""
        import pyarrow.ipc  # type: ignore[import-untyped]

        body = self.statement(sql, disposition="EXTERNAL_LINKS", format="ARROW_STREAM")
        chunks = int((body.get("manifest") or {}).get("total_chunk_count") or 0)
        links = (body.get("result") or {}).get("external_links") or []
        for index in range(chunks):
            if index > 0:
                links = (
                    self.request(
                        "GET",
                        f"/api/2.0/sql/statements/{body['statement_id']}/result/chunks/{index}",
                    ).get("external_links")
                    or []
                )
            for link in links:
                # Presigned URL: no Authorization header.
                data = httpx.get(link["external_link"], timeout=300).raise_for_status().content
                yield pyarrow.ipc.open_stream(data).read_all()


def utc_from_ms(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value) / 1000, UTC) if value else None
