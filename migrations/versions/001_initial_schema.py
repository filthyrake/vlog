"""initial_schema

Revision ID: 001
Revises:
Create Date: 2025-12-04

This migration captures the initial database schema for VLog.
For existing databases, use 'alembic stamp 001' to mark as current.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables for VLog database."""
    # Categories table
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("created_at", sa.DateTime),
    )

    # Videos table
    op.create_table(
        "videos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("duration", sa.Float, server_default="0"),
        sa.Column("source_width", sa.Integer, server_default="0"),
        sa.Column("source_height", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("published_at", sa.DateTime, nullable=True),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_videos_status", "videos", ["status"])
    op.create_index("ix_videos_category_id", "videos", ["category_id"])
    op.create_index("ix_videos_created_at", "videos", ["created_at"])
    op.create_index("ix_videos_published_at", "videos", ["published_at"])
    op.create_index("ix_videos_deleted_at", "videos", ["deleted_at"])

    # Video qualities table
    op.create_table(
        "video_qualities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE")),
        sa.Column("quality", sa.String(10)),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("bitrate", sa.Integer),
    )

    # Viewers table (analytics)
    op.create_table(
        "viewers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.String(64), unique=True, nullable=False),
        sa.Column("first_seen", sa.DateTime),
        sa.Column("last_seen", sa.DateTime),
    )

    # Playback sessions table (analytics)
    op.create_table(
        "playback_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("viewer_id", sa.Integer, sa.ForeignKey("viewers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("session_token", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("ended_at", sa.DateTime, nullable=True),
        sa.Column("duration_watched", sa.Float, server_default="0"),
        sa.Column("max_position", sa.Float, server_default="0"),
        sa.Column("quality_used", sa.String(10), nullable=True),
        sa.Column("completed", sa.Boolean, server_default="0"),
    )
    op.create_index("ix_playback_sessions_video_id", "playback_sessions", ["video_id"])
    op.create_index("ix_playback_sessions_started_at", "playback_sessions", ["started_at"])
    op.create_index("ix_playback_sessions_session_token", "playback_sessions", ["session_token"])

    # Transcoding jobs table
    op.create_table(
        "transcoding_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("worker_id", sa.String(36), nullable=True),
        sa.Column("current_step", sa.String(50), nullable=True),
        sa.Column("progress_percent", sa.Integer, server_default="0"),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("last_checkpoint", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("attempt_number", sa.Integer, server_default="1"),
        sa.Column("max_attempts", sa.Integer, server_default="3"),
        sa.Column("last_error", sa.Text, nullable=True),
    )

    # Quality progress table
    op.create_table(
        "quality_progress",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("transcoding_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quality", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("segments_total", sa.Integer, nullable=True),
        sa.Column("segments_completed", sa.Integer, server_default="0"),
        sa.Column("progress_percent", sa.Integer, server_default="0"),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.UniqueConstraint("job_id", "quality", name="uq_job_quality"),
    )

    # Transcriptions table
    op.create_table(
        "transcriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("language", sa.String(10), server_default="en"),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("transcript_text", sa.Text, nullable=True),
        sa.Column("vtt_path", sa.String(255), nullable=True),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("transcriptions")
    op.drop_table("quality_progress")
    op.drop_table("transcoding_jobs")
    op.drop_table("playback_sessions")
    op.drop_table("viewers")
    op.drop_table("video_qualities")
    op.drop_table("videos")
    op.drop_table("categories")
