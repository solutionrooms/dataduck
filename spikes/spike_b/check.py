"""Seed the source table for Spike B, then verify dbt's output from a separate session.

A separate read-only session proves the table was committed to the DuckLake metadata, not just
reported as built (dbt-labs/dbt#16486 describes silent non-commits).
"""

from __future__ import annotations

import sys

import duckdb

from duckbricks.core.session import open_session

SOURCE = "dev.default.spike_b_src"
TARGET = "dev.default.spike_b_orders"


def seed() -> None:
    con = open_session("dbk.small", default_catalog="dev")
    con.execute(f"DROP TABLE IF EXISTS {TARGET}")
    con.execute(
        f"CREATE OR REPLACE TABLE {SOURCE} AS SELECT range AS order_id, range % 50 AS customer_id,"
        " round(10 + (range % 7) * 2.5, 2) AS amount FROM range(1000)"
    )
    con.close()
    print(f"seeded {SOURCE}: 1000 orders, 50 customers")


def verify(driver: str) -> int:
    failures = []
    platform = f"v{duckdb.__version__}"
    if driver != platform:
        failures.append(f"dbt's DuckDB driver is {driver}; the platform runs {platform}")

    con = open_session("dbk.small", read_only=True)
    try:
        expected = con.execute(
            f"SELECT count(DISTINCT customer_id), count(*), sum(amount) FROM {SOURCE}"
        ).fetchone()
        actual = con.execute(f"SELECT count(*), sum(orders), sum(revenue) FROM {TARGET}").fetchone()
        files = con.execute(
            "SELECT data_file FROM ducklake_list_files('dev', 'spike_b_orders', "
            "schema => 'default')"
        ).fetchall()
    except duckdb.Error as exc:
        failures.append(f"could not read {TARGET} from a separate session: {exc}")
        expected = actual = None
        files = []
    finally:
        con.close()

    if actual is not None and actual != expected:
        failures.append(f"{TARGET} has (customers, orders, revenue) {actual}; expected {expected}")
    if actual is not None and not any(f[0].startswith("s3://lakehouse/dev/") for f in files):
        failures.append(f"no Parquet data file under s3://lakehouse/dev/ for {TARGET}: {files}")

    print(f"dbt DuckDB driver {driver}, platform DuckDB {platform}")
    print(f"{TARGET}: {actual}, files: {[f[0] for f in files]}")

    cleanup = open_session("dbk.small")
    cleanup.execute(f"DROP TABLE IF EXISTS {TARGET}")
    cleanup.execute(f"DROP TABLE IF EXISTS {SOURCE}")
    cleanup.close()

    if failures:
        print("SPIKE B FAILED\n- " + "\n- ".join(failures))
        return 1
    print("SPIKE B PASSED")
    return 0


def lineage(info_schema_dir: str) -> None:
    """Print the column lineage dbt's static analysis wrote (full distribution only)."""
    con = open_session("dbk.small", catalogs=[])
    try:
        rows = con.execute(
            f"SELECT * FROM read_parquet('{info_schema_dir}/dbt.column_lineage.parquet') LIMIT 20"
        ).fetchall()
        cols = [d[0] for d in con.description]
        for name in ("models", "sources", "dag_nodes", "edges"):
            described = con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{info_schema_dir}/dbt.{name}.parquet')"
            ).fetchall()
            print(f"dbt.{name} columns: {[d[0] for d in described]}")
            sample = con.execute(
                f"SELECT * FROM read_parquet('{info_schema_dir}/dbt.{name}.parquet') LIMIT 2"
            ).fetchall()
            print(f"dbt.{name} sample: {sample}")
    finally:
        con.close()
    print(f"column_lineage columns: {cols}")
    for row in rows:
        print(row)


if __name__ == "__main__":
    if sys.argv[1] == "seed":
        seed()
    elif sys.argv[1] == "lineage":
        lineage(sys.argv[2])
    else:
        sys.exit(verify(sys.argv[2]))
