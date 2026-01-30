"""add_live_stream_metrics

Revision ID: 034
Revises: 033
Create Date: 2026-01-29

Adds live_stream_metrics table for storing aggregated stream health metrics.
Also adds health-related columns to live_streams table.

Implements GitHub issue #524 (Broadcaster Dashboard - Phase 1).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "034"
down_revision: Union[str, Sequence[str], None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create live_stream_metrics table and add health columns to live_streams."""
    # Add health-related columns to live_streams
    op.add_column(
        "live_streams",
        sa.Column("current_bitrate", sa.Integer, nullable=True),
    )
    op.add_column(
        "live_streams",
        sa.Column(
            "connection_health",
            sa.String(20),
            sa.CheckConstraint(
                "connection_health IN ('good', 'degraded', 'poor', 'unknown')",
                name="ck_live_streams_connection_health",
            ),
            default="unknown",
            nullable=True,
        ),
    )
    op.add_column(
        "live_streams",
        sa.Column("last_metric_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create live_stream_metrics table
    op.create_table(
        "live_stream_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "stream_id",
            sa.Integer,
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        # Bitrate metrics (bytes per second)
        sa.Column("bitrate_video", sa.Integer, nullable=True),
        sa.Column("bitrate_audio", sa.Integer, nullable=True),
        sa.Column("bitrate_total", sa.Integer, nullable=True),
        # Latency and reliability
        sa.Column("segment_push_latency_ms", sa.Integer, nullable=True),
        sa.Column("segments_received", sa.Integer, default=0, nullable=False),
        sa.Column("segments_dropped", sa.Integer, default=0, nullable=False),
        # Aggregation window (default 10 seconds)
        sa.Column("interval_seconds", sa.Integer, default=10, nullable=False),
    )

    # Primary index for querying metrics by stream and time (descending for recent-first)
    op.create_index(
        "ix_live_metrics_stream_ts_desc",
        "live_stream_metrics",
        ["stream_id", sa.text("timestamp DESC")],
    )

    # Index for cleanup task (find old metrics efficiently)
    op.create_index(
        "ix_live_metrics_timestamp",
        "live_stream_metrics",
        ["timestamp"],
    )


def downgrade() -> None:
    """Remove live_stream_metrics table and health columns from live_streams."""
    # Drop indexes and table
    op.drop_index("ix_live_metrics_timestamp", table_name="live_stream_metrics")
    op.drop_index("ix_live_metrics_stream_ts_desc", table_name="live_stream_metrics")
    op.drop_table("live_stream_metrics")

    # Remove health columns from live_streams
    op.drop_column("live_streams", "last_metric_at")
    # Drop check constraint before dropping column
    op.drop_constraint("ck_live_streams_connection_health", "live_streams", type_="check")
    op.drop_column("live_streams", "connection_health")
    op.drop_column("live_streams", "current_bitrate")
