"""Unit tests for the Databricks mirror: planning, config, filters, credentials, connections."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from duckbricks.core.config import Settings
from duckbricks.core.errors import InvalidParameter
from duckbricks.core.session import open_session
from duckbricks.federation.connections import Connection, normalise_options
from duckbricks.federation.databricks import DatabricksError, RemoteTable, storage_secret_sql
from duckbricks.federation.mirror import MirrorConfig, plan, resolve_filter

LOCATION = "abfss://lake@acct1.dfs.core.windows.net/tables/abc"


def table(name: str, schema: str = "sales", version: int | None = 7, **kw: Any) -> RemoteTable:
    props = {"delta.lastUpdateVersion": str(version)} if version is not None else {}
    return RemoteTable(
        catalog="prod",
        schema=schema,
        name=name,
        table_type=kw.pop("table_type", "MANAGED"),
        data_source_format=kw.pop("data_source_format", "DELTA"),
        table_id=f"id-{name}",
        storage_location=kw.pop("storage_location", f"{LOCATION}/{name}"),
        properties=props,
        **kw,
    )


def view(name: str, schema: str = "sales") -> RemoteTable:
    return table(name, schema, version=None, table_type="VIEW", storage_location=None)


def synced(version: int | None, filter: str | None = None) -> dict[str, Any]:
    return {"status": "SYNCED", "remote_version": version, "filter": filter}


def kinds(actions: list[Any]) -> dict[str, tuple[str, str]]:
    return {a.key: (a.kind, a.reason) for a in actions}


def test_plan_copies_new_and_changed_and_skips_unchanged() -> None:
    config = MirrorConfig(schemas=["sales"])
    remote = [table("orders", version=9), table("customers", version=3), table("items")]
    state = {("sales", "orders"): synced(8), ("sales", "customers"): synced(3)}
    out = kinds(plan(config, remote, state))
    assert out["sales.orders"] == ("COPY", "version 8 -> 9")
    assert out["sales.customers"] == ("SKIP", "unchanged (version 3)")
    assert out["sales.items"] == ("COPY", "new")


def test_plan_retries_failures_and_unknown_versions() -> None:
    config = MirrorConfig(schemas=["sales"])
    remote = [table("orders"), table("odd", version=None)]
    state = {
        ("sales", "orders"): {"status": "FAILED", "remote_version": None, "filter": None},
        ("sales", "odd"): synced(None),
    }
    out = kinds(plan(config, remote, state))
    assert out["sales.orders"] == ("COPY", "retry after failure")
    assert out["sales.odd"] == ("COPY", "remote version unknown")


def test_plan_recopies_when_the_filter_changes() -> None:
    config = MirrorConfig(schemas=["sales"], filters={"Sales.Orders": "year >= 2025"})
    remote = [table("orders", version=7)]
    out = kinds(plan(config, remote, {("sales", "orders"): synced(7, "year >= 2024")}))
    assert out["sales.orders"] == ("COPY", "filter changed")
    same = kinds(plan(config, remote, {("sales", "orders"): synced(7, "year >= 2025")}))
    assert same["sales.orders"][0] == "SKIP"


def test_plan_full_refresh_copies_everything() -> None:
    config = MirrorConfig(schemas=["sales"])
    out = kinds(
        plan(config, [table("orders")], {("sales", "orders"): synced(7)}, full_refresh=True)
    )
    assert out["sales.orders"] == ("COPY", "full refresh")


def test_plan_include_exclude_and_drops() -> None:
    config = MirrorConfig(schemas=["sales"], exclude=["sales.big_*"])
    remote = [table("orders"), table("big_journal")]
    state = {
        ("sales", "big_journal"): synced(7),
        ("sales", "gone"): synced(1),
        ("hr", "people"): synced(1),
        ("sales", "old"): {"status": "EXCLUDED", "remote_version": 1, "filter": None},
    }
    out = kinds(plan(config, remote, state))
    assert out["sales.big_journal"] == ("DROP", "excluded by the config")
    assert out["sales.gone"] == ("DROP", "dropped remotely")
    assert out["hr.people"] == ("DROP", "schema no longer mirrored")
    assert "sales.old" not in out  # already excluded: nothing to do
    statuses = {a.key: a.status for a in plan(config, remote, state) if a.kind == "DROP"}
    assert statuses == {
        "sales.big_journal": "EXCLUDED",
        "sales.gone": "DROPPED",
        "hr.people": "EXCLUDED",
    }
    only = MirrorConfig(schemas=["sales"], include=["sales.ord*"])
    assert set(kinds(plan(only, remote, {}))) == {"sales.orders"}


def test_plan_views_copy_on_schedule_and_skip_on_poll() -> None:
    config = MirrorConfig(schemas=["sales"])
    remote = [view("v_orders")]
    assert kinds(plan(config, remote, {}, trigger="SCHEDULE"))["sales.v_orders"][0] == "COPY"
    assert kinds(plan(config, remote, {}, trigger="POLL"))["sales.v_orders"][0] == "SKIP"
    no_views = MirrorConfig(schemas=["sales"], include_views=False)
    assert plan(no_views, remote, {}) == []


def test_plan_only_limits_tables_and_never_drops() -> None:
    config = MirrorConfig(schemas=["sales"])
    remote = [table("orders"), table("items")]
    state = {("sales", "gone"): synced(1)}
    out = kinds(plan(config, remote, state, only=["sales.orders"]))
    assert out == {"sales.orders": ("COPY", "new")}


def test_remote_table_kinds() -> None:
    assert not table("t").is_view
    assert table("t").version == 7
    assert view("v").is_view
    assert table("p", data_source_format="PARQUET").is_view
    assert table("mv", table_type="MATERIALIZED_VIEW").is_view


@pytest.mark.parametrize(
    "bad",
    [
        {"schemas": []},
        {"schemas": ["s"], "filters": {"nodot": "x > 1"}},
        {"schemas": ["s"], "filters": {"s.t": "x >"}},
        {"schemas": ["s"], "poll_minutes": {"s.t": 0}},
        {"schemas": ["s"], "schedule": {"quartz_cron_expression": "not cron"}},
        {"schemas": ["s"], "unknown": 1},
    ],
)
def test_config_rejects_invalid(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MirrorConfig.model_validate(bad)


def test_resolve_filter_evaluates_scalar_subqueries() -> None:
    con = open_session("dbk.small", catalogs=[])
    try:
        con.execute(
            "CREATE TEMP VIEW source AS SELECT * FROM (VALUES ('2025007', 1), ('2026007', 2)) "
            "t(period, n)"
        )
        predicate = (
            "period > (SELECT CAST(max(CAST(period AS INTEGER)) - 1000 AS VARCHAR) FROM source)"
        )
        resolved = resolve_filter(con, predicate)
        assert resolved == "period > '2025007'"
        assert con.execute(f"SELECT n FROM source WHERE {resolved}").fetchall() == [(2,)]
        assert resolve_filter(con, "n > (SELECT min(n) FROM source)") == "n > 1"
    finally:
        con.close()


def test_storage_secret_is_scoped_and_azure_only() -> None:
    sql = storage_secret_sql(
        LOCATION, {"azure_user_delegation_sas": {"sas_token": "sv=1&sig=x"}, "url": LOCATION}
    )
    assert "TYPE azure" in sql
    assert f"SCOPE '{LOCATION}/'" in sql
    assert "AccountName=acct1;" in sql and "SharedAccessSignature=sv=1&sig=x" in sql
    with pytest.raises(DatabricksError, match="only Azure"):
        storage_secret_sql(LOCATION, {"aws_temp_credentials": {}})


def test_connection_options_drop_secrets_and_default_from_env() -> None:
    settings = Settings(
        DATABRICKS_HOST="adb-1.azuredatabricks.net/", DATABRICKS_WAREHOUSE_ID="abc123"
    )
    stored, warnings = normalise_options({"personalAccessToken": "dapi-secret"}, settings)
    assert stored == {
        "host": "https://adb-1.azuredatabricks.net",
        "duckbricks_warehouse_id": "abc123",
    }
    assert any("personalAccessToken" in w for w in warnings)
    stored, _ = normalise_options(
        {"host": "https://h", "httpPath": "/sql/1.0/warehouses/f00d"}, settings
    )
    from datetime import UTC, datetime

    c = Connection("c", "DATABRICKS", stored, None, datetime.now(UTC))
    assert c.warehouse_id == "f00d"
    with pytest.raises(InvalidParameter):
        normalise_options({}, Settings(DATABRICKS_HOST=""))
