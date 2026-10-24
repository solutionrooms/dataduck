from pydantic import SecretStr

from duckbricks.core.config import Settings
from duckbricks.core.session import (
    CatalogAttachment,
    attach_statement,
    secret_statements,
    sql_identifier,
    sql_literal,
)

SETTINGS = Settings(
    _env_file=None,
    pg_host="postgres",
    pg_port=5432,
    pg_password=SecretStr("pa'ss"),
    s3_endpoint="seaweedfs:8333",
    s3_secret_key=SecretStr("s3cr'et"),
)
DEV = CatalogAttachment("dev", metadata_schema="dev", data_path="s3://lakehouse/dev/")


def test_literals_and_identifiers_are_escaped() -> None:
    assert sql_literal("it's") == "'it''s'"
    assert sql_identifier('we"ird') == '"we""ird"'


def test_secrets_escape_values_and_use_path_style_without_ssl() -> None:
    s3, pg = secret_statements(SETTINGS)
    assert "SECRET 's3cr''et'" in s3
    assert "ENDPOINT 'seaweedfs:8333'" in s3
    assert "URL_STYLE 'path'" in s3 and "USE_SSL false" in s3
    assert "PASSWORD 'pa''ss'" in pg and "HOST 'postgres'" in pg and "PORT 5432" in pg


def test_attach_uses_schema_per_catalog_and_named_pg_secret() -> None:
    sql = attach_statement(DEV, SETTINGS, read_only=False)
    assert sql.startswith("ATTACH IF NOT EXISTS 'ducklake:postgres:dbname=lake' AS \"dev\"")
    assert "DATA_PATH 's3://lakehouse/dev/'" in sql
    assert "METADATA_SCHEMA 'dev'" in sql
    assert "METADATA_PARAMETERS MAP {'SECRET': 'dbk_pg'}" in sql
    assert "READ_ONLY" not in sql


def test_attach_read_only() -> None:
    assert attach_statement(DEV, SETTINGS, read_only=True).endswith(", READ_ONLY)")


def test_attach_never_embeds_credentials() -> None:
    sql = attach_statement(DEV, SETTINGS, read_only=False)
    assert "pa''ss" not in sql and "pa'ss" not in sql
