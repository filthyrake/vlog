"""add_stream_analytics

Revision ID: 036
Revises: 035
Create Date: 2026-01-31

Adds analytics tables for live streaming:
- stream_viewer_counts: Historical viewer count snapshots
- stream_analytics_summary: Aggregated stream analytics

Implements GitHub issue #530 (Studio Phase 2D).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "036"
down_revision: Union[str, Sequence[str], None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add analytics tables."""

    # ==========================================================================
    # stream_viewer_counts table
    # ==========================================================================
    # Records viewer count snapshots during live streams (typically every minute)
    op.create_table(
        "stream_viewer_counts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "stream_id",
            sa.Integer,
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("viewer_count", sa.Integer, nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Index for querying viewer counts by stream and time
    op.create_index(
        "ix_stream_viewer_counts_stream_time",
        "stream_viewer_counts",
        ["stream_id", "recorded_at"],
    )

    # ==========================================================================
    # stream_analytics_summary table
    # ==========================================================================
    # Pre-computed analytics summaries for ended streams
    # Updated via background task when stream ends or on-demand
    op.create_table(
        "stream_analytics_summary",
        sa.Column(
            "stream_id",
            sa.Integer,
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Viewer metrics
        sa.Column("peak_viewers", sa.Integer, server_default="0", nullable=False),
        sa.Column("average_viewers", sa.Float, server_default="0", nullable=False),
        sa.Column("total_unique_viewers", sa.Integer, server_default="0", nullable=False),
        # Chat metrics
        sa.Column("total_chat_messages", sa.Integer, server_default="0", nullable=False),
        # Watch time metrics
        sa.Column("total_watch_minutes", sa.Float, server_default="0", nullable=False),
        sa.Column("average_watch_time_seconds", sa.Float, server_default="0", nullable=False),
        # Stream duration
        sa.Column("stream_duration_seconds", sa.Integer, server_default="0", nullable=False),
        # When analytics were last computed
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove analytics tables."""

    # Drop stream_analytics_summary
    op.drop_table("stream_analytics_summary")

    # Drop stream_viewer_counts
    op.drop_index("ix_stream_viewer_counts_stream_time", table_name="stream_viewer_counts")
    op.drop_table("stream_viewer_counts")
