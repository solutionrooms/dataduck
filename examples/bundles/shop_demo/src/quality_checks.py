"""Quality gate after dbt: gold has rows, and gold revenue matches silver within 0.01.

Runs as a spark_python_task. On Duckbricks `spark` is SQLFrame over DuckDB; on Databricks
the `duckbricks.runtime` import falls back to the cluster's own `spark` and `dbutils`.
"""

import argparse

try:
    from duckbricks.runtime import dbutils, spark
except ImportError:  # running on Databricks
    from databricks.sdk.runtime import dbutils, spark  # type: ignore[no-redef]

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", default="dev")
args = parser.parse_args()
catalog = args.catalog

gold = spark.sql(
    f"SELECT count(*) AS days, coalesce(sum(revenue), 0) AS revenue "
    f"FROM {catalog}.gold.daily_revenue"
).collect()[0]
silver = spark.sql(
    f"SELECT coalesce(sum(amount), 0) AS revenue FROM {catalog}.silver.orders "
    f"WHERE status IN ('completed', 'shipped')"
).collect()[0]

days = int(gold["days"])
gold_revenue = float(gold["revenue"])
silver_revenue = float(silver["revenue"])
print(f"gold.daily_revenue: {days} day(s), revenue {gold_revenue:,.2f}")
print(f"silver.orders revenue: {silver_revenue:,.2f}")

assert days > 0, "gold.daily_revenue is empty"
assert abs(gold_revenue - silver_revenue) <= 0.01, (
    f"gold revenue {gold_revenue:.2f} does not match silver {silver_revenue:.2f}"
)
dbutils.jobs.taskValues.set(key="gold_days", value=days)
print("quality checks passed")
