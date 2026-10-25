"""Platform tables: jobs, runs, statements, warehouses, bundles, pipelines, lineage, workspace

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25 08:21:55.563793
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Job runs and task runs share one id space, as in Databricks.
    op.execute(sa.schema.CreateSequence(sa.Sequence("run_id_seq", start=1000)))
    op.create_table(
        "bundle_deployments",
        sa.Column("bundle_name", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "deployed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("bundle_name", "target"),
    )
    op.create_table(
        "bundle_resources",
        sa.Column("bundle_name", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column(
            "deployed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("bundle_name", "target", "resource_type", "resource_key"),
    )
    op.create_table(
        "column_lineage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_table_full_name", sa.Text(), nullable=False),
        sa.Column("source_column_name", sa.Text(), nullable=False),
        sa.Column("target_table_full_name", sa.Text(), nullable=False),
        sa.Column("target_column_name", sa.Text(), nullable=False),
        sa.Column("evolution", sa.Text(), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("entity_run_id", sa.Text(), nullable=True),
        sa.Column(
            "event_time",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_column_lineage_target",
        "column_lineage",
        ["target_table_full_name", "target_column_name"],
        unique=False,
    )
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.BigInteger(), sa.Identity(always=False, start=100), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("creator_user_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_table(
        "pipeline_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("pipeline_id", sa.Text(), nullable=False),
        sa.Column("update_id", sa.Text(), nullable=True),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_events", "pipeline_events", ["pipeline_id", "id"], unique=False)
    op.create_table(
        "pipeline_files",
        sa.Column("pipeline_id", sa.Text(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("pipeline_id", "table_name", "file_path"),
    )
    op.create_table(
        "pipelines",
        sa.Column("pipeline_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("creator_user_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("pipeline_id"),
    )
    op.create_table(
        "statements",
        sa.Column("statement_id", sa.Text(), nullable=False),
        sa.Column("statement_text", sa.Text(), nullable=False),
        sa.Column("warehouse_id", sa.Text(), nullable=True),
        sa.Column("catalog", sa.Text(), nullable=True),
        sa.Column("schema", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), server_default="API", nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column(
            "start_time",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("statement_id"),
    )
    op.create_index("ix_statements_start", "statements", ["start_time"], unique=False)
    op.create_table(
        "table_lineage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source_table_full_name", sa.Text(), nullable=False),
        sa.Column("target_table_full_name", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("entity_run_id", sa.Text(), nullable=True),
        sa.Column(
            "event_time",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_table_lineage_source", "table_lineage", ["source_table_full_name"], unique=False
    )
    op.create_index(
        "ix_table_lineage_target", "table_lineage", ["target_table_full_name"], unique=False
    )
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("cluster_size", sa.Text(), nullable=False),
        sa.Column("auto_stop_mins", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("creator_user_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "workers",
        sa.Column("worker_id", sa.Text(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=False),
        sa.Column("slots", sa.Integer(), nullable=False),
        sa.Column("dbt_version", sa.Text(), nullable=True),
        sa.Column("dbt_distribution", sa.Text(), nullable=True),
        sa.Column("dbt_duckdb_version", sa.Text(), nullable=True),
        sa.Column("duckdb_version", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_table(
        "workspace_favorites",
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("path"),
    )
    op.create_table(
        "workspace_trash",
        sa.Column("trash_id", sa.Text(), nullable=False),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("trash_id"),
    )
    op.create_table(
        "job_runs",
        sa.Column(
            "run_id",
            sa.BigInteger(),
            server_default=sa.text("nextval('run_id_seq')"),
            nullable=False,
        ),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("run_name", sa.Text(), nullable=False),
        sa.Column("run_type", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "job_parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("life_cycle_state", sa.Text(), nullable=False),
        sa.Column("result_state", sa.Text(), nullable=True),
        sa.Column("state_message", sa.Text(), server_default="", nullable=False),
        sa.Column("parent_run_id", sa.BigInteger(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "start_time",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_job_runs_job_id", "job_runs", ["job_id", "run_id"], unique=False)
    op.create_table(
        "job_triggers",
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "state", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_table(
        "pipeline_updates",
        sa.Column("update_id", sa.Text(), nullable=False),
        sa.Column("pipeline_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("cause", sa.Text(), nullable=False),
        sa.Column("full_refresh", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "creation_time",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["pipeline_id"], ["pipelines.pipeline_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("update_id"),
    )
    op.create_table(
        "task_runs",
        sa.Column(
            "run_id",
            sa.BigInteger(),
            server_default=sa.text("nextval('run_id_seq')"),
            nullable=False,
        ),
        sa.Column("job_run_id", sa.BigInteger(), nullable=False),
        sa.Column("task_key", sa.Text(), nullable=False),
        sa.Column("task", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempt_number", sa.Integer(), server_default="0", nullable=False),
        sa.Column("life_cycle_state", sa.Text(), nullable=False),
        sa.Column("result_state", sa.Text(), nullable=True),
        sa.Column("state_message", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "not_before",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_trace", sa.Text(), nullable=True),
        sa.Column("log_path", sa.Text(), nullable=True),
        sa.Column(
            "output", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.ForeignKeyConstraint(["job_run_id"], ["job_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_task_runs_job_run", "task_runs", ["job_run_id", "task_key"], unique=False)
    op.create_index(
        "ix_task_runs_queue", "task_runs", ["life_cycle_state", "not_before"], unique=False
    )
    op.create_table(
        "task_values",
        sa.Column("job_run_id", sa.BigInteger(), nullable=False),
        sa.Column("task_key", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["job_run_id"], ["job_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_run_id", "task_key", "key"),
    )


def downgrade() -> None:
    op.drop_table("task_values")
    op.drop_index("ix_task_runs_queue", table_name="task_runs")
    op.drop_index("ix_task_runs_job_run", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_table("pipeline_updates")
    op.drop_table("job_triggers")
    op.drop_index("ix_job_runs_job_id", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_table("workspace_trash")
    op.drop_table("workspace_favorites")
    op.drop_table("workers")
    op.drop_table("warehouses")
    op.drop_index("ix_table_lineage_target", table_name="table_lineage")
    op.drop_index("ix_table_lineage_source", table_name="table_lineage")
    op.drop_table("table_lineage")
    op.drop_index("ix_statements_start", table_name="statements")
    op.drop_table("statements")
    op.drop_table("pipelines")
    op.drop_table("pipeline_files")
    op.drop_index("ix_pipeline_events", table_name="pipeline_events")
    op.drop_table("pipeline_events")
    op.drop_table("jobs")
    op.drop_index("ix_column_lineage_target", table_name="column_lineage")
    op.drop_table("column_lineage")
    op.drop_table("bundle_resources")
    op.drop_table("bundle_deployments")
    op.execute(sa.schema.DropSequence(sa.Sequence("run_id_seq")))
