# VLog Architecture

## Overview

VLog is a self-hosted video platform built with Python/FastAPI for the backend and Alpine.js/Tailwind CSS for the frontend. It provides 4K video support with HLS adaptive bitrate streaming.

## System Architecture

```
                                    +------------------+
                                    |   Web Browser    |
                                    +--------+---------+
                                             |
                              +--------------+--------------+
                              |                             |
                     +--------v--------+          +--------v--------+
                     |  nginx (opt.)   |          |  nginx (opt.)   |
                     |  :80/:443       |          |  internal only  |
                     +--------+--------+          +--------+--------+
                              |                             |
                     +--------v--------+          +--------v--------+
                     |  Public API     |          |  Admin API      |
                     |  FastAPI :9000  |          |  FastAPI :9001  |
                     +--------+--------+          +--------+--------+
                              |                             |
                              +-------------+---------------+
                                            |
                    +-----------------------+-----------------------+
                    |                       |                       |
        +-----------v-----------+   +-------v-------+   +-----------v-----------+
        |  PostgreSQL Database  |   | Redis (opt.)  |   |     Worker API        |
        |   (vlog database)     |   | Job Queue &   |   |     FastAPI :9002     |
        |   + Settings Service  |   |  Pub/Sub      |   +-----------+-----------+
        +-----------+-----------+   +-------+-------+               |
                    |                       |                       |
     +--------------+--+--------------------+-------+---------------+
     |                 |                    |       |               |
+----v----+    +-------v-----+    +--------v--+  +--v----------+  +-v------------+
| Local   |    | Transcription|   | NAS Storage|  | Kubernetes |  | Webhook      |
|Transcoder|   | Worker       |   | /mnt/nas/  |  | Workers    |  | Delivery &   |
|(inotify) |   | (whisper)    |   | vlog-storage| | (remote)   |  | SSE Updates  |
+----------+   +--------------+   +------------+  +------------+  +--------------+
```

## Components

### 1. Public API (Port 9000)

**Location:** `api/public.py`

The public-facing API server handles:
- Video browsing and search
- Category listing
- HLS video streaming (with custom MIME type handling)
- Playback analytics collection
- Transcoding progress tracking
- Transcription status

**Key Features:**
- Custom `HLSStaticFiles` class for proper `.ts` and `.m3u8` MIME types
- CORS middleware for cross-origin HLS playback
- Session-based analytics without user tracking

### 2. Admin API (Port 9001)

**Location:** `api/admin.py`

Internal-only API for video management:
- Video upload (multipart form data)
- Video metadata editing
- Category management (CRUD)
- Video soft-delete (moves to archive) and restore
- Video re-upload (replace source file)
- Failed transcoding retry
- Manual transcription triggers
- Analytics dashboard data
- **Real-time SSE endpoints** for progress and worker status updates

**SSE Endpoints:**
- `GET /api/events/progress?video_ids=1,2,3` - Real-time transcoding progress
- `GET /api/events/workers` - Real-time worker status changes

**Security Note:** This API should NOT be exposed publicly.

**Rate Limiting:** Both APIs implement rate limiting via slowapi with configurable limits per endpoint.

### 3. Worker API (Port 9002)

**Location:** `api/worker_api.py`

Central coordinator for distributed transcoding:

**Endpoints:**
- Worker registration with API key generation
- Heartbeat for worker health tracking
- Atomic job claiming with expiration
- Progress updates (reflected in admin UI)
- Source file download and HLS upload
- Deployment event tracking

**Security:**
- API key authentication (argon2id hashed, migrated from SHA-256)
- Prefix-based key lookup for efficient authentication
- Per-worker revocation
- Version gating to prevent stale workers from processing

### Shared API Utilities

**Location:** `api/` directory

