-- Append new landing files to bronze. A file already loaded is skipped (matched by name),
-- so re-running the job never double-loads. The task parameters set catalog and schema.

CREATE TABLE IF NOT EXISTS orders_raw (
    order_id VARCHAR,
    customer_id VARCHAR,
    product_id VARCHAR,
    order_ts VARCHAR,
    quantity VARCHAR,
    unit_price VARCHAR,
    status VARCHAR,
    _source_file VARCHAR,
    _loaded_at TIMESTAMP WITH TIME ZONE
);

INSERT INTO orders_raw
SELECT
    order_id,
    customer_id,
    product_id,
    order_ts,
    quantity,
    unit_price,
    status,
    filename AS _source_file,
    now() AS _loaded_at
FROM read_csv(
    's3://landing/shop/orders/*/*.csv',
    header = true,
    all_varchar = true,
    filename = true,
    hive_partitioning = false
)
WHERE filename NOT IN (SELECT DISTINCT _source_file FROM orders_raw);

-- Customers arrive as one full snapshot; replace it each run.
CREATE OR REPLACE TABLE customers_raw AS
SELECT *, filename AS _source_file, now() AS _loaded_at
FROM read_csv(
    's3://landing/shop/customers/*.csv',
    header = true,
    all_varchar = true,
    filename = true
);

SELECT
    (SELECT count(*) FROM orders_raw) AS orders_raw_rows,
    (SELECT count(DISTINCT _source_file) FROM orders_raw) AS files_loaded,
    (SELECT count(*) FROM customers_raw) AS customers_raw_rows;
