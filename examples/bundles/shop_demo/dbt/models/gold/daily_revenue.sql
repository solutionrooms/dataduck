-- Revenue per order day. Cancelled and returned orders count as orders but not as revenue.
select
    order_date,
    count(*) as orders,
    count(distinct customer_id) as customers,
    cast(
        sum(case when status in ('completed', 'shipped') then amount else 0 end)
        as decimal(14, 2)
    ) as revenue
from {{ ref('orders') }}
group by order_date