| Module | Purpose |
|--------|---------|
| `common.py` | Security middleware, health checks, rate limiting helpers |
| `analytics_cache.py` | In-memory caching for analytics queries |
| `audit.py` | Audit logging for security-relevant operations |
| `db_retry.py` | Database retry logic for transient errors (deadlocks, connection issues) |
| `enums.py` | Enum definitions (VideoStatus, TranscriptionStatus) |
| `errors.py` | Error message sanitization utilities |
| `exception_utils.py` | Exception handling decorators |
| `redis_client.py` | Redis connection pool with circuit breaker pattern |
| `job_queue.py` | Redis Streams job queue abstraction with priority levels |
| `pubsub.py` | Redis Pub/Sub for real-time progress updates |
| `settings_service.py` | Database-backed settings with caching and env var fallback |
| `webhook_service.py` | Webhook notification delivery with circuit breaker |
| `worker_auth.py` | API key authentication with argon2id hashing |

### 4. Local Transcoding Worker

**Location:** `worker/transcoder.py`

Event-driven background process for local video transcoding:

**Processing Flow:**
1. **Detection** - Monitors uploads directory via inotify (watchdog)
2. **Probe** - Extracts metadata with ffprobe
3. **Thumbnail** - Generates preview image at 5s mark
4. **Transcode** - Creates HLS variants for applicable qualities
5. **Playlist** - Generates master.m3u8
6. **Finalize** - Updates database, cleans up source file

**Resilience Features:**
- Checkpoint-based progress tracking
- Crash recovery on worker startup
- Per-quality progress persistence
- Configurable retry attempts with exponential backoff
- Graceful shutdown handling

### Shared Worker Utilities

**Location:** `worker/` directory

| Module | Purpose |
|--------|---------|
| `hwaccel.py` | GPU detection and hardware encoder selection (NVENC, VAAPI) |
| `http_client.py` | HTTP client with circuit breaker for worker-to-API communication |
| `alerts.py` | Webhook alerting for transcoding events (stale jobs, failures, retries) |

### 5. Remote Transcoding Workers (Kubernetes)

**Location:** `worker/remote_transcoder.py`, `Dockerfile.worker.gpu`

Containerized workers for horizontal scaling with GPU hardware acceleration.

**Container Image:**
- Base: Rocky Linux 10
- FFmpeg 7.1.2 from RPM Fusion (pre-built with nvenc, vaapi, qsv encoders)
- intel-media-driver 25.2.6 (Battlemage/Arc B580 support)
- Python 3.12
- Local registry: `localhost:9003/vlog-worker-gpu:rocky10`

**Processing Flow:**
1. **Register** - Worker registers via API, receives API key
2. **Poll** - Periodically claims available jobs
3. **Download** - Fetches source file from Worker API
4. **Transcode** - Runs ffmpeg locally with GPU acceleration
5. **Upload** - Sends output (archive or streaming segments) to Worker API
6. **Complete** - Reports completion, job marked ready

**Upload Modes:**

| Mode | Description | Use Case |
|------|-------------|----------|
| **Archive** | Single tar.gz at job completion | Simpler, default mode |
| **Streaming** | Progressive segment upload during transcoding | Faster first playback, lower peak bandwidth |

Enable streaming upload via `VLOG_WORKER_STREAMING_UPLOAD=true`.

**GPU Hardware Acceleration:**

| Type | Encoders | K8s Resource | Runtime |
|------|----------|--------------|---------|
| NVIDIA NVENC | h264_nvenc, hevc_nvenc, av1_nvenc | `nvidia.com/gpu` | `runtimeClassName: nvidia` |
| Intel VAAPI | h264_vaapi, hevc_vaapi, av1_vaapi | `gpu.intel.com/xe` | Standard (mounts /dev/dri) |

**Cluster Requirements:**
- NVIDIA: nvidia-container-toolkit + nvidia device plugin
- Intel: Node Feature Discovery (NFD) + Intel GPU device plugin

