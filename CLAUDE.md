# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VLog is a self-hosted video platform with 4K support and HLS streaming. It consists of these services:
- **Public API** (port 9000): FastAPI server for video browsing, playback, and analytics
- **Admin API** (port 9001): FastAPI server for uploads and management (internal only)
- **Worker API** (port 9002): FastAPI server for remote worker registration, job claiming, and file transfer
- **Transcoding Worker**: Background process that converts uploads to HLS with multiple quality variants
  - **Local mode**: Event-driven with inotify, runs as systemd service
  - **Remote mode**: Containerized workers in Kubernetes, communicate via Worker API

Storage is on NAS at `/mnt/nas/vlog-storage` (videos/ and uploads/), while the SQLite database stays local for performance.

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
├── admin.py        # Upload/management API, multipart uploads, CRUD operations, soft-delete
├── worker_api.py   # Worker API for remote transcoder registration, job claiming, file transfer
├── worker_auth.py  # API key authentication for workers
├── worker_schemas.py # Pydantic models for Worker API
├── database.py     # SQLAlchemy table definitions (categories, videos, workers, transcoding_jobs, etc.)
└── schemas.py      # Pydantic models for request/response validation

worker/
├── transcoder.py       # Local event-driven (inotify) transcoder with checkpoint-based resumable processing
├── remote_transcoder.py # Containerized worker for distributed transcoding via Worker API
├── hwaccel.py          # GPU detection and hardware encoder selection (NVENC, VAAPI)
├── http_client.py      # HTTP client for worker-to-API communication
└── transcription.py    # Whisper transcription worker

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
└── versions/     # Migration scripts (001_initial_schema.py, 003_add_workers_table.py, etc.)
```

### Key Flows

**Upload flow**: File goes to `UPLOADS_DIR/{video_id}.ext` -> worker detects via inotify (or fallback polling) -> ffmpeg transcodes to HLS in `VIDEOS_DIR/{slug}/` -> generates `master.m3u8` with quality variants -> marks video "ready"

**HLS output structure**: Each video gets `{slug}/master.m3u8` (adaptive playlist) + `{quality}.m3u8` + `{quality}_XXXX.ts` segments + `thumbnail.jpg`

**Quality ladder**: Only generates qualities <= source resolution. Presets defined in `config.py`: 2160p (15Mbps), 1440p (8Mbps), 1080p (5Mbps), 720p (2.5Mbps), 480p (1Mbps), 360p (600kbps)

**Transcoding recovery**: Jobs have per-quality checkpoints. On crash, worker detects stale jobs and resumes from last checkpoint. Completed qualities are preserved on retry.

**Transcription**: Optional auto-transcription using faster-whisper generates WebVTT subtitles. Configurable model size and language detection.

**Soft-delete**: Videos are soft-deleted (moved to archive) with configurable retention. Can be restored or permanently deleted.

**Rate limiting**: Configurable per-endpoint rate limits using slowapi. Supports memory or Redis storage.

**Database migrations**: Schema changes are managed by Alembic. New databases get all tables via `python api/database.py`. Existing databases being upgraded should first run `python api/database.py stamp 001` to mark current state, then future migrations apply normally.

**Distributed transcoding**: Remote workers register via Worker API and receive API keys. Workers poll for jobs, claim them atomically, download source files via HTTP, transcode locally, and upload HLS output as tar.gz. Progress updates are sent to the API and visible in the admin UI.

**Hardware acceleration**: Remote workers can use GPU encoding for faster transcoding:
- **NVIDIA NVENC**: h264_nvenc, hevc_nvenc, av1_nvenc (RTX 40 series)
- **Intel VAAPI**: h264_vaapi, hevc_vaapi, av1_vaapi (Arc GPUs, QuickSync)
- GPU is auto-detected at worker startup; falls back to CPU if unavailable
- Consumer NVIDIA GPUs have session limits (RTX 3090: 3 sessions, RTX 4090: 5 sessions)
- Use `Dockerfile.worker.gpu` for GPU-enabled containers

### Database Schema

Core tables: `categories`, `videos` (with `deleted_at` for soft-delete), `video_qualities`
Analytics: `viewers`, `playback_sessions` (cookie-based viewer tracking, watch progress)
Transcoding: `transcoding_jobs`, `quality_progress` (checkpoint-based resumable transcoding)
Workers: `workers`, `worker_api_keys` (remote worker registration with API key auth)
Transcription: `transcriptions` (whisper-generated subtitles with VTT output)

## Important Configuration

- `pyproject.toml`: Package configuration with dependencies and CLI entry point
- `alembic.ini`: Database migration config (URL set dynamically from config.py)
- `config.py`: Central config for paths, ports, quality presets, worker settings, transcription options
  - All settings support environment variable overrides (prefix: `VLOG_`)
  - Rate limiting: `VLOG_RATE_LIMIT_ENABLED`, `VLOG_RATE_LIMIT_PUBLIC_DEFAULT`, etc.
  - CORS: `VLOG_CORS_ORIGINS`, `VLOG_ADMIN_CORS_ORIGINS`
  - Archive: `VLOG_ARCHIVE_RETENTION_DAYS` (default: 30)
  - Worker API: `VLOG_WORKER_API_PORT`, `VLOG_WORKER_API_URL`, `VLOG_WORKER_API_KEY`
  - Remote workers: `VLOG_WORKER_HEARTBEAT_INTERVAL`, `VLOG_WORKER_CLAIM_DURATION`, `VLOG_WORKER_POLL_INTERVAL`
  - Hardware acceleration: `VLOG_HWACCEL_TYPE` (auto, nvidia, intel, none), `VLOG_HWACCEL_PREFERRED_CODEC` (h264, hevc, av1)
- NAS mount: `//10.0.10.84/MainPool` mounted at `/mnt/nas` via fstab
- systemd services: Located in `systemd/` folder, use venv Python with security hardening
- Package installed in development mode: `pip install -e .` makes `vlog` CLI available

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