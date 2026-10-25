"""SPEC section 12 acceptance test, driven through the `dbk` CLI like a user would.

`make e2e` brings the stack up and seeds the landing bucket first. The test deploys to the
`dev` target and destroys it at the end, which drops dev.bronze, dev.silver and dev.gold.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from duckbricks.client import DuckbricksClient

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
SHOP = REPO / "examples" / "bundles" / "shop_demo"
WEB = REPO / "examples" / "bundles" / "web_events"
SEEDED_DAYS = 90


def dbk(*args: str, cwd: Path = SHOP, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["uv", "run", "--quiet", "dbk", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"dbk {' '.join(args)} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
    return result


def dbk_json(*args: str, cwd: Path = SHOP) -> Any:
    return json.loads(dbk(*args, "-o", "json", cwd=cwd).stdout)


def scalar(client: DuckbricksClient, sql: str) -> int:
    return int(next(iter(client.rows(sql)[0].values())))


@pytest.fixture(scope="module")
def client() -> DuckbricksClient:
    return DuckbricksClient()


def jobs_named(client: DuckbricksClient, suffix: str) -> list[dict[str, Any]]:
    jobs = client.get("/api/2.2/jobs/list", limit=100).get("jobs", [])
    return [j for j in jobs if j["settings"]["name"].endswith(suffix)]


def test_shop_demo_end_to_end(client: DuckbricksClient) -> None:
    # 2. validate and deploy
    summary = dbk_json("bundle", "validate", "-t", "dev")
    assert summary["ok"] and summary["resources"]["jobs"] == ["shop_daily"]
    report = dbk_json("bundle", "deploy", "-t", "dev")
    assert not report.get("deleted")

    # 3. run and assert SUCCESS
    run = dbk_json("bundle", "run", "shop_daily", "-t", "dev")
    assert run["state"]["result_state"] == "SUCCESS", run["state"]
    assert [t["state"]["result_state"] for t in run["tasks"]] == ["SUCCESS"] * 3

    # 4. gold has one row per seeded day
    assert scalar(client, "SELECT count(*) FROM dev.gold.daily_revenue") == SEEDED_DAYS
    bronze_rows = scalar(client, "SELECT count(*) FROM dev.bronze.orders_raw")
    assert bronze_rows > 0

    # 5. redeploy: nothing changes, no duplicate job
    again = dbk_json("bundle", "deploy", "-t", "dev")
    assert not again.get("created") and not again.get("deleted")
    assert len(jobs_named(client, "shop_daily")) == 1

    # 6. run again: bronze does not double-load
    rerun = dbk_json("bundle", "run", "shop_daily", "-t", "dev")
    assert rerun["state"]["result_state"] == "SUCCESS"
    assert scalar(client, "SELECT count(*) FROM dev.bronze.orders_raw") == bronze_rows

    # lineage was captured from dbt
    lineage = client.get("/api/duckbricks/lineage/table", name="dev.gold.daily_revenue")
    assert {u["name"] for u in lineage["upstreams"]} >= {"dev.silver.orders"}

    # an unsupported field is a warning, not a failure
    assert any("email_notifications" in w for w in summary["warnings"])

    # 7. destroy
    destroyed = dbk_json("bundle", "destroy", "-t", "dev", "--auto-approve")
    assert "jobs.shop_daily" in destroyed["deleted"]
    assert not jobs_named(client, "shop_daily")


def test_web_events_pipeline(client: DuckbricksClient) -> None:
    dbk("bundle", "deploy", "-t", "dev", cwd=WEB)
    try:
        run = dbk_json("bundle", "run", "web_events_refresh", "-t", "dev", cwd=WEB)
        assert run["state"]["result_state"] == "SUCCESS", run["state"]
        bronze = scalar(client, "SELECT count(*) FROM dev.web.events_bronze")
        clean = scalar(client, "SELECT count(*) FROM dev.web.events_clean")
        assert 0 < clean < bronze  # the DROP ROW expectation removed invalid durations
        assert scalar(client, "SELECT count(*) FROM dev.web.daily_funnel") >= SEEDED_DAYS
        # incremental: a second update reads no new files
        dbk_json("bundle", "run", "web_events_refresh", "-t", "dev", cwd=WEB)
        assert scalar(client, "SELECT count(*) FROM dev.web.events_bronze") == bronze
    finally:
        dbk("bundle", "destroy", "-t", "dev", "--auto-approve", cwd=WEB)
