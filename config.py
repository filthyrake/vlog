import logging
import os
from pathlib import Path
from typing import Optional, Set

# Configure logger for config module warnings
logger = logging.getLogger(__name__)

# Track which deprecation warnings have been issued (to avoid repeated warnings)
_deprecation_warnings_issued: Set[str] = set()

# Environment variables that should be migrated to database settings
# These will trigger a deprecation warning if set
DEPRECATED_ENV_VARS = {
    # Transcoding settings
    "VLOG_HLS_SEGMENT_DURATION": "transcoding.hls_segment_duration",
    "VLOG_CHECKPOINT_INTERVAL": "transcoding.checkpoint_interval",
    "VLOG_MAX_RETRY_ATTEMPTS": "transcoding.max_retries",
    "VLOG_RETRY_BACKOFF_BASE": "transcoding.retry_backoff_base",
    "VLOG_JOB_STALE_TIMEOUT": "transcoding.job_stale_timeout",
    "VLOG_CLEANUP_PARTIAL_ON_FAILURE": "transcoding.cleanup_partial_on_failure",
    "VLOG_KEEP_COMPLETED_QUALITIES": "transcoding.keep_completed_qualities",
    "VLOG_FFMPEG_TIMEOUT_BASE_MULTIPLIER": "transcoding.ffmpeg_timeout_multiplier",
    "VLOG_FFMPEG_TIMEOUT_MINIMUM": "transcoding.ffmpeg_timeout_minimum",
    "VLOG_FFMPEG_TIMEOUT_MAXIMUM": "transcoding.ffmpeg_timeout_maximum",
    # Watermark settings
    "VLOG_WATERMARK_ENABLED": "watermark.enabled",
    "VLOG_WATERMARK_TYPE": "watermark.type",
    "VLOG_WATERMARK_IMAGE": "watermark.image",
    "VLOG_WATERMARK_TEXT": "watermark.text",
    "VLOG_WATERMARK_TEXT_SIZE": "watermark.text_size",
    "VLOG_WATERMARK_TEXT_COLOR": "watermark.text_color",
    "VLOG_WATERMARK_POSITION": "watermark.position",
    "VLOG_WATERMARK_OPACITY": "watermark.opacity",
    "VLOG_WATERMARK_PADDING": "watermark.padding",
    "VLOG_WATERMARK_MAX_WIDTH_PERCENT": "watermark.max_width_percent",
    # Worker settings
    "VLOG_WORKER_HEARTBEAT_INTERVAL": "workers.heartbeat_interval",
    "VLOG_WORKER_CLAIM_DURATION": "workers.claim_duration_minutes",
    "VLOG_WORKER_POLL_INTERVAL": "workers.poll_interval",
    "VLOG_WORKER_FALLBACK_POLL_INTERVAL": "workers.fallback_poll_interval",
    "VLOG_WORKER_DEBOUNCE_DELAY": "workers.debounce_delay",
    "VLOG_WORKER_OFFLINE_THRESHOLD": "workers.offline_threshold_minutes",
    "VLOG_STALE_JOB_CHECK_INTERVAL": "workers.stale_job_check_interval",
    "VLOG_PROGRESS_UPDATE_INTERVAL": "workers.progress_update_interval",
    # Analytics settings
    "VLOG_ANALYTICS_CACHE_ENABLED": "analytics.cache_enabled",
    "VLOG_ANALYTICS_CACHE_TTL": "analytics.cache_ttl",
    "VLOG_ANALYTICS_CLIENT_CACHE_MAX_AGE": "analytics.client_cache_max_age",
    # Alert settings
    "VLOG_ALERT_WEBHOOK_URL": "alerts.webhook_url",
    "VLOG_ALERT_WEBHOOK_TIMEOUT": "alerts.webhook_timeout",
    "VLOG_ALERT_RATE_LIMIT_SECONDS": "alerts.rate_limit_seconds",
    # Transcription settings
    "VLOG_WHISPER_MODEL": "transcription.whisper_model",
    "VLOG_TRANSCRIPTION_ENABLED": "transcription.enabled",
    "VLOG_TRANSCRIPTION_LANGUAGE": "transcription.language",
    "VLOG_TRANSCRIPTION_ON_UPLOAD": "transcription.on_upload",
    "VLOG_TRANSCRIPTION_COMPUTE_TYPE": "transcription.compute_type",
    "VLOG_TRANSCRIPTION_TIMEOUT": "transcription.timeout",
    # Storage settings
    "VLOG_ARCHIVE_RETENTION_DAYS": "storage.archive_retention_days",
    "VLOG_MAX_UPLOAD_SIZE": "storage.max_upload_size_mb",
    "VLOG_MAX_THUMBNAIL_SIZE": "storage.max_thumbnail_size_mb",
    "VLOG_THUMBNAIL_WIDTH": "storage.thumbnail_width",
}


