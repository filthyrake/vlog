# VLog Configuration Reference

All configuration is centralized in `config.py`. Every setting can be overridden via environment variables with the `VLOG_` prefix.

## Environment Variables

All settings support environment variable configuration. Set these in your shell, `.env` file, or systemd service files.

### Storage Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_STORAGE_PATH` | `/mnt/nas/vlog-storage` | NAS storage root |
| `VLOG_VIDEOS_SUBDIR` | `videos` | Subdirectory for transcoded HLS output |
| `VLOG_UPLOADS_SUBDIR` | `uploads` | Subdirectory for temporary uploads |
| `VLOG_ARCHIVE_SUBDIR` | `archive` | Subdirectory for soft-deleted videos |
| `VLOG_DATABASE_PATH` | `./vlog.db` | SQLite database file path |

**Notes:**
- Video files are stored on NAS for capacity
- Database is kept local because SQLite performs poorly over network filesystems
- Directories are created automatically on startup (except in test mode)

### Server Ports

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_PUBLIC_PORT` | `9000` | Public API port (video browsing/playback) |
| `VLOG_ADMIN_PORT` | `9001` | Admin API port (uploads/management) |

### Soft-Delete Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_ARCHIVE_RETENTION_DAYS` | `30` | Days to keep soft-deleted videos before permanent deletion |

### Quality Presets

Defined in `config.py` (not configurable via env vars):

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

### HLS Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_HLS_SEGMENT_DURATION` | `6` | Duration of each video segment in seconds |

**Trade-offs:**
- Shorter segments = faster seeking, more HTTP requests
- Longer segments = fewer requests, slower seeking
- 6 seconds is a good balance for most use cases

### Checkpoint/Resumable Transcoding

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_CHECKPOINT_INTERVAL` | `30` | How often to update checkpoint timestamp (seconds) |
| `VLOG_JOB_STALE_TIMEOUT` | `1800` | Time before job is considered stale/crashed (seconds) |
| `VLOG_MAX_RETRY_ATTEMPTS` | `3` | Maximum retry attempts for failed jobs |
| `VLOG_RETRY_BACKOFF_BASE` | `60` | Base delay between retries (doubles each attempt) |
| `VLOG_CLEANUP_PARTIAL_ON_FAILURE` | `true` | Whether to clean up partial files on failure |
| `VLOG_KEEP_COMPLETED_QUALITIES` | `true` | Preserve completed qualities when retrying |

**Crash Recovery:**
- Worker periodically checkpoints progress
- On startup, detects jobs not updated within `JOB_STALE_TIMEOUT`
- Resets stale jobs for retry if under `MAX_RETRY_ATTEMPTS`
- Already-completed qualities are preserved if `KEEP_COMPLETED_QUALITIES` is true

### FFmpeg Timeout Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_FFMPEG_TIMEOUT_MULTIPLIER` | `3` | Multiply video duration by this for timeout |
| `VLOG_FFMPEG_TIMEOUT_MINIMUM` | `300` | Minimum timeout in seconds (5 min) |
| `VLOG_FFMPEG_TIMEOUT_MAXIMUM` | `3600` | Maximum timeout in seconds (1 hour) |

Timeout calculation: `min(max(duration * multiplier, minimum), maximum)`

### Transcription Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_WHISPER_MODEL` | `medium` | Whisper model size: tiny, base, small, medium, large-v3 |
| `VLOG_TRANSCRIPTION_ENABLED` | `true` | Enable/disable auto-transcription feature |
| `VLOG_TRANSCRIPTION_LANGUAGE` | (none) | Language code (e.g., "en") or empty for auto-detect |
| `VLOG_TRANSCRIPTION_ON_UPLOAD` | `true` | Auto-transcribe new uploads when ready |
| `VLOG_TRANSCRIPTION_COMPUTE_TYPE` | `int8` | Compute type: float16, int8, int8_float16 |
| `VLOG_TRANSCRIPTION_TIMEOUT` | `3600` | Transcription timeout in seconds |
| `VLOG_AUDIO_EXTRACTION_TIMEOUT` | `300` | Audio extraction timeout in seconds |

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

