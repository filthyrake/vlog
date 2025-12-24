# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VLog is a self-hosted video platform with 4K support and HLS streaming. It consists of these services:
- **Public API** (port 9000): FastAPI server for video browsing, playback, and analytics
- **Admin API** (port 9001): FastAPI server for uploads and management (optional API key auth via `VLOG_ADMIN_API_SECRET`)
- **Worker API** (port 9002): FastAPI server for remote worker registration, job claiming, and file transfer
- **Transcoding Worker**: Background process that converts uploads to HLS with multiple quality variants
  - **Local mode**: Event-driven with inotify, runs as systemd service
  - **Remote mode**: Containerized workers in Kubernetes, communicate via Worker API

Storage is on NAS at `/mnt/nas/vlog-storage` (videos/ and uploads/). PostgreSQL is the default database backend.

## Commands

```bash
# Setup - install package in development mode
python3 -m venv venv
source venv/bin/activate
pip install -e .  # Installs vlog package and CLI command

# Development - start all services
source venv/bin/activate
./start.sh

# Or start individually
./start-public.sh   # Port 9000
./start-admin.sh    # Port 9001
./start-worker.sh   # Background transcoder

# Production - managed by systemd
sudo systemctl start vlog.target     # Start all
sudo systemctl status vlog-public vlog-admin vlog-worker
sudo journalctl -u vlog-worker -f    # Watch transcoder logs

# CLI usage (vlog command installed by package)
vlog upload video.mp4 -t "Title" -c "Category"
vlog list
vlog categories --create "Name"
vlog download "https://youtube.com/..." -c "Category"

# Worker management
vlog worker register --name "worker-1"  # Register new worker, get API key
vlog worker list                         # List all registered workers
vlog worker status                       # Show active/idle/offline workers
vlog worker revoke <worker-id>           # Revoke worker's API key

# Database migrations (using Alembic)
python api/database.py              # Apply all pending migrations
python api/database.py stamp 001    # Mark existing DB as migrated (for upgrades)
alembic current                     # Show current migration revision
alembic upgrade head                # Apply all migrations
alembic downgrade -1                # Rollback one migration
alembic revision -m "description"   # Create new migration manually
alembic revision --autogenerate -m "description"  # Auto-generate from model changes

# Testing
VLOG_TEST_MODE=1 pytest                    # Run all tests
VLOG_TEST_MODE=1 pytest tests/test_public_api.py  # Run single test file
VLOG_TEST_MODE=1 pytest -k "test_list"     # Run tests matching pattern
VLOG_TEST_MODE=1 pytest --cov=api --cov=worker    # With coverage

# Linting
VLOG_TEST_MODE=1 ruff check api/ worker/ cli/ tests/ config.py
ruff format api/ worker/ cli/ tests/ config.py   # Auto-format
```

## Architecture

