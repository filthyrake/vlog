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

**Notes:**
- Video files are stored on NAS for capacity
- Directories are created automatically on startup (except in test mode)

### Database Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_DATABASE_URL` | `postgresql://vlog:vlog_password@localhost/vlog` | PostgreSQL connection URL |
| `VLOG_DATABASE_PATH` | `./vlog.db` | Legacy SQLite path (for migration scripts only) |

**Notes:**
- PostgreSQL is the default and recommended database
- For SQLite (not recommended for production): `VLOG_DATABASE_URL=sqlite:///./vlog.db`
- Connection URL format: `postgresql://user:password@host:port/database`

### Server Ports

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_PUBLIC_PORT` | `9000` | Public API port (video browsing/playback) |
| `VLOG_ADMIN_PORT` | `9001` | Admin API port (uploads/management) |
| `VLOG_WORKER_API_PORT` | `9002` | Worker API port (remote worker coordination) |
| `VLOG_WORKER_HEALTH_PORT` | `8080` | HTTP health server port for K8s liveness/readiness probes |

### Admin Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_ADMIN_API_SECRET` | (none) | Secret for API key authentication (X-Admin-Secret header) |
| `VLOG_ADMIN_SESSION_EXPIRY_HOURS` | `24` | Browser session expiry in hours |

**Admin Authentication:**
- When `VLOG_ADMIN_API_SECRET` is set, all admin API endpoints require authentication
- CLI commands automatically use this secret when set
- Browser sessions use HTTP-only cookies for security

Generate a secret:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

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

### Thumbnail Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_MAX_THUMBNAIL_SIZE` | `10485760` | Maximum custom thumbnail upload size in bytes (10MB) |
| `VLOG_THUMBNAIL_WIDTH` | `640` | Thumbnail width in pixels (height auto-calculated) |

**Custom Thumbnails:**
- Users can upload custom thumbnails (JPEG, PNG, WebP)
- Images are converted to JPEG at the configured width
- Alternatively, users can select a frame from the video at any timestamp

### Hardware Acceleration

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_HWACCEL_TYPE` | `auto` | Hardware acceleration type: auto, nvidia, intel, none |
| `VLOG_HWACCEL_PREFERRED_CODEC` | `h264` | Preferred codec: h264, hevc, av1 |
| `VLOG_HWACCEL_FALLBACK_TO_CPU` | `true` | Fall back to CPU if GPU encoding fails |
| `VLOG_HWACCEL_MAX_SESSIONS` | `3` | Maximum concurrent GPU encoding sessions |
| `VLOG_HWACCEL_VAAPI_DEVICE` | (auto) | VAAPI device path (Intel GPU, auto-detected if empty) |

**GPU Encoding:**
- **NVIDIA NVENC:** Requires nvidia-container-toolkit in Kubernetes
- **Intel VAAPI:** Requires Intel GPU device plugin, works with Arc/QuickSync
- Consumer NVIDIA GPUs have session limits (RTX 3090: 3 sessions, RTX 4090: 5 sessions)

**Auto-detection:**
When `VLOG_HWACCEL_TYPE=auto`, the worker probes for available GPUs in order:
1. NVIDIA (checks for nvidia-smi)
2. Intel VAAPI (checks for /dev/dri/renderD*)
3. Falls back to CPU encoding

### Checkpoint/Resumable Transcoding

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_CHECKPOINT_INTERVAL` | `30` | How often to update checkpoint timestamp (seconds) |
| `VLOG_JOB_STALE_TIMEOUT` | `1800` | Time before job is considered stale/crashed (seconds) |
| `VLOG_MAX_RETRY_ATTEMPTS` | `3` | Maximum retry attempts for failed jobs |
| `VLOG_RETRY_BACKOFF_BASE` | `60` | Base delay between retries (doubles each attempt) |
| `VLOG_CLEANUP_PARTIAL_ON_FAILURE` | `true` | Whether to clean up partial files on failure |
| `VLOG_CLEANUP_SOURCE_ON_PERMANENT_FAILURE` | `true` | Delete source file after max retries exceeded |
| `VLOG_KEEP_COMPLETED_QUALITIES` | `true` | Preserve completed qualities when retrying |

