"""add_live_stream_viewers

Revision ID: 035
Revises: 034
Create Date: 2026-01-29

Adds live_stream_viewers table for tracking active viewers.
Also adds viewer count columns to live_streams table.

Implements GitHub issue #524 (Broadcaster Dashboard - Phase 2).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "035"
down_revision: Union[str, Sequence[str], None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create live_stream_viewers table and add viewer count columns to live_streams."""
    # Add viewer count columns to live_streams
    op.add_column(
        "live_streams",
        sa.Column("viewer_count_current", sa.Integer, default=0, nullable=False, server_default="0"),
    )
    op.add_column(
        "live_streams",
        sa.Column("viewer_count_peak", sa.Integer, default=0, nullable=False, server_default="0"),
    )
    op.add_column(
        "live_streams",
        sa.Column("viewer_count_total", sa.Integer, default=0, nullable=False, server_default="0"),
    )

    # Create live_stream_viewers table
    op.create_table(
        "live_stream_viewers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "stream_id",
            sa.Integer,
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Server-generated session ID (cryptographically random, 256 bits)
        sa.Column("session_id", sa.String(64), nullable=False),
        # Optional user ID for logged-in viewers
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Timestamps
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        # Viewing metadata
        sa.Column("quality_watched", sa.String(10), nullable=True),
        # Salted IP hash for unique viewer tracking (privacy-preserving)
        sa.Column("ip_hash", sa.String(128), nullable=True),
        # Unique constraint on stream + session
        sa.UniqueConstraint("stream_id", "session_id", name="uq_live_viewers_stream_session"),
    )

    # Index for finding active viewers by stream and heartbeat
    op.create_index(
        "ix_live_viewers_stream_heartbeat",
        "live_stream_viewers",
        ["stream_id", "last_heartbeat"],
    )

    # Index for stale viewer cleanup
    op.create_index(
        "ix_live_viewers_cleanup",
        "live_stream_viewers",
        ["last_heartbeat"],
    )

    # Index for user lookups
    op.create_index(
        "ix_live_viewers_user_id",
        "live_stream_viewers",
        ["user_id"],
    )


def downgrade() -> None:
    """Remove live_stream_viewers table and viewer count columns from live_streams."""
    # Drop indexes and table
    op.drop_index("ix_live_viewers_user_id", table_name="live_stream_viewers")
    op.drop_index("ix_live_viewers_cleanup", table_name="live_stream_viewers")
    op.drop_index("ix_live_viewers_stream_heartbeat", table_name="live_stream_viewers")
    op.drop_table("live_stream_viewers")

    # Remove viewer count columns from live_streams
    op.drop_column("live_streams", "viewer_count_total")
    op.drop_column("live_streams", "viewer_count_peak")
    op.drop_column("live_streams", "viewer_count_current")
