import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from databases import Database

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Create database instance - works with PostgreSQL or SQLite
# PostgreSQL is the default and recommended database
# Connection pool limits prevent exhausting PostgreSQL's max_connections (Issue #429)
database = Database(DATABASE_URL, min_size=5, max_size=20)
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
        sa.CheckConstraint("status IN ('pending', 'processing', 'ready', 'failed')", name="ck_videos_status"),
        default="pending",
    ),  # pending, processing, ready, failed
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),  # Soft-delete timestamp (NULL = not deleted)
    # Thumbnail metadata for custom thumbnail selection
    sa.Column(
        "thumbnail_source",
        sa.String(20),
        sa.CheckConstraint("thumbnail_source IN ('auto', 'selected', 'custom')", name="ck_videos_thumbnail_source"),
        default="auto",
    ),  # auto, selected, custom
    sa.Column("thumbnail_timestamp", sa.Float, nullable=True),  # timestamp for selected thumbnails
    # Streaming format columns (added in migration 013)
    sa.Column(
        "streaming_format",
        sa.String(10),
        sa.CheckConstraint("streaming_format IN ('hls_ts', 'cmaf')", name="ck_videos_streaming_format"),
        default="hls_ts",
    ),  # hls_ts (legacy MPEG-TS) or cmaf (modern fMP4)
    sa.Column(
        "primary_codec",
        sa.String(10),
        sa.CheckConstraint("primary_codec IN ('h264', 'hevc', 'av1')", name="ck_videos_primary_codec"),
        default="h264",
    ),  # Video codec used
    # Featured video columns (Issue #413 Phase 3)
    sa.Column("is_featured", sa.Boolean, default=False),  # Admin-curated featured flag
    sa.Column("featured_at", sa.DateTime(timezone=True), nullable=True),  # When marked featured
    # Chapter optimization (Issue #413 Phase 7)
    sa.Column("has_chapters", sa.Boolean, default=False),  # Avoids chapter query for most videos
    # Sprite sheet columns (Issue #413 Phase 7B)
    sa.Column(
        "sprite_sheet_status",
        sa.String(20),
        sa.CheckConstraint(
            "sprite_sheet_status IS NULL OR sprite_sheet_status IN ('pending', 'generating', 'ready', 'failed')",
            name="ck_videos_sprite_sheet_status",
        ),
        nullable=True,
    ),  # pending, generating, ready, failed
    sa.Column("sprite_sheet_error", sa.Text, nullable=True),  # Error message if failed
    sa.Column("sprite_sheet_count", sa.Integer, nullable=True, default=0),  # Number of sprite sheets
    sa.Column("sprite_sheet_interval", sa.Integer, nullable=True),  # Seconds between frames
    sa.Column("sprite_sheet_tile_size", sa.Integer, nullable=True),  # Grid size (e.g., 10 for 10x10)
    sa.Column("sprite_sheet_frame_width", sa.Integer, nullable=True),  # Width of each frame
    sa.Column("sprite_sheet_frame_height", sa.Integer, nullable=True),  # Height of each frame
    # Video ownership for multi-user auth (Issue #200)
    # Nullable for backward compatibility - existing videos assigned to first admin during migration
    sa.Column(
        "owner_id",
        sa.String(36),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    # Comments and ratings settings (Issue #213)
    # NULL = inherit from global settings, true/false = per-video override
    sa.Column("comments_enabled", sa.Boolean, nullable=True),
    sa.Column("ratings_enabled", sa.Boolean, nullable=True),
    # Denormalized aggregates for comments/ratings (updated via triggers)
    sa.Column("comment_count", sa.Integer, default=0),
    sa.Column("rating_avg", sa.Numeric(3, 2), nullable=True),
    sa.Column("rating_count", sa.Integer, default=0),
    sa.Column("rating_distribution", sa.Text, default="{}"),  # JSON: {"1": 5, "2": 3, ...}
    sa.Column("likes_count", sa.Integer, default=0),  # For thumbs up/down mode
    sa.Column("dislikes_count", sa.Integer, default=0),  # For thumbs up/down mode
    sa.Index("ix_videos_status", "status"),
    sa.Index("ix_videos_category_id", "category_id"),
    sa.Index("ix_videos_created_at", "created_at"),
    sa.Index("ix_videos_published_at", "published_at"),
    sa.Index("ix_videos_deleted_at", "deleted_at"),
    sa.Index("ix_videos_streaming_format", "streaming_format"),
    sa.Index("ix_videos_sprite_sheet_status", "sprite_sheet_status"),
    sa.Index("ix_videos_owner_id", "owner_id"),
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
            name="ck_video_qualities_quality",
        ),
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
            name="ck_playback_sessions_quality_used",
        ),
        nullable=True,
    ),  # primary quality
    sa.Column("completed", sa.Boolean, default=False),  # watched >= 90%
    sa.Index("ix_playback_sessions_video_id", "video_id"),
    sa.Index("ix_playback_sessions_viewer_id", "viewer_id"),
    sa.Index("ix_playback_sessions_started_at", "started_at"),
)

