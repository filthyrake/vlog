import os
from pathlib import Path

# Paths - configurable via environment variables
BASE_DIR = Path(__file__).parent
NAS_STORAGE = Path(os.getenv("VLOG_STORAGE_PATH", "/mnt/nas/vlog-storage"))
VIDEOS_DIR = NAS_STORAGE / os.getenv("VLOG_VIDEOS_SUBDIR", "videos")
UPLOADS_DIR = NAS_STORAGE / os.getenv("VLOG_UPLOADS_SUBDIR", "uploads")
ARCHIVE_DIR = NAS_STORAGE / os.getenv("VLOG_ARCHIVE_SUBDIR", "archive")
DATABASE_PATH = Path(os.getenv("VLOG_DATABASE_PATH", str(BASE_DIR / "vlog.db")))

# Database connection pool settings
# SQLite handles concurrent reads well but serializes writes
# These settings control the async connection pool
DATABASE_POOL_MIN_SIZE = int(os.getenv("VLOG_DATABASE_POOL_MIN_SIZE", "1"))
DATABASE_POOL_MAX_SIZE = int(os.getenv("VLOG_DATABASE_POOL_MAX_SIZE", "10"))

# Ensure directories exist (skip in test/CI environments)
if not os.environ.get("VLOG_TEST_MODE"):
    try:
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass  # CI environment without NAS access

# Soft-delete settings
ARCHIVE_RETENTION_DAYS = int(os.getenv("VLOG_ARCHIVE_RETENTION_DAYS", "30"))

# Server ports
PUBLIC_PORT = int(os.getenv("VLOG_PUBLIC_PORT", "9000"))
ADMIN_PORT = int(os.getenv("VLOG_ADMIN_PORT", "9001"))

# Transcoding quality presets (YouTube-style)
QUALITY_PRESETS = [
    {"name": "2160p", "height": 2160, "bitrate": "15000k", "audio_bitrate": "192k"},
    {"name": "1440p", "height": 1440, "bitrate": "8000k", "audio_bitrate": "192k"},
    {"name": "1080p", "height": 1080, "bitrate": "5000k", "audio_bitrate": "128k"},
    {"name": "720p", "height": 720, "bitrate": "2500k", "audio_bitrate": "128k"},
    {"name": "480p", "height": 480, "bitrate": "1000k", "audio_bitrate": "96k"},
    {"name": "360p", "height": 360, "bitrate": "600k", "audio_bitrate": "96k"},
]

# HLS settings
HLS_SEGMENT_DURATION = int(os.getenv("VLOG_HLS_SEGMENT_DURATION", "6"))

