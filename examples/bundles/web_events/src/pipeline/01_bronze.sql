-- Streaming table: each update reads only files it has not seen before.
CREATE OR REFRESH STREAMING TABLE events_bronze
COMMENT 'Raw web events, appended as files arrive'
AS SELECT
  *,
  _metadata.file_path AS source_file
FROM STREAM read_files('s3://landing/shop/events/', format => 'json');
