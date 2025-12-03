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