# Transcoding jobs with checkpoint support
#
# Job state is derived from nullable field combinations. For explicit state
# determination and SQL condition generation, use the TranscodingJobStateMachine
# class in api/job_state.py.
#
# See also: docs/TRANSCODING_ARCHITECTURE.md
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
            "progress_percent >= 0 AND progress_percent <= 100", name="ck_transcoding_jobs_progress_percent_range"
        ),
        default=0,
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
            name="ck_quality_progress_quality",
        ),
        nullable=False,
    ),  # 2160p, 1080p, etc.
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'uploading', 'completed', 'failed', 'skipped', 'uploaded')",
            name="ck_quality_progress_status",
        ),
        nullable=False,
        default="pending",
    ),  # pending, in_progress, uploading, completed, failed, skipped, uploaded
    sa.Column("segments_total", sa.Integer, nullable=True),
    sa.Column("segments_completed", sa.Integer, default=0),
    sa.Column(
        "progress_percent",
        sa.Integer,
        sa.CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100", name="ck_quality_progress_percent_range"
        ),
        default=0,
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
            "status IN ('pending', 'processing', 'completed', 'failed')", name="ck_transcriptions_status"
        ),
        nullable=False,
        default="pending",
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
        sa.CheckConstraint("worker_type IN ('local', 'remote')", name="ck_workers_worker_type"),
        default="remote",
    ),  # 'local' or 'remote'
    sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint("status IN ('active', 'idle', 'busy', 'offline', 'disabled')", name="ck_workers_status"),
        default="active",
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
#    → Stored as argon2id hash for security (SHA-256 for legacy keys)
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
# - key_hash: Hash of the API key (format depends on hash_version)
#   → Never store plaintext keys
# - hash_version: Algorithm version (1=SHA-256 legacy, 2=argon2id)
#   → New keys use argon2id (version 2)
#   → Legacy keys use SHA-256 (version 1)
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
    sa.Column("key_hash", sa.String(255), nullable=False),  # argon2id (~100 chars) or SHA-256 (64 chars)
    sa.Column("hash_version", sa.Integer, nullable=False, server_default="2"),  # 1=SHA-256, 2=argon2id
    sa.Column("key_prefix", sa.String(8), nullable=False),  # First 8 chars for lookup
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    # Rotation tracking (Issue #226): self-referential FK to track key rotation chain
    # NULL = original key, otherwise points to the previous key that was rotated
    sa.Column(
        "rotated_from",
        sa.Integer,
        sa.ForeignKey("worker_api_keys.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Index("ix_worker_api_keys_key_prefix", "key_prefix"),
    sa.Index("ix_worker_api_keys_worker_id", "worker_id"),
    sa.Index("ix_worker_api_keys_rotated_from", "rotated_from"),
    sa.Index("ix_worker_api_keys_expires_at", "expires_at"),
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
            name="ck_deployment_events_type",
        ),
        nullable=False,
    ),  # Type of deployment event
    sa.Column("old_version", sa.String(64), nullable=True),  # Previous version
    sa.Column("new_version", sa.String(64), nullable=True),  # New version after event
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'failed')", name="ck_deployment_events_status"
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
            name="ck_custom_field_definitions_field_type",
        ),
        nullable=False,
    ),
    sa.Column("options", sa.Text, nullable=True),  # JSON array for select/multi_select
    sa.Column("required", sa.Boolean, default=False, nullable=False),
    sa.Column(
        "category_id", sa.Integer, sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=True
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
    sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
    sa.Column("field_id", sa.Integer, sa.ForeignKey("custom_field_definitions.id", ondelete="CASCADE"), nullable=False),
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
            "value_type IN ('string', 'integer', 'float', 'boolean', 'enum', 'json')", name="ck_settings_value_type"
        ),
        default="string",
    ),
    sa.Column("constraints", sa.Text, nullable=True),  # JSON-encoded
    sa.Column("updated_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("updated_by", sa.String(255), nullable=True),
    sa.Index("ix_settings_key", "key"),
    sa.Index("ix_settings_category", "category"),
)