**Current rocky10-desktop Configuration:**
```
┌─────────────────────────────────────────────────────────────┐
│                    rocky10-desktop                          │
├─────────────────────────────────────────────────────────────┤
│  vlog-worker-rocky10          vlog-worker-intel             │
│  ├─ RTX 3090 (NVENC)          ├─ Arc B580 (VAAPI)          │
│  ├─ nvidia.com/gpu: 1         ├─ gpu.intel.com/xe: 1       │
│  └─ runtimeClassName: nvidia  └─ Standard runtime          │
└─────────────────────────────────────────────────────────────┘
```

**Deployment Files:**
- `k8s/worker-deployment-nvidia.yaml` - NVIDIA GPU workers
- `k8s/worker-deployment-intel.yaml` - Intel Arc/QuickSync workers
- `k8s/worker-deployment.yaml` - CPU-only workers

**Encoding Performance (1080p test):**

| Encoder | GPU | Speed |
|---------|-----|-------|
| h264_nvenc | RTX 3090 | 3.74x |
| hevc_nvenc | RTX 3090 | 3.57x |
| h264_vaapi | Arc B580 | 8.36x |
| hevc_vaapi | Arc B580 | 6.64x |
| av1_vaapi | Arc B580 | 6.86x |

### 6. Transcription Worker

**Location:** `worker/transcription.py`

Background process for automatic captioning:

**Processing Flow:**
1. Polls database for videos with status='ready' and no transcription
2. Finds highest quality HLS playlist as audio source
3. Runs faster-whisper transcription with VAD filter
4. Generates WebVTT subtitle file
5. Updates database with transcript and metadata

**Features:**
- Lazy model loading to save memory
- Configurable model size (tiny → large-v3)
- Language auto-detection
- Word count and duration tracking

### 7. Database

**Database:** PostgreSQL (default)

PostgreSQL provides concurrent read/write support, making it suitable for multi-instance deployments. The database URL is configurable via `VLOG_DATABASE_URL`.

**Tables:**
- `categories` - Video organization
- `videos` - Video metadata and status (with soft-delete via `deleted_at`)
- `video_qualities` - Available HLS variants per video
- `tags` - Tag definitions for granular content organization
- `video_tags` - Many-to-many relationship between videos and tags
- `playlists` - Playlist/collection definitions
- `playlist_items` - Many-to-many with ordering between playlists and videos
- `chapters` - Video chapter markers for timeline navigation
- `sprite_queue` - Background sprite sheet generation jobs
- `custom_field_definitions` - User-defined metadata field definitions
- `video_custom_fields` - Custom field values per video
- `viewers` - Cookie-based viewer tracking
- `playback_sessions` - Watch analytics (partitioned by month)
- `transcoding_jobs` - Job tracking with checkpoints and job claiming
- `quality_progress` - Per-quality transcoding progress
- `transcriptions` - Whisper transcription records
- `workers` - Registered remote workers with status tracking
- `worker_api_keys` - API key authentication (argon2id with hash_version tracking)
- `settings` - Runtime configuration with categories, types, and constraints
- `webhooks` - Webhook endpoint configurations with event subscriptions
- `webhook_deliveries` - Delivery history with retry tracking
- `reencode_queue` - Background re-encode job queue with priority levels
- `deployment_events` - Worker deployment tracking for rollbacks

### 8. Redis (Optional)

**Purpose:** Real-time job dispatch and progress updates

**Features:**
- **Redis Streams:** Instant job dispatch with priority queues (high/normal/low)
- **Pub/Sub:** Real-time transcoding progress and worker status for SSE
- **Circuit Breaker:** Automatic fallback to database polling if Redis unavailable

**Configuration:** Set `VLOG_REDIS_URL` to enable Redis features. See `CONFIGURATION.md` for all Redis settings.

### 9. Storage (NAS)

**Mount Point:** `/mnt/nas/vlog-storage`