def check_deprecated_env_vars() -> None:
    """
    Check for deprecated environment variables and log warnings.

    Called at startup to warn users about env vars that should be migrated
    to the database-backed settings system.
    """
    deprecated_found = []

    for env_var, setting_key in DEPRECATED_ENV_VARS.items():
        if os.getenv(env_var) is not None and env_var not in _deprecation_warnings_issued:
            deprecated_found.append((env_var, setting_key))
            _deprecation_warnings_issued.add(env_var)

    if deprecated_found:
        logger.warning(
            "The following environment variables are deprecated and should be migrated to database settings:"
        )
        for env_var, setting_key in deprecated_found:
            logger.warning(f"  {env_var} -> {setting_key}")
        logger.warning(
            "Run 'vlog settings migrate-from-env' to migrate these settings to the database. "
            "The env vars will continue to work as fallbacks until removed."
        )


def get_int_env(
    name: str,
    default: int,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
) -> int:
    """Get an integer from environment variable with error handling and validation.

    Args:
        name: Environment variable name
        default: Default value if env var is missing or invalid
        min_val: Optional minimum value (inclusive)
        max_val: Optional maximum value (inclusive)

    Returns:
        Parsed integer value, or default if parsing fails or value is out of range
    """
    value = os.getenv(name)
    if value is None:
        # Environment variable not set; use default without validation
        return default

    try:
        result = int(value)
    except ValueError:
        logger.warning(f"Invalid {name}='{value}', using default {default}")
        return default

    # Range validation (only applied to user-provided values)
    if min_val is not None and result < min_val:
        logger.warning(
            f"{name}={result} is below minimum {min_val}, using default {default}"
        )
        return default
    if max_val is not None and result > max_val:
        logger.warning(
            f"{name}={result} is above maximum {max_val}, using default {default}"
        )
        return default

    return result


def get_float_env(
    name: str,
    default: float,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    """Get a float from environment variable with error handling and validation.

    Args:
        name: Environment variable name
        default: Default value if env var is missing or invalid
        min_val: Optional minimum value (inclusive)
        max_val: Optional maximum value (inclusive)

    Returns:
        Parsed float value, or default if parsing fails or value is out of range
    """
    import math

    value = os.getenv(name)
    if value is None:
        # Environment variable not set; use default without validation
        return default

    try:
        result = float(value)
    except ValueError:
        logger.warning(f"Invalid {name}='{value}', using default {default}")
        return default

    # Reject special float values (inf, -inf, nan)
    if math.isinf(result) or math.isnan(result):
        logger.warning(f"Invalid {name}='{value}' (special float), using default {default}")
        return default

    # Range validation (only applied to user-provided values)
    if min_val is not None and result < min_val:
        logger.warning(
            f"{name}={result} is below minimum {min_val}, using default {default}"
        )
        return default
    if max_val is not None and result > max_val:
        logger.warning(
            f"{name}={result} is above maximum {max_val}, using default {default}"
        )
        return default

    return result

# Supported video file extensions (centralized to avoid duplication)
SUPPORTED_VIDEO_EXTENSIONS = frozenset([".mp4", ".mkv", ".webm", ".mov", ".avi"])
SUPPORTED_VIDEO_EXTENSIONS_STR = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))