```
api/
├── public.py       # Public browsing API, serves /api/videos, /api/categories, HLS files, analytics
├── admin.py        # Upload/management API, multipart uploads, CRUD operations, soft-delete, batch ops, SSE endpoints
├── worker_api.py   # Worker API for remote transcoder registration, job claiming, file transfer
├── worker_auth.py  # API key authentication for workers
├── worker_schemas.py # Pydantic models for Worker API
├── database.py     # SQLAlchemy table definitions (categories, videos, workers, transcoding_jobs, etc.)
├── schemas.py      # Pydantic models for request/response validation
├── common.py       # Shared utilities (security middleware, health checks, rate limiting helpers)
├── analytics_cache.py # In-memory caching for analytics endpoints
├── audit.py        # Audit logging for security-relevant operations
├── db_retry.py     # Database retry logic for transient errors (deadlocks, connection issues)
├── enums.py        # Enum definitions (VideoStatus, TranscriptionStatus)
├── errors.py       # Error message sanitization utilities
├── exception_utils.py # Exception handling decorators and utilities
├── redis_client.py # Redis connection pool with circuit breaker pattern
├── job_queue.py    # Redis Streams job queue abstraction with priority levels
└── pubsub.py       # Redis Pub/Sub for real-time progress updates

worker/
├── transcoder.py       # Local event-driven (inotify) transcoder with checkpoint-based resumable processing
├── remote_transcoder.py # Containerized worker for distributed transcoding via Worker API
├── hwaccel.py          # GPU detection and hardware encoder selection (NVENC, VAAPI)
├── http_client.py      # HTTP client for worker-to-API communication
├── transcription.py    # Whisper transcription worker
└── alerts.py           # Webhook alerting for transcoding events (stale jobs, failures, etc.)

web/
├── public/       # Tailwind + Alpine.js frontend for browsing
└── admin/        # Admin UI for uploads and video management

cli/
└── main.py       # Argparse CLI, talks to admin API via httpx, includes worker management

k8s/              # Kubernetes manifests for containerized workers
├── namespace.yaml, configmap.yaml, secret.yaml
├── worker-deployment.yaml          # CPU-only worker deployment
├── worker-deployment-nvidia.yaml   # NVIDIA GPU worker deployment (NVENC)
├── worker-deployment-intel.yaml    # Intel Arc/QuickSync worker deployment (VAAPI)
├── worker-hpa.yaml
└── README.md

migrations/
├── env.py        # Alembic environment config (loads from config.py)
└── versions/     # Migration scripts:
    ├── 001_initial_schema.py
    ├── 002_add_session_token_unique_constraint.py
    ├── 003_add_workers_table.py
    ├── 004_add_missing_indexes.py
    ├── 005_add_workers_current_job_fk.py
    ├── 006_add_processed_by_worker.py
    └── 007_add_tags_tables.py
```

### Key Flows

**Upload flow**: File goes to `UPLOADS_DIR/{video_id}.ext` -> worker detects via inotify (or fallback polling) -> ffmpeg transcodes to HLS in `VIDEOS_DIR/{slug}/` -> generates `master.m3u8` with quality variants -> marks video "ready"

**HLS output structure**: Each video gets `{slug}/master.m3u8` (adaptive playlist) + `{quality}.m3u8` + `{quality}_XXXX.ts` segments + `thumbnail.jpg`

**Quality ladder**: Only generates qualities <= source resolution. Presets defined in `config.py`: 2160p (15Mbps), 1440p (8Mbps), 1080p (5Mbps), 720p (2.5Mbps), 480p (1Mbps), 360p (600kbps)

**Transcoding recovery**: Jobs have per-quality checkpoints. On crash, worker detects stale jobs and resumes from last checkpoint. Completed qualities are preserved on retry.

**Transcription**: Optional auto-transcription using faster-whisper generates WebVTT subtitles. Configurable model size and language detection.

**Soft-delete**: Videos are soft-deleted (moved to archive) with configurable retention. Can be restored or permanently deleted.

**Rate limiting**: Configurable per-endpoint rate limits using slowapi. Supports memory or Redis storage.

**Watermark overlay**: Client-side watermark displayed on the video player (does not modify video files). Two types available:
- **Image watermark**: Set `VLOG_WATERMARK_TYPE=image` and `VLOG_WATERMARK_IMAGE=watermark.png`. Upload via Admin API (`POST /api/settings/watermark/upload`) or place directly in NAS storage. Supports PNG (with transparency), JPEG, WebP, SVG, GIF.
- **Text watermark**: Set `VLOG_WATERMARK_TYPE=text` and `VLOG_WATERMARK_TEXT="© 2025 MyBrand"`. Configure font size with `VLOG_WATERMARK_TEXT_SIZE` and color with `VLOG_WATERMARK_TEXT_COLOR`.

**Database migrations**: Schema changes are managed by Alembic. New databases get all tables via `python api/database.py`. Existing databases being upgraded should first run `python api/database.py stamp 001` to mark current state, then future migrations apply normally.

