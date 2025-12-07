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
                              +-------------v--------------+
                              |      SQLite Database       |
                              |      (vlog.db - local)     |
                              +-------------+--------------+
                                            |
     +----------------+---------------------+--------------------+----------------+
     |                |                     |                    |                |
+----v----+    +------v------+    +--------v--------+    +------v------+  +------v------+
| Worker  |    | Local       |    | Transcription   |    | NAS Storage |  | Kubernetes  |
| API     |    | Transcoder  |    | Worker          |    | /mnt/nas/   |  | Workers     |
| :9002   |    | (inotify)   |    | (whisper)       |    | vlog-storage|  | (remote)    |
+---------+    +-------------+    +-----------------+    +-------------+  +-------------+
     ^                                                                          |
     |                          HTTP: claim jobs, download/upload               |
     +--------------------------------------------------------------------------+
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

**Security:**
- API key authentication (SHA-256 hashed)
- Prefix-based key lookup
- Per-worker revocation

### Shared API Utilities

**Location:** `api/` directory

| Module | Purpose |
|--------|---------|
| `common.py` | Security middleware, health checks, rate limiting helpers |
| `analytics_cache.py` | In-memory caching for analytics queries |
| `audit.py` | Audit logging for security-relevant operations |
| `db_retry.py` | Database retry logic for SQLite locking |
| `enums.py` | Enum definitions (VideoStatus, TranscriptionStatus) |
| `errors.py` | Error message sanitization utilities |
| `exception_utils.py` | Exception handling decorators |

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
5. **Upload** - Sends HLS output as tar.gz to Worker API
6. **Complete** - Reports completion, job marked ready

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

**Location:** `vlog.db` (local SQLite)

**Why Local?** SQLite performance degrades significantly over network filesystems. The database is kept local while video files are stored on NAS.

**Tables:**
- `categories` - Video organization
- `videos` - Video metadata and status
- `video_qualities` - Available HLS variants per video
- `viewers` - Cookie-based viewer tracking
- `playback_sessions` - Watch analytics
- `transcoding_jobs` - Job tracking with checkpoints
- `quality_progress` - Per-quality transcoding progress
- `transcriptions` - Whisper transcription records
- `workers` - Registered remote workers with status tracking
- `worker_api_keys` - API key authentication (SHA-256 hashed)

### 8. Storage (NAS)

**Mount Point:** `/mnt/nas/vlog-storage`

**Structure:**
```
/mnt/nas/vlog-storage/
├── uploads/           # Temporary upload storage
│   └── {video_id}.mp4
├── videos/            # HLS output
│   └── {slug}/
│       ├── master.m3u8      # Adaptive bitrate playlist
│       ├── 2160p.m3u8       # Quality-specific playlist
│       ├── 2160p_0000.ts    # Video segments
│       ├── 2160p_0001.ts
│       ├── 1080p.m3u8
│       ├── 1080p_0000.ts
│       ├── thumbnail.jpg
│       └── captions.vtt     # WebVTT subtitles
└── archive/           # Soft-deleted videos
    └── {slug}/        # Same structure as videos/
```

**Soft-Delete Flow:** When a video is deleted, its files are moved from `videos/` to `archive/`. Archived videos can be restored via the API. Permanent deletion occurs after the configured retention period.

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
| Database | SQLite + SQLAlchemy |
| Async DB Access | aiosqlite, databases |
| Rate Limiting | slowapi (memory or Redis) |
| Video Processing | FFmpeg 7.1.2 (NVENC, VAAPI, QSV) |
| Transcription | faster-whisper |
| File Monitoring | watchdog (inotify) |
| Frontend Framework | Alpine.js |
| CSS | Tailwind CSS v4 |
| Video Player | hls.js |
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

## Scalability Considerations

### Current Design (Single Server)

- SQLite database (single-writer limitation)
- Single transcoding worker
- Single transcription worker
- NAS for video storage (horizontal storage scaling)

### Future Scaling Options

1. **Database** - Migrate to PostgreSQL for concurrent writes
2. **Workers** - Add message queue (Redis/RabbitMQ) for distributed processing
3. **Storage** - Object storage (S3-compatible) with CDN
4. **API** - Horizontal scaling behind load balancer
