"""Federation: connections, foreign catalogs and the Databricks mirror (ADR 0013)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25 11:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("connection_type", sa.Text(), nullable=False),
        sa.Column(
            "options", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )
    op.add_column(
        "catalogs",
        sa.Column("catalog_type", sa.Text(), server_default="MANAGED_CATALOG", nullable=False),
    )
    op.add_column("catalogs", sa.Column("connection_name", sa.Text(), nullable=True))
    op.add_column(
        "catalogs",
        sa.Column(
            "options", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )
    op.create_foreign_key(
        "fk_catalogs_connection", "catalogs", "connections", ["connection_name"], ["name"]
    )
    op.create_table(
        "mirrors",
        sa.Column("catalog", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["catalog"], ["catalogs.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("catalog"),
    )
    op.create_table(
        "mirror_tables",
        sa.Column("catalog", sa.Text(), nullable=False),
        sa.Column("schema_name", sa.Text(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("table_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("remote_version", sa.BigInteger(), nullable=True),
        sa.Column("filter", sa.Text(), nullable=True),
        sa.Column("rows", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["catalog"], ["catalogs.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("catalog", "schema_name", "table_name"),
    )
    op.create_table(
        "mirror_syncs",
        sa.Column("sync_id", sa.BigInteger(), sa.Identity(start=1), nullable=False),
        sa.Column("catalog", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column(
            "request", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("copied", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["catalog"], ["catalogs.name"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sync_id"),
    )
    op.create_index("ix_mirror_syncs_state", "mirror_syncs", ["state", "queued_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_mirror_syncs_state", table_name="mirror_syncs")
    op.drop_table("mirror_syncs")
    op.drop_table("mirror_tables")
    op.drop_table("mirrors")
    op.drop_constraint("fk_catalogs_connection", "catalogs", type_="foreignkey")
    op.drop_column("catalogs", "options")
    op.drop_column("catalogs", "connection_name")
    op.drop_column("catalogs", "catalog_type")
    op.drop_table("connections")