**Distributed transcoding**: Remote workers register via Worker API and receive API keys. Workers poll for jobs, claim them atomically, download source files via HTTP, transcode locally, and upload HLS output as tar.gz. Progress updates are sent to the API and visible in the admin UI.

**Hardware acceleration**: Remote workers can use GPU encoding for faster transcoding:
- **NVIDIA NVENC**: h264_nvenc, hevc_nvenc, av1_nvenc (RTX 40 series)
- **Intel VAAPI**: h264_vaapi, hevc_vaapi, av1_vaapi (Arc GPUs, QuickSync)
- GPU is auto-detected at worker startup; falls back to CPU if unavailable
- Consumer NVIDIA GPUs have session limits (RTX 3090: 3 sessions, RTX 4090: 5 sessions)
- Use `Dockerfile.worker.gpu` for GPU-enabled containers (Rocky Linux 10 based)

**GPU Worker Container**: The `Dockerfile.worker.gpu` is based on Rocky Linux 10:
- FFmpeg 7.1.2 from RPM Fusion (pre-built with nvenc, vaapi, qsv encoders)
- intel-media-driver 25.2.6 for Battlemage (Arc B580) and newer Arc GPUs
- Python 3.12
- Local registry available at `localhost:9003` (image: `vlog-worker-gpu:rocky10`)

**Parallel Quality Encoding**: Encode multiple quality variants simultaneously to reduce total transcoding time:
- Configurable via `VLOG_PARALLEL_QUALITIES` (default: 1 for sequential, 3 recommended for GPUs)
- Auto-detection via `VLOG_PARALLEL_QUALITIES_AUTO=true` (default): uses `min(3, gpu.max_sessions - 1)`
- Qualities grouped by resolution: high-res (1080p+) processed together, then low-res
- Example: 6 qualities with parallel=3 produces 2 batches instead of 6 sequential encodes
- ~2x speedup on GPUs that support concurrent encoding sessions

**Redis Job Queue & Real-Time Updates**: Optional Redis integration for instant job dispatch and real-time UI updates:
- **Redis Streams**: Replaces database polling with instant job dispatch (configurable priority: high/normal/low)
- **Pub/Sub + SSE**: Real-time transcoding progress and worker status updates in the admin UI
- **Graceful fallback**: Falls back to database polling if Redis is unavailable
- **Circuit breaker**: Automatically disables Redis after consecutive failures with exponential backoff
- **Modes**: `database` (default, polling only), `redis` (Redis required), `hybrid` (Redis preferred, fallback to polling)

To enable Redis:
```bash
# Set up Redis password (required for security)
sudo mkdir -p /etc/vlog
sudo cp systemd/vlog-redis.env.example /etc/vlog/redis.env
sudo chmod 600 /etc/vlog/redis.env
# Edit /etc/vlog/redis.env and set REDIS_PASSWORD to a strong value
# Generate password: python -c "import secrets; print(secrets.token_urlsafe(32))"

# Start Redis container (systemd service provided)
sudo cp systemd/vlog-redis.service.template /etc/systemd/system/vlog-redis.service
sudo systemctl daemon-reload
sudo systemctl enable --now vlog-redis

# Configure vlog to use Redis (include password in URL)
export VLOG_REDIS_URL="redis://:YOUR_REDIS_PASSWORD@localhost:6379"
export VLOG_JOB_QUEUE_MODE="hybrid"  # or "redis" for Redis-only
```

**Alerting & Monitoring**: Optional webhook notifications for transcoding events:
- **Stale job recovery**: Alert when jobs are recovered from crashed/stale workers
- **Max retries exceeded**: Alert when a job fails after all retry attempts
- **Repeated failures**: Alert when the same video fails multiple times (pattern detection)
- **Worker lifecycle**: Optional alerts for worker startup/shutdown
- **Rate limiting**: Configurable cooldown between alerts to prevent flooding
- **Metrics tracking**: Internal counters for monitoring (stale recoveries, max retries, failures)