# Paths - configurable via environment variables
BASE_DIR = Path(__file__).parent
NAS_STORAGE = Path(os.getenv("VLOG_STORAGE_PATH", "/mnt/nas/vlog-storage"))
VIDEOS_DIR = NAS_STORAGE / os.getenv("VLOG_VIDEOS_SUBDIR", "videos")
UPLOADS_DIR = NAS_STORAGE / os.getenv("VLOG_UPLOADS_SUBDIR", "uploads")
ARCHIVE_DIR = NAS_STORAGE / os.getenv("VLOG_ARCHIVE_SUBDIR", "archive")
# Database configuration - PostgreSQL is the default
# Set VLOG_DATABASE_URL to override (e.g., for SQLite: sqlite:///./vlog.db)
DATABASE_URL = os.getenv("VLOG_DATABASE_URL", "postgresql://vlog:vlog_password@localhost/vlog")

# Legacy SQLite path (kept for migration scripts)
DATABASE_PATH = Path(os.getenv("VLOG_DATABASE_PATH", str(BASE_DIR / "vlog.db")))

# Ensure directories exist (skip in test/CI environments)
if not os.environ.get("VLOG_TEST_MODE"):
    try:
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass  # CI environment without NAS access

# Soft-delete settings
ARCHIVE_RETENTION_DAYS = get_int_env("VLOG_ARCHIVE_RETENTION_DAYS", 30, min_val=0)

# Server ports
PUBLIC_PORT = get_int_env("VLOG_PUBLIC_PORT", 9000, min_val=1, max_val=65535)
ADMIN_PORT = get_int_env("VLOG_ADMIN_PORT", 9001, min_val=1, max_val=65535)

# Transcoding quality presets (YouTube-style)
QUALITY_PRESETS = [
    {"name": "2160p", "height": 2160, "bitrate": "15000k", "audio_bitrate": "192k"},
    {"name": "1440p", "height": 1440, "bitrate": "8000k", "audio_bitrate": "192k"},
    {"name": "1080p", "height": 1080, "bitrate": "5000k", "audio_bitrate": "128k"},
    {"name": "720p", "height": 720, "bitrate": "2500k", "audio_bitrate": "128k"},
    {"name": "480p", "height": 480, "bitrate": "1000k", "audio_bitrate": "96k"},
    {"name": "360p", "height": 360, "bitrate": "600k", "audio_bitrate": "96k"},
]

# All quality names including "original" (used for pattern matching)
QUALITY_NAMES = frozenset([q["name"] for q in QUALITY_PRESETS] + ["original"])

# HLS settings
HLS_SEGMENT_DURATION = get_int_env("VLOG_HLS_SEGMENT_DURATION", 6, min_val=1)

# Checkpoint/resumable transcoding settings
CHECKPOINT_INTERVAL = get_int_env("VLOG_CHECKPOINT_INTERVAL", 30, min_val=1)
JOB_STALE_TIMEOUT = get_int_env("VLOG_JOB_STALE_TIMEOUT", 1800, min_val=60)
MAX_RETRY_ATTEMPTS = get_int_env("VLOG_MAX_RETRY_ATTEMPTS", 3, min_val=0)
RETRY_BACKOFF_BASE = get_int_env("VLOG_RETRY_BACKOFF_BASE", 60, min_val=0)
CLEANUP_PARTIAL_ON_FAILURE = os.getenv("VLOG_CLEANUP_PARTIAL_ON_FAILURE", "true").lower() == "true"
KEEP_COMPLETED_QUALITIES = os.getenv("VLOG_KEEP_COMPLETED_QUALITIES", "true").lower() == "true"
CLEANUP_SOURCE_ON_PERMANENT_FAILURE = os.getenv("VLOG_CLEANUP_SOURCE_ON_PERMANENT_FAILURE", "true").lower() == "true"

# FFmpeg timeout settings (prevents stuck transcoding jobs)
# Base multiplier applied to video duration (scaled by resolution)
FFMPEG_TIMEOUT_BASE_MULTIPLIER = get_float_env("VLOG_FFMPEG_TIMEOUT_BASE_MULTIPLIER", 2.0, min_val=0.1)
FFMPEG_TIMEOUT_MINIMUM = get_int_env("VLOG_FFMPEG_TIMEOUT_MINIMUM", 300, min_val=1)
FFMPEG_TIMEOUT_MAXIMUM = get_int_env("VLOG_FFMPEG_TIMEOUT_MAXIMUM", 14400, min_val=60)  # 4 hours

