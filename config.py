from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
NAS_STORAGE = Path("/mnt/nas/vlog-storage")
VIDEOS_DIR = NAS_STORAGE / "videos"
UPLOADS_DIR = NAS_STORAGE / "uploads"
DATABASE_PATH = BASE_DIR / "vlog.db"  # Keep DB local for performance

# Ensure directories exist
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Server ports
PUBLIC_PORT = 9000
ADMIN_PORT = 9001

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
HLS_SEGMENT_DURATION = 6  # seconds

# Checkpoint/resumable transcoding settings
CHECKPOINT_INTERVAL = 30          # seconds between checkpoint updates
JOB_STALE_TIMEOUT = 1800          # seconds (30 min) before job considered stale
MAX_RETRY_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 60           # seconds, doubles each retry
CLEANUP_PARTIAL_ON_FAILURE = True
KEEP_COMPLETED_QUALITIES = True   # on retry, don't re-transcode completed qualities

# Transcription settings
WHISPER_MODEL = "medium"           # tiny, base, small, medium, large-v3
TRANSCRIPTION_ENABLED = True       # Enable/disable auto-transcription
TRANSCRIPTION_LANGUAGE = None      # None for auto-detect, or "en", "es", etc.
TRANSCRIPTION_ON_UPLOAD = True     # Auto-transcribe new uploads
TRANSCRIPTION_COMPUTE_TYPE = "int8"  # float16, int8, int8_float16 (for faster-whisper)