# Playlists for organizing videos into ordered collections
# Supports playlists, collections, series, and courses
#
# VISIBILITY:
# -----------
# - public: Anyone can view
# - private: Only admin can view (future: owner)
# - unlisted: Viewable with direct link, not in listings
#
# PLAYLIST TYPES:
# ---------------
# - playlist: General purpose ordered list
# - collection: Curated featured content
# - series: Multi-part video series (episodes)
# - course: Educational content with ordered lessons
#
# See: https://github.com/filthyrake/vlog/issues/223
playlists = sa.Table(
    "playlists",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("slug", sa.String(255), unique=True, nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("thumbnail_path", sa.String(500), nullable=True),
    sa.Column(
        "visibility",
        sa.String(20),
        sa.CheckConstraint(
            "visibility IN ('public', 'private', 'unlisted')",
            name="ck_playlists_visibility",
        ),
        default="public",
    ),
    sa.Column(
        "playlist_type",
        sa.String(20),
        sa.CheckConstraint(
            "playlist_type IN ('playlist', 'collection', 'series', 'course')",
            name="ck_playlists_type",
        ),
        default="playlist",
    ),
    sa.Column("is_featured", sa.Boolean, default=False),
    sa.Column("user_id", sa.String(100), nullable=True),  # Future: user playlists
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),  # Soft delete
    sa.Index("ix_playlists_slug", "slug"),
    sa.Index("ix_playlists_visibility", "visibility"),
    sa.Index("ix_playlists_is_featured", "is_featured"),
    sa.Index("ix_playlists_deleted_at", "deleted_at"),
    sa.Index("ix_playlists_playlist_type", "playlist_type"),
)

# Many-to-many relationship between playlists and videos with ordering
playlist_items = sa.Table(
    "playlist_items",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column(
        "playlist_id",
        sa.Integer,
        sa.ForeignKey("playlists.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "video_id",
        sa.Integer,
        sa.ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("position", sa.Integer, default=0, nullable=False),
    sa.Column("added_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.UniqueConstraint("playlist_id", "video_id", name="uq_playlist_video"),
    sa.Index("ix_playlist_items_playlist_id", "playlist_id"),
    sa.Index("ix_playlist_items_video_id", "video_id"),
    sa.Index("ix_playlist_items_position", "position"),
    # Composite index for efficient ordered retrieval: WHERE playlist_id = ? ORDER BY position
    sa.Index("ix_playlist_items_playlist_position", "playlist_id", "position"),
)

# Video chapters for timeline navigation
# Chapters allow users to jump to specific sections of a video
# See: https://github.com/filthyrake/vlog/issues/413 Phase 7
chapters = sa.Table(
    "chapters",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column(
        "video_id",
        sa.Integer,
        sa.ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("start_time", sa.Float, nullable=False),  # seconds
    sa.Column("end_time", sa.Float, nullable=True),  # seconds (optional)
    sa.Column("position", sa.Integer, default=0, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    # Constraints per reviewer feedback
    sa.CheckConstraint("start_time >= 0", name="ck_chapters_start_time_positive"),
    sa.CheckConstraint("end_time IS NULL OR end_time > start_time", name="ck_chapters_end_time_valid"),
    sa.UniqueConstraint("video_id", "position", name="uq_chapter_video_position"),
    sa.Index("ix_chapters_video_id", "video_id"),
    sa.Index("ix_chapters_position", "position"),
    # Composite index for efficient ordered retrieval: WHERE video_id = ? ORDER BY position
    sa.Index("ix_chapters_video_position", "video_id", "position"),
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
        sa.CheckConstraint("target_format IN ('hls_ts', 'cmaf')", name="ck_reencode_queue_target_format"),
        default="cmaf",
    ),
    sa.Column(
        "target_codec",
        sa.String(10),
        sa.CheckConstraint("target_codec IN ('h264', 'hevc', 'av1')", name="ck_reencode_queue_target_codec"),
        default="hevc",
    ),
    sa.Column(
        "priority",
        sa.String(10),
        sa.CheckConstraint("priority IN ('high', 'normal', 'low')", name="ck_reencode_queue_priority"),
        default="normal",
    ),
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'failed', 'cancelled')", name="ck_reencode_queue_status"
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


# Sprite generation queue (Issue #413 Phase 7B)
# Background queue for generating sprite sheets for timeline thumbnails
sprite_queue = sa.Table(
    "sprite_queue",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column(
        "video_id",
        sa.Integer,
        sa.ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "priority",
        sa.String(10),
        sa.CheckConstraint("priority IN ('high', 'normal', 'low')", name="ck_sprite_queue_priority"),
        default="normal",
    ),
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')", name="ck_sprite_queue_status"
        ),
        default="pending",
    ),
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    ),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("processed_by_worker_id", sa.Integer, nullable=True),
    sa.Index("ix_sprite_queue_status", "status"),
    sa.Index("ix_sprite_queue_video_id", "video_id"),
    sa.Index("ix_sprite_queue_priority_created", "priority", "created_at"),
)


# Webhook subscriptions for external integrations (Issue #203)
# Allows external systems to receive notifications about VLog events
#
# SUPPORTED EVENTS:
# -----------------
# - video.uploaded: New video uploaded
# - video.ready: Transcoding completed
# - video.failed: Transcoding failed
# - video.deleted: Video deleted
# - video.restored: Video restored from archive
# - transcription.completed: Transcription finished
# - worker.registered: New worker registered
# - worker.offline: Worker went offline
#
# SECURITY:
# ---------
# - Payloads are signed with HMAC-SHA256 using the webhook's secret
# - Signature is sent in X-VLog-Signature header
# - Recommend verifying signature on receiving end
#
# See: https://github.com/filthyrake/vlog/issues/203
webhooks = sa.Table(
    "webhooks",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(100), nullable=False),  # Human-readable name
    sa.Column("url", sa.String(500), nullable=False),  # Webhook endpoint URL
    sa.Column("events", sa.Text, nullable=False),  # JSON array: ["video.ready", "video.failed"]
    sa.Column("secret", sa.String(64), nullable=True),  # HMAC-SHA256 signing key
    sa.Column("active", sa.Boolean, default=True),  # Can be disabled without deletion
    sa.Column("headers", sa.Text, nullable=True),  # JSON: custom headers to include
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
    # Statistics for monitoring
    sa.Column("total_deliveries", sa.Integer, default=0),
    sa.Column("successful_deliveries", sa.Integer, default=0),
    sa.Column("failed_deliveries", sa.Integer, default=0),
    sa.Index("ix_webhooks_active", "active"),
    sa.Index("ix_webhooks_created_at", "created_at"),
)