# Per-resolution timeout multipliers (applied on top of base multiplier)
# Lower resolutions encode faster, higher resolutions need more time
FFMPEG_TIMEOUT_RESOLUTION_MULTIPLIERS = {
    360: 1.0,  # 360p: fast encode
    480: 1.25,
    720: 1.5,
    1080: 2.0,
    1440: 2.5,
    2160: 3.5,  # 4K: slowest encode
}

# Transcription settings
WHISPER_MODEL = os.getenv("VLOG_WHISPER_MODEL", "medium")
TRANSCRIPTION_ENABLED = os.getenv("VLOG_TRANSCRIPTION_ENABLED", "true").lower() == "true"
TRANSCRIPTION_LANGUAGE = os.getenv("VLOG_TRANSCRIPTION_LANGUAGE", None) or None
TRANSCRIPTION_ON_UPLOAD = os.getenv("VLOG_TRANSCRIPTION_ON_UPLOAD", "true").lower() == "true"
TRANSCRIPTION_COMPUTE_TYPE = os.getenv("VLOG_TRANSCRIPTION_COMPUTE_TYPE", "int8")
TRANSCRIPTION_TIMEOUT = get_int_env("VLOG_TRANSCRIPTION_TIMEOUT", 3600, min_val=60)
AUDIO_EXTRACTION_TIMEOUT = get_int_env("VLOG_AUDIO_EXTRACTION_TIMEOUT", 300, min_val=10)

# Hardware Acceleration Settings (for remote workers with GPUs)
# VLOG_HWACCEL_TYPE: "auto" (detect), "nvidia", "intel", or "none"
HWACCEL_TYPE = os.getenv("VLOG_HWACCEL_TYPE", "auto")
# Preferred codec: "h264" (max compatibility), "hevc" (smaller files), "av1" (best compression)
HWACCEL_PREFERRED_CODEC = os.getenv("VLOG_HWACCEL_PREFERRED_CODEC", "h264")
# Fall back to CPU encoding if GPU encoding fails
HWACCEL_FALLBACK_TO_CPU = os.getenv("VLOG_HWACCEL_FALLBACK_TO_CPU", "true").lower() == "true"
# Max concurrent encode sessions (NVIDIA consumer GPUs have limits: RTX 3090=3, RTX 4090=5)
HWACCEL_MAX_CONCURRENT_SESSIONS = get_int_env("VLOG_HWACCEL_MAX_SESSIONS", 3, min_val=1)
# Intel VAAPI device path (auto-detected if empty)
HWACCEL_VAAPI_DEVICE = os.getenv("VLOG_HWACCEL_VAAPI_DEVICE", "")

# Parallel Quality Encoding Settings
# Number of qualities to encode simultaneously (1 = sequential, 3 = recommended for GPUs)
# Used when PARALLEL_QUALITIES_AUTO is false, or when no GPU is detected
PARALLEL_QUALITIES = get_int_env("VLOG_PARALLEL_QUALITIES", 1, min_val=1)
# Auto-detect optimal parallelism based on GPU capabilities
# When true AND a GPU is detected, overrides PARALLEL_QUALITIES with min(3, gpu.max_sessions - 1)
# When true but no GPU is detected, falls back to PARALLEL_QUALITIES value
PARALLEL_QUALITIES_AUTO = os.getenv("VLOG_PARALLEL_QUALITIES_AUTO", "true").lower() == "true"

# Worker settings (event-driven processing for local worker)
WORKER_USE_FILESYSTEM_WATCHER = os.getenv("VLOG_WORKER_USE_FILESYSTEM_WATCHER", "true").lower() == "true"
WORKER_FALLBACK_POLL_INTERVAL = get_int_env("VLOG_WORKER_FALLBACK_POLL_INTERVAL", 60, min_val=1)
WORKER_DEBOUNCE_DELAY = get_float_env("VLOG_WORKER_DEBOUNCE_DELAY", 1.0, min_val=0.0)

