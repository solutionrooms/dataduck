"""Platform settings, read from the environment (and `.env` on the host)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Connection settings for Postgres, SeaweedFS and the API.

    Defaults suit the host side of `make up`. Containers override hosts and ports
    in docker-compose.yml.
    """

    model_config = SettingsConfigDict(env_prefix="DBK_", env_file=".env", extra="ignore")

    pg_host: str = "127.0.0.1"
    pg_port: int = 5433
    pg_user: str = "duckbricks"
    pg_password: SecretStr = SecretStr("duckbricks")
    control_db: str = "control"
    lake_db: str = "lake"

    s3_endpoint: str = "127.0.0.1:8333"
    s3_access_key: SecretStr = SecretStr("duckbricks")
    s3_secret_key: SecretStr = SecretStr("duckbricks")
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    lakehouse_bucket: str = "lakehouse"
    landing_bucket: str = "landing"

    node_types_file: Path = REPO_ROOT / "config" / "node_types.yml"
    token: SecretStr = SecretStr("")

    # The single platform user (`workspace.current_user.userName` in bundles).
    user_name: str = "user@duckbricks.local"
    # Where the API is reached from task code, the CLI and the MCP server.
    host: str = "http://127.0.0.1:8080"
    # Where a browser reaches the UI (run_page_url and links). Same as `host` on the host side.
    public_url: str = "http://127.0.0.1:8080"

    workspace_root: Path = REPO_ROOT / "data" / "workspace"
    logs_root: Path = REPO_ROOT / "data" / "logs"
    worker_slots: int = 4
    api_node_type: str = "dbk.medium"

    # Warehouses (Quack) and notebooks (marimo).
    warehouse_agent_url: str = "http://127.0.0.1:9400"
    warehouse_host: str = "127.0.0.1"
    warehouse_token: SecretStr = SecretStr("")
    notebooks_url: str = "http://127.0.0.1:2718"
    notebooks_token: SecretStr = SecretStr("")

    # A Databricks workspace for live foreign catalogs and the mirror (ADR 0013). Optional; the
    # Databricks CLI's variable names, so one .env serves both.
    databricks_host: str = Field(default="", validation_alias="DATABRICKS_HOST")
    databricks_token: SecretStr = Field(default=SecretStr(""), validation_alias="DATABRICKS_TOKEN")
    databricks_warehouse_id: str = Field(default="", validation_alias="DATABRICKS_WAREHOUSE_ID")

    @property
    def user_short_name(self) -> str:
        """`workspace.current_user.short_name`: the user name up to `@`, Databricks-style."""
        return self.user_name.split("@", 1)[0].replace(".", "_")

    @property
    def lake_url(self) -> str:
        return self._pg_url(self.lake_db)

    @property
    def control_url(self) -> str:
        """SQLAlchemy URL for the control database."""
        return self._pg_url(self.control_db)

    def _pg_url(self, database: str) -> str:
        password = self.pg_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.pg_user}:{password}"
            f"@{self.pg_host}:{self.pg_port}/{database}"
        )

    def data_path(self, catalog: str) -> str:
        """Object-store prefix that holds a catalog's table data."""
        return f"s3://{self.lakehouse_bucket}/{catalog}/"


@lru_cache
def get_settings() -> Settings:
    return Settings()