# Webhook delivery attempts and history (Issue #203)
# Tracks each delivery attempt with retry support
#
# STATUS LIFECYCLE:
# -----------------
# pending -> delivered (success)
# pending -> pending (retry scheduled)
# pending -> failed_permanent (max retries exceeded)
#
# RETRY STRATEGY:
# ---------------
# Exponential backoff: delay = base_delay * (backoff_multiplier ^ attempt_number)
# Default: 30s, 60s, 120s, 240s, 480s (5 attempts over ~15 minutes)
webhook_deliveries = sa.Table(
    "webhook_deliveries",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column(
        "webhook_id",
        sa.Integer,
        sa.ForeignKey("webhooks.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("event_type", sa.String(50), nullable=False),  # video.ready, etc.
    sa.Column("event_data", sa.Text, nullable=False),  # JSON payload
    sa.Column("request_body", sa.Text, nullable=True),  # Full request sent
    sa.Column("response_status", sa.Integer, nullable=True),  # HTTP status code
    sa.Column("response_body", sa.Text, nullable=True),  # Response (truncated)
    sa.Column("error_message", sa.Text, nullable=True),  # Error if request failed
    sa.Column("attempt_number", sa.Integer, default=1),
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed', 'failed_permanent')",
            name="ck_webhook_deliveries_status",
        ),
        default="pending",
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("duration_ms", sa.Integer, nullable=True),  # Request duration
    sa.Index("ix_webhook_deliveries_webhook_id", "webhook_id"),
    sa.Index("ix_webhook_deliveries_status", "status"),
    sa.Index("ix_webhook_deliveries_event_type", "event_type"),
    sa.Index("ix_webhook_deliveries_next_retry_at", "next_retry_at"),
    sa.Index("ix_webhook_deliveries_created_at", "created_at"),
    # Composite index for efficient pending delivery queries
    sa.Index("ix_webhook_deliveries_status_next_retry", "status", "next_retry_at"),
)

# Live streaming via HTTP segment push
#
# STREAM STATES:
# --------------
# - idle: Stream created but not yet started (no segments received)
# - live: Actively receiving segments
# - ending: No segments received for threshold period (grace period)
# - ended: Stream explicitly ended or stale timeout exceeded
#
# ARCHITECTURE:
# -------------
# - Client (FFmpeg) encodes locally into HLS/CMAF segments
# - Client pushes segments to VLog API via HTTP PUT
# - VLog stores segments, writes playlists to disk on each segment
# - Viewers watch via standard HLS playback (static file serving)
# - On stream end, segments become a VOD recording
#
# AUTH:
# -----
# - Stream keys follow worker API key pattern (argon2id hashed)
# - Keys are prefixed with "sk_live_" for easy identification
# - Prefix lookup for fast authentication
#
# See: https://github.com/filthyrake/vlog/issues/XXX
live_streams = sa.Table(
    "live_streams",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("slug", sa.String(255), unique=True, nullable=False),
    sa.Column("description", sa.Text, default=""),
    # Auth (argon2id hashed like worker API keys)
    sa.Column("stream_key_hash", sa.Text, nullable=False),
    sa.Column("stream_key_prefix", sa.String(8), nullable=False),
    sa.Column("hash_version", sa.Integer, nullable=False, server_default="2"),
    # Status
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('idle', 'live', 'ending', 'ended')",
            name="ck_live_streams_status",
        ),
        default="idle",
    ),
    sa.Column("qualities", sa.Text, nullable=True),  # JSON: ["720p", "480p"]
    # Timestamps
    sa.Column("created_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_segment_at", sa.DateTime(timezone=True), nullable=True),
    # Metrics
    sa.Column("segment_count", sa.Integer, default=0),
    # DVR/VOD
    sa.Column("dvr_enabled", sa.Boolean, default=True),
    sa.Column("dvr_window_seconds", sa.Integer, default=7200),  # 2 hours
    sa.Column("auto_record_vod", sa.Boolean, default=True),
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
    # Stream ownership for multi-user auth (Issue #524)
    # Nullable for backward compatibility - existing streams assigned to first admin during migration
    sa.Column(
        "owner_id",
        sa.String(36),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Index("ix_live_streams_slug", "slug"),
    sa.Index("ix_live_streams_status", "status"),
    sa.Index("ix_live_streams_stream_key_prefix", "stream_key_prefix"),
    sa.Index("ix_live_streams_created_at", "created_at"),
    sa.Index("ix_live_streams_owner_id", "owner_id"),
)