**Crash Recovery:**
- Worker periodically checkpoints progress
- On startup, detects jobs not updated within `JOB_STALE_TIMEOUT`
- Resets stale jobs for retry if under `MAX_RETRY_ATTEMPTS`
- Already-completed qualities are preserved if `KEEP_COMPLETED_QUALITIES` is true

### FFmpeg Timeout Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_FFMPEG_TIMEOUT_BASE_MULTIPLIER` | `2.0` | Base multiplier applied to video duration |
| `VLOG_FFMPEG_TIMEOUT_MINIMUM` | `300` | Minimum timeout in seconds (5 min) |
| `VLOG_FFMPEG_TIMEOUT_MAXIMUM` | `14400` | Maximum timeout in seconds (4 hours) |

Timeout calculation includes per-resolution multipliers that adjust based on quality level. Lower resolutions encode faster, higher resolutions (4K) need more time.

**Per-Resolution Multipliers (applied on top of base):**
- 360p: 1.0x
- 480p: 1.25x
- 720p: 1.5x
- 1080p: 2.0x
- 1440p: 2.5x
- 2160p (4K): 3.5x

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

### Local Worker Settings

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

### Watermark Settings

Client-side watermark overlay on the video player.

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_WATERMARK_ENABLED` | `false` | Enable/disable watermark overlay |
| `VLOG_WATERMARK_TYPE` | `image` | Watermark type: image or text |
| `VLOG_WATERMARK_IMAGE` | (none) | Path to watermark image file (PNG, JPEG, WebP, SVG, GIF) |
| `VLOG_WATERMARK_TEXT` | (none) | Text to display as watermark (when type=text) |
| `VLOG_WATERMARK_TEXT_SIZE` | `16` | Text watermark font size in pixels (8-72) |
| `VLOG_WATERMARK_TEXT_COLOR` | `white` | Text watermark color (CSS color value) |
| `VLOG_WATERMARK_POSITION` | `bottom-right` | Position: top-left, top-right, bottom-left, bottom-right, center |
| `VLOG_WATERMARK_OPACITY` | `0.5` | Watermark opacity (0.0-1.0) |
| `VLOG_WATERMARK_PADDING` | `16` | Padding from edge in pixels |
| `VLOG_WATERMARK_MAX_WIDTH_PERCENT` | `15` | Max width as percentage of video (1-50, images only) |

**Watermark Types:**
- **Image:** PNG, JPEG, WebP, SVG, or GIF file. Scales to max width while preserving aspect ratio.
- **Text:** Rendered with configured font size and color. Good for copyright notices.

**Example Configuration:**
```bash
# Image watermark
VLOG_WATERMARK_ENABLED=true
VLOG_WATERMARK_TYPE=image
VLOG_WATERMARK_IMAGE=watermark.png
VLOG_WATERMARK_POSITION=bottom-right
VLOG_WATERMARK_OPACITY=0.7

# Text watermark
VLOG_WATERMARK_ENABLED=true
VLOG_WATERMARK_TYPE=text
VLOG_WATERMARK_TEXT=© 2025 MyBrand
VLOG_WATERMARK_TEXT_SIZE=14
VLOG_WATERMARK_TEXT_COLOR=rgba(255,255,255,0.8)
```

### Remote Worker / Worker API Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_WORKER_API_URL` | `http://localhost:9002` | Worker API URL for remote workers |
| `VLOG_WORKER_API_KEY` | (none) | API key for remote worker authentication (required) |
| `VLOG_WORKER_ADMIN_SECRET` | (none) | Secret for worker admin endpoints (register, list, revoke) |
| `VLOG_WORKER_HEARTBEAT_INTERVAL` | `30` | Heartbeat interval in seconds |
| `VLOG_WORKER_POLL_INTERVAL` | `10` | Job polling interval in seconds |
| `VLOG_WORKER_WORK_DIR` | `/tmp/vlog-worker` | Working directory for downloads/transcoding |
| `VLOG_WORKER_JOB_TIMEOUT` | `7200` | Maximum job duration before expiration (seconds) |

