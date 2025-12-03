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
              +-----------------------------+-----------------------------+
              |                             |                             |
    +---------v---------+       +-----------v-----------+     +-----------v-----------+
    |  Transcoding      |       |   Transcription       |     |   NAS Storage         |
    |  Worker           |       |   Worker              |     |   /mnt/nas/vlog-      |
    |  (event-driven)   |       |   (faster-whisper)    |     |   storage/            |
    +-------------------+       +-----------------------+     +-----------------------+
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
- Video deletion with file cleanup
- Failed transcoding retry
- Manual transcription triggers
- Analytics dashboard data

**Security Note:** This API should NOT be exposed publicly.

### 3. Transcoding Worker

**Location:** `worker/transcoder.py`

Event-driven background process for video transcoding:

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

### 4. Transcription Worker

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

### 5. Database

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

### 6. Storage (NAS)

**Mount Point:** `/mnt/nas/vlog-storage`

**Structure:**
```
/mnt/nas/vlog-storage/
├── uploads/           # Temporary upload storage
│   └── {video_id}.mp4
└── videos/            # HLS output
    └── {slug}/
        ├── master.m3u8      # Adaptive bitrate playlist
        ├── 2160p.m3u8       # Quality-specific playlist
        ├── 2160p_0000.ts    # Video segments
        ├── 2160p_0001.ts
        ├── 1080p.m3u8
        ├── 1080p_0000.ts
        ├── thumbnail.jpg
        └── captions.vtt     # WebVTT subtitles
```

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
| Video Processing | ffmpeg, ffprobe |
| Transcription | faster-whisper |
| File Monitoring | watchdog (inotify) |
| Frontend Framework | Alpine.js |
| CSS | Tailwind CSS v4 |
| Video Player | hls.js |
| Process Management | systemd |

## Configuration

All configuration is centralized in `config.py`:

- **Paths** - Storage locations, database path
- **Ports** - Service ports (9000, 9001)
- **Quality Presets** - Bitrate ladder (2160p → 360p)
- **HLS Settings** - Segment duration
- **Checkpoint Settings** - Interval, stale timeout, retry attempts
- **Transcription Settings** - Model, language, compute type
- **Worker Settings** - Filesystem watcher, polling intervals

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
