"""add_reencode_queue

Revision ID: 014
Revises: 013
Create Date: 2025-12-25

Adds reencode_queue table for tracking background re-encoding jobs.
This enables migrating existing HLS/TS videos to CMAF format with
priority queue support and status tracking.

See: https://github.com/filthyrake/vlog/issues/212
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "014"
down_revision: Union[str, Sequence[str], None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create reencode_queue table for background re-encoding jobs."""
    op.create_table(
        "reencode_queue",
        sa.Column("id", sa.Integer, primary_key=True),
        # Video to re-encode
        sa.Column(
            "video_id",
            sa.Integer,
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Target streaming format
        sa.Column(
            "target_format",
            sa.String(20),
            nullable=False,
            server_default="cmaf",
        ),
        # Target codec
        sa.Column(
            "target_codec",
            sa.String(10),
            nullable=False,
            server_default="hevc",
        ),
        # Priority for queue ordering
        sa.Column(
            "priority",
            sa.String(10),
            nullable=False,
            server_default="normal",
        ),
        # Job status
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # Error tracking
        sa.Column("error_message", sa.Text, nullable=True),
        # Retry tracking
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        # Worker that processed this job
        sa.Column("processed_by_worker_id", sa.Integer, nullable=True),
        # Check constraints
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'failed', 'cancelled')",
            name="ck_reencode_queue_status",
        ),
        sa.CheckConstraint(
            "target_format IN ('hls_ts', 'cmaf')",
            name="ck_reencode_queue_target_format",
        ),
        sa.CheckConstraint(
            "target_codec IN ('h264', 'hevc', 'av1')",
            name="ck_reencode_queue_target_codec",
        ),
        sa.CheckConstraint(
            "priority IN ('high', 'normal', 'low')",
            name="ck_reencode_queue_priority",
        ),
    )

    # Indexes for efficient queue processing
    op.create_index("ix_reencode_queue_status", "reencode_queue", ["status"])
    op.create_index("ix_reencode_queue_video_id", "reencode_queue", ["video_id"])
    op.create_index(
        "ix_reencode_queue_priority_created",
        "reencode_queue",
        ["priority", "created_at"],
    )


def downgrade() -> None:
    """Drop reencode_queue table."""
    op.drop_index("ix_reencode_queue_priority_created", table_name="reencode_queue")
    op.drop_index("ix_reencode_queue_video_id", table_name="reencode_queue")
    op.drop_index("ix_reencode_queue_status", table_name="reencode_queue")
    op.drop_table("reencode_queue")
