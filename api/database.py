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
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_videos_status"
        ),
        default="pending"
    ),  # pending, processing, ready, failed
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),  # Soft-delete timestamp (NULL = not deleted)
    # Thumbnail metadata for custom thumbnail selection
    sa.Column(
        "thumbnail_source",
        sa.String(20),
        sa.CheckConstraint(
            "thumbnail_source IN ('auto', 'selected', 'custom')",
            name="ck_videos_thumbnail_source"
        ),
        default="auto"
    ),  # auto, selected, custom
    sa.Column("thumbnail_timestamp", sa.Float, nullable=True),  # timestamp for selected thumbnails
    # Streaming format columns (added in migration 013)
    sa.Column(
        "streaming_format",
        sa.String(10),
        sa.CheckConstraint(
            "streaming_format IN ('hls_ts', 'cmaf')",
            name="ck_videos_streaming_format"
        ),
        default="hls_ts"
    ),  # hls_ts (legacy MPEG-TS) or cmaf (modern fMP4)
    sa.Column(
        "primary_codec",
        sa.String(10),
        sa.CheckConstraint(
            "primary_codec IN ('h264', 'hevc', 'av1')",
            name="ck_videos_primary_codec"
        ),
        default="h264"
    ),  # Video codec used
    sa.Index("ix_videos_status", "status"),
    sa.Index("ix_videos_category_id", "category_id"),
    sa.Index("ix_videos_created_at", "created_at"),
    sa.Index("ix_videos_published_at", "published_at"),
    sa.Index("ix_videos_deleted_at", "deleted_at"),
    sa.Index("ix_videos_streaming_format", "streaming_format"),
)

# Available quality variants for each video
video_qualities = sa.Table(
    "video_qualities",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE")),
    sa.Column(
        "quality",
        sa.String(10),
        sa.CheckConstraint(
            "quality IN ('2160p', '1440p', '1080p', '720p', '480p', '360p', 'original')",
            name="ck_video_qualities_quality"
        )
    ),  # 2160p, 1080p, etc.
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
    sa.Column(
        "quality_used",
        sa.String(10),
        sa.CheckConstraint(
            "quality_used IN ('2160p', '1440p', '1080p', '720p', '480p', '360p', 'original') OR quality_used IS NULL",
            name="ck_playback_sessions_quality_used"
        ),
        nullable=True
    ),  # primary quality
    sa.Column("completed", sa.Boolean, default=False),  # watched >= 90%
    sa.Index("ix_playback_sessions_video_id", "video_id"),
    sa.Index("ix_playback_sessions_viewer_id", "viewer_id"),
    sa.Index("ix_playback_sessions_started_at", "started_at"),
)

