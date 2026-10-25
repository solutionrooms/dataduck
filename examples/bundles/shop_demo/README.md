# shop_demo

The acceptance example (SPEC section 12). Seed the landing bucket, then deploy and run:

```sh
uv run python scripts/seed_landing.py
dbk bundle validate -t dev
dbk bundle deploy -t dev
dbk bundle run shop_daily -t dev
dbk sql "select * from dev.gold.daily_revenue order by order_date desc limit 5"
```

- `ingest_bronze` (sql_task) appends new CSV files from `s3://landing/shop/orders/dt=*/`
  to `bronze.orders_raw` and replaces `bronze.customers_raw`.
- `transform` (dbt_task) runs `dbt build`: `silver.orders`, `silver.customers`,
  `gold.daily_revenue`, with unique, not_null and accepted_values tests.
- `quality_checks` (spark_python_task) asserts gold has rows and matches silver revenue.

In `dev` (mode: development) the job is named `[dev <you>] shop_daily` and its schedule is
paused. `prod` writes to the `prod` catalog with the 06:00 Europe/London schedule active.
