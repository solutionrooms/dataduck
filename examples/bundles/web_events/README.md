# web_events

A SQL pipeline over the web events the seed script writes to `s3://landing/shop/events/`:

- `events_bronze`: streaming table, incremental by file (`read_files` + `_metadata.file_path`).
- `events_clean`: materialized view with two expectations (`DROP ROW` and warn-only).
- `daily_funnel`: materialized view per day.

The job `web_events_refresh` runs the pipeline whenever new files arrive under the prefix
(paused in dev; run it with `dbk bundle run web_events_refresh`).
