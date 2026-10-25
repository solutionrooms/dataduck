"""Control database: table definitions (SQLAlchemy Core) and the engine.

Only the api and worker processes use this module. Schema changes go through Alembic.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Sequence,
    Table,
    Text,
    create_engine,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()


def _created_at() -> Column[Any]:
    return Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())


catalogs = Table(
    "catalogs",
    metadata,
    Column("name", Text, primary_key=True),
    Column("comment", Text),
    Column("metadata_schema", Text, nullable=False, unique=True),
    Column("data_path", Text, nullable=False),
    _created_at(),
)

# Job runs and task runs share one id space, as in Databricks.
run_id_seq = Sequence("run_id_seq", start=1000, metadata=metadata)

jobs = Table(
    "jobs",
    metadata,
    Column("job_id", BigInteger, Identity(start=100), primary_key=True),
    Column("name", Text, nullable=False),
    Column("settings", JSONB, nullable=False),
    Column("creator_user_name", Text, nullable=False),
    _created_at(),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

job_runs = Table(
    "job_runs",
    metadata,
    Column(
        "run_id", BigInteger, run_id_seq, primary_key=True, server_default=run_id_seq.next_value()
    ),
    Column("job_id", BigInteger, ForeignKey("jobs.job_id", ondelete="SET NULL")),
    Column("run_name", Text, nullable=False),
    Column("run_type", Text, nullable=False),  # JOB_RUN, SUBMIT_RUN, WORKFLOW_RUN
    Column("trigger", Text, nullable=False),  # ONE_TIME, PERIODIC, RUN_JOB_TASK, ...
    Column("settings", JSONB, nullable=False),  # job settings snapshot at trigger time
    Column("job_parameters", JSONB, nullable=False, server_default="{}"),
    Column("life_cycle_state", Text, nullable=False),
    Column("result_state", Text),
    Column("state_message", Text, nullable=False, server_default=""),
    Column("parent_run_id", BigInteger),
    Column("cancel_requested", Boolean, nullable=False, server_default="false"),
    Column("start_time", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("end_time", DateTime(timezone=True)),
    Index("ix_job_runs_job_id", "job_id", "run_id"),
)

task_runs = Table(
    "task_runs",
    metadata,
    Column(
        "run_id", BigInteger, run_id_seq, primary_key=True, server_default=run_id_seq.next_value()
    ),
    Column(
        "job_run_id", BigInteger, ForeignKey("job_runs.run_id", ondelete="CASCADE"), nullable=False
    ),
    Column("task_key", Text, nullable=False),
    Column("task", JSONB, nullable=False),  # task settings with dynamic values unresolved
    Column("attempt_number", Integer, nullable=False, server_default="0"),
    Column("life_cycle_state", Text, nullable=False),
    Column("result_state", Text),
    Column("state_message", Text, nullable=False, server_default=""),
    Column("queued_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("not_before", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("worker_id", Text),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("start_time", DateTime(timezone=True)),
    Column("end_time", DateTime(timezone=True)),
    Column("cancel_requested", Boolean, nullable=False, server_default="false"),
    Column("error", Text),
    Column("error_trace", Text),
    Column("log_path", Text),
    Column("output", JSONB, nullable=False, server_default="{}"),
    Index("ix_task_runs_job_run", "job_run_id", "task_key"),
    Index("ix_task_runs_queue", "life_cycle_state", "not_before"),
)

task_values = Table(
    "task_values",
    metadata,
    Column(
        "job_run_id",
        BigInteger,
        ForeignKey("job_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("task_key", Text, primary_key=True),
    Column("key", Text, primary_key=True),
    Column("value", JSONB, nullable=False),
)

job_triggers = Table(
    "job_triggers",
    metadata,
    Column("job_id", BigInteger, ForeignKey("jobs.job_id", ondelete="CASCADE"), primary_key=True),
    Column("last_fired_at", DateTime(timezone=True)),
    Column("state", JSONB, nullable=False, server_default="{}"),  # e.g. files seen
)

statements = Table(
    "statements",
    metadata,
    Column("statement_id", Text, primary_key=True),
    Column("statement_text", Text, nullable=False),
    Column("warehouse_id", Text),
    Column("catalog", Text),
    Column("schema", Text),
    Column("source", Text, nullable=False, server_default="API"),
    Column("status", Text, nullable=False),
    Column("error", Text),
    Column("row_count", BigInteger),
    Column("start_time", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("end_time", DateTime(timezone=True)),
    Index("ix_statements_start", "start_time"),
)

warehouses = Table(
    "warehouses",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("cluster_size", Text, nullable=False),
    Column("auto_stop_mins", Integer, nullable=False),
    Column("state", Text, nullable=False),
    Column("port", Integer),
    Column("last_activity_at", DateTime(timezone=True)),
    Column("settings", JSONB, nullable=False, server_default="{}"),
    Column("creator_user_name", Text, nullable=False),
    _created_at(),
)

bundle_deployments = Table(
    "bundle_deployments",
    metadata,
    Column("bundle_name", Text, primary_key=True),
    Column("target", Text, primary_key=True),
    Column("root_path", Text, nullable=False),
    Column("mode", Text),
    Column("config", JSONB, nullable=False),
    Column("deployed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

bundle_resources = Table(
    "bundle_resources",
    metadata,
    Column("bundle_name", Text, primary_key=True),
    Column("target", Text, primary_key=True),
    Column("resource_type", Text, primary_key=True),  # jobs, schemas, pipelines
    Column("resource_key", Text, primary_key=True),
    Column("resource_id", Text, nullable=False),
    Column("config_hash", Text, nullable=False),
    Column("deployed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

pipelines = Table(
    "pipelines",
    metadata,
    Column("pipeline_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("spec", JSONB, nullable=False),
    Column("creator_user_name", Text, nullable=False),
    _created_at(),
)

pipeline_updates = Table(
    "pipeline_updates",
    metadata,
    Column("update_id", Text, primary_key=True),
    Column(
        "pipeline_id",
        Text,
        ForeignKey("pipelines.pipeline_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("state", Text, nullable=False),  # QUEUED, RUNNING, COMPLETED, FAILED, CANCELED
    Column("cause", Text, nullable=False),
    Column("full_refresh", Boolean, nullable=False, server_default="false"),
    Column("task_run_id", BigInteger),
    Column("creation_time", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("end_time", DateTime(timezone=True)),
    Column("message", Text),
)

pipeline_events = Table(
    "pipeline_events",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("pipeline_id", Text, nullable=False),
    Column("update_id", Text),
    Column("timestamp", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("level", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("details", JSONB, nullable=False, server_default="{}"),
    Index("ix_pipeline_events", "pipeline_id", "id"),
)

pipeline_files = Table(
    "pipeline_files",
    metadata,
    Column("pipeline_id", Text, primary_key=True),
    Column("table_name", Text, primary_key=True),
    Column("file_path", Text, primary_key=True),
    Column("loaded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

table_lineage = Table(
    "table_lineage",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("source_table_full_name", Text, nullable=False),
    Column("target_table_full_name", Text, nullable=False),
    Column("source_type", Text, nullable=False),  # dbt, pipeline
    Column("entity_id", Text),  # job id or pipeline id
    Column("entity_run_id", Text),
    Column("event_time", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_table_lineage_target", "target_table_full_name"),
    Index("ix_table_lineage_source", "source_table_full_name"),
)

column_lineage = Table(
    "column_lineage",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("source_table_full_name", Text, nullable=False),
    Column("source_column_name", Text, nullable=False),
    Column("target_table_full_name", Text, nullable=False),
    Column("target_column_name", Text, nullable=False),
    Column("evolution", Text),  # dbt: copy, mod, scan, ...
    Column("source_type", Text, nullable=False),
    Column("entity_id", Text),
    Column("entity_run_id", Text),
    Column("event_time", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_column_lineage_target", "target_table_full_name", "target_column_name"),
)

workspace_favorites = Table(
    "workspace_favorites",
    metadata,
    Column("path", Text, primary_key=True),
    _created_at(),
)

workspace_trash = Table(
    "workspace_trash",
    metadata,
    Column("trash_id", Text, primary_key=True),
    Column("original_path", Text, nullable=False),
    Column("object_type", Text, nullable=False),
    Column("deleted_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

workers = Table(
    "workers",
    metadata,
    Column("worker_id", Text, primary_key=True),
    Column("hostname", Text, nullable=False),
    Column("slots", Integer, nullable=False),
    Column("dbt_version", Text),
    Column("dbt_distribution", Text),
    Column("dbt_duckdb_version", Text),
    Column("duckdb_version", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("heartbeat_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


@lru_cache
def get_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)
