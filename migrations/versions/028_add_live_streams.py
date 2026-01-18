"""add_live_streams

Revision ID: 028
Revises: 027
Create Date: 2025-01-18

Adds live streaming via HTTP segment push:
- live_streams: Stream configuration and status
- live_stream_segments: Segment tracking for DVR and VOD

Implements GitHub issue #XXX (Live Streaming).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "028"
down_revision: Union[str, Sequence[str], None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create live_streams and live_stream_segments tables."""
    # Create live_streams table
    op.create_table(
        "live_streams",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("description", sa.Text, default="", nullable=False),
        # Auth (argon2id hashed like worker API keys)
        sa.Column("stream_key_hash", sa.Text, nullable=False),
        sa.Column("stream_key_prefix", sa.String(8), nullable=False),
        sa.Column("hash_version", sa.Integer, default=2, nullable=False),
        # Status
        sa.Column(
            "status",
            sa.String(20),
            sa.CheckConstraint(
                "status IN ('idle', 'live', 'ending', 'ended')",
                name="ck_live_streams_status",
            ),
            default="idle",
            nullable=False,
        ),
        sa.Column("qualities", sa.Text, nullable=True),  # JSON: ["720p", "480p"]
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_segment_at", sa.DateTime(timezone=True), nullable=True),
        # Metrics
        sa.Column("segment_count", sa.Integer, default=0, nullable=False),
        # DVR/VOD
        sa.Column("dvr_enabled", sa.Boolean, default=True, nullable=False),
        sa.Column("dvr_window_seconds", sa.Integer, default=7200, nullable=False),
        sa.Column("auto_record_vod", sa.Boolean, default=True, nullable=False),
        sa.Column(
            "vod_video_id",
            sa.Integer,
            sa.ForeignKey("videos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "category_id",
            sa.Integer,
            sa.ForeignKey("categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_live_streams_slug", "live_streams", ["slug"])
    op.create_index("ix_live_streams_status", "live_streams", ["status"])
    op.create_index("ix_live_streams_stream_key_prefix", "live_streams", ["stream_key_prefix"])
    op.create_index("ix_live_streams_created_at", "live_streams", ["created_at"])

    # Create live_stream_segments table
    op.create_table(
        "live_stream_segments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "stream_id",
            sa.Integer,
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quality", sa.String(10), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        # Unique constraint prevents duplicate segment uploads
        sa.UniqueConstraint("stream_id", "quality", "sequence_number", name="uq_live_segment_stream_quality_seq"),
    )
    # Critical indexes for performance (from Brendan's review)
    op.create_index(
        "ix_live_segments_stream_quality_seq",
        "live_stream_segments",
        ["stream_id", "quality", "sequence_number"],
    )
    op.create_index(
        "ix_live_segments_received_at",
        "live_stream_segments",
        ["received_at"],
    )
    op.create_index(
        "ix_live_segments_cleanup",
        "live_stream_segments",
        ["stream_id", "received_at"],
    )


def downgrade() -> None:
    """Remove live_streams and live_stream_segments tables."""
    # Drop live_stream_segments indexes and table
    op.drop_index("ix_live_segments_cleanup", table_name="live_stream_segments")
    op.drop_index("ix_live_segments_received_at", table_name="live_stream_segments")
    op.drop_index("ix_live_segments_stream_quality_seq", table_name="live_stream_segments")
    op.drop_table("live_stream_segments")

    # Drop live_streams indexes and table
    op.drop_index("ix_live_streams_created_at", table_name="live_streams")
    op.drop_index("ix_live_streams_stream_key_prefix", table_name="live_streams")
    op.drop_index("ix_live_streams_status", table_name="live_streams")
    op.drop_index("ix_live_streams_slug", table_name="live_streams")
    op.drop_table("live_streams")
