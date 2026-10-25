"""SQL warehouses: lifecycle and statements routed over Quack (make up)."""

import uuid

import httpx
import pytest

from duckbricks.client import ApiCallError, DuckbricksClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client() -> DuckbricksClient:
    return DuckbricksClient()


def test_warehouse_lifecycle_and_statements(client: DuckbricksClient) -> None:
    name = f"it-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/2.0/sql/warehouses",
        {"name": name, "cluster_size": "X-Small", "auto_stop_mins": 5, "enable_photon": True},
    )
    warehouse_id = created["id"]
    assert created["warnings"] == [
        "Field 'enable_photon' is accepted but has no effect in Duckbricks"
    ]
    try:
        assert client.get(f"/api/2.0/sql/warehouses/{warehouse_id}")["state"] == "STOPPED"
        # A statement starts a stopped warehouse, as on Databricks.
        result = client.post(
            "/api/2.0/sql/statements",
            {
                "statement": (
                    "SELECT :n * 2 AS doubled, :s AS s, ':n' AS literal, current_database() AS db"
                ),
                "warehouse_id": warehouse_id,
                "catalog": "dev",
                "wait_timeout": "50s",
                "parameters": [
                    {"name": "n", "value": "21", "type": "INT"},
                    {"name": "s", "value": "it's"},
                ],
            },
        )
        assert result["status"]["state"] == "SUCCEEDED", result["status"]
        assert result["result"]["data_array"] == [["42", "it's", ":n", "dev"]]
        info = client.get(f"/api/2.0/sql/warehouses/{warehouse_id}")
        assert info["state"] == "RUNNING" and info["quack_uri"].startswith("quack:127.0.0.1:")
        assert info["node_type_id"] == "dbk.small"

        # DDL and DML run on the warehouse and are visible to everyone.
        table = f"dev.default.wh_{uuid.uuid4().hex[:8]}"
        client.rows(
            f"CREATE TABLE {table} AS SELECT range AS i FROM range(5); "
            f"INSERT INTO {table} VALUES (99)",
            warehouse_id=warehouse_id,
        )
        assert client.rows(f"SELECT count(*) AS n FROM {table}")[0]["n"] == "6"
        client.rows(f"DROP TABLE {table}", warehouse_id=warehouse_id)

        client.post(f"/api/2.0/sql/warehouses/{warehouse_id}/stop")
        assert client.get(f"/api/2.0/sql/warehouses/{warehouse_id}")["state"] == "STOPPED"
    finally:
        client.delete(f"/api/2.0/sql/warehouses/{warehouse_id}")
    with pytest.raises(ApiCallError) as missing:
        client.get(f"/api/2.0/sql/warehouses/{warehouse_id}")
    assert missing.value.error_code == "RESOURCE_DOES_NOT_EXIST"


def test_bad_cluster_size_is_rejected(client: DuckbricksClient) -> None:
    with pytest.raises(ApiCallError) as error:
        client.post("/api/2.0/sql/warehouses", {"name": "bad", "cluster_size": "Huge"})
    assert error.value.error_code == "INVALID_PARAMETER_VALUE"


def test_notebooks_service_is_up() -> None:
    response = httpx.get("http://127.0.0.1:2718/", timeout=10, follow_redirects=True)
    assert response.status_code in (200, 401, 403)
