"""Pipeline SQL parsing: dataset kinds, expectations, references and read_files rewriting."""

from __future__ import annotations

import pytest

from duckbricks.pipelines.parser import (
    PipelineSqlError,
    duckdb_reader,
    find_read_files,
    parse_statement,
    references,
    rewrite_metadata_columns,
    split_statements,
    strip_stream,
)

SOURCE = """
-- bronze; a ';' in a comment
CREATE OR REFRESH STREAMING TABLE events_bronze
COMMENT 'Raw events; don''t touch'
AS SELECT *, _metadata.file_path AS source_file
FROM STREAM read_files('s3://landing/events/', format => 'json', inferColumnTypes => 'true');

CREATE OR REFRESH MATERIALIZED VIEW events_clean (
  CONSTRAINT valid_duration EXPECT (duration_ms >= 0) ON VIOLATION DROP ROW,
  CONSTRAINT has_ts EXPECT (event_ts IS NOT NULL) ON VIOLATION FAIL UPDATE,
  CONSTRAINT known_customer EXPECT (customer_id IS NOT NULL)
) AS SELECT * FROM LIVE.events_bronze;

CREATE TEMPORARY LIVE VIEW recent AS
SELECT * FROM events_clean WHERE event_ts > now() - INTERVAL 1 DAY;
"""


def test_split_ignores_semicolons_in_comments_and_strings() -> None:
    statements = split_statements(SOURCE)
    assert len(statements) == 3


def test_streaming_table() -> None:
    dataset = parse_statement(split_statements(SOURCE)[0], "01.sql")
    assert dataset is not None
    assert dataset.kind == "STREAMING_TABLE"
    assert dataset.name == "events_bronze"
    assert dataset.comment == "Raw events; don't touch"
    assert dataset.query.lstrip().upper().startswith("SELECT")


def test_expectations_and_live_prefix() -> None:
    dataset = parse_statement(split_statements(SOURCE)[1])
    assert dataset is not None
    assert dataset.kind == "MATERIALIZED_VIEW"
    assert [(e.name, e.action) for e in dataset.expectations] == [
        ("valid_duration", "DROP"),
        ("has_ts", "FAIL"),
        ("known_customer", "WARN"),
    ]
    assert dataset.expectations[0].expr == "duration_ms >= 0"
    assert "LIVE." not in dataset.query
    assert references(dataset.query) == {"events_bronze"}


def test_temporary_view() -> None:
    dataset = parse_statement(split_statements(SOURCE)[2])
    assert dataset is not None
    assert dataset.kind == "VIEW"
    assert dataset.temporary


def test_non_dataset_statement_is_skipped_or_rejected() -> None:
    assert parse_statement("SET pipelines.trigger.interval = '1 hour'") is None


def test_read_files_becomes_a_duckdb_reader() -> None:
    query = parse_statement(split_statements(SOURCE)[0]).query  # type: ignore[union-attr]
    (rf,) = find_read_files(query)
    assert rf.path == "s3://landing/events/"
    assert rf.format == "json"
    assert rf.stream
    reader, warnings = duckdb_reader(rf, ["s3://landing/events/a.json", "s3://x/it's.json"])
    assert reader.startswith("read_json_auto(['s3://landing/events/a.json', 's3://x/it''s.json']")
    assert "filename = true" in reader
    assert warnings == []
    rewritten = query[: rf.start] + reader + query[rf.end :]
    assert "STREAM" not in rewritten.upper().split("FROM", 1)[1].split("(", 1)[0]


def test_csv_options_and_unknown_option_warning() -> None:
    (rf,) = find_read_files(
        "SELECT * FROM read_files('s3://b/x/', format => 'csv', header => 'true', "
        "sep => ';', mode => 'FAILFAST')"
    )
    reader, warnings = duckdb_reader(rf, ["s3://b/x/1.csv"])
    assert "header = true" in reader and "delim = ';'" in reader
    assert warnings == ["read_files option mode ignored"]


def test_unsupported_format() -> None:
    (rf,) = find_read_files("SELECT * FROM read_files('s3://b/x/', format => 'avro')")
    with pytest.raises(PipelineSqlError):
        duckdb_reader(rf, ["s3://b/x/1.avro"])


def test_metadata_columns_and_stream_tables() -> None:
    assert rewrite_metadata_columns("SELECT _metadata.file_path AS f") == "SELECT filename AS f"
    assert "STREAM" not in strip_stream("SELECT * FROM STREAM(events_bronze)").upper()
    assert "STREAM" not in strip_stream("SELECT * FROM STREAM events_bronze").upper()
