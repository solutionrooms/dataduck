import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    from duckbricks.runtime import con, spark

    return con, mo, spark


@app.cell
def _(mo):
    mo.md(
        """
        # Getting started with Duckbricks

        This notebook runs in the **notebooks** service with every catalog attached.

        - `con` is a DuckDB connection (DuckDB SQL, three-part names `catalog.schema.table`).
        - `spark` is SQLFrame: the PySpark DataFrame API, executed by DuckDB.
        - SQL cells (`mo.sql`) run on `con`.

        Run `make seed` and deploy `examples/bundles/shop_demo` to fill `dev.gold`.
        """
    )
    return


@app.cell
def _(con, mo):
    catalogs = mo.sql(
        """
        SELECT database_name AS catalog, count(*) AS tables
        FROM duckdb_tables()
        GROUP BY ALL
        ORDER BY 1
        """,
        engine=con,
    )
    return (catalogs,)


@app.cell
def _(con, mo):
    revenue = mo.sql(
        """
        SELECT order_date, orders, revenue
        FROM dev.gold.daily_revenue
        ORDER BY order_date DESC
        LIMIT 30
        """,
        engine=con,
    )
    return (revenue,)


@app.cell
def _(spark):
    from sqlframe.duckdb import functions as F

    by_status = (
        spark.table("dev.silver.orders")
        .groupBy("status")
        .agg(F.count("*").alias("orders"), F.sum("amount").alias("amount"))
        .orderBy("status")
    )
    by_status.toPandas()
    return


if __name__ == "__main__":
    app.run()
