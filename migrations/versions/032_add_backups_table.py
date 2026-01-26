"""add_backups_table

Revision ID: 032
Revises: 031
Create Date: 2026-01-26

Adds backup and restore system for VLog:
- backups: Backup metadata and status tracking

Implements GitHub issue #216.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "032"
down_revision: Union[str, Sequence[str], None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create backups table."""
    op.create_table(
        "backups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("backup_id", sa.String(50), unique=True, nullable=False),
        sa.Column(
            "backup_type",
            sa.String(20),
            sa.CheckConstraint(
                "backup_type IN ('full', 'database_only', 'incremental')",
                name="ck_backups_backup_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(30),
            sa.CheckConstraint(
                "status IN ('pending', 'backing_up_database', 'backing_up_files', "
                "'uploading_s3', 'completed', 'failed')",
                name="ck_backups_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        # Size and content statistics
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("database_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("files_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("video_count", sa.Integer, nullable=True),
        sa.Column("file_count", sa.Integer, nullable=True),
        # Description
        sa.Column("description", sa.Text, nullable=True),
        # Storage locations
        sa.Column("local_path", sa.String(500), nullable=True),
        sa.Column("s3_location", sa.String(500), nullable=True),
        # Manifest
        sa.Column("manifest_json", sa.Text, nullable=True),
        sa.Column("manifest_signature", sa.String(64), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # Provenance
        sa.Column("created_by", sa.String(100), nullable=True),
        # Error tracking
        sa.Column("error_message", sa.Text, nullable=True),
        # Version info
        sa.Column("vlog_version", sa.String(50), nullable=True),
        sa.Column("schema_version", sa.String(10), nullable=True),
        sa.Column("database_type", sa.String(20), nullable=True),
    )
    op.create_index("ix_backups_backup_id", "backups", ["backup_id"])
    op.create_index("ix_backups_status", "backups", ["status"])
    op.create_index("ix_backups_created_at", "backups", ["created_at"])
    op.create_index("ix_backups_backup_type", "backups", ["backup_type"])


def downgrade() -> None:
    """Remove backups table."""
    op.drop_index("ix_backups_backup_type", table_name="backups")
    op.drop_index("ix_backups_created_at", table_name="backups")
    op.drop_index("ix_backups_status", table_name="backups")
    op.drop_index("ix_backups_backup_id", table_name="backups")
    op.drop_table("backups")