**Structure:**
```
/mnt/nas/vlog-storage/
├── uploads/           # Temporary upload storage
│   └── {video_id}.mp4
├── videos/            # Transcoded output
│   └── {slug}/
│       ├── master.m3u8      # HLS adaptive bitrate playlist
│       ├── manifest.mpd     # DASH manifest (CMAF format)
│       ├── 1080p/           # CMAF quality directory
│       │   ├── init.mp4         # Initialization segment
│       │   ├── segment_0.m4s    # Media segments
│       │   └── ...
│       ├── 1080p.m3u8       # Legacy HLS playlist
│       ├── 1080p_0000.ts    # Legacy HLS segments
│       ├── thumbnail.jpg
│       └── captions.vtt     # WebVTT subtitles
├── archive/           # Soft-deleted videos
│   └── {slug}/
└── backups/           # PostgreSQL database backups
    └── vlog-*.dump
```

**Soft-Delete Flow:** When a video is deleted, its files are moved from `videos/` to `archive/`. Archived videos can be restored via the API. Permanent deletion occurs after the configured retention period.

### 10. Streaming Formats

VLog supports two streaming formats:

| Format | Container | Manifests | Player | Codecs |
|--------|-----------|-----------|--------|--------|
| **CMAF** (new) | fMP4 (.m4s) | HLS + DASH | Shaka Player | H.264, HEVC, AV1 |
| **HLS/TS** (legacy) | MPEG-TS (.ts) | HLS only | hls.js | H.264 |

**CMAF (Common Media Application Format):**
- Modern fragmented MP4 container
- Supports both HLS (`master.m3u8`) and DASH (`manifest.mpd`) delivery
- Better codec support (HEVC, AV1)
- More efficient seeking
- Uses Shaka Player for playback

**HLS/TS (Legacy):**
- Traditional MPEG-TS container
- HLS-only delivery
- Maximum compatibility with older devices
- Uses hls.js for playback

The public player auto-detects the format and uses the appropriate player.

### 11. Re-encode Queue

The re-encode queue enables batch conversion of legacy HLS/TS videos to modern CMAF format.

**Use Cases:**
- Upgrade codec from H.264 to HEVC or AV1
- Convert container from MPEG-TS to fMP4
- Re-process videos with new quality settings

**Flow:**
```
Admin queues video → Re-encode worker claims job →
Download source (via Worker API) → Re-transcode to CMAF →
Generate new manifests → Upload result → Update database
```

**Priority Levels:** high, normal, low

**Related Endpoints:**
- `POST /api/reencode/queue` - Queue specific videos
- `POST /api/reencode/queue-all` - Queue all legacy videos
- `GET /api/reencode/status` - Queue statistics

### 12. Observability

**Prometheus Metrics:**

VLog exposes comprehensive metrics at `/metrics` (Admin API) and `/api/metrics` (Worker API):

| Category | Examples |
|----------|----------|
| HTTP | Request count, latency, error rates |
| Videos | Total by status, uploads, views |
| Transcoding | Jobs active, queue size, duration |
| Workers | Online count, heartbeats |
| Database | Connections, query latency, retries |
| Redis | Operations, circuit breaker state |

**Health Checks:**
- `/health` - Basic health status
- `/ready` (workers) - Readiness with FFmpeg and API checks

**Audit Logging:**
- Security-relevant operations logged
- Rotating file handler with configurable retention

See [MONITORING.md](MONITORING.md) for complete metrics documentation.

### 13. Settings Service

**Location:** `api/settings_service.py`

Database-backed runtime configuration system replacing environment variables:

**Features:**
- In-memory caching with configurable TTL (default: 60 seconds)
- Fallback to environment variables during migration
- Type coercion and validation (integer, float, boolean, string, json)
- Constraints (min, max, allowed values)
- Category-based organization
- Thread-safe cache operations

**Setting Categories:**
- `transcoding.*` - HLS segment duration, quality settings
- `transcription.*` - Whisper model, language settings
- `rate_limiting.*` - Per-endpoint rate limits
- `webhooks.*` - Webhook delivery configuration
- `storage.*` - Path and retention settings
- `alerts.*` - Alert thresholds and webhook URLs

