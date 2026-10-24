"""Platform settings, read from the environment (and `.env` on the host)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
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
