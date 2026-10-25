select
    customer_id,
    trim(name) as name,
    lower(trim(email)) as email,
    upper(trim(country)) as country,
    try_cast(signup_date as date) as signup_date
from {{ source('bronze', 'customers_raw') }}
where nullif(trim(customer_id), '') is not null
qualify row_number() over (partition by customer_id order by _loaded_at desc) = 1