### Worker Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_WORKER_USE_FILESYSTEM_WATCHER` | `true` | Use inotify-based file watching |
| `VLOG_WORKER_FALLBACK_POLL_INTERVAL` | `60` | Fallback poll interval if watcher unavailable (seconds) |
| `VLOG_WORKER_DEBOUNCE_DELAY` | `1.0` | Debounce delay after file event (seconds) |
| `VLOG_PROGRESS_UPDATE_INTERVAL` | `5.0` | Rate limit for database progress updates (seconds) |

**Event-Driven Processing:**
- When `WORKER_USE_FILESYSTEM_WATCHER = true`, uses inotify via watchdog
- Immediately detects new uploads without polling
- Falls back to polling if watchdog unavailable
- Debouncing prevents multiple triggers during large file uploads

### Upload Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_MAX_UPLOAD_SIZE` | `107374182400` | Maximum upload size in bytes (100 GB) |
| `VLOG_UPLOAD_CHUNK_SIZE` | `1048576` | Upload chunk size in bytes (1 MB) |

### CORS Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_CORS_ORIGINS` | (none) | Comma-separated allowed origins for public API |
| `VLOG_ADMIN_CORS_ORIGINS` | `*` | Comma-separated allowed origins for admin API |

**Examples:**
```bash
# Allow specific origins for public API
VLOG_CORS_ORIGINS=http://localhost:9000,https://videos.example.com

# Restrict admin API to internal network
VLOG_ADMIN_CORS_ORIGINS=http://10.0.10.1:9001,http://192.168.1.100:9001
```

**Notes:**
- Empty `VLOG_CORS_ORIGINS` = same-origin only
- Admin API defaults to `*` since it should only be accessible internally

### Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_RATE_LIMIT_ENABLED` | `true` | Enable/disable rate limiting |
| `VLOG_RATE_LIMIT_PUBLIC_DEFAULT` | `100/minute` | Default limit for public API endpoints |
| `VLOG_RATE_LIMIT_PUBLIC_VIDEOS_LIST` | `60/minute` | Limit for video listing endpoint |
| `VLOG_RATE_LIMIT_PUBLIC_ANALYTICS` | `120/minute` | Limit for analytics endpoints |
| `VLOG_RATE_LIMIT_ADMIN_DEFAULT` | `200/minute` | Default limit for admin API endpoints |
| `VLOG_RATE_LIMIT_ADMIN_UPLOAD` | `10/hour` | Limit for upload endpoint |
| `VLOG_RATE_LIMIT_STORAGE_URL` | `memory://` | Storage backend URL |

**Rate Limit Format:** `count/period` where period is `second`, `minute`, `hour`, or `day`

**Storage Backends:**
- `memory://` - In-memory storage (per-process, resets on restart)
- `redis://localhost:6379` - Redis storage (shared across processes)

---

## Customization Examples

### Lower Bandwidth Server

Reduce bitrates for bandwidth-constrained environments:

```python
# Edit config.py
QUALITY_PRESETS = [
    {"name": "1080p", "height": 1080, "bitrate": "3000k", "audio_bitrate": "128k"},
    {"name": "720p", "height": 720, "bitrate": "1500k", "audio_bitrate": "96k"},
    {"name": "480p", "height": 480, "bitrate": "800k", "audio_bitrate": "96k"},
]
```

### Disable Features

```bash
# Disable transcription
VLOG_TRANSCRIPTION_ENABLED=false

# Disable rate limiting
VLOG_RATE_LIMIT_ENABLED=false
```

### Local Storage Instead of NAS

```bash
VLOG_STORAGE_PATH=/home/user/vlog-storage
```

### Faster Transcription (Lower Quality)

```bash
VLOG_WHISPER_MODEL=tiny
VLOG_TRANSCRIPTION_COMPUTE_TYPE=int8
```

### More Aggressive Retry

```bash
VLOG_MAX_RETRY_ATTEMPTS=5
VLOG_RETRY_BACKOFF_BASE=30
VLOG_JOB_STALE_TIMEOUT=900
```

### Production with Redis Rate Limiting

```bash
VLOG_RATE_LIMIT_STORAGE_URL=redis://localhost:6379
VLOG_RATE_LIMIT_PUBLIC_DEFAULT=200/minute
```

---

## Test Mode

Set `VLOG_TEST_MODE=1` to:
- Skip NAS directory creation
- Use temporary directories for tests
- Required for CI/CD environments