# Worker API service settings (for distributed workers)
WORKER_API_PORT = get_int_env("VLOG_WORKER_API_PORT", 9002, min_val=1, max_val=65535)

# Remote worker client settings
WORKER_API_URL = os.getenv("VLOG_WORKER_API_URL", "http://localhost:9002")
WORKER_API_KEY = os.getenv("VLOG_WORKER_API_KEY", "")

# Worker admin secret for registration and management endpoints (#109, #110)
# Required for: POST /api/worker/register, GET /api/workers, POST /api/workers/{id}/revoke
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
WORKER_ADMIN_SECRET = os.getenv("VLOG_WORKER_ADMIN_SECRET", "")

# Admin API secret for authentication (#234)
# When set, all /api/ endpoints on the Admin API require X-Admin-Secret header
# If empty/unset, Admin API endpoints are unauthenticated (for backwards compatibility)
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
ADMIN_API_SECRET = os.getenv("VLOG_ADMIN_API_SECRET", "")
# Session expiry for admin UI (hours). Sessions are stored server-side with HTTP-only cookies.
# See: https://github.com/filthyrake/vlog/issues/324
ADMIN_SESSION_EXPIRY_HOURS = get_int_env("VLOG_ADMIN_SESSION_EXPIRY_HOURS", 24, min_val=1)
WORKER_HEARTBEAT_INTERVAL = get_int_env("VLOG_WORKER_HEARTBEAT_INTERVAL", 30, min_val=1)
WORKER_CLAIM_DURATION_MINUTES = get_int_env("VLOG_WORKER_CLAIM_DURATION", 30, min_val=1)
WORKER_POLL_INTERVAL = get_int_env("VLOG_WORKER_POLL_INTERVAL", 10, min_val=1)
WORKER_WORK_DIR = Path(os.getenv("VLOG_WORKER_WORK_DIR", "/tmp/vlog-worker"))
WORKER_OFFLINE_THRESHOLD_MINUTES = get_int_env("VLOG_WORKER_OFFLINE_THRESHOLD", 5, min_val=1)

# Worker health check server port (for K8s liveness/readiness probes)
WORKER_HEALTH_PORT = get_int_env("VLOG_WORKER_HEALTH_PORT", 8080, min_val=1, max_val=65535)

# How often to check for stale jobs from offline workers (in seconds)
STALE_JOB_CHECK_INTERVAL = get_int_env("VLOG_STALE_JOB_CHECK_INTERVAL", 60, min_val=1)

# Progress update rate limiting (prevents database overload during transcoding)
PROGRESS_UPDATE_INTERVAL = get_float_env("VLOG_PROGRESS_UPDATE_INTERVAL", 5.0, min_val=0.1)

# Upload size limits (default 100GB - reasonable for 4K video)
MAX_UPLOAD_SIZE = get_int_env("VLOG_MAX_UPLOAD_SIZE", 100 * 1024 * 1024 * 1024, min_val=1)  # 100 GB
UPLOAD_CHUNK_SIZE = get_int_env("VLOG_UPLOAD_CHUNK_SIZE", 1024 * 1024, min_val=1024)  # 1 MB chunks

# Thumbnail settings
SUPPORTED_IMAGE_EXTENSIONS = frozenset([".jpg", ".jpeg", ".png", ".webp"])
MAX_THUMBNAIL_UPLOAD_SIZE = get_int_env("VLOG_MAX_THUMBNAIL_SIZE", 10 * 1024 * 1024, min_val=1024)  # 10 MB
THUMBNAIL_WIDTH = get_int_env("VLOG_THUMBNAIL_WIDTH", 640, min_val=1)
# Percentages of video duration for frame picker options
THUMBNAIL_FRAME_PERCENTAGES = [0.10, 0.25, 0.50, 0.75, 0.90]

