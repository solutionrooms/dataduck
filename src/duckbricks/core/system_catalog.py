"""The `sys` catalog: Databricks system tables as read-only views over the control database.

DuckDB reserves the name `system`, so the catalog is `sys` (ADR 0007). The control database is
attached read-only through DuckDB's postgres extension under an internal alias, and `sys` is an
in-memory database holding views over it.
"""

from __future__ import annotations

import duckdb

from duckbricks.core.config import Settings
from duckbricks.core.session import PG_SECRET, sql_literal

SYSTEM_CATALOG = "sys"
CONTROL_ALIAS = "__dbk_control"

_C = f"{CONTROL_ALIAS}.public"

VIEWS: dict[str, dict[str, str]] = {
    "jobs": {
        "jobs": f"""
            SELECT job_id, name, creator_user_name, created_at, updated_at,
                   CAST(settings AS VARCHAR) AS settings
            FROM {_C}.jobs""",
        "job_runs": f"""
            SELECT run_id, job_id, run_name, run_type, trigger, life_cycle_state,
                   result_state, state_message, start_time, end_time,
                   date_diff('millisecond', start_time, coalesce(end_time, now()))
                       AS duration_ms,
                   CAST(job_parameters AS VARCHAR) AS job_parameters, parent_run_id
            FROM {_C}.job_runs""",
        "task_runs": f"""
            SELECT t.run_id, t.job_run_id, r.job_id, t.task_key, t.attempt_number,
                   t.life_cycle_state, t.result_state, t.state_message, t.error,
                   t.start_time, t.end_time, t.worker_id,
                   date_diff('millisecond', t.start_time, coalesce(t.end_time, now()))
                       AS duration_ms
            FROM {_C}.task_runs t JOIN {_C}.job_runs r ON r.run_id = t.job_run_id""",
    },
    "lineage": {
        "table_lineage": f"""
            SELECT source_table_full_name, target_table_full_name, source_type,
                   entity_id, entity_run_id, event_time
            FROM {_C}.table_lineage""",
        "column_lineage": f"""
            SELECT source_table_full_name, source_column_name, target_table_full_name,
                   target_column_name, evolution, source_type, entity_id, entity_run_id,
                   event_time
            FROM {_C}.column_lineage""",
    },
    "query": {
        "history": f"""
            SELECT statement_id, statement_text, warehouse_id, catalog, schema, source,
                   status, error, row_count, start_time, end_time,
                   date_diff('millisecond', start_time, end_time) AS duration_ms
            FROM {_C}.statements""",
    },
    "compute": {
        "warehouses": f"""
            SELECT id AS warehouse_id, name, cluster_size, auto_stop_mins, state,
                   last_activity_at, created_at
            FROM {_C}.warehouses""",
        "workers": f"""
            SELECT worker_id, hostname, slots, duckdb_version, dbt_version,
                   dbt_distribution, dbt_duckdb_version, started_at, heartbeat_at
            FROM {_C}.workers""",
    },
    "pipelines": {
        "pipelines": f"SELECT pipeline_id, name, creator_user_name, created_at FROM {_C}.pipelines",
        "updates": f"""
            SELECT update_id, pipeline_id, state, cause, full_refresh, creation_time, end_time,
                   message
            FROM {_C}.pipeline_updates""",
        "events": f"""
            SELECT id, pipeline_id, update_id, timestamp, level, event_type, message,
                   CAST(details AS VARCHAR) AS details
            FROM {_C}.pipeline_events""",
    },
}


def attach_system_catalog(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    con.execute(
        f"ATTACH IF NOT EXISTS {sql_literal('dbname=' + settings.control_db)} AS {CONTROL_ALIAS} "
        f"(TYPE postgres, READ_ONLY, SECRET {PG_SECRET})"
    )
    con.execute(f"ATTACH IF NOT EXISTS ':memory:' AS {SYSTEM_CATALOG}")
    for schema, views in VIEWS.items():
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {SYSTEM_CATALOG}.{schema}")
        for name, query in views.items():
            con.execute(f"CREATE OR REPLACE VIEW {SYSTEM_CATALOG}.{schema}.{name} AS {query}")