**Remote Worker Architecture:**
- Workers register with the Worker API and receive an API key
- Workers poll for available jobs via `POST /api/worker/claim`
- Source files are downloaded via HTTP from the Worker API
- HLS output is uploaded as a tar.gz archive
- Progress updates are sent periodically during transcoding
- Heartbeats maintain worker status for health monitoring

**API Key Security:**
- Keys are generated on worker registration
- Stored as SHA-256 hashes in the database
- Each worker has a unique key that can be revoked
- Prefix-based lookup for efficient authentication

**Admin Secret Authentication:**
Worker management endpoints (register, list, revoke) require the `X-Admin-Secret` header with the value of `VLOG_WORKER_ADMIN_SECRET`. Generate a secure secret:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

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
VLOG_ADMIN_CORS_ORIGINS=http://your-server:9001,http://192.168.1.100:9001
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
| `VLOG_RATE_LIMIT_WORKER_DEFAULT` | `300/minute` | Default limit for worker API endpoints |
| `VLOG_RATE_LIMIT_WORKER_REGISTER` | `5/hour` | Limit for worker registration |
| `VLOG_RATE_LIMIT_WORKER_PROGRESS` | `600/minute` | Limit for progress update endpoints |
| `VLOG_RATE_LIMIT_STORAGE_URL` | `memory://` | Storage backend URL |

**Rate Limit Format:** `count/period` where period is `second`, `minute`, `hour`, or `day`

**Storage Backends:**
- `memory://` - In-memory storage (per-process, resets on restart)
- `redis://localhost:6379` - Redis storage (shared across processes)

**⚠️ Multi-Instance Deployments:**

The default in-memory storage does not work correctly when running multiple API instances behind a load balancer. Each process maintains its own rate limit counter, so:
- With N instances, effective rate limit is N × configured limit
- Users can bypass rate limits by hitting different instances

For production deployments with multiple instances, you **must** use Redis:

```bash
# Install redis Python package
pip install redis

# Configure Redis backend
export VLOG_RATE_LIMIT_STORAGE_URL=redis://localhost:6379/0
```

The API will log a warning at startup if rate limiting is enabled with in-memory storage.

### Redis Configuration

Optional Redis integration for job queue and real-time updates.

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_REDIS_URL` | (empty) | Redis connection URL (empty = disabled) |
| `VLOG_REDIS_POOL_SIZE` | `10` | Connection pool size |
| `VLOG_REDIS_SOCKET_TIMEOUT` | `5.0` | Socket timeout in seconds |
| `VLOG_REDIS_SOCKET_CONNECT_TIMEOUT` | `5.0` | Connection timeout in seconds |
| `VLOG_REDIS_HEALTH_CHECK_INTERVAL` | `30` | Health check interval in seconds |

**Job Queue Mode:**

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_JOB_QUEUE_MODE` | `database` | Queue mode: database, redis, or hybrid |

- `database` - Poll database for jobs (always works)
- `redis` - Use Redis Streams for instant dispatch (requires `REDIS_URL`)
- `hybrid` - Use Redis when available, fall back to database

**Redis Streams Settings:**

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_REDIS_STREAM_MAX_LEN` | `10000` | Maximum stream length |
| `VLOG_REDIS_CONSUMER_GROUP` | `vlog-workers` | Consumer group name |
| `VLOG_REDIS_CONSUMER_BLOCK_MS` | `5000` | Block timeout for XREADGROUP |
| `VLOG_REDIS_PENDING_TIMEOUT_MS` | `300000` | Pending message timeout (5 min) |

**Pub/Sub Settings:**

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_REDIS_PUBSUB_PREFIX` | `vlog` | Channel name prefix |

**Enable Redis:**

```bash
# Start Redis
sudo systemctl enable --now redis

# Configure VLog
export VLOG_REDIS_URL="redis://localhost:6379"
export VLOG_JOB_QUEUE_MODE="hybrid"  # or "redis" for Redis-only
```

### SSE (Server-Sent Events) Settings

