"""Unit tests for live foreign catalogs: which tables a statement reads, and the prelude."""

from __future__ import annotations

import time
from typing import Any

import pytest

from duckbricks.federation import live
from duckbricks.federation.databricks import DatabricksError, RemoteTable, StorageCredential
from duckbricks.federation.live import LiveCatalog, prepare, referenced_tables

UC = LiveCatalog("main_prod", "main_prod", "https://adb-1.azuredatabricks.net", "dapi-x", "wh1")


def test_referenced_tables_three_two_and_one_part_names() -> None:
    refs = referenced_tables(
        "SELECT * FROM main_prod.sales.orders o JOIN sales.items i ON o.id = i.id, customers",
        "main_prod",
        "sales",
    )
    assert ("main_prod", "sales", "orders") in refs
    assert ("main_prod", "sales", "items") in refs  # schema.table in the default catalog
    assert ("sales", "default", "items") in refs  # or catalog.table in `default`
    assert ("main_prod", "sales", "customers") in refs


def test_referenced_tables_skips_ctes_and_table_functions() -> None:
    refs = referenced_tables(
        "WITH recent AS (SELECT 1) SELECT * FROM recent, read_parquet('s3://x/*.parquet')",
        "main_prod",
        "sales",
    )
    assert refs == set()
    assert referenced_tables("not sql at all (((") == set()


def test_prepare_is_empty_without_live_tables() -> None:
    assert prepare("SELECT * FROM workspace.default.t", [UC]) == []
    assert prepare("SELECT 1", []) == []


def test_prepare_attaches_read_only_and_scopes_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    table = RemoteTable(
        "main_prod",
        "sales",
        "orders",
        "MANAGED",
        "DELTA",
        "id1",
        "abfss://c@a.dfs.core.windows.net/t",
    )
    calls: list[str] = []

    def fake_table(c: LiveCatalog, full_name: str) -> RemoteTable:
        calls.append(full_name)
        if full_name.endswith(".default.orders"):
            raise DatabricksError("missing", "TABLE_DOES_NOT_EXIST", 404)
        return table

    def fake_credential(c: LiveCatalog, t: RemoteTable) -> StorageCredential:
        return StorageCredential(
            t.storage_location or "", "CREATE SECRET scoped", time.time() + 3600
        )

    monkeypatch.setattr(live._cache, "table", fake_table)
    monkeypatch.setattr(live._cache, "credential", fake_credential)
    out = prepare("SELECT count(*) FROM main_prod.sales.orders", [UC], "workspace", None)
    assert any(
        "ATTACH IF NOT EXISTS 'main_prod' AS \"main_prod\"" in s and "READ_ONLY" in s for s in out
    )
    assert any("TYPE unity_catalog" in s for s in out)
    assert out[-1] == "CREATE SECRET scoped"
    assert calls == ["main_prod.sales.orders"]


def test_prepare_rejects_views(monkeypatch: pytest.MonkeyPatch) -> None:
    view = RemoteTable("main_prod", "sales", "v", "VIEW")

    def fake_table(c: LiveCatalog, full_name: str) -> Any:
        return view

    monkeypatch.setattr(live._cache, "table", fake_table)
    with pytest.raises(DatabricksError, match="cannot read views"):
        prepare("SELECT * FROM main_prod.sales.v", [UC])


def test_live_catalog_repr_hides_the_token() -> None:
    assert "dapi-x" not in repr(UC)