**CLI Management:**
```bash
vlog settings list                     # List all settings
vlog settings get transcoding.hls_segment_duration
vlog settings set transcoding.hls_segment_duration 10
vlog settings migrate-from-env         # Import from environment
```

### 14. Webhook Notification Service

**Location:** `api/webhook_service.py`

Event-driven notification system for external integrations:

**Supported Events:**
- `video.ready` / `video.processing` / `video.failed`
- `video.deleted` / `video.restored` / `video.purged`
- `transcription.complete` / `transcription.failed`
- `worker.connected` / `worker.disconnected`

**Security Features:**
- HMAC-SHA256 signature verification
- SSRF protection (blocks private IP ranges, localhost, link-local)
- Per-webhook secret keys
- Circuit breaker pattern (opens after consecutive failures)

**Delivery Features:**
- Exponential backoff retry (configurable max retries)
- Concurrent delivery with configurable parallelism
- Delivery history with response tracking
- Manual retry from admin UI

**Flow:**
```
Event occurs → Create delivery records for matching webhooks →
Background worker sends HTTP POST → Record response →
Retry on failure with backoff → Circuit breaker if persistent failure
```

### 15. Video Downloads

**Location:** `api/public.py` (download endpoints)

Optional feature allowing users to download video files:

**Download Types:**
- Original source file (if enabled)
- Transcoded quality variants (1080p, 720p, etc.)

**Security:**
- Disabled by default
- Per-IP rate limiting (requests/hour)
- Concurrent download limiting
- Original downloads separately toggleable

**Endpoints:**
- `GET /api/videos/{slug}/download` - Download highest quality
- `GET /api/videos/{slug}/download?quality=720p` - Specific quality
- `GET /api/videos/{slug}/download?original=true` - Original file