# Live stream segment tracking for DVR and VOD recording
#
# SEGMENT SEMANTICS:
# ------------------
# - Each segment is a single HLS/CMAF fragment
# - Segments are stored on disk at: live/{slug}/{quality}/seg_{sequence}.m4s
# - Init segments stored at: live/{slug}/{quality}/init.mp4
# - Playlists generated dynamically from database records
#
# DVR CLEANUP:
# ------------
# - Background task deletes segments older than dvr_window_seconds
# - Batched DELETEs (10 at a time) to reduce lock contention
# - Async file deletion to avoid blocking segment uploads
#
# VOD RECORDING:
# --------------
# - On stream end, segments are hardlinked to videos directory
# - Fallback to copy if hardlinks fail (different filesystems)
live_stream_segments = sa.Table(
    "live_stream_segments",
    metadata,
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
    sa.Column("received_at", sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    sa.UniqueConstraint("stream_id", "quality", "sequence_number", name="uq_live_segment_stream_quality_seq"),
    # Critical indexes for performance (from Brendan's review)
    sa.Index("ix_live_segments_stream_quality_seq", "stream_id", "quality", "sequence_number"),
    sa.Index("ix_live_segments_received_at", "received_at"),
    sa.Index("ix_live_segments_cleanup", "stream_id", "received_at"),
)


# =============================================================================
# User Authentication Tables (Issue #200)
# Multi-user authentication with session-based browser auth, API keys, and RBAC
# =============================================================================

# User accounts for multi-user authentication
#
# ROLES:
# ------
# - admin: Full system access + user management
# - editor: Upload, edit/delete own videos, view own analytics
# - viewer: Browse and watch videos (for private instances)
#
# STATUS:
# -------
# - active: Normal user access
# - disabled: Account disabled by admin (cannot login)
# - pending: Awaiting email verification or admin approval
#
# SECURITY:
# ---------
# - Passwords hashed with argon2id (same as worker API keys)
# - Failed login tracking with lockout support
# - Email verification support for self-registration
#
# See: https://github.com/filthyrake/vlog/issues/200
users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),  # UUID
    sa.Column("username", sa.String(100), unique=True, nullable=False),
    sa.Column("email", sa.String(255), unique=True, nullable=False),
    sa.Column("password_hash", sa.String(255), nullable=True),  # NULL for OIDC-only users
    sa.Column("display_name", sa.String(100), nullable=True),
    sa.Column("avatar_url", sa.String(500), nullable=True),
    sa.Column(
        "role",
        sa.String(20),
        sa.CheckConstraint("role IN ('admin', 'editor', 'viewer')", name="ck_users_role"),
        default="viewer",
        nullable=False,
    ),
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint("status IN ('active', 'disabled', 'pending')", name="ck_users_status"),
        default="active",
        nullable=False,
    ),
    sa.Column("email_verified", sa.Boolean, default=False, nullable=False),
    sa.Column("failed_login_attempts", sa.Integer, default=0, nullable=False),
    sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_by", sa.String(36), nullable=True),  # FK to users.id (nullable for first admin)
    sa.Index("ix_users_username", "username"),
    sa.Index("ix_users_email", "email"),
    sa.Index("ix_users_role", "role"),
    sa.Index("ix_users_status", "status"),
    sa.Index("ix_users_created_at", "created_at"),
)

