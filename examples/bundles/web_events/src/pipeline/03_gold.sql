CREATE OR REFRESH MATERIALIZED VIEW daily_funnel
COMMENT 'Page views, add-to-carts and checkouts per day'
AS SELECT
  CAST(event_ts AS DATE) AS event_date,
  count(*) FILTER (WHERE event_type = 'page_view') AS page_views,
  count(*) FILTER (WHERE event_type = 'add_to_cart') AS add_to_carts,
  count(*) FILTER (WHERE event_type = 'checkout') AS checkouts,
  count(DISTINCT customer_id) AS visitors
FROM events_clean
GROUP BY ALL;
