# 0004. Write conflicts: use DuckLake's built-in commit retry

- Status: accepted
- Date: 2026-10-25

## Context

SPEC.md section 5: use DuckLake's commit-retry settings if they exist; otherwise retry in the
executor up to 3 times.

## Findings

DuckLake in DuckDB 2.0.0 has built-in retry settings:

| Setting | Default |
|---|---|
| `ducklake_max_retry_count` | 10 |
| `ducklake_retry_wait_ms` | 100 |
| `ducklake_retry_backoff` | 1.5 |

Spike A ran two processes concurrently: both created tables in the same schema at the same moment,
appended 100 commits each to their own table, then appended to one shared table concurrently. All
commits succeeded with no errors surfaced to the caller.

## Decision

Rely on the built-in retry with its defaults. No executor-level retry loop. When a commit still
fails, the task fails with DuckDB's error, which names the table.

## Consequences

If real jobs show conflicts, tune the three settings in `open_session` rather than adding retries
elsewhere.
