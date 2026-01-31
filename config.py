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
    env_var_name: str,
    default_value: int,
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
    value = os.getenv(env_var_name)
    if value is None:
        # Environment variable not set; use default without validation
        return default_value

    try:
        result = int(value)
    except ValueError:
        logger.warning(f"Invalid {env_var_name}='{value}', using default {default_value}")
        return default_value

    # Range validation (only applied to user-provided values)
    if min_val is not None and result < min_val:
        logger.warning(f"{env_var_name}={result} is below minimum {min_val}, using default {default_value}")
        return default_value
    if max_val is not None and result > max_val:
        logger.warning(f"{env_var_name}={result} is above maximum {max_val}, using default {default_value}")
        return default_value

    return result


def get_float_env(
    env_var_name: str,
    default_value: float,
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

    value = os.getenv(env_var_name)
    if value is None:
        # Environment variable not set; use default without validation
        return default_value

    try:
        result = float(value)
    except ValueError:
        logger.warning(f"Invalid {env_var_name}='{value}', using default {default_value}")
        return default_value

    # Reject special float values (inf, -inf, nan)
    if math.isinf(result) or math.isnan(result):
        logger.warning(f"Invalid {env_var_name}='{value}' (special float), using default {default_value}")
        return default_value

    # Range validation (only applied to user-provided values)
    if min_val is not None and result < min_val:
        logger.warning(f"{env_var_name}={result} is below minimum {min_val}, using default {default_value}")
        return default_value
    if max_val is not None and result > max_val:
        logger.warning(f"{env_var_name}={result} is above maximum {max_val}, using default {default_value}")
        return default_value

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
# IMPORTANT: Always set VLOG_DATABASE_URL in production with a secure password
_default_db_url = "postgresql://vlog@localhost/vlog"
DATABASE_URL = os.getenv("VLOG_DATABASE_URL", _default_db_url)
if DATABASE_URL == _default_db_url and not os.environ.get("VLOG_TEST_MODE"):
    logger.warning(
        "VLOG_DATABASE_URL not set - using default without password. "
        "Set VLOG_DATABASE_URL with credentials for production use."
    )

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

# =============================================================================
# API Versioning Configuration (Issue #218)
# Supports versioned API routes (e.g., /api/v1/videos) and OpenAPI documentation
# =============================================================================

# Current API version (used in route prefixes and documentation)
# Changing this does NOT affect existing routes - it only sets the "current" version marker
API_VERSION = os.getenv("VLOG_API_VERSION", "v1")

# List of supported API versions (for documentation and deprecation notices)
# Versions listed here will have routes registered and documentation generated
API_SUPPORTED_VERSIONS = ["v1"]

# Enable deprecation notices for older API versions
# When True, deprecated versions include Deprecation and Sunset headers
API_DEPRECATION_NOTICE = os.getenv("VLOG_API_DEPRECATION_NOTICE", "true").lower() in ("true", "1", "yes")

# Sunset date for deprecated API versions (ISO 8601 format)
# Leave empty to not include Sunset header
API_DEPRECATION_SUNSET = os.getenv("VLOG_API_DEPRECATION_SUNSET", "")

# Include legacy unversioned routes (/api/videos) that alias to current version
# Set to false to require explicit version in all API requests
API_INCLUDE_LEGACY_ROUTES = os.getenv("VLOG_API_INCLUDE_LEGACY_ROUTES", "true").lower() in ("true", "1", "yes")

# OpenAPI documentation customization
OPENAPI_TITLE = os.getenv("VLOG_OPENAPI_TITLE", "VLog API")
OPENAPI_DESCRIPTION = os.getenv(
    "VLOG_OPENAPI_DESCRIPTION",
    "Self-hosted video platform API with versioned endpoints",
)
OPENAPI_TERMS_OF_SERVICE = os.getenv("VLOG_OPENAPI_TERMS_OF_SERVICE", "")
OPENAPI_CONTACT_NAME = os.getenv("VLOG_OPENAPI_CONTACT_NAME", "")
OPENAPI_CONTACT_EMAIL = os.getenv("VLOG_OPENAPI_CONTACT_EMAIL", "")
OPENAPI_LICENSE_NAME = os.getenv("VLOG_OPENAPI_LICENSE_NAME", "")
OPENAPI_LICENSE_URL = os.getenv("VLOG_OPENAPI_LICENSE_URL", "")

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

# Admin API secret for authentication (#234) - DEPRECATED
# This is the legacy single-admin secret. Use user authentication (Issue #200) instead.
# When set AND no users exist, enables legacy admin mode for backward compatibility.
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
ADMIN_API_SECRET = os.getenv("VLOG_ADMIN_API_SECRET", "")
# Session expiry for admin UI (hours). Sessions are stored server-side with HTTP-only cookies.
# See: https://github.com/filthyrake/vlog/issues/324
ADMIN_SESSION_EXPIRY_HOURS = get_int_env("VLOG_ADMIN_SESSION_EXPIRY_HOURS", 24, min_val=1)

# =============================================================================
# User Authentication Configuration (Issue #200)
# Multi-user authentication with session-based browser auth, API keys, and RBAC
# =============================================================================

# Session Secret Key (REQUIRED in production)
# Used for signing session tokens and CSRF tokens.
# MUST be set in production - startup fails if not configured.
# Generate with: openssl rand -base64 32
SESSION_SECRET_KEY = os.getenv("VLOG_SESSION_SECRET_KEY", "")

# Session expiry settings
USER_SESSION_EXPIRY_HOURS = get_int_env("VLOG_SESSION_EXPIRY_HOURS", 24, min_val=1)
USER_REFRESH_TOKEN_EXPIRY_DAYS = get_int_env("VLOG_REFRESH_EXPIRY_DAYS", 7, min_val=1)

# Session grace period (seconds) - allow expired sessions briefly during refresh
USER_SESSION_GRACE_SECONDS = get_int_env("VLOG_SESSION_GRACE_SECONDS", 30, min_val=0, max_val=300)

# Maximum concurrent sessions per user
USER_MAX_SESSIONS = get_int_env("VLOG_MAX_SESSIONS_PER_USER", 10, min_val=1, max_val=100)

# Registration mode: "invite" (default), "open", or "disabled"
# - invite: Users can only register via admin-generated invite links
# - open: Anyone can register (use with caution)
# - disabled: No new registrations allowed
REGISTRATION_MODE = os.getenv("VLOG_REGISTRATION_MODE", "invite")

# Invite expiry (days)
INVITE_EXPIRY_DAYS = get_int_env("VLOG_INVITE_EXPIRY_DAYS", 7, min_val=1, max_val=90)

# Password policy
PASSWORD_MIN_LENGTH = get_int_env("VLOG_PASSWORD_MIN_LENGTH", 12, min_val=8, max_val=128)

# Brute force protection
LOGIN_LOCKOUT_THRESHOLD = get_int_env("VLOG_LOCKOUT_THRESHOLD", 5, min_val=1, max_val=20)
LOGIN_LOCKOUT_DURATION_MINUTES = get_int_env("VLOG_LOCKOUT_DURATION_MINUTES", 30, min_val=1, max_val=1440)

# Password reset token expiry (hours)
PASSWORD_RESET_EXPIRY_HOURS = get_int_env("VLOG_PASSWORD_RESET_EXPIRY_HOURS", 1, min_val=1, max_val=24)

# =============================================================================
# OIDC Configuration (Issue #200)
# Generic OpenID Connect for self-hosted identity providers
# =============================================================================

# Enable/disable OIDC authentication
OIDC_ENABLED = os.getenv("VLOG_OIDC_ENABLED", "false").lower() in ("true", "1", "yes")

# Display name for OIDC provider (shown on login button)
OIDC_PROVIDER_NAME = os.getenv("VLOG_OIDC_PROVIDER_NAME", "SSO")

# OIDC Discovery URL (e.g., https://keycloak.example.com/realms/vlog/.well-known/openid-configuration)
OIDC_DISCOVERY_URL = os.getenv("VLOG_OIDC_DISCOVERY_URL", "")

# OIDC Client credentials
OIDC_CLIENT_ID = os.getenv("VLOG_OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.getenv("VLOG_OIDC_CLIENT_SECRET", "")

# OIDC Scopes (comma-separated)
OIDC_SCOPES = os.getenv("VLOG_OIDC_SCOPES", "openid,profile,email")

# Auto-create users on first OIDC login
OIDC_AUTO_CREATE_USERS = os.getenv("VLOG_OIDC_AUTO_CREATE_USERS", "false").lower() in ("true", "1", "yes")

# Default role for auto-created OIDC users
OIDC_DEFAULT_ROLE = os.getenv("VLOG_OIDC_DEFAULT_ROLE", "viewer")

# OIDC request timeout (seconds)
OIDC_TIMEOUT_SECONDS = get_int_env("VLOG_OIDC_TIMEOUT_SECONDS", 10, min_val=1, max_val=60)

# OIDC state expiry (minutes)
OIDC_STATE_EXPIRY_MINUTES = get_int_env("VLOG_OIDC_STATE_EXPIRY_MINUTES", 10, min_val=1, max_val=30)

WORKER_HEARTBEAT_INTERVAL = get_int_env("VLOG_WORKER_HEARTBEAT_INTERVAL", 30, min_val=1)
WORKER_CLAIM_DURATION_MINUTES = get_int_env("VLOG_WORKER_CLAIM_DURATION", 30, min_val=1)
WORKER_POLL_INTERVAL = get_int_env("VLOG_WORKER_POLL_INTERVAL", 10, min_val=1)
WORKER_WORK_DIR = Path(os.getenv("VLOG_WORKER_WORK_DIR", "/tmp/vlog-worker"))
WORKER_OFFLINE_THRESHOLD_MINUTES = get_int_env("VLOG_WORKER_OFFLINE_THRESHOLD", 5, min_val=1)

# Worker health check server port (for K8s liveness/readiness probes)
WORKER_HEALTH_PORT = get_int_env("VLOG_WORKER_HEALTH_PORT", 8080, min_val=1, max_val=65535)

# Streaming format settings for remote workers (Issue #222)
# These override database settings for distributed workers that can't access the DB
STREAMING_FORMAT = os.getenv("VLOG_STREAMING_FORMAT", "cmaf")  # "hls_ts" or "cmaf"
STREAMING_CODEC = os.getenv("VLOG_STREAMING_CODEC", "hevc")  # "h264", "hevc", "av1"
STREAMING_ENABLE_DASH = os.getenv("VLOG_STREAMING_ENABLE_DASH", "true").lower() in ("true", "1", "yes")

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

# Admin API CORS configuration
# Default: Empty list (same-origin only) for defense-in-depth security (Issue #433)
# Only set this if you access admin UI from a different hostname than the API
# Example: VLOG_ADMIN_CORS_ORIGINS=http://192.168.1.100:3000,http://devbox.local:3000
_admin_cors_env = os.getenv("VLOG_ADMIN_CORS_ORIGINS", "")
ADMIN_CORS_ALLOWED_ORIGINS = [origin.strip() for origin in _admin_cors_env.split(",") if origin.strip()]

# Validate CORS origins format (fail fast with clear errors)
def _validate_cors_origins(origins: list, var_name: str) -> None:
    """Validate CORS origin format at startup."""
    has_wildcard = "*" in origins
    has_specific = any(o != "*" for o in origins)

    # Warn about mixing wildcard with specific origins (breaks credentials/session auth)
    if has_wildcard and has_specific:
        logger.warning(
            f"{var_name} contains '*' mixed with specific origins. "
            "This disables credentials (session auth won't work). "
            "Use '*' alone or specific origins only."
        )

    for origin in origins:
        if origin == "*":
            continue
        # Check for common mistakes
        if not origin.startswith(("http://", "https://")):
            raise ValueError(
                f"Invalid CORS origin in {var_name}: '{origin}' - "
                "must start with http:// or https:// (e.g., http://192.168.1.100:3000)"
            )
        if origin.endswith("/"):
            raise ValueError(
                f"Invalid CORS origin in {var_name}: '{origin}' - "
                "must not have trailing slash"
            )

_validate_cors_origins(CORS_ALLOWED_ORIGINS, "VLOG_CORS_ORIGINS")
_validate_cors_origins(ADMIN_CORS_ALLOWED_ORIGINS, "VLOG_ADMIN_CORS_ORIGINS")

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

# Live ingest API limits (per stream key, not per IP since authenticated)
# 300 segments/minute is ~5 segments/second which covers 4-second segments with margin
RATE_LIMIT_LIVE_SEGMENT = os.getenv("VLOG_RATE_LIMIT_LIVE_SEGMENT", "300/minute")
# Global per-IP limit to prevent flood attacks via multiple stream keys
RATE_LIMIT_LIVE_GLOBAL = os.getenv("VLOG_RATE_LIMIT_LIVE_GLOBAL", "1000/minute")

# Storage backend for rate limiting
# Options: "memory" (per-process), or a Redis URL like "redis://localhost:6379"
# SECURITY: In-memory rate limiting doesn't work with multiple API instances.
# If VLOG_REDIS_URL is configured, we default to using it for rate limiting.
# Set VLOG_RATE_LIMIT_STORAGE_URL explicitly to override this behavior.
_explicit_rate_limit_storage = os.getenv("VLOG_RATE_LIMIT_STORAGE_URL")
_redis_url_for_rate_limit = os.getenv("VLOG_REDIS_URL")

if _explicit_rate_limit_storage:
    # Explicit configuration takes precedence
    RATE_LIMIT_STORAGE_URL = _explicit_rate_limit_storage
elif _redis_url_for_rate_limit:
    # Auto-detect: use Redis if VLOG_REDIS_URL is configured
    RATE_LIMIT_STORAGE_URL = _redis_url_for_rate_limit
    if not os.environ.get("VLOG_TEST_MODE"):
        logger.info(
            f"Rate limiting auto-detected Redis from VLOG_REDIS_URL: {_redis_url_for_rate_limit}"
        )
else:
    # Fallback to in-memory (single instance only)
    RATE_LIMIT_STORAGE_URL = "memory://"

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

# TAR Extraction Timeout (Issue #451)
# Timeout for tar extraction operations in seconds
# NAS I/O can hang indefinitely on stale mounts - this prevents thread pool exhaustion
# Default: 600 seconds (10 minutes) - sufficient for large quality archives on slow NAS
TAR_EXTRACTION_TIMEOUT = get_int_env("VLOG_TAR_EXTRACTION_TIMEOUT", 600, min_val=60)

# Orphaned Quality File Cleanup (Issue #450)
# Enable/disable automatic cleanup of orphaned quality directories
ORPHAN_CLEANUP_ENABLED = os.getenv("VLOG_ORPHAN_CLEANUP_ENABLED", "true").lower() in ("true", "1", "yes")
# How often to run orphan cleanup check (seconds, default: 1 hour)
ORPHAN_CLEANUP_INTERVAL = get_int_env("VLOG_ORPHAN_CLEANUP_INTERVAL", 3600, min_val=300)
# Minimum age before a directory is considered orphaned (seconds, default: 24 hours)
# Directories younger than this are not cleaned up - allows time for job completion
ORPHAN_CLEANUP_MIN_AGE = get_int_env("VLOG_ORPHAN_CLEANUP_MIN_AGE", 86400, min_val=3600)

# Audit Logging Configuration
AUDIT_LOG_ENABLED = os.getenv("VLOG_AUDIT_LOG_ENABLED", "true").lower() not in ("false", "0", "no")
AUDIT_LOG_PATH = Path(os.getenv("VLOG_AUDIT_LOG_PATH", "/var/log/vlog/audit.log"))
AUDIT_LOG_LEVEL = os.getenv("VLOG_AUDIT_LOG_LEVEL", "INFO").upper()
# Log rotation settings
# Maximum size of audit log file before rotation (in bytes, default: 10MB)
AUDIT_LOG_MAX_BYTES = get_int_env("VLOG_AUDIT_LOG_MAX_BYTES", 10 * 1024 * 1024, min_val=1024)
# Number of backup files to keep (default: 5, so 6 total files including current)
AUDIT_LOG_BACKUP_COUNT = get_int_env("VLOG_AUDIT_LOG_BACKUP_COUNT", 5, min_val=0)

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

# Streaming Segment Upload (Issue #478)
# When enabled, workers upload segments individually as FFmpeg writes them
# instead of creating a tar.gz after all qualities complete. This eliminates
# event loop blocking during upload and keeps heartbeats alive.
WORKER_STREAMING_UPLOAD = os.getenv("VLOG_WORKER_STREAMING_UPLOAD", "false").lower() in ("true", "1", "yes")

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

# =============================================================================
# Sprite Sheet Configuration (Issue #413 Phase 7B)
# Timeline thumbnail previews on video progress bar hover
# =============================================================================

# Enable/disable sprite sheet generation for timeline thumbnails
SPRITE_SHEET_ENABLED = os.getenv("VLOG_SPRITE_SHEET_ENABLED", "true").lower() in ("true", "1", "yes")

# Seconds between frames in sprite sheet (default: 5 seconds)
# Lower values = more frames = larger file size but smoother preview
SPRITE_SHEET_FRAME_INTERVAL = get_int_env("VLOG_SPRITE_SHEET_FRAME_INTERVAL", 5, min_val=1, max_val=30)

# Width of each thumbnail frame in pixels (height auto-calculated from aspect ratio)
# Smaller = smaller file size, larger = clearer preview
SPRITE_SHEET_THUMBNAIL_WIDTH = get_int_env("VLOG_SPRITE_SHEET_THUMBNAIL_WIDTH", 160, min_val=80, max_val=320)

# Number of frames per row/column in sprite sheet grid (e.g., 10 = 10x10 = 100 frames per sheet)
SPRITE_SHEET_TILE_SIZE = get_int_env("VLOG_SPRITE_SHEET_TILE_SIZE", 10, min_val=5, max_val=20)

# JPEG quality for sprite sheets (lower = smaller files, 60 recommended per reviewer feedback)
# Range: 1-100, with 60-75 being good quality/size tradeoff
SPRITE_SHEET_JPEG_QUALITY = get_int_env("VLOG_SPRITE_SHEET_JPEG_QUALITY", 60, min_val=30, max_val=95)

# Maximum number of sprite sheets per video (prevents excessive storage for very long videos)
# 100 sheets × 100 frames/sheet × 5 sec/frame = ~14 hours of video coverage
SPRITE_SHEET_MAX_SHEETS = get_int_env("VLOG_SPRITE_SHEET_MAX_SHEETS", 100, min_val=1, max_val=1000)

# Timeout multiplier for sprite generation (relative to video duration)
# e.g., 0.5 = sprite gen timeout is 50% of video duration
SPRITE_SHEET_TIMEOUT_MULTIPLIER = get_float_env("VLOG_SPRITE_SHEET_TIMEOUT_MULTIPLIER", 0.5, min_val=0.1, max_val=2.0)

# Minimum and maximum timeout for sprite generation (seconds)
SPRITE_SHEET_TIMEOUT_MINIMUM = get_int_env("VLOG_SPRITE_SHEET_TIMEOUT_MINIMUM", 60, min_val=30)
SPRITE_SHEET_TIMEOUT_MAXIMUM = get_int_env("VLOG_SPRITE_SHEET_TIMEOUT_MAXIMUM", 600, min_val=120)

# Auto-generate sprites after successful transcode (set to false to only generate on-demand)
SPRITE_SHEET_AUTO_GENERATE = os.getenv("VLOG_SPRITE_SHEET_AUTO_GENERATE", "false").lower() in ("true", "1", "yes")

# Memory threshold for sprite generation (percentage of available memory)
# Worker will skip job if available memory is below this percentage
SPRITE_SHEET_MEMORY_THRESHOLD_PERCENT = get_int_env(
    "VLOG_SPRITE_SHEET_MEMORY_THRESHOLD_PERCENT", 20, min_val=5, max_val=50
)

# Maximum video duration (in seconds) that the sprite worker will process
# Videos longer than this will be skipped to prevent OOM (default: 4 hours)
SPRITE_SHEET_MAX_VIDEO_DURATION = get_int_env("VLOG_SPRITE_SHEET_MAX_VIDEO_DURATION", 14400, min_val=600)

# =============================================================================
# Video Download Configuration (Issue #202)
# Allow users to download videos in original or transcoded formats
# =============================================================================

# Master switch for download feature - disabled by default for security
# Set VLOG_DOWNLOADS_ENABLED=true to enable
DOWNLOADS_ENABLED = os.getenv("VLOG_DOWNLOADS_ENABLED", "false").lower() in ("true", "1", "yes")

# Allow downloading original source files (as uploaded)
DOWNLOADS_ALLOW_ORIGINAL = os.getenv("VLOG_DOWNLOADS_ALLOW_ORIGINAL", "false").lower() in ("true", "1", "yes")

# Allow downloading transcoded quality variants (e.g., 1080p, 720p MP4)
DOWNLOADS_ALLOW_TRANSCODED = os.getenv("VLOG_DOWNLOADS_ALLOW_TRANSCODED", "true").lower() in ("true", "1", "yes")

# Rate limit for download requests per IP (requests per hour)
# Set to 0 to disable rate limiting for downloads
DOWNLOADS_RATE_LIMIT_PER_HOUR = get_int_env("VLOG_DOWNLOADS_RATE_LIMIT_PER_HOUR", 10, min_val=0)

# Maximum concurrent downloads per IP (prevents bandwidth abuse)
# This is tracked in-memory so resets on server restart
DOWNLOADS_MAX_CONCURRENT = get_int_env("VLOG_DOWNLOADS_MAX_CONCURRENT", 2, min_val=1, max_val=10)

# =============================================================================
# Playback Configuration (Issue #211)
# Autoplay and "Up Next" settings for video player
# =============================================================================

# Enable autoplay feature globally (can be overridden by user preferences)
AUTOPLAY_ENABLED = os.getenv("VLOG_AUTOPLAY_ENABLED", "true").lower() in ("true", "1", "yes")

# Enable "Up Next" suggestions after video ends
UPNEXT_ENABLED = os.getenv("VLOG_UPNEXT_ENABLED", "true").lower() in ("true", "1", "yes")

# Countdown duration in seconds before autoplay starts (5-30)
AUTOPLAY_COUNTDOWN_SECONDS = get_int_env("VLOG_AUTOPLAY_COUNTDOWN_SECONDS", 10, min_val=5, max_val=30)

# =============================================================================
# Video Embed Configuration (Issue #210)
# Allow embedding videos on external websites with security controls
# =============================================================================

# Master switch for embed feature
EMBED_ENABLED = os.getenv("VLOG_EMBED_ENABLED", "true").lower() in ("true", "1", "yes")

# Domain whitelist for frame-ancestors CSP directive
# SECURITY: Defaults to 'self' only - external embeds blocked by default
# Set to comma-separated domains to allow specific sites: "example.com,blog.example.com"
# This is the recommended secure configuration for controlled embedding
EMBED_ALLOWED_DOMAINS = os.getenv("VLOG_EMBED_ALLOWED_DOMAINS", "'self'")

# Allow embedding on ANY domain (sets frame-ancestors to *)
# SECURITY WARNING: Only enable for truly public video platforms
# When True, overrides EMBED_ALLOWED_DOMAINS
EMBED_ALLOW_ALL_DOMAINS = os.getenv("VLOG_EMBED_ALLOW_ALL_DOMAINS", "false").lower() in ("true", "1", "yes")

# Default autoplay behavior for embeds (can be overridden via query param)
EMBED_DEFAULT_AUTOPLAY = os.getenv("VLOG_EMBED_DEFAULT_AUTOPLAY", "false").lower() in ("true", "1", "yes")

# Minimum seconds of playback before counting as a view (prevents view inflation)
EMBED_MIN_PLAYBACK_FOR_VIEW = get_int_env("VLOG_EMBED_MIN_PLAYBACK_FOR_VIEW", 5, min_val=1, max_val=60)

# Rate limit for embed page requests (higher than normal to allow pages with multiple embeds)
RATE_LIMIT_EMBED = os.getenv("VLOG_RATE_LIMIT_EMBED", "500/minute")

# =============================================================================
# Live Streaming Configuration
# HTTP segment push for live streaming without RTMP/SRT servers
# =============================================================================

# Master switch for live streaming feature
LIVE_ENABLED = os.getenv("VLOG_LIVE_ENABLED", "true").lower() in ("true", "1", "yes")

# Storage path for live stream segments
LIVE_STORAGE_PATH = NAS_STORAGE / os.getenv("VLOG_LIVE_SUBDIR", "live")

# Default DVR window in seconds (2 hours)
LIVE_DEFAULT_DVR_WINDOW = get_int_env("VLOG_LIVE_DEFAULT_DVR_WINDOW", 7200, min_val=60, max_val=86400)

# Stale stream detection threshold in seconds
# If no segment received within this period, stream enters "ending" state
LIVE_STALE_THRESHOLD = get_int_env("VLOG_LIVE_STALE_THRESHOLD", 60, min_val=10, max_val=300)

# Grace period multiplier for stale detection
# Stream finalizes to "ended" after stale_threshold * this multiplier
LIVE_STALE_GRACE_MULTIPLIER = 2

# Maximum segment size (50MB - generous for high bitrate 4K)
LIVE_MAX_SEGMENT_SIZE = get_int_env("VLOG_LIVE_MAX_SEGMENT_SIZE", 50 * 1024 * 1024, min_val=1024 * 1024)

# Rate limiting: segments per minute per stream
LIVE_SEGMENT_RATE_LIMIT = get_int_env("VLOG_LIVE_SEGMENT_RATE_LIMIT", 300, min_val=10)

# Global rate limit: segments per minute per source IP
LIVE_GLOBAL_SEGMENT_RATE_LIMIT = get_int_env("VLOG_LIVE_GLOBAL_SEGMENT_RATE_LIMIT", 1000, min_val=100)

# Maximum concurrent live streams
LIVE_MAX_CONCURRENT_STREAMS = get_int_env("VLOG_LIVE_MAX_CONCURRENT_STREAMS", 10, min_val=1)

# DVR cleanup interval in seconds
LIVE_DVR_CLEANUP_INTERVAL = get_int_env("VLOG_LIVE_DVR_CLEANUP_INTERVAL", 30, min_val=10, max_val=300)

# Batch size for DVR cleanup DELETEs (reduces lock contention)
LIVE_DVR_CLEANUP_BATCH_SIZE = get_int_env("VLOG_LIVE_DVR_CLEANUP_BATCH_SIZE", 10, min_val=1, max_val=100)

# HLS segment duration for live streams (must match FFmpeg -hls_time)
LIVE_HLS_SEGMENT_DURATION = get_int_env("VLOG_LIVE_HLS_SEGMENT_DURATION", 4, min_val=1, max_val=10)

# Number of segments to include in live playlist (sliding window)
LIVE_HLS_PLAYLIST_LENGTH = get_int_env("VLOG_LIVE_HLS_PLAYLIST_LENGTH", 5, min_val=3, max_val=30)

# Allowed quality names for live streams
LIVE_ALLOWED_QUALITIES = frozenset({"2160p", "1440p", "1080p", "720p", "480p", "360p"})

# RTMP ingest URL for display in studio dashboard (Issue #524)
# This is the base URL shown to users for OBS/FFmpeg configuration
# The actual ingest happens via HTTP segment push, but users may use RTMP re-streaming
LIVE_RTMP_INGEST_URL = os.getenv("VLOG_LIVE_RTMP_INGEST_URL", "rtmp://localhost/live")

# Ensure live storage directory exists (skip in test/CI environments)
if not os.environ.get("VLOG_TEST_MODE"):
    try:
        LIVE_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass  # CI environment without NAS access

# =============================================================================
# Structured Logging Configuration (Issue #208)
# JSON logging for production, text format for development
# =============================================================================

# Log format: "json" for production (log aggregation), "text" for development
LOG_FORMAT = os.getenv("VLOG_LOG_FORMAT", "json")

# Default log level for all loggers
LOG_LEVEL = os.getenv("VLOG_LOG_LEVEL", "INFO")

# Module-specific log levels (comma-separated, e.g., "api.auth=DEBUG,worker=WARNING")
LOG_LEVELS = os.getenv("VLOG_LOG_LEVELS", "")

# Optional log file output (in addition to stdout)
# Leave empty to only log to stdout
LOG_FILE = os.getenv("VLOG_LOG_FILE", "")

# Log file rotation settings
LOG_FILE_MAX_BYTES = get_int_env("VLOG_LOG_FILE_MAX_BYTES", 10 * 1024 * 1024, min_val=1024)  # 10 MB default
LOG_FILE_BACKUP_COUNT = get_int_env("VLOG_LOG_FILE_BACKUP_COUNT", 5, min_val=0)

# =============================================================================
# Backup and Restore Configuration (Issue #216)
# Comprehensive backup system with database, file, and S3 support
# =============================================================================

# Master switch for backup feature
BACKUP_ENABLED = os.getenv("VLOG_BACKUP_ENABLED", "true").lower() in ("true", "1", "yes")

# Local backup storage path (default: NAS_STORAGE/backups)
# SECURITY: Path is validated at runtime to prevent path traversal
_backup_path_env = os.getenv("VLOG_BACKUP_PATH", "")
if _backup_path_env:
    BACKUP_PATH = Path(_backup_path_env)
else:
    BACKUP_PATH = NAS_STORAGE / "backups"

# Validate backup path - reject suspicious patterns
def _validate_backup_path(path: Path, var_name: str) -> None:
    """Validate backup path at startup to prevent path traversal attacks."""
    path_str = str(path)
    # Check for path traversal attempts
    if ".." in path_str:
        raise ValueError(
            f"Invalid {var_name}: path contains '..'. "
            "Use an absolute path without parent directory references."
        )
    # Check for shell metacharacters
    dangerous_chars = [";", "|", "&", "$", "`", "(", ")", "{", "}", "<", ">", "\\"]
    for char in dangerous_chars:
        if char in path_str:
            raise ValueError(
                f"Invalid {var_name}: path contains shell metacharacter '{char}'. "
                "Use a simple absolute path."
            )

if _backup_path_env and not os.environ.get("VLOG_TEST_MODE"):
    _validate_backup_path(BACKUP_PATH, "VLOG_BACKUP_PATH")

# Number of backups to retain (older backups are deleted)
BACKUP_RETENTION_COUNT = get_int_env("VLOG_BACKUP_RETENTION_COUNT", 7, min_val=1, max_val=365)

# Include video files in backup (increases backup size significantly)
BACKUP_INCLUDE_VIDEOS = os.getenv("VLOG_BACKUP_INCLUDE_VIDEOS", "false").lower() in ("true", "1", "yes")

# Maximum backup size in GB (prevents runaway storage consumption)
BACKUP_MAX_SIZE_GB = get_int_env("VLOG_BACKUP_MAX_SIZE_GB", 500, min_val=1)

# Timeout settings (seconds)
BACKUP_DB_TIMEOUT = get_int_env("VLOG_BACKUP_DB_TIMEOUT", 3600, min_val=60)  # 1 hour default
BACKUP_S3_TIMEOUT = get_int_env("VLOG_BACKUP_S3_TIMEOUT", 14400, min_val=60)  # 4 hours default

# HMAC signing key for manifest integrity verification
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
# If not set, manifest signing is disabled (not recommended for production)
BACKUP_SIGNING_KEY = os.getenv("VLOG_BACKUP_SIGNING_KEY", "")

# S3-compatible storage configuration
BACKUP_S3_BUCKET = os.getenv("VLOG_BACKUP_S3_BUCKET", "")
BACKUP_S3_PREFIX = os.getenv("VLOG_BACKUP_S3_PREFIX", "vlog-backups/")
BACKUP_S3_REGION = os.getenv("VLOG_BACKUP_S3_REGION", "us-east-1")
# Custom endpoint URL for S3-compatible storage (MinIO, DigitalOcean Spaces, etc.)
BACKUP_S3_ENDPOINT_URL = os.getenv("VLOG_BACKUP_S3_ENDPOINT_URL", "")

# Scheduler configuration
BACKUP_SCHEDULE_ENABLED = os.getenv("VLOG_BACKUP_SCHEDULE_ENABLED", "false").lower() in ("true", "1", "yes")
# Time to run scheduled backups (24-hour format, e.g., "02:00")
BACKUP_SCHEDULE_TIME = os.getenv("VLOG_BACKUP_SCHEDULE_TIME", "02:00")
# Day of week for weekly backups (0=Monday, 6=Sunday), empty for daily
BACKUP_SCHEDULE_DAY = os.getenv("VLOG_BACKUP_SCHEDULE_DAY", "")

# Restore rate limiting (API only, CLI can bypass with --force)
BACKUP_RESTORE_COOLDOWN_SECONDS = get_int_env("VLOG_BACKUP_RESTORE_COOLDOWN_SECONDS", 3600, min_val=0)

# Ensure backup directory exists (skip in test/CI environments)
if not os.environ.get("VLOG_TEST_MODE") and BACKUP_ENABLED:
    try:
        BACKUP_PATH.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass  # CI environment or insufficient permissions