# User sessions for browser authentication (HTTP-only cookies)
#
# SESSION TOKENS:
# ---------------
# - session_token: Short-lived access token (default 24 hours)
# - refresh_token: Long-lived token for session rotation (default 7 days)
#
# REFRESH TOKEN ROTATION:
# -----------------------
# - refresh_family_id: Groups related refresh tokens together
# - refresh_generation: Incremented on each rotation
# - Detects token theft: if a rotated token is reused, entire family is revoked
#
# SECURITY:
# ---------
# - All tokens stored as argon2id hashes
# - IP and user-agent logged for audit
# - Revocation support via revoked_at timestamp
#
# See: https://github.com/filthyrake/vlog/issues/200
user_sessions = sa.Table(
    "user_sessions",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),  # UUID
    sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
    sa.Column("token_prefix", sa.String(8), nullable=True),  # For indexed lookup
    sa.Column("refresh_token_hash", sa.String(255), unique=True, nullable=True),
    sa.Column("refresh_token_prefix", sa.String(8), nullable=True),  # For indexed lookup
    sa.Column("refresh_family_id", sa.String(36), nullable=True),  # UUID for token family
    sa.Column("refresh_generation", sa.Integer, default=0, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("ip_address", sa.String(45), nullable=True),  # IPv6 max length
    sa.Column("user_agent", sa.String(512), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),  # Track session usage
    sa.Index("ix_user_sessions_user_id", "user_id"),
    sa.Index("ix_user_sessions_token_hash", "token_hash"),
    sa.Index("ix_user_sessions_token_prefix", "token_prefix"),
    sa.Index("ix_user_sessions_refresh_token_hash", "refresh_token_hash"),
    sa.Index("ix_user_sessions_refresh_token_prefix", "refresh_token_prefix"),
    sa.Index("ix_user_sessions_expires_at", "expires_at"),
    sa.Index("ix_user_sessions_refresh_family_id", "refresh_family_id"),
)

# User API keys for programmatic access
#
# KEY LIFECYCLE:
# -------------
# 1. Generation: POST /api/v1/api-keys → generates 256-bit API key
#    → Key shown once at creation, never retrievable again
#    → Stored as argon2id hash
# 2. Usage: Client includes key in X-API-Key header
#    → Fast lookup via key_prefix (first 8 chars)
# 3. Permissions: Inherited from user's role (no per-key overrides)
# 4. Expiration: Optional expires_at timestamp
# 5. Revocation: DELETE /api/v1/api-keys/{id} sets revoked_at
#
# See: https://github.com/filthyrake/vlog/issues/200
user_api_keys = sa.Table(
    "user_api_keys",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),  # UUID
    sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("name", sa.String(100), nullable=False),
    sa.Column("key_prefix", sa.String(8), nullable=False),  # First 8 chars for lookup
    sa.Column("key_hash", sa.String(255), nullable=False),  # argon2id hash
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_user_api_keys_user_id", "user_id"),
    sa.Index("ix_user_api_keys_key_prefix", "key_prefix"),
)

# OIDC connections for linking users to external identity providers
#
# Supports any OIDC-compliant provider:
# - Keycloak, Authentik, Authelia, Zitadel
# - Google, GitHub, Microsoft (if configured as OIDC)
#
# Users can have multiple OIDC connections (different providers)
# OIDC-only users have NULL password_hash in users table
#
# See: https://github.com/filthyrake/vlog/issues/200
oidc_connections = sa.Table(
    "oidc_connections",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),  # UUID
    sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("provider_user_id", sa.String(255), nullable=False),  # Subject claim from OIDC
    sa.Column("provider_email", sa.String(255), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_oidc_connections_user_id", "user_id"),
    sa.Index("ix_oidc_connections_provider_user_id", "provider_user_id"),
    sa.UniqueConstraint("provider_user_id", name="uq_oidc_connections_provider_user_id"),
)