See [CONFIGURATION.md](CONFIGURATION.md#video-download-settings) for configuration options.

## Data Flow

### Upload Flow

```
User → Admin UI/CLI → Admin API → uploads/{id}.ext
                                       ↓
                              Worker detects file (inotify)
                                       ↓
                              Probe → Thumbnail → Transcode
                                       ↓
                              videos/{slug}/ (HLS output)
                                       ↓
                              Database updated (status='ready')
```

### Playback Flow

```
User → Public UI → GET /api/videos/{slug}
                          ↓
              Video metadata + stream_url
                          ↓
              hls.js loads /videos/{slug}/master.m3u8
                          ↓
              Adaptive streaming based on bandwidth
                          ↓
              Analytics heartbeats every 30s
```

### Transcription Flow

```
Video ready → Transcription worker polls
                     ↓
             Find HLS playlist → faster-whisper
                     ↓
             Generate WebVTT → Save to videos/{slug}/
                     ↓
             Update transcriptions table
```

## Technology Stack

| Layer | Technology |
|-------|------------|
| Backend Framework | FastAPI |
| ASGI Server | Uvicorn |
| Database | PostgreSQL + SQLAlchemy |
| Async DB Access | asyncpg, databases |
| Job Queue (optional) | Redis Streams |
| Real-time Updates | Redis Pub/Sub + SSE |
| Rate Limiting | slowapi (memory or Redis) |
| Video Processing | FFmpeg 7.1.2 (NVENC, VAAPI, QSV) |
| Transcription | faster-whisper |
| File Monitoring | watchdog (inotify) |
| Frontend Framework | Alpine.js |
| CSS | Tailwind CSS v4 |
| Video Player | Shaka Player (CMAF/DASH), hls.js (legacy HLS/TS) |
| Process Management | systemd |
| Container Base | Rocky Linux 10 |
| Container Registry | Docker Registry v2 (localhost:9003) |
| Container Orchestration | Kubernetes (k3s) |
| GPU Management | NVIDIA device plugin, Intel GPU device plugin |

## Configuration

All configuration is centralized in `config.py`. Every setting supports environment variable override with `VLOG_` prefix:

- **Paths** - Storage locations, database path, archive directory
- **Ports** - Service ports (9000, 9001)
- **Quality Presets** - Bitrate ladder (2160p → 360p)
- **HLS Settings** - Segment duration
- **Checkpoint Settings** - Interval, stale timeout, retry attempts
- **FFmpeg Timeouts** - Prevent stuck transcoding jobs
- **Transcription Settings** - Model, language, compute type, timeouts
- **Worker Settings** - Filesystem watcher, polling intervals
- **Archive Settings** - Retention period for soft-deleted videos
- **Rate Limiting** - Per-endpoint limits, storage backend (memory/Redis)
- **CORS** - Allowed origins for public and admin APIs
- **Upload Limits** - Maximum file size, chunk size

## Playlists and Collections

Playlists enable organizing videos into curated groups:

| Type | Use Case |
|------|----------|
| **playlist** | General purpose grouping |
| **collection** | Curated content sets |
| **series** | Sequential viewing (numbered) |
| **course** | Learning paths |

**Visibility Options:** public, private, unlisted

**API Endpoints:** See [API.md](API.md#playlists-api) for full documentation.

---

## Video Chapters

Chapters provide timeline navigation within videos:

- Maximum 50 chapters per video
- Start time required, end time optional
- Supports reordering
- Displayed in player timeline
- Stored in `chapters` table, `has_chapters` flag on videos for performance

---

## Sprite Sheets (Timeline Previews)

Sprite sheets enable thumbnail previews when scrubbing the video timeline:

**Generation:**
1. Admin queues sprite generation via API
2. Background worker processes queue
3. Generates 10x10 grid of thumbnails
4. Stores sprite sheets in `videos/{slug}/sprites/`

**Configuration:**
- Interval: seconds between frames (default 10)
- Tile size: grid dimensions (default 10x10 = 100 frames)
- Frame size: thumbnail dimensions (default 160x90)

**Files:**
```
videos/{slug}/sprites/
├── sprite_0.jpg    # First 100 frames
├── sprite_1.jpg    # Next 100 frames
└── ...
```

---

## Circuit Breaker Pattern

The worker HTTP client implements a circuit breaker to prevent cascading failures:

```
        ┌─────────────────────────────────────────────────────┐
        │                                                      │
        ▼                                                      │
    ┌───────┐  failure_threshold  ┌──────┐  recovery_timeout  │
    │CLOSED │ ─────────────────→ │ OPEN │ ─────────────────→  │
    └───────┘                    └──────┘                      │
        ▲                            │                         │
        │                            ▼                         │
        │   success_threshold   ┌─────────┐                    │
        └───────────────────── │HALF-OPEN│ ────────failure─────┘
                               └─────────┘
```

**States:**
- **Closed:** Normal operation, requests proceed
- **Open:** All requests fail immediately (API unavailable)
- **Half-Open:** Limited requests test recovery

**Configuration:** See [CONFIGURATION.md](CONFIGURATION.md#circuit-breaker-settings)

---

## Scalability Considerations

### Current Design

- **PostgreSQL database** - Full concurrent read/write support
- **Optional Redis** - Job queue with priority and real-time SSE updates
- **Distributed transcoding** - Multiple GPU workers via Kubernetes
- **NAS for video storage** - Horizontal storage scaling
- **Table partitioning** - `playback_sessions` partitioned by month for analytics performance

### Scaling Options

1. **API Instances** - Multiple API instances behind load balancer (requires Redis for shared rate limiting)
2. **Transcoding Workers** - Add more GPU workers in Kubernetes (auto-scaling with HPA)
3. **Storage** - Object storage (S3-compatible) with CDN
4. **Redis** - Enable Redis for instant job dispatch and real-time progress updates
