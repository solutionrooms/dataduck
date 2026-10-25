-- Expectations: invalid durations are dropped; unknown customers are kept and counted.
CREATE OR REFRESH MATERIALIZED VIEW events_clean (
  CONSTRAINT valid_duration EXPECT (duration_ms >= 0) ON VIOLATION DROP ROW,
  CONSTRAINT known_customer EXPECT (customer_id IS NOT NULL)
)
COMMENT 'Typed events with invalid rows removed'
AS SELECT
  CAST(event_ts AS TIMESTAMP) AS event_ts,
  customer_id,
  event_type,
  page,
  duration_ms
FROM events_bronze;