# OIDC state tokens for CSRF and replay protection
#
# SECURITY:
# ---------
# - state: Random value sent to OIDC provider, returned in callback
#   → CSRF protection: validates callback originated from our flow
# - nonce: Random value included in ID token request
#   → Replay protection: validates token is for this specific flow
#
# States are single-use and expire after 10 minutes
#
# See: https://github.com/filthyrake/vlog/issues/200
oidc_states = sa.Table(
    "oidc_states",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),  # UUID
    sa.Column("state", sa.String(64), unique=True, nullable=False),
    sa.Column("nonce", sa.String(64), nullable=False),
    sa.Column("redirect_uri", sa.String(500), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_oidc_states_state", "state"),
    sa.Index("ix_oidc_states_expires_at", "expires_at"),
)

# Password reset tokens
#
# SECURITY:
# ---------
# - Tokens are single-use (used_at set on use)
# - Short expiry (default 1 hour)
# - IP address logged for abuse detection
# - Reset endpoint returns constant-time response to prevent user enumeration
#
# See: https://github.com/filthyrake/vlog/issues/200
password_reset_tokens = sa.Table(
    "password_reset_tokens",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),  # UUID
    sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
    sa.Column("ip_address", sa.String(45), nullable=True),  # For abuse detection
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_password_reset_tokens_user_id", "user_id"),
    sa.Index("ix_password_reset_tokens_token_hash", "token_hash"),
    sa.Index("ix_password_reset_tokens_expires_at", "expires_at"),
)

# User invites for invite-only registration
#
# FLOW:
# -----
# 1. Admin creates invite: POST /api/v1/invites
# 2. Email sent with invite link containing token
# 3. User clicks link, creates account: POST /api/v1/invites/{token}/accept
# 4. Invite marked as used, user created with specified role
#
# SECURITY:
# ---------
# - Tokens are single-use (used_at set on acceptance)
# - Configurable expiry (default 7 days)
# - Role pre-assigned by admin
#
# See: https://github.com/filthyrake/vlog/issues/200
user_invites = sa.Table(
    "user_invites",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),  # UUID
    sa.Column("email", sa.String(255), nullable=False),
    sa.Column(
        "role",
        sa.String(20),
        sa.CheckConstraint("role IN ('admin', 'editor', 'viewer')", name="ck_user_invites_role"),
        default="viewer",
        nullable=False,
    ),
    sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("used_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sa.Index("ix_user_invites_email", "email"),
    sa.Index("ix_user_invites_token_hash", "token_hash"),
    sa.Index("ix_user_invites_expires_at", "expires_at"),
)


# =============================================================================
# Comments and Ratings Tables (Issue #213)
# Social engagement features with threading support and moderation
# =============================================================================

# Comments with ltree materialized path for efficient threading
#
# THREADING:
# ----------
# Uses PostgreSQL ltree extension for hierarchical queries.
# - path: Materialized path like "1.5.23" (comment 23 is a reply to comment 5, which replies to 1)
# - depth: 1 for root comments, max 5 for deep nesting
# - parent_id: Direct parent reference for cascade deletes
#
# STATUS:
# -------
# - pending: Awaiting moderation (when comments_require_approval is enabled)
# - approved: Visible to all users
# - rejected: Hidden by moderator
# - spam: Flagged as spam
#
# SOFT DELETE:
# ------------
# - deleted_at: When set, comment is hidden but preserved for audit
# - Hard delete only via admin force-delete endpoint
#
# See: https://github.com/filthyrake/vlog/issues/213
comments = sa.Table(
    "comments",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column(
        "video_id",
        sa.Integer,
        sa.ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "user_id",
        sa.String(36),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # Materialized path for threading (stored as text, handled as ltree in PostgreSQL)
    # Example paths: "1" (root), "1.5" (reply to 1), "1.5.23" (reply to reply)
    sa.Column("path", sa.Text, nullable=False),
    sa.Column(
        "depth",
        sa.Integer,
        sa.CheckConstraint("depth >= 1 AND depth <= 5", name="ck_comments_depth"),
        nullable=False,
        default=1,
    ),
    sa.Column(
        "parent_id",
        sa.Integer,
        sa.ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
    ),
    sa.Column("content", sa.Text, nullable=False),
    # Video timestamp for "comment at X:XX" feature (millisecond precision)
    sa.Column("video_timestamp", sa.Numeric(10, 3), nullable=True),
    sa.Column(
        "status",
        sa.String(20),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'spam')",
            name="ck_comments_status",
        ),
        nullable=False,
        default="approved",
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),  # Soft delete
    # Note: GiST index on path is created in migration using raw SQL for ltree
    sa.Index("ix_comments_video_id", "video_id"),
    sa.Index("ix_comments_user_id", "user_id"),
    sa.Index("ix_comments_parent_id", "parent_id"),
    sa.Index("ix_comments_status", "status"),
    sa.Index("ix_comments_created_at", "created_at"),
)