To enable webhook alerts:
```bash
# Configure webhook URL (Slack, Discord, custom endpoint, etc.)
export VLOG_ALERT_WEBHOOK_URL="https://hooks.slack.com/services/xxx"

# Optional: adjust rate limiting (default: 300 seconds between same alert type)
export VLOG_ALERT_RATE_LIMIT_SECONDS=300

# Optional: adjust webhook timeout (default: 10 seconds)
export VLOG_ALERT_WEBHOOK_TIMEOUT=10
```

Alert payload format (JSON):
```json
{
  "event": "job_max_retries_exceeded",
  "timestamp": "2024-01-15T10:30:00Z",
  "details": {
    "video_id": 123,
    "video_slug": "my-video",
    "max_attempts": 3,
    "last_error": "FFmpeg failed..."
  },
  "metrics": {
    "stale_jobs_recovered": 5,
    "jobs_max_retries_exceeded": 2,
    "jobs_failed": 10
  }
}
```

### Database Schema

Core tables: `categories`, `videos` (with `deleted_at` for soft-delete), `video_qualities`
Tags: `tags`, `video_tags` (many-to-many relationship for granular content organization)
Analytics: `viewers`, `playback_sessions` (cookie-based viewer tracking, watch progress)
Transcoding: `transcoding_jobs`, `quality_progress` (checkpoint-based resumable transcoding)
Workers: `workers`, `worker_api_keys` (remote worker registration with API key auth)
Transcription: `transcriptions` (whisper-generated subtitles with VTT output)

## Multi-Instance Deployment Notes

When running multiple API instances (e.g., behind a load balancer):

- **Analytics cache**: By default uses in-memory storage (per-process). For consistent analytics across instances, use Redis: `VLOG_ANALYTICS_CACHE_STORAGE_URL=redis://localhost:6379`
  - To disable caching entirely: `VLOG_ANALYTICS_CACHE_ENABLED=false`
  - With in-memory cache, different instances may show slightly different analytics counts until caches expire (default: 60 seconds)
  - With Redis cache, all instances share the same cache state for consistent results

- **Rate limiting storage**: By default uses in-memory storage (per-process). For consistent rate limiting across instances, use Redis: `VLOG_RATE_LIMIT_STORAGE_URL=redis://localhost:6379`

- **PostgreSQL**: The default database backend. Supports concurrent readers and writers, making it suitable for multi-instance deployments.

- **Worker API admin endpoints**: Require `VLOG_WORKER_ADMIN_SECRET` to be configured. Generate a secret:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

- **Admin API authentication**: Optional API key authentication for the Admin API (port 9001). When `VLOG_ADMIN_API_SECRET` is set, all `/api/*` endpoints require the `X-Admin-Secret` header. The admin web UI will prompt for the secret on first access. If not set, the Admin API is unauthenticated (backwards compatible).
  ```bash
  # Generate a secret
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  # Enable authentication
  export VLOG_ADMIN_API_SECRET="your-generated-secret"
  ```

## Important Configuration