# Transcoding jobs with checkpoint support
#
# STATE SEMANTICS:
# ----------------
# Job States (derived from fields):
# - Unclaimed: claimed_at = NULL AND completed_at = NULL
#   → Job is available for any worker to claim
# - Claimed: claimed_at != NULL AND claim_expires_at > NOW() AND completed_at = NULL
#   → Worker actively processing, claim is valid
# - Expired: claimed_at != NULL AND claim_expires_at <= NOW() AND completed_at = NULL
#   → Worker failed to update, job ready for reclaim by stale checker
# - Completed: completed_at != NULL
#   → Transcoding finished successfully
# - Failed: last_error != NULL AND attempt_number >= max_attempts
#   → Permanently failed after all retry attempts
# - Retrying: last_error != NULL AND attempt_number < max_attempts AND claimed_at = NULL
#   → Failed but available for retry
#
# FIELD SEMANTICS:
# ----------------
# - video_id: Foreign key to videos table, unique (one job per video)
# - worker_id: UUID of worker processing this job (NULL = unclaimed)
# - claimed_at: Timestamp when worker claimed job (NULL = unclaimed)
# - claim_expires_at: When claim expires (NULL = no active claim)
#   → Extended by WORKER_CLAIM_DURATION_MINUTES on each progress update
#   → Typically 30 minutes from last update
# - started_at: First claim timestamp (persists across retries)
# - last_checkpoint: Last progress update timestamp
# - completed_at: Job completion timestamp (NULL = not complete)
# - attempt_number: Current retry attempt (1-based, default 1)
# - max_attempts: Maximum allowed attempts (default 3)
# - last_error: Error message from most recent failure (NULL = no error)
# - processed_by_worker_id/name: Permanent audit record of worker that processed job
#   → Set on first claim, persists even if job is reclaimed
#
# STATE TRANSITIONS:
# -----------------
# 1. Creation: Upload → unclaimed (all claim fields NULL)
# 2. Claim: Worker claims → set claimed_at, claim_expires_at, worker_id, started_at
# 3. Progress: Worker updates → extend claim_expires_at, update last_checkpoint
# 4. Complete: Worker finishes → set completed_at
# 5. Fail (retriable): Worker fails → clear claim fields, increment attempt_number
# 6. Fail (permanent): attempt_number >= max_attempts → keep claim data for audit
# 7. Expire: claim_expires_at passes → stale checker clears claim fields
#
# CONSTRAINTS & INDEXES:
# ---------------------
# - video_id: UNIQUE (one job per video)
# - claim_expires_at: INDEXED (for stale job detection)
#
# See docs/TRANSCODING_ARCHITECTURE.md for complete state machine documentation.
transcoding_jobs = sa.Table(
    "transcoding_jobs",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True),
    sa.Column("worker_id", sa.String(36), nullable=True),
    # Progress tracking
    sa.Column("current_step", sa.String(50), nullable=True),  # probe, thumbnail, transcode, master_playlist, finalize
    sa.Column(
        "progress_percent",
        sa.Integer,
        sa.CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="ck_transcoding_jobs_progress_percent_range"
        ),
        default=0
    ),
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
    # Retranscode metadata - JSON with cleanup info for deferred retranscode (Issue #408)
    # Format: {"retranscode_all": bool, "qualities_to_delete": [...], "delete_transcription": bool}
    sa.Column("retranscode_metadata", sa.Text, nullable=True),
    sa.Index("ix_transcoding_jobs_video_id", "video_id"),
    sa.Index("ix_transcoding_jobs_claim_expires", "claim_expires_at"),
)