# Ratings with composite primary key (one rating per user per video)
#
# RATING VALUES:
# --------------
# For stars mode (rating_type = "stars"):
# - rating_value: 1-5 (star count)
#
# For thumbs mode (rating_type = "thumbs"):
# - rating_value: 1 (like) or -1 (dislike)
#
# Aggregates are maintained on videos table via triggers:
# - rating_avg: Average of all rating_values
# - rating_count: Total number of ratings
# - rating_distribution: JSON object {"1": count, "2": count, ...}
# - likes_count: Count of rating_value > 0 (for thumbs mode)
# - dislikes_count: Count of rating_value < 0 (for thumbs mode)
#
# See: https://github.com/filthyrake/vlog/issues/213
ratings = sa.Table(
    "ratings",
    metadata,
    sa.Column(
        "video_id",
        sa.Integer,
        sa.ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "user_id",
        sa.String(36),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # Rating value: 1-5 for stars, 1 or -1 for thumbs
    sa.Column("rating_value", sa.Integer, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint("video_id", "user_id"),
    sa.Index("ix_ratings_video_id", "video_id"),
    sa.Index("ix_ratings_user_id", "user_id"),
)


# =============================================================================
# Backup and Restore Tables (Issue #216)
# Comprehensive backup system with database, file, and S3 support
# =============================================================================

# Backup records for tracking backup operations
#
# BACKUP TYPES:
# -------------
# - full: Complete backup (database + optionally video files)
# - database_only: Database dump only (fastest)
# - incremental: Only new/changed files since last backup
#
# STATUS LIFECYCLE:
# -----------------
# pending -> backing_up_database -> backing_up_files -> uploading_s3 -> completed
# pending -> backing_up_database -> failed (on error at any stage)
#
# STORAGE:
# --------
# - local_path: Path on local/NAS storage
# - s3_location: S3 URI (s3://bucket/prefix/backup_id.tar.gz)
#
# MANIFEST:
# ---------
# - manifest_json: Cached manifest for quick access (full manifest stored in backup)
# - Manifest includes checksums for integrity verification
#
# See: https://github.com/filthyrake/vlog/issues/216
backups = sa.Table(
    "backups",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("backup_id", sa.String(50), unique=True, nullable=False),  # backup_20260126_020000
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
        default="pending",
    ),
    # Size and content statistics
    sa.Column("size_bytes", sa.BigInteger, nullable=True),  # Total backup size
    sa.Column("database_size_bytes", sa.BigInteger, nullable=True),  # DB dump size
    sa.Column("files_size_bytes", sa.BigInteger, nullable=True),  # Video files size
    sa.Column("video_count", sa.Integer, nullable=True),  # Number of videos backed up
    sa.Column("file_count", sa.Integer, nullable=True),  # Number of files in backup
    # Description and metadata
    sa.Column("description", sa.Text, nullable=True),  # User-provided description
    # Storage locations
    sa.Column("local_path", sa.String(500), nullable=True),  # Local filesystem path
    sa.Column("s3_location", sa.String(500), nullable=True),  # S3 URI
    # Manifest (JSON for quick access, full manifest in backup archive)
    sa.Column("manifest_json", sa.Text, nullable=True),  # JSON-encoded manifest
    sa.Column("manifest_signature", sa.String(64), nullable=True),  # HMAC-SHA256 signature
    # Timestamps
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    # Provenance
    sa.Column("created_by", sa.String(100), nullable=True),  # User/system that created
    # Error tracking
    sa.Column("error_message", sa.Text, nullable=True),
    # VLog version info (for compatibility checking during restore)
    sa.Column("vlog_version", sa.String(50), nullable=True),
    sa.Column("schema_version", sa.String(10), nullable=True),  # Migration version
    sa.Column("database_type", sa.String(20), nullable=True),  # postgresql or sqlite
    sa.Index("ix_backups_backup_id", "backup_id"),
    sa.Index("ix_backups_status", "status"),
    sa.Index("ix_backups_created_at", "created_at"),
    sa.Index("ix_backups_backup_type", "backup_type"),
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
    logger.info("Database tables created successfully!")