# Checkpoint/resumable transcoding settings
CHECKPOINT_INTERVAL = int(os.getenv("VLOG_CHECKPOINT_INTERVAL", "30"))
JOB_STALE_TIMEOUT = int(os.getenv("VLOG_JOB_STALE_TIMEOUT", "1800"))
MAX_RETRY_ATTEMPTS = int(os.getenv("VLOG_MAX_RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF_BASE = int(os.getenv("VLOG_RETRY_BACKOFF_BASE", "60"))
CLEANUP_PARTIAL_ON_FAILURE = os.getenv("VLOG_CLEANUP_PARTIAL_ON_FAILURE", "true").lower() == "true"
KEEP_COMPLETED_QUALITIES = os.getenv("VLOG_KEEP_COMPLETED_QUALITIES", "true").lower() == "true"

# FFmpeg timeout settings (prevents stuck transcoding jobs)
# Base multiplier applied to video duration (scaled by resolution)
FFMPEG_TIMEOUT_BASE_MULTIPLIER = float(os.getenv("VLOG_FFMPEG_TIMEOUT_BASE_MULTIPLIER", "2.0"))
FFMPEG_TIMEOUT_MINIMUM = int(os.getenv("VLOG_FFMPEG_TIMEOUT_MINIMUM", "300"))
FFMPEG_TIMEOUT_MAXIMUM = int(os.getenv("VLOG_FFMPEG_TIMEOUT_MAXIMUM", "14400"))  # 4 hours

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
TRANSCRIPTION_TIMEOUT = int(os.getenv("VLOG_TRANSCRIPTION_TIMEOUT", "3600"))
AUDIO_EXTRACTION_TIMEOUT = int(os.getenv("VLOG_AUDIO_EXTRACTION_TIMEOUT", "300"))

# Hardware Acceleration Settings (for remote workers with GPUs)
# VLOG_HWACCEL_TYPE: "auto" (detect), "nvidia", "intel", or "none"
HWACCEL_TYPE = os.getenv("VLOG_HWACCEL_TYPE", "auto")
# Preferred codec: "h264" (max compatibility), "hevc" (smaller files), "av1" (best compression)
HWACCEL_PREFERRED_CODEC = os.getenv("VLOG_HWACCEL_PREFERRED_CODEC", "h264")
# Fall back to CPU encoding if GPU encoding fails
HWACCEL_FALLBACK_TO_CPU = os.getenv("VLOG_HWACCEL_FALLBACK_TO_CPU", "true").lower() == "true"
# Max concurrent encode sessions (NVIDIA consumer GPUs have limits: RTX 3090=3, RTX 4090=5)
HWACCEL_MAX_CONCURRENT_SESSIONS = int(os.getenv("VLOG_HWACCEL_MAX_SESSIONS", "3"))
# Intel VAAPI device path (auto-detected if empty)
HWACCEL_VAAPI_DEVICE = os.getenv("VLOG_HWACCEL_VAAPI_DEVICE", "")

# Worker settings (event-driven processing for local worker)
WORKER_USE_FILESYSTEM_WATCHER = os.getenv("VLOG_WORKER_USE_FILESYSTEM_WATCHER", "true").lower() == "true"
WORKER_FALLBACK_POLL_INTERVAL = int(os.getenv("VLOG_WORKER_FALLBACK_POLL_INTERVAL", "60"))
WORKER_DEBOUNCE_DELAY = float(os.getenv("VLOG_WORKER_DEBOUNCE_DELAY", "1.0"))

# Worker API service settings (for distributed workers)
WORKER_API_PORT = int(os.getenv("VLOG_WORKER_API_PORT", "9002"))

# Remote worker client settings
WORKER_API_URL = os.getenv("VLOG_WORKER_API_URL", "http://localhost:9002")
WORKER_API_KEY = os.getenv("VLOG_WORKER_API_KEY", "")
WORKER_HEARTBEAT_INTERVAL = int(os.getenv("VLOG_WORKER_HEARTBEAT_INTERVAL", "30"))
WORKER_CLAIM_DURATION_MINUTES = int(os.getenv("VLOG_WORKER_CLAIM_DURATION", "30"))
WORKER_POLL_INTERVAL = int(os.getenv("VLOG_WORKER_POLL_INTERVAL", "10"))
WORKER_WORK_DIR = Path(os.getenv("VLOG_WORKER_WORK_DIR", "/tmp/vlog-worker"))
WORKER_OFFLINE_THRESHOLD_MINUTES = int(os.getenv("VLOG_WORKER_OFFLINE_THRESHOLD", "2"))

# Progress update rate limiting (prevents database overload during transcoding)
PROGRESS_UPDATE_INTERVAL = float(os.getenv("VLOG_PROGRESS_UPDATE_INTERVAL", "5.0"))

# Upload size limits (default 100GB - reasonable for 4K video)
MAX_UPLOAD_SIZE = int(os.getenv("VLOG_MAX_UPLOAD_SIZE", str(100 * 1024 * 1024 * 1024)))  # 100 GB
UPLOAD_CHUNK_SIZE = int(os.getenv("VLOG_UPLOAD_CHUNK_SIZE", str(1024 * 1024)))  # 1 MB chunks

# HLS archive extraction limits (prevent tar bomb attacks)
# Max number of files in an HLS archive (master playlist + quality playlists + segments + thumbnail)
# 6 qualities × 1200 segments (2hrs @ 6s each) + playlists + thumbnails = ~7200 files for 2hr video
# Using 50,000 as generous default to support very long videos (8+ hours)
MAX_HLS_ARCHIVE_FILES = int(os.getenv("VLOG_MAX_HLS_ARCHIVE_FILES", "50000"))
# Max total extracted size (200 GB - generous for long 4K HLS output with all qualities)
MAX_HLS_ARCHIVE_SIZE = int(os.getenv("VLOG_MAX_HLS_ARCHIVE_SIZE", str(200 * 1024 * 1024 * 1024)))
# Max size per individual file (500 MB - largest reasonable .ts segment at high bitrate)
MAX_HLS_SINGLE_FILE_SIZE = int(os.getenv("VLOG_MAX_HLS_SINGLE_FILE_SIZE", str(500 * 1024 * 1024)))

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

# Storage backend for rate limiting
# Options: "memory" (default, per-process), or a Redis URL like "redis://localhost:6379"
RATE_LIMIT_STORAGE_URL = os.getenv("VLOG_RATE_LIMIT_STORAGE_URL", "memory://")

# Trusted proxy configuration for X-Forwarded-For header
# Only trust X-Forwarded-For when request comes from these IPs
# Set VLOG_TRUSTED_PROXIES to comma-separated IPs (e.g., "127.0.0.1,10.0.0.1,192.168.1.1")
# If empty (default), X-Forwarded-For is never trusted (prevents rate limit bypass)
_trusted_proxies_env = os.getenv("VLOG_TRUSTED_PROXIES", "")
TRUSTED_PROXIES = set(ip.strip() for ip in _trusted_proxies_env.split(",") if ip.strip())

# Analytics Caching Configuration
# Set to "0" or "false" to disable analytics caching
ANALYTICS_CACHE_ENABLED = os.getenv("VLOG_ANALYTICS_CACHE_ENABLED", "true").lower() not in ("false", "0", "no")

# Cache TTL in seconds (default: 60 seconds)
ANALYTICS_CACHE_TTL = int(os.getenv("VLOG_ANALYTICS_CACHE_TTL", "60"))

# Client-side cache max-age in seconds (default: 60 seconds)
# This controls the Cache-Control header sent to clients
ANALYTICS_CLIENT_CACHE_MAX_AGE = int(os.getenv("VLOG_ANALYTICS_CLIENT_CACHE_MAX_AGE", "60"))