# Per-quality progress tracking
quality_progress = sa.Table(
    "quality_progress",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("job_id", sa.Integer, sa.ForeignKey("transcoding_jobs.id", ondelete="CASCADE"), nullable=False),
    sa.Column(
        "quality",
        sa.String(10),
        sa.CheckConstraint(
            "quality IN ('2160p', '1440p', '1080p', '720p', '480p', '360p', 'original')",
            name="ck_quality_progress_quality"
        ),
        nullable=False
    ),  # 2160p, 1080p, etc.
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'uploading', 'completed', 'failed', 'skipped', 'uploaded')",
            name="ck_quality_progress_status"
        ),
        nullable=False,
        default="pending"
    ),  # pending, in_progress, uploading, completed, failed, skipped, uploaded
    sa.Column("segments_total", sa.Integer, nullable=True),
    sa.Column("segments_completed", sa.Integer, default=0),
    sa.Column(
        "progress_percent",
        sa.Integer,
        sa.CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="ck_quality_progress_percent_range"
        ),
        default=0
    ),
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
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_transcriptions_status"
        ),
        nullable=False,
        default="pending"
    ),  # pending, processing, completed, failed
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
#
# WORKER STATES:
# --------------
# - active: Recently heartbeated, available for work
# - idle: Active but not currently processing (used for GPU priority)
# - busy: Currently processing a job
# - offline: No recent heartbeat (threshold: WORKER_OFFLINE_THRESHOLD_MINUTES, default 5)
# - disabled: Manually disabled by admin (permanent)
#
# FIELD SEMANTICS:
# ----------------
# - worker_id: UUID for this worker (unique across all workers)
# - worker_name: Human-readable name (optional, auto-generated if not provided)
# - worker_type: "local" (inotify-based) or "remote" (containerized)
# - registered_at: When worker was first registered
# - last_heartbeat: Last heartbeat timestamp (NULL = never heartbeated)
#   → Workers send heartbeats every WORKER_HEARTBEAT_INTERVAL seconds (default 30)
#   → NULL indicates worker registered but never became active
# - status: Current worker state (see states above)
#   → Set by worker via heartbeat endpoint
#   → Set to "offline" by stale job checker when last_heartbeat is stale
# - current_job_id: Job currently being processed (NULL = idle/offline)
#   → Set when worker claims a job
#   → Cleared when job completes/fails or worker goes offline
# - capabilities: JSON metadata about worker capabilities
#   → hwaccel_enabled: Whether GPU acceleration is available
#   → hwaccel_type: "nvidia", "intel", etc.
#   → encoders: List of available encoders (h264_nvenc, etc.)
#   → Max size: 10KB
# - metadata: JSON metadata (Kubernetes pod info, etc.)
#   → pod_name, namespace, node_name, etc.
#   → Max size: 10KB
#
# STATE TRANSITIONS:
# -----------------
# 1. Registration: POST /api/worker/register → active (with initial heartbeat)
# 2. Heartbeat: POST /api/worker/heartbeat → idle or busy (based on request)
# 3. Claim Job: Worker claims → status = busy, current_job_id set
# 4. Complete Job: Worker completes → status = idle, current_job_id cleared
# 5. Fail Job: Worker fails → status = idle, current_job_id cleared
# 6. Go Offline: No heartbeat for threshold → status = offline, current_job_id cleared
# 7. Recover: Heartbeat after offline → status = idle or busy (based on request)
# 8. Disable: Admin disables → status = disabled (permanent)
#
# OFFLINE DETECTION:
# -----------------
# Background task check_stale_jobs() runs every STALE_JOB_CHECK_INTERVAL seconds (default 60).
# Workers marked offline if:
# - last_heartbeat < NOW() - WORKER_OFFLINE_THRESHOLD_MINUTES, OR
# - last_heartbeat IS NULL AND registered_at < NOW() - WORKER_OFFLINE_THRESHOLD_MINUTES
# Atomic conditional update prevents race with concurrent heartbeat.
#
# CONSTRAINTS & INDEXES:
# ---------------------
# - worker_id: UNIQUE, INDEXED (for lookups)
# - last_heartbeat: INDEXED (for stale detection queries)
# - status: INDEXED (for finding available workers)
#
# See docs/TRANSCODING_ARCHITECTURE.md for complete state machine documentation.
workers = sa.Table(
    "workers",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("worker_id", sa.String(36), unique=True, nullable=False),  # UUID
    sa.Column("worker_name", sa.String(100), nullable=True),
    sa.Column(
        "worker_type",
        sa.String(20),
        sa.CheckConstraint(
            "worker_type IN ('local', 'remote')",
            name="ck_workers_worker_type"
        ),
        default="remote"
    ),  # 'local' or 'remote'
    sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('active', 'idle', 'busy', 'offline', 'disabled')",
            name="ck_workers_status"
        ),
        default="active"
    ),  # 'active', 'idle', 'busy', 'offline', 'disabled'
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
#
# KEY LIFECYCLE:
# -------------
# 1. Generation: POST /api/worker/register → generates 256-bit API key
#    → Key shown once at registration, never retrievable again
#    → Stored as SHA-256 hash for security
# 2. Usage: Worker includes key in X-API-Key header
#    → Fast lookup via key_prefix (first 8 chars)
#    → Full hash verification for security
# 3. Expiration: Optional expires_at timestamp (NULL = never expires)
# 4. Revocation: Admin can revoke key via POST /api/workers/{id}/revoke
#    → Sets revoked_at timestamp
#    → Key immediately invalid for authentication
# 5. Tracking: last_used_at updated on each successful authentication
#
# FIELD SEMANTICS:
# ----------------
# - worker_id: Foreign key to workers table (CASCADE on delete)
# - key_hash: SHA-256 hash of the API key (64 hex chars)
#   → Never store plaintext keys
# - key_prefix: First 8 chars of API key (for fast lookup)
#   → Used to quickly find candidate keys before full hash verification
# - created_at: When key was generated
# - expires_at: Optional expiration timestamp (NULL = never expires)
# - revoked_at: When key was revoked (NULL = active)
# - last_used_at: Last successful authentication (NULL = never used)
#
# CONSTRAINTS & INDEXES:
# ---------------------
# - key_prefix: INDEXED (for fast lookup during authentication)
# - worker_id: INDEXED (for listing keys per worker)
#
# See api/worker_auth.py for authentication implementation.
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

