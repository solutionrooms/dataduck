-- Reads a DuckLake table written by the platform and writes a new DuckLake table.
select
    customer_id,
    count(*) as orders,
    sum(amount) as revenue
from {{ source('spike', 'spike_b_src') }}
group by customer_id
