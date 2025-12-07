from datetime import datetime, timezone

import sqlalchemy as sa
from databases import Database

from config import DATABASE_URL

# Create database instance - works with PostgreSQL or SQLite
# PostgreSQL is the default and recommended database
database = Database(DATABASE_URL)
metadata = sa.MetaData()


async def configure_database():
    """
    Configure database-specific settings after connection.
    For PostgreSQL, this is a no-op since FK constraints are always enforced.
    """
    # PostgreSQL enforces foreign keys by default - no configuration needed
    pass


categories = sa.Table(
    "categories",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(100), nullable=False),
    sa.Column("slug", sa.String(100), unique=True, nullable=False),
    sa.Column("description", sa.Text, default=""),
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
)

videos = sa.Table(
    "videos",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("slug", sa.String(255), unique=True, nullable=False),
    sa.Column("description", sa.Text, default=""),
    sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=True),
    sa.Column("duration", sa.Float, default=0),  # seconds
    sa.Column("source_width", sa.Integer, default=0),
    sa.Column("source_height", sa.Integer, default=0),
    sa.Column("status", sa.String(20), default="pending"),  # pending, processing, ready, failed
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),  # Soft-delete timestamp (NULL = not deleted)
    sa.Index("ix_videos_status", "status"),
    sa.Index("ix_videos_category_id", "category_id"),
    sa.Index("ix_videos_created_at", "created_at"),
    sa.Index("ix_videos_published_at", "published_at"),
    sa.Index("ix_videos_deleted_at", "deleted_at"),
)

# Available quality variants for each video
video_qualities = sa.Table(
    "video_qualities",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE")),
    sa.Column("quality", sa.String(10)),  # 2160p, 1080p, etc.
    sa.Column("width", sa.Integer),
    sa.Column("height", sa.Integer),
    sa.Column("bitrate", sa.Integer),  # kbps
    sa.Index("ix_video_qualities_video_id", "video_id"),
)

# Analytics: unique viewers (cookie-based)
viewers = sa.Table(
    "viewers",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("session_id", sa.String(64), unique=True, nullable=False),
    sa.Column("first_seen", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("last_seen", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
)

# Analytics: playback sessions
playback_sessions = sa.Table(
    "playback_sessions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
    sa.Column("viewer_id", sa.Integer, sa.ForeignKey("viewers.id", ondelete="SET NULL"), nullable=True),
    sa.Column("session_token", sa.String(64), unique=True, nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("duration_watched", sa.Float, default=0),  # seconds actually watched
    sa.Column("max_position", sa.Float, default=0),  # furthest point reached
    sa.Column("quality_used", sa.String(10), nullable=True),  # primary quality
    sa.Column("completed", sa.Boolean, default=False),  # watched >= 90%
    sa.Index("ix_playback_sessions_video_id", "video_id"),
    sa.Index("ix_playback_sessions_viewer_id", "viewer_id"),
    sa.Index("ix_playback_sessions_started_at", "started_at"),
)

# Transcoding jobs with checkpoint support
transcoding_jobs = sa.Table(
    "transcoding_jobs",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True),
    sa.Column("worker_id", sa.String(36), nullable=True),
    # Progress tracking
    sa.Column("current_step", sa.String(50), nullable=True),  # probe, thumbnail, transcode, master_playlist, finalize
    sa.Column("progress_percent", sa.Integer, default=0),
    # Timing
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_checkpoint", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    # Job claiming for distributed workers
    sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    # Retry tracking
    sa.Column("attempt_number", sa.Integer, default=1),
    sa.Column("max_attempts", sa.Integer, default=3),
    # Error tracking
    sa.Column("last_error", sa.Text, nullable=True),
    # Permanent record of which worker processed this job (for audit/debugging)
    sa.Column("processed_by_worker_id", sa.String(36), nullable=True),
    sa.Column("processed_by_worker_name", sa.String(100), nullable=True),
    sa.Index("ix_transcoding_jobs_video_id", "video_id"),
    sa.Index("ix_transcoding_jobs_claim_expires", "claim_expires_at"),
)

# Per-quality progress tracking
quality_progress = sa.Table(
    "quality_progress",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("job_id", sa.Integer, sa.ForeignKey("transcoding_jobs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("quality", sa.String(10), nullable=False),  # 2160p, 1080p, etc.
    sa.Column(
        "status", sa.String(20), nullable=False, default="pending"
    ),  # pending, in_progress, completed, failed, skipped
    sa.Column("segments_total", sa.Integer, nullable=True),
    sa.Column("segments_completed", sa.Integer, default=0),
    sa.Column("progress_percent", sa.Integer, default=0),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Index("ix_quality_progress_job_id", "job_id"),
    sa.UniqueConstraint("job_id", "quality", name="uq_job_quality"),
)

# Transcription tracking
transcriptions = sa.Table(
    "transcriptions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True),
    # Status tracking
    sa.Column("status", sa.String(20), nullable=False, default="pending"),  # pending, processing, completed, failed
    sa.Column("language", sa.String(10), default="en"),  # detected or specified language
    # Timing
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("duration_seconds", sa.Float, nullable=True),  # how long transcription took
    # Output
    sa.Column("transcript_text", sa.Text, nullable=True),  # full transcript as plain text
    sa.Column("vtt_path", sa.String(255), nullable=True),  # path to WebVTT file
    # Metadata
    sa.Column("word_count", sa.Integer, nullable=True),
    # Error tracking
    sa.Column("error_message", sa.Text, nullable=True),
)

# Worker registration for distributed transcoding
workers = sa.Table(
    "workers",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("worker_id", sa.String(36), unique=True, nullable=False),  # UUID
    sa.Column("worker_name", sa.String(100), nullable=True),
    sa.Column("worker_type", sa.String(20), default="remote"),  # 'local' or 'remote'
    sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
    sa.Column("status", sa.String(20), default="active"),  # 'active', 'offline', 'disabled'
    sa.Column(
        "current_job_id",
        sa.Integer,
        sa.ForeignKey("transcoding_jobs.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("capabilities", sa.Text, nullable=True),  # JSON
    sa.Column("metadata", sa.Text, nullable=True),  # JSON (k8s pod info, etc.)
    sa.Index("ix_workers_status", "status"),
    sa.Index("ix_workers_last_heartbeat", "last_heartbeat"),
    sa.Index("ix_workers_worker_id", "worker_id"),
)

# Worker API keys for authentication
worker_api_keys = sa.Table(
    "worker_api_keys",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("worker_id", sa.Integer, sa.ForeignKey("workers.id", ondelete="CASCADE"), nullable=False),
    sa.Column("key_hash", sa.String(64), nullable=False),  # SHA-256 hash
    sa.Column("key_prefix", sa.String(8), nullable=False),  # First 8 chars for lookup
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_worker_api_keys_key_prefix", "key_prefix"),
    sa.Index("ix_worker_api_keys_worker_id", "worker_id"),
)


def create_tables():
    """
    Create database tables directly using SQLAlchemy metadata.
    This creates all tables if they don't exist.
    """
    engine = sa.create_engine(DATABASE_URL)
    metadata.create_all(engine)
    engine.dispose()


if __name__ == "__main__":
    create_tables()
    print("Database tables created successfully!")