Real-time progress updates in the admin UI.

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_SSE_HEARTBEAT_INTERVAL` | `30` | Heartbeat interval in seconds |
| `VLOG_SSE_RECONNECT_TIMEOUT_MS` | `3000` | Client reconnect timeout |

**Note:** SSE uses Redis Pub/Sub when available, otherwise falls back to database polling.

### Parallel Quality Encoding

Encode multiple quality variants simultaneously to reduce transcoding time.

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_PARALLEL_QUALITIES` | `1` | Number of qualities to encode in parallel |
| `VLOG_PARALLEL_QUALITIES_AUTO` | `true` | Auto-detect based on GPU capabilities |

**Behavior:**
- When `AUTO=true` and GPU is detected: uses `min(3, gpu.max_sessions - 1)`
- When `AUTO=true` but no GPU: uses `PARALLEL_QUALITIES` value
- Recommended: `PARALLEL_QUALITIES=3` for GPUs
- ~2x speedup on GPUs with concurrent encoding support

### Error Message Truncation

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_ERROR_SUMMARY_MAX_LENGTH` | `100` | Brief error summaries |
| `VLOG_ERROR_DETAIL_MAX_LENGTH` | `500` | Detailed error messages |
| `VLOG_ERROR_LOG_MAX_LENGTH` | `2000` | Full error logs |

---

## Multi-Instance Deployment Notes

When running multiple API instances (e.g., behind a load balancer):

### Analytics Cache

By default, the analytics cache uses in-memory storage (per-process). For consistent analytics across instances, use Redis:
```bash
VLOG_ANALYTICS_CACHE_STORAGE_URL=redis://localhost:6379
```

**Options:**
- `VLOG_ANALYTICS_CACHE_ENABLED=false` - Disable caching entirely (higher database load)
- `VLOG_ANALYTICS_CACHE_TTL=60` - Cache TTL in seconds (default: 60)
- `VLOG_ANALYTICS_CACHE_STORAGE_URL=memory://` - In-memory cache (default, per-process)
- `VLOG_ANALYTICS_CACHE_STORAGE_URL=redis://localhost:6379` - Redis-backed shared cache

With in-memory cache, different instances may show slightly different analytics counts until caches expire. With Redis, all instances share the same cache state.

### Rate Limiting

By default, rate limiting uses in-memory storage (per-process). For consistent rate limiting across instances:
```bash
VLOG_RATE_LIMIT_STORAGE_URL=redis://localhost:6379
```

### Database

PostgreSQL fully supports concurrent reads and writes. No special configuration needed for multi-instance deployments.

### Redis for Real-Time Features

For SSE endpoints to work consistently across instances, Redis is required:
```bash
VLOG_REDIS_URL=redis://localhost:6379
VLOG_JOB_QUEUE_MODE=hybrid
```

### Worker Admin Endpoints

Worker management endpoints require `VLOG_WORKER_ADMIN_SECRET` to be configured. All instances must share the same secret value.

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

## Security Settings

### Trusted Proxies

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_TRUSTED_PROXIES` | (empty) | Comma-separated list of trusted proxy IPs |

Only trust X-Forwarded-For header from these IPs. If empty, X-Forwarded-For is never trusted (prevents rate limit bypass).

```bash
VLOG_TRUSTED_PROXIES=127.0.0.1,10.0.0.1
```

### Cookie Security

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_SECURE_COOKIES` | `true` | Use secure cookies (HTTPS only) |

Set to `false` for local development without HTTPS.

---

## HLS Archive Settings

Security limits for worker upload tar.gz archives:

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_MAX_HLS_ARCHIVE_FILES` | `50000` | Max number of files in HLS archive |
| `VLOG_MAX_HLS_ARCHIVE_SIZE` | `200GB` | Max total extracted size |
| `VLOG_MAX_HLS_SINGLE_FILE_SIZE` | `500MB` | Max size per individual file |

These limits prevent tar bomb attacks during worker uploads.

---

## Storage Health Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_STORAGE_CHECK_TIMEOUT` | `2` | Timeout for health check storage test (seconds) |

Reduced from 5 to 2 for faster failure detection on stale NFS mounts.

---

## Audit Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_AUDIT_LOG_ENABLED` | `true` | Enable audit logging |
| `VLOG_AUDIT_LOG_PATH` | `/var/log/vlog/audit.log` | Path to audit log file |
| `VLOG_AUDIT_LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |

---

## Worker Monitoring

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_WORKER_OFFLINE_THRESHOLD` | `2` | Minutes before worker marked offline |
| `VLOG_STALE_JOB_CHECK_INTERVAL` | `60` | Seconds between stale job checks |