- `pyproject.toml`: Package configuration with dependencies and CLI entry point
- `alembic.ini`: Database migration config (URL set dynamically from config.py)
- `config.py`: Central config for paths, ports, quality presets, worker settings, transcription options
  - All settings support environment variable overrides (prefix: `VLOG_`)
  - Database: `VLOG_DATABASE_URL` (default: `postgresql://vlog:vlog_password@localhost/vlog`)
  - Rate limiting: `VLOG_RATE_LIMIT_ENABLED`, `VLOG_RATE_LIMIT_PUBLIC_DEFAULT`, etc.
  - CORS: `VLOG_CORS_ORIGINS`, `VLOG_ADMIN_CORS_ORIGINS`
  - Archive: `VLOG_ARCHIVE_RETENTION_DAYS` (default: 30)
  - Admin API: `VLOG_ADMIN_API_SECRET` (empty = no auth, set to enable API key authentication)
  - Worker API: `VLOG_WORKER_API_PORT`, `VLOG_WORKER_API_URL`, `VLOG_WORKER_API_KEY`, `VLOG_WORKER_ADMIN_SECRET`
  - Remote workers: `VLOG_WORKER_HEARTBEAT_INTERVAL`, `VLOG_WORKER_CLAIM_DURATION`, `VLOG_WORKER_POLL_INTERVAL`
  - Hardware acceleration: `VLOG_HWACCEL_TYPE` (auto, nvidia, intel, none), `VLOG_HWACCEL_PREFERRED_CODEC` (h264, hevc, av1)
  - Parallel encoding: `VLOG_PARALLEL_QUALITIES` (default: 1), `VLOG_PARALLEL_QUALITIES_AUTO` (default: true)
  - Audit logging: `VLOG_AUDIT_LOG_ENABLED` (default: true), `VLOG_AUDIT_LOG_PATH` (default: /var/log/vlog/audit.log)
  - Analytics cache: `VLOG_ANALYTICS_CACHE_ENABLED`, `VLOG_ANALYTICS_CACHE_TTL`, `VLOG_ANALYTICS_CACHE_STORAGE_URL` (memory:// or redis://)
  - Redis: `VLOG_REDIS_URL` (empty = disabled), `VLOG_JOB_QUEUE_MODE` (database, redis, hybrid), `VLOG_REDIS_POOL_SIZE` (default: 10)
  - SSE: `VLOG_SSE_HEARTBEAT_INTERVAL` (default: 30s), `VLOG_SSE_RECONNECT_TIMEOUT_MS` (default: 3000)
  - Alerting: `VLOG_ALERT_WEBHOOK_URL` (empty = disabled), `VLOG_ALERT_WEBHOOK_TIMEOUT` (default: 10s), `VLOG_ALERT_RATE_LIMIT_SECONDS` (default: 300)
  - Watermark: `VLOG_WATERMARK_ENABLED` (default: false), `VLOG_WATERMARK_TYPE` (image or text), `VLOG_WATERMARK_IMAGE` (path for image type), `VLOG_WATERMARK_TEXT` (text content for text type), `VLOG_WATERMARK_TEXT_SIZE` (8-72px, default: 16), `VLOG_WATERMARK_TEXT_COLOR` (CSS color, default: white), `VLOG_WATERMARK_POSITION` (top-left, top-right, bottom-left, bottom-right, center), `VLOG_WATERMARK_OPACITY` (0.0-1.0, default: 0.5), `VLOG_WATERMARK_PADDING` (pixels, default: 16), `VLOG_WATERMARK_MAX_WIDTH_PERCENT` (1-50, default: 15, for images only)
- NAS mount: Configure your NAS share in `/etc/fstab` to mount at `/mnt/nas`
- systemd services: Located in `systemd/` folder, use venv Python with security hardening
- Package installed in development mode: `pip install -e .` makes `vlog` CLI available
- Local Docker registry: Running on port 9003 for GPU worker images (`localhost:9003/vlog-worker-gpu:rocky10`)

## Python Version Note

Uses Python 3.9 - avoid `str | None` union syntax, use `Optional[str]` from typing instead.

## Testing Notes

- Set `VLOG_TEST_MODE=1` to skip NAS directory creation (required for tests/CI)
- Tests use pytest-asyncio with function-scoped async fixtures
- Test clients patch `config` and `api.database` to use temp directories
- CI runs tests and ruff linting on push/PR to main (see `.github/workflows/tests.yml`)

## Package Structure

The project uses proper Python packaging via `pyproject.toml`:
- All modules (api, worker, cli, config) are installed as a package
- No sys.path manipulation needed
- CLI installed as console script entry point: `vlog` command
- Development install: `pip install -e .` from repository root

## Git Workflow

- ALWAYS use pull requests - never push directly to main
- Branch protection is enabled; the "All Tests Passed" CI job must succeed
- CI runs pytest and ruff check on all PRs to main