# Deployment events for worker management (Issue #410)
deployment_events = sa.Table(
    "deployment_events",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("worker_id", sa.String(36), nullable=False),  # UUID of worker
    sa.Column("worker_name", sa.String(100), nullable=True),
    sa.Column(
        "event_type",
        sa.String(20),
        sa.CheckConstraint(
            "event_type IN ('restart', 'stop', 'update', 'deploy', 'rollback', 'version_change')",
            name="ck_deployment_events_type"
        ),
        nullable=False,
    ),  # Type of deployment event
    sa.Column("old_version", sa.String(64), nullable=True),  # Previous version
    sa.Column("new_version", sa.String(64), nullable=True),  # New version after event
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'failed')",
            name="ck_deployment_events_status"
        ),
        default="pending",
    ),  # Status of the deployment
    sa.Column("triggered_by", sa.String(100), nullable=True),  # Who triggered (user, system)
    sa.Column("details", sa.Text, nullable=True),  # JSON details (error message, etc.)
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_deployment_events_worker_id", "worker_id"),
    sa.Index("ix_deployment_events_created_at", "created_at"),
)

# Tags for granular content organization
tags = sa.Table(
    "tags",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(50), unique=True, nullable=False),
    sa.Column("slug", sa.String(50), unique=True, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Index("ix_tags_slug", "slug"),
)

# Many-to-many relationship between videos and tags
video_tags = sa.Table(
    "video_tags",
    metadata,
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
    sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id", ondelete="CASCADE"), nullable=False),
    sa.PrimaryKeyConstraint("video_id", "tag_id"),
    sa.Index("ix_video_tags_video_id", "video_id"),
    sa.Index("ix_video_tags_tag_id", "tag_id"),
)

# Custom field definitions for flexible video metadata
# Fields can be defined globally (category_id=NULL) or per-category
#
# FIELD TYPES:
# -----------
# - text: Free-form text input
# - number: Numeric value (integer or float)
# - date: Date value (stored as ISO 8601 string)
# - select: Single choice from options list
# - multi_select: Multiple choices from options list (stored as JSON array)
# - url: URL value with validation
#
# FIELD SEMANTICS:
# ----------------
# - name: Display name shown in UI
# - slug: URL-safe identifier for API queries (unique within category scope)
# - field_type: One of the types above (immutable after creation)
# - options: JSON array of strings for select/multi_select fields
# - required: Whether field must have a value when editing videos
# - category_id: NULL for global fields, category ID for category-specific
# - position: Display order (lower = first)
# - constraints: JSON object with validation rules (min, max, pattern, etc.)
# - description: Help text shown in UI
#
# See: https://github.com/filthyrake/vlog/issues/224
custom_field_definitions = sa.Table(
    "custom_field_definitions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(100), nullable=False),
    sa.Column("slug", sa.String(100), nullable=False),
    sa.Column(
        "field_type",
        sa.String(20),
        sa.CheckConstraint(
            "field_type IN ('text', 'number', 'date', 'select', 'multi_select', 'url')",
            name="ck_custom_field_definitions_field_type"
        ),
        nullable=False
    ),
    sa.Column("options", sa.Text, nullable=True),  # JSON array for select/multi_select
    sa.Column("required", sa.Boolean, default=False, nullable=False),
    sa.Column(
        "category_id",
        sa.Integer,
        sa.ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=True
    ),  # NULL = global field
    sa.Column("position", sa.Integer, default=0, nullable=False),
    sa.Column("constraints", sa.Text, nullable=True),  # JSON validation rules
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.UniqueConstraint("slug", "category_id", name="uq_custom_field_slug_category"),
    sa.Index("ix_custom_field_definitions_category_id", "category_id"),
    sa.Index("ix_custom_field_definitions_position", "position"),
)

# Custom field values for each video
# Stores the actual values that users enter for each custom field on a video
#
# FIELD SEMANTICS:
# ----------------
# - video_id: The video this value belongs to
# - field_id: The custom field definition this value is for
# - value: JSON-encoded value (supports all types including arrays for multi_select)
#
# CASCADE DELETE:
# - When a video is deleted, all its custom field values are deleted
# - When a field definition is deleted, all values for that field are deleted
video_custom_fields = sa.Table(
    "video_custom_fields",
    metadata,
    sa.Column(
        "video_id",
        sa.Integer,
        sa.ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False
    ),
    sa.Column(
        "field_id",
        sa.Integer,
        sa.ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
        nullable=False
    ),
    sa.Column("value", sa.Text, nullable=True),  # JSON-encoded value
    sa.PrimaryKeyConstraint("video_id", "field_id"),
    sa.Index("ix_video_custom_fields_video_id", "video_id"),
    sa.Index("ix_video_custom_fields_field_id", "field_id"),
)