---

## Database-Backed Settings System

VLog supports a database-backed settings system that allows runtime configuration changes without restarting services.

### Overview

Settings are stored in a PostgreSQL table and can be managed via:
- **Admin UI**: Settings tab in the admin interface
- **CLI**: `vlog settings` command
- **API**: `/api/settings/*` endpoints

### Bootstrap vs Runtime Settings

**Bootstrap settings** are required at startup and cannot be changed at runtime:
- Database connection URL
- Storage paths
- Server ports
- API secrets

**Runtime settings** can be changed without restart:
- Transcoding parameters (HLS segment duration, timeouts)
- Watermark configuration
- Analytics cache settings
- Alert webhook configuration
- Worker poll intervals

### Settings Migration

To migrate existing environment variables to the database:

```bash
# Migrate all migrateable settings from environment
vlog settings migrate-from-env

# List all settings
vlog settings list

# Get a specific setting
vlog settings get transcoding.hls_segment_duration

# Set a setting
vlog settings set transcoding.hls_segment_duration 10
```

### Auto-Seeding

On fresh installations, VLog automatically seeds the database with settings from environment variables. This happens once during the first startup when no settings exist in the database.

### Settings Categories

| Category | Description |
|----------|-------------|
| `transcoding` | HLS, timeout, and retry settings |
| `watermark` | Watermark overlay configuration |
| `workers` | Worker poll intervals and heartbeat settings |
| `analytics` | Analytics caching configuration |
| `alerts` | Webhook and notification settings |
| `storage` | Upload size limits |

### Cache Behavior

Settings are cached in memory for 60 seconds to avoid database round-trips on every request. This means:
- Changes take up to 60 seconds to take effect
- No restart required for runtime settings
- Bootstrap settings still require restart

### Deprecation Warnings

When using deprecated environment variables, VLog logs warnings at startup with guidance on migrating to the database-backed system:

```
DEPRECATION WARNING: Environment variable 'VLOG_HLS_SEGMENT_DURATION' is deprecated.
Use 'vlog settings set transcoding.hls_segment_duration <value>' to configure via database.
Run 'vlog settings migrate-from-env' to migrate all settings.
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/settings` | GET | List all settings |
| `/api/settings/{key}` | GET | Get a specific setting |
| `/api/settings/{key}` | PUT | Update a setting |
| `/api/settings/seed` | POST | Seed settings from environment |
| `/api/settings/export` | GET | Export all settings as JSON |

---

---

## Streaming Format Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_STREAMING_FORMAT` | `cmaf` | Default streaming format: cmaf or hls_ts |
| `VLOG_STREAMING_CODEC` | `hevc` | Default codec: h264, hevc, av1 |
| `VLOG_STREAMING_ENABLE_DASH` | `true` | Generate DASH manifest for CMAF videos |

**Format Comparison:**

| Setting | CMAF (default) | HLS/TS (legacy) |
|---------|----------------|-----------------|
| Container | fMP4 (.m4s) | MPEG-TS (.ts) |
| Manifests | HLS + DASH | HLS only |
| Codecs | H.264, HEVC, AV1 | H.264 |
| Player | Shaka Player | hls.js |

---

## CDN Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_CDN_ENABLED` | `false` | Enable CDN for video URLs |
| `VLOG_CDN_BASE_URL` | (none) | CDN base URL (e.g., https://cdn.example.com) |

When enabled, video URLs in API responses use the CDN base URL instead of the origin server.

---

## Audit Log Rotation

| Variable | Default | Description |
|----------|---------|-------------|
| `VLOG_AUDIT_LOG_MAX_BYTES` | `10485760` | Max size per log file (10 MB) |
| `VLOG_AUDIT_LOG_BACKUP_COUNT` | `5` | Number of backup files to keep |

Audit logs automatically rotate when reaching the max size. Old backups are deleted when the count exceeds the limit.

---

## Test Mode

Set `VLOG_TEST_MODE=1` to:
- Skip NAS directory creation
- Use temporary directories for tests
- Required for CI/CD environments
