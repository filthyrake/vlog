# VLog Configuration Reference

All configuration is centralized in `config.py`. This document explains each setting.

## Storage Paths

```python
# Project root directory
BASE_DIR = Path(__file__).parent

# NAS storage root
NAS_STORAGE = Path("/mnt/nas/vlog-storage")

# Transcoded video output (HLS files)
VIDEOS_DIR = NAS_STORAGE / "videos"

# Temporary upload storage
UPLOADS_DIR = NAS_STORAGE / "uploads"

# SQLite database (kept local for performance)
DATABASE_PATH = BASE_DIR / "vlog.db"
```

**Notes:**
- Video files are stored on NAS for capacity
- Database is kept local because SQLite performs poorly over network filesystems
- Directories are created automatically on startup

## Server Ports

```python
# Public API (video browsing and playback)
PUBLIC_PORT = 9000

# Admin API (uploads and management)
ADMIN_PORT = 9001
```

## Quality Presets

```python
QUALITY_PRESETS = [
    {"name": "2160p", "height": 2160, "bitrate": "15000k", "audio_bitrate": "192k"},
    {"name": "1440p", "height": 1440, "bitrate": "8000k", "audio_bitrate": "192k"},
    {"name": "1080p", "height": 1080, "bitrate": "5000k", "audio_bitrate": "128k"},
    {"name": "720p", "height": 720, "bitrate": "2500k", "audio_bitrate": "128k"},
    {"name": "480p", "height": 480, "bitrate": "1000k", "audio_bitrate": "96k"},
    {"name": "360p", "height": 360, "bitrate": "600k", "audio_bitrate": "96k"},
]
```

**Behavior:**
- Only generates qualities at or below source resolution
- If source is 1080p, generates: 1080p, 720p, 480p, 360p
- Bitrate values follow YouTube-style guidelines

## HLS Settings

```python
# Duration of each video segment in seconds
HLS_SEGMENT_DURATION = 6
```

**Trade-offs:**
- Shorter segments = faster seeking, more HTTP requests
- Longer segments = fewer requests, slower seeking
- 6 seconds is a good balance for most use cases

## Checkpoint/Resumable Transcoding

```python
# How often to update checkpoint timestamp (seconds)
CHECKPOINT_INTERVAL = 30

# Time before a job is considered stale/crashed (seconds)
JOB_STALE_TIMEOUT = 1800  # 30 minutes

# Maximum retry attempts for failed jobs
MAX_RETRY_ATTEMPTS = 3

# Base delay between retries (doubles each attempt)
RETRY_BACKOFF_BASE = 60  # 60s, 120s, 240s...

# Whether to clean up partial files on failure
CLEANUP_PARTIAL_ON_FAILURE = True

# Preserve completed qualities when retrying
KEEP_COMPLETED_QUALITIES = True
```

**Crash Recovery:**
- Worker periodically checkpoints progress
- On startup, detects jobs not updated within `JOB_STALE_TIMEOUT`
- Resets stale jobs for retry if under `MAX_RETRY_ATTEMPTS`
- Already-completed qualities are preserved if `KEEP_COMPLETED_QUALITIES` is True

## Transcription Settings

```python
# Whisper model size: tiny, base, small, medium, large-v3
WHISPER_MODEL = "medium"

# Enable/disable auto-transcription feature
TRANSCRIPTION_ENABLED = True

# Language: None for auto-detect, or "en", "es", etc.
TRANSCRIPTION_LANGUAGE = None

# Auto-transcribe new uploads when they finish processing
TRANSCRIPTION_ON_UPLOAD = True

# Compute type for faster-whisper: float16, int8, int8_float16
TRANSCRIPTION_COMPUTE_TYPE = "int8"
```

**Model Size Trade-offs:**

| Model | Size | Speed | Accuracy |
|-------|------|-------|----------|
| tiny | 75MB | Fastest | Basic |
| base | 142MB | Fast | Good |
| small | 466MB | Medium | Better |
| medium | 1.5GB | Slow | Great |
| large-v3 | 3GB | Slowest | Best |

**Compute Type:**
- `float16` - GPU with FP16 support
- `int8` - CPU optimized (recommended for CPU-only)
- `int8_float16` - Mixed precision

## Worker Settings

```python
# Use inotify-based file watching instead of polling
WORKER_USE_FILESYSTEM_WATCHER = True

# Fallback poll interval if watcher unavailable (seconds)
WORKER_FALLBACK_POLL_INTERVAL = 60

# Debounce delay after file event (seconds)
WORKER_DEBOUNCE_DELAY = 1.0
```

**Event-Driven Processing:**
- When `WORKER_USE_FILESYSTEM_WATCHER = True`, uses inotify via watchdog
- Immediately detects new uploads without polling
- Falls back to polling if watchdog unavailable
- Debouncing prevents multiple triggers during large file uploads

---

## Environment Variables

The application doesn't use environment variables by default, but you can modify `config.py` to read from them:

```python
import os

WHISPER_MODEL = os.environ.get("VLOG_WHISPER_MODEL", "medium")
PUBLIC_PORT = int(os.environ.get("VLOG_PUBLIC_PORT", 9000))
```

---

## Customization Examples

### Lower Quality for Bandwidth-Limited Servers

```python
QUALITY_PRESETS = [
    {"name": "1080p", "height": 1080, "bitrate": "3000k", "audio_bitrate": "128k"},
    {"name": "720p", "height": 720, "bitrate": "1500k", "audio_bitrate": "96k"},
    {"name": "480p", "height": 480, "bitrate": "800k", "audio_bitrate": "96k"},
]
```

### Disable Transcription

```python
TRANSCRIPTION_ENABLED = False
```

### Local Storage Instead of NAS

```python
NAS_STORAGE = Path("/home/damen/vlog-storage")
VIDEOS_DIR = NAS_STORAGE / "videos"
UPLOADS_DIR = NAS_STORAGE / "uploads"
```

### Faster Transcription (Lower Quality)

```python
WHISPER_MODEL = "tiny"
TRANSCRIPTION_COMPUTE_TYPE = "int8"
```

### More Aggressive Retry

```python
MAX_RETRY_ATTEMPTS = 5
RETRY_BACKOFF_BASE = 30  # Shorter delay
JOB_STALE_TIMEOUT = 900  # 15 minutes
```