# Admin sessions for secure HTTP-only cookie-based authentication
# Fixes XSS vulnerability where admin secret was stored in sessionStorage
# See: https://github.com/filthyrake/vlog/issues/324
admin_sessions = sa.Table(
    "admin_sessions",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    # 128 chars provides safety margin for 64-char tokens from secrets.token_urlsafe(48)
    sa.Column("session_token", sa.String(128), unique=True, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("ip_address", sa.String(45), nullable=True),  # IPv6 max length
    sa.Column("user_agent", sa.String(512), nullable=True),
    sa.Index("ix_admin_sessions_session_token", "session_token"),
    sa.Index("ix_admin_sessions_expires_at", "expires_at"),
)

# Runtime configuration settings (database-backed, manageable via Admin UI)
# Replaces 100+ environment variables with a single database table.
# Settings are cached in memory with TTL and fall back to env vars for migration.
#
# FIELD SEMANTICS:
# ----------------
# - key: Unique identifier in dot notation (e.g., "transcoding.hls_segment_duration")
# - value: JSON-encoded value (supports all types: string, number, boolean, array, object)
# - category: For UI grouping (e.g., "transcoding", "watermark", "workers")
# - description: Help text shown in Admin UI
# - value_type: One of: string, integer, float, boolean, enum, json
# - constraints: JSON object with validation rules (min, max, enum_values, pattern)
# - updated_at: Last modification timestamp
# - updated_by: Who made the change (for audit trail)
#
# CATEGORIES:
# -----------
# - transcoding: Quality presets, HLS settings, FFmpeg timeouts, hardware acceleration
# - watermark: Client-side watermark overlay settings
# - workers: Heartbeat intervals, claim duration, retry settings
# - storage: Cleanup policies, archive settings
# - rate_limiting: Request limits per endpoint type
# - analytics: Cache TTL, session timeout, tracking settings
# - alerts: Webhook URL, rate limiting, enabled events
# - transcription: Model, language, compute type settings
# - security: Cookie settings, CORS (non-secret)
# - ui: Theme, branding settings
#
# See: https://github.com/filthyrake/vlog/issues/400
settings = sa.Table(
    "settings",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("key", sa.String(255), unique=True, nullable=False),
    sa.Column("value", sa.Text, nullable=False),  # JSON-encoded
    sa.Column("category", sa.String(100), nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column(
        "value_type",
        sa.String(50),
        sa.CheckConstraint(
            "value_type IN ('string', 'integer', 'float', 'boolean', 'enum', 'json')",
            name="ck_settings_value_type"
        ),
        default="string"
    ),
    sa.Column("constraints", sa.Text, nullable=True),  # JSON-encoded
    sa.Column("updated_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("updated_by", sa.String(255), nullable=True),
    sa.Index("ix_settings_key", "key"),
    sa.Index("ix_settings_category", "category"),
)

# Re-encode queue for background conversion to CMAF format
reencode_queue = sa.Table(
    "reencode_queue",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column(
        "video_id",
        sa.Integer,
        sa.ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "target_format",
        sa.String(20),
        sa.CheckConstraint(
            "target_format IN ('hls_ts', 'cmaf')",
            name="ck_reencode_queue_target_format"
        ),
        default="cmaf",
    ),
    sa.Column(
        "target_codec",
        sa.String(10),
        sa.CheckConstraint(
            "target_codec IN ('h264', 'hevc', 'av1')",
            name="ck_reencode_queue_target_codec"
        ),
        default="hevc",
    ),
    sa.Column(
        "priority",
        sa.String(10),
        sa.CheckConstraint(
            "priority IN ('high', 'normal', 'low')",
            name="ck_reencode_queue_priority"
        ),
        default="normal",
    ),
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'failed', 'cancelled')",
            name="ck_reencode_queue_status"
        ),
        default="pending",
    ),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    ),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column("retry_count", sa.Integer, default=0),
    sa.Column("processed_by_worker_id", sa.Integer, nullable=True),
    sa.Index("ix_reencode_queue_status", "status"),
    sa.Index("ix_reencode_queue_video_id", "video_id"),
    sa.Index("ix_reencode_queue_priority_created", "priority", "created_at"),
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
