-- Typed, deduplicated orders. Dirty rows (no customer, non-positive quantity, unknown status)
-- are dropped here; the latest load of an order wins.
with typed as (
    select
        order_id,
        nullif(trim(customer_id), '') as customer_id,
        product_id,
        try_cast(order_ts as timestamp) as order_ts,
        try_cast(quantity as integer) as quantity,
        try_cast(unit_price as decimal(10, 2)) as unit_price,
        lower(trim(status)) as status,
        _loaded_at,
        _source_file
    from {{ source('bronze', 'orders_raw') }}
    where nullif(trim(order_id), '') is not null
),

ranked as (
    select
        *,
        row_number() over (
            partition by order_id order by _loaded_at desc, _source_file desc
        ) as row_rank
    from typed
)

select
    order_id,
    customer_id,
    product_id,
    order_ts,
    cast(order_ts as date) as order_date,
    quantity,
    unit_price,
    cast(quantity * unit_price as decimal(12, 2)) as amount,
    status
from ranked
where
    row_rank = 1
    and customer_id is not null
    and order_ts is not null
    and quantity > 0
    and unit_price is not null
    and status in ('completed', 'shipped', 'cancelled', 'returned')