# HLS archive extraction limits (prevent tar bomb attacks)
# Max number of files in an HLS archive (master playlist + quality playlists + segments + thumbnail)
# 6 qualities × 1200 segments (2hrs @ 6s each) + playlists + thumbnails = ~7200 files for 2hr video
# Using 50,000 as generous default to support very long videos (8+ hours)
MAX_HLS_ARCHIVE_FILES = get_int_env("VLOG_MAX_HLS_ARCHIVE_FILES", 50000, min_val=1)
# Max total extracted size (200 GB - generous for long 4K HLS output with all qualities)
MAX_HLS_ARCHIVE_SIZE = get_int_env("VLOG_MAX_HLS_ARCHIVE_SIZE", 200 * 1024 * 1024 * 1024, min_val=1)
# Max size per individual file (500 MB - largest reasonable .ts segment at high bitrate)
MAX_HLS_SINGLE_FILE_SIZE = get_int_env("VLOG_MAX_HLS_SINGLE_FILE_SIZE", 500 * 1024 * 1024, min_val=1)

# CORS Configuration
# Set VLOG_CORS_ORIGINS to comma-separated origins, or leave empty/unset to allow same-origin only
# Example: VLOG_CORS_ORIGINS=http://localhost:9000,http://localhost:9001,https://example.com
_cors_origins_env = os.getenv("VLOG_CORS_ORIGINS", "")
CORS_ALLOWED_ORIGINS = [origin.strip() for origin in _cors_origins_env.split(",") if origin.strip()]

# For admin API - internal only, not exposed externally
# Defaults to allow all origins since it's behind firewall/not public
_admin_cors_env = os.getenv("VLOG_ADMIN_CORS_ORIGINS", "")
ADMIN_CORS_ALLOWED_ORIGINS = (
    [origin.strip() for origin in _admin_cors_env.split(",") if origin.strip()]
    if _admin_cors_env
    else ["*"]  # Admin is internal-only, allow all origins by default
)

# Rate Limiting Configuration
# Set to "0" or "false" to disable rate limiting entirely
RATE_LIMIT_ENABLED = os.getenv("VLOG_RATE_LIMIT_ENABLED", "true").lower() not in ("false", "0", "no")

# Default rate limits (format: "count/period" where period is second, minute, hour, day)
# Public API limits (more restrictive, exposed externally)
RATE_LIMIT_PUBLIC_DEFAULT = os.getenv("VLOG_RATE_LIMIT_PUBLIC_DEFAULT", "100/minute")
RATE_LIMIT_PUBLIC_VIDEOS_LIST = os.getenv("VLOG_RATE_LIMIT_PUBLIC_VIDEOS_LIST", "60/minute")
RATE_LIMIT_PUBLIC_ANALYTICS = os.getenv("VLOG_RATE_LIMIT_PUBLIC_ANALYTICS", "120/minute")

# Admin API limits (more permissive, internal only)
RATE_LIMIT_ADMIN_DEFAULT = os.getenv("VLOG_RATE_LIMIT_ADMIN_DEFAULT", "200/minute")
RATE_LIMIT_ADMIN_UPLOAD = os.getenv("VLOG_RATE_LIMIT_ADMIN_UPLOAD", "10/hour")

# Worker API limits (authenticated workers + registration)
RATE_LIMIT_WORKER_DEFAULT = os.getenv("VLOG_RATE_LIMIT_WORKER_DEFAULT", "300/minute")
RATE_LIMIT_WORKER_REGISTER = os.getenv("VLOG_RATE_LIMIT_WORKER_REGISTER", "5/hour")
RATE_LIMIT_WORKER_PROGRESS = os.getenv("VLOG_RATE_LIMIT_WORKER_PROGRESS", "600/minute")

# Storage backend for rate limiting
# Options: "memory" (default, per-process), or a Redis URL like "redis://localhost:6379"
RATE_LIMIT_STORAGE_URL = os.getenv("VLOG_RATE_LIMIT_STORAGE_URL", "memory://")

