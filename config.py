import os
from pathlib import Path

# Paths - configurable via environment variables
BASE_DIR = Path(__file__).parent
NAS_STORAGE = Path(os.getenv("VLOG_STORAGE_PATH", "/mnt/nas/vlog-storage"))
VIDEOS_DIR = NAS_STORAGE / os.getenv("VLOG_VIDEOS_SUBDIR", "videos")
UPLOADS_DIR = NAS_STORAGE / os.getenv("VLOG_UPLOADS_SUBDIR", "uploads")
ARCHIVE_DIR = NAS_STORAGE / os.getenv("VLOG_ARCHIVE_SUBDIR", "archive")
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
FFMPEG_TIMEOUT_MULTIPLIER = int(os.getenv("VLOG_FFMPEG_TIMEOUT_MULTIPLIER", "3"))
FFMPEG_TIMEOUT_MINIMUM = int(os.getenv("VLOG_FFMPEG_TIMEOUT_MINIMUM", "300"))
FFMPEG_TIMEOUT_MAXIMUM = int(os.getenv("VLOG_FFMPEG_TIMEOUT_MAXIMUM", "3600"))

# Transcription settings
WHISPER_MODEL = os.getenv("VLOG_WHISPER_MODEL", "medium")
TRANSCRIPTION_ENABLED = os.getenv("VLOG_TRANSCRIPTION_ENABLED", "true").lower() == "true"
TRANSCRIPTION_LANGUAGE = os.getenv("VLOG_TRANSCRIPTION_LANGUAGE", None) or None
TRANSCRIPTION_ON_UPLOAD = os.getenv("VLOG_TRANSCRIPTION_ON_UPLOAD", "true").lower() == "true"
TRANSCRIPTION_COMPUTE_TYPE = os.getenv("VLOG_TRANSCRIPTION_COMPUTE_TYPE", "int8")
TRANSCRIPTION_TIMEOUT = int(os.getenv("VLOG_TRANSCRIPTION_TIMEOUT", "3600"))
AUDIO_EXTRACTION_TIMEOUT = int(os.getenv("VLOG_AUDIO_EXTRACTION_TIMEOUT", "300"))

# Worker settings (event-driven processing)
WORKER_USE_FILESYSTEM_WATCHER = os.getenv("VLOG_WORKER_USE_FILESYSTEM_WATCHER", "true").lower() == "true"
WORKER_FALLBACK_POLL_INTERVAL = int(os.getenv("VLOG_WORKER_FALLBACK_POLL_INTERVAL", "60"))
WORKER_DEBOUNCE_DELAY = float(os.getenv("VLOG_WORKER_DEBOUNCE_DELAY", "1.0"))

# Progress update rate limiting (prevents database overload during transcoding)
PROGRESS_UPDATE_INTERVAL = float(os.getenv("VLOG_PROGRESS_UPDATE_INTERVAL", "5.0"))

# Upload size limits (default 100GB - reasonable for 4K video)
MAX_UPLOAD_SIZE = int(os.getenv("VLOG_MAX_UPLOAD_SIZE", str(100 * 1024 * 1024 * 1024)))  # 100 GB
UPLOAD_CHUNK_SIZE = int(os.getenv("VLOG_UPLOAD_CHUNK_SIZE", str(1024 * 1024)))  # 1 MB chunks

# CORS Configuration
# Set VLOG_CORS_ORIGINS to comma-separated origins, or leave empty/unset to allow same-origin only
# Example: VLOG_CORS_ORIGINS=http://localhost:9000,http://localhost:9001,https://example.com
_cors_origins_env = os.getenv("VLOG_CORS_ORIGINS", "")
CORS_ALLOWED_ORIGINS = [origin.strip() for origin in _cors_origins_env.split(",") if origin.strip()]

# For admin API, defaults to same-machine origins only (9000 and 9001)
_admin_cors_env = os.getenv("VLOG_ADMIN_CORS_ORIGINS", "")
ADMIN_CORS_ALLOWED_ORIGINS = (
    [origin.strip() for origin in _admin_cors_env.split(",") if origin.strip()]
    if _admin_cors_env
    else [f"http://localhost:{PUBLIC_PORT}", f"http://localhost:{ADMIN_PORT}"]
)
