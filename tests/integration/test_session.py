"""Session factory against the running stack (make up)."""

import uuid

import pytest

from duckbricks.core.catalogs import BOOTSTRAP_CATALOGS
from duckbricks.core.session import open_session

pytestmark = pytest.mark.integration


def test_every_bootstrap_catalog_is_attached() -> None:
    con = open_session("dbk.small")
    try:
        rows = con.sql("SELECT database_name FROM duckdb_databases() WHERE type = 'ducklake'")
        attached = {r[0] for r in rows.fetchall()}
    finally:
        con.close()
    assert set(BOOTSTRAP_CATALOGS) <= attached


def test_node_type_limits_apply() -> None:
    con = open_session("dbk.small")
    try:
        assert con.execute("SELECT current_setting('threads')").fetchone() == (2,)
    finally:
        con.close()


def test_write_then_read_back_from_another_session() -> None:
    table = f"dev.default.it_{uuid.uuid4().hex[:8]}"
    writer = open_session("dbk.small", default_catalog="dev")
    try:
        writer.execute(f"CREATE TABLE {table} AS SELECT range AS id FROM range(100)")
        reader = open_session("dbk.small", read_only=True)
        try:
            assert reader.execute(f"SELECT count(*) FROM {table}").fetchone() == (100,)
        finally:
            reader.close()
    finally:
        writer.execute(f"DROP TABLE IF EXISTS {table}")
        writer.close()


def test_read_only_session_rejects_writes() -> None:
    con = open_session("dbk.small", read_only=True)
    try:
        with pytest.raises(Exception, match="read-only"):
            con.execute("CREATE TABLE dev.default.should_fail AS SELECT 1")
    finally:
        con.close()