# Redis Configuration (for job queue and pub/sub)
# Set VLOG_REDIS_URL to enable Redis features (e.g., "redis://localhost:6379")
# Empty string disables Redis features (database polling used instead)
REDIS_URL = os.getenv("VLOG_REDIS_URL", "")
REDIS_POOL_SIZE = get_int_env("VLOG_REDIS_POOL_SIZE", 10, min_val=1)
REDIS_SOCKET_TIMEOUT = get_float_env("VLOG_REDIS_SOCKET_TIMEOUT", 5.0, min_val=0.1)
REDIS_SOCKET_CONNECT_TIMEOUT = get_float_env("VLOG_REDIS_SOCKET_CONNECT_TIMEOUT", 5.0, min_val=0.1)
REDIS_HEALTH_CHECK_INTERVAL = get_int_env("VLOG_REDIS_HEALTH_CHECK_INTERVAL", 30, min_val=1)

# Job Queue Mode
# "database" (default) - Poll database for jobs (current behavior, always works)
# "redis" - Use Redis Streams for job dispatch (requires REDIS_URL)
# "hybrid" - Use Redis when available, fall back to database polling
JOB_QUEUE_MODE = os.getenv("VLOG_JOB_QUEUE_MODE", "database")

# Redis Streams Settings
REDIS_STREAM_MAX_LEN = get_int_env("VLOG_REDIS_STREAM_MAX_LEN", 10000, min_val=100)
REDIS_CONSUMER_GROUP = os.getenv("VLOG_REDIS_CONSUMER_GROUP", "vlog-workers")
REDIS_CONSUMER_BLOCK_MS = get_int_env("VLOG_REDIS_CONSUMER_BLOCK_MS", 5000, min_val=100)
REDIS_PENDING_TIMEOUT_MS = get_int_env("VLOG_REDIS_PENDING_TIMEOUT_MS", 300000, min_val=1000)  # 5 min

# Pub/Sub Channel Settings
REDIS_PUBSUB_PREFIX = os.getenv("VLOG_REDIS_PUBSUB_PREFIX", "vlog")

# SSE (Server-Sent Events) Settings
SSE_HEARTBEAT_INTERVAL = get_int_env("VLOG_SSE_HEARTBEAT_INTERVAL", 30, min_val=1)
SSE_RECONNECT_TIMEOUT_MS = get_int_env("VLOG_SSE_RECONNECT_TIMEOUT_MS", 3000, min_val=100)

# Trusted proxy configuration for X-Forwarded-For header
# Only trust X-Forwarded-For when request comes from these IPs
# Set VLOG_TRUSTED_PROXIES to comma-separated IPs (e.g., "127.0.0.1,10.0.0.1,192.168.1.1")
# If empty (default), X-Forwarded-For is never trusted (prevents rate limit bypass)
_trusted_proxies_env = os.getenv("VLOG_TRUSTED_PROXIES", "")
TRUSTED_PROXIES = set(ip.strip() for ip in _trusted_proxies_env.split(",") if ip.strip())

# Cookie Security Configuration
# Set to "false" for local development without HTTPS
# Production should always use secure cookies (default: True)
SECURE_COOKIES = os.getenv("VLOG_SECURE_COOKIES", "true").lower() not in ("false", "0", "no")

# Analytics Caching Configuration
# Set to "0" or "false" to disable analytics caching
ANALYTICS_CACHE_ENABLED = os.getenv("VLOG_ANALYTICS_CACHE_ENABLED", "true").lower() not in ("false", "0", "no")

# Cache TTL in seconds (default: 60 seconds)
ANALYTICS_CACHE_TTL = get_int_env("VLOG_ANALYTICS_CACHE_TTL", 60, min_val=1)

# Storage backend for analytics cache
# Options: "memory" (default, per-process), or a Redis URL like "redis://localhost:6379"
# When Redis is configured, analytics cache is shared across all API instances
ANALYTICS_CACHE_STORAGE_URL = os.getenv("VLOG_ANALYTICS_CACHE_STORAGE_URL", "memory://")

# Client-side cache max-age in seconds (default: 60 seconds)
# This controls the Cache-Control header sent to clients
ANALYTICS_CLIENT_CACHE_MAX_AGE = get_int_env("VLOG_ANALYTICS_CLIENT_CACHE_MAX_AGE", 60, min_val=0)

# Storage Health Check Configuration
# Timeout for health check storage access test (seconds)
# Reduced from 5 to 2 for faster failure detection on stale NFS mounts
STORAGE_CHECK_TIMEOUT = get_int_env("VLOG_STORAGE_CHECK_TIMEOUT", 2, min_val=1)

# Audit Logging Configuration
AUDIT_LOG_ENABLED = os.getenv("VLOG_AUDIT_LOG_ENABLED", "true").lower() not in ("false", "0", "no")
AUDIT_LOG_PATH = Path(os.getenv("VLOG_AUDIT_LOG_PATH", "/var/log/vlog/audit.log"))
AUDIT_LOG_LEVEL = os.getenv("VLOG_AUDIT_LOG_LEVEL", "INFO").upper()

# Error Message Truncation Limits
# Standardized limits for consistent debugging experience across the codebase
ERROR_SUMMARY_MAX_LENGTH = get_int_env("VLOG_ERROR_SUMMARY_MAX_LENGTH", 100, min_val=10)  # Brief error summaries
ERROR_DETAIL_MAX_LENGTH = get_int_env("VLOG_ERROR_DETAIL_MAX_LENGTH", 500, min_val=10)  # Detailed error messages
ERROR_LOG_MAX_LENGTH = get_int_env("VLOG_ERROR_LOG_MAX_LENGTH", 2000, min_val=10)  # Full error logs

# Alerting Configuration
# Webhook URL for sending alerts (stale jobs, max retries exceeded, etc.)
# Leave empty to disable webhook alerts
ALERT_WEBHOOK_URL = os.getenv("VLOG_ALERT_WEBHOOK_URL", "")
# Timeout for webhook requests in seconds
ALERT_WEBHOOK_TIMEOUT = get_int_env("VLOG_ALERT_WEBHOOK_TIMEOUT", 10, min_val=1)
# Minimum interval between alerts for the same event type (seconds)
# Prevents alert flooding when multiple jobs fail in quick succession
ALERT_RATE_LIMIT_SECONDS = get_int_env("VLOG_ALERT_RATE_LIMIT_SECONDS", 300, min_val=0)

# Watermark Configuration (client-side overlay, does not modify video files)
# Enable/disable watermark overlay on video player
WATERMARK_ENABLED = os.getenv("VLOG_WATERMARK_ENABLED", "false").lower() in ("true", "1", "yes")
# Watermark type: "image" or "text"
WATERMARK_TYPE = os.getenv("VLOG_WATERMARK_TYPE", "image")
# Path to watermark image (relative to NAS_STORAGE, e.g., "watermark.png")
# Only used when WATERMARK_TYPE is "image"
WATERMARK_IMAGE = os.getenv("VLOG_WATERMARK_IMAGE", "")
# Text to display as watermark (e.g., "© 2025 MyBrand" or "Example.com")
# Only used when WATERMARK_TYPE is "text"
WATERMARK_TEXT = os.getenv("VLOG_WATERMARK_TEXT", "")
# Text watermark font size in pixels (default: 16)
WATERMARK_TEXT_SIZE = get_int_env("VLOG_WATERMARK_TEXT_SIZE", 16, min_val=8, max_val=72)
# Text watermark color (CSS color value, e.g., "white", "#ffffff", "rgba(255,255,255,0.8)")
WATERMARK_TEXT_COLOR = os.getenv("VLOG_WATERMARK_TEXT_COLOR", "white")
# Position: top-left, top-right, bottom-left, bottom-right, center
WATERMARK_POSITION = os.getenv("VLOG_WATERMARK_POSITION", "bottom-right")
# Opacity: 0.0 (invisible) to 1.0 (fully opaque)
WATERMARK_OPACITY = get_float_env("VLOG_WATERMARK_OPACITY", 0.5, min_val=0.0, max_val=1.0)
# Padding from edge in pixels
WATERMARK_PADDING = get_int_env("VLOG_WATERMARK_PADDING", 16, min_val=0)
# Maximum width as percentage of video player (keeps watermark proportional, for images only)
WATERMARK_MAX_WIDTH_PERCENT = get_int_env("VLOG_WATERMARK_MAX_WIDTH_PERCENT", 15, min_val=1, max_val=50)
