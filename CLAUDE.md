# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VLog is a self-hosted video platform with 4K support and HLS streaming. It consists of three services:
- **Public API** (port 9000): FastAPI server for video browsing, playback, and analytics
- **Admin API** (port 9001): FastAPI server for uploads and management (internal only)
- **Transcoding Worker**: Background process that converts uploads to HLS with multiple quality variants (event-driven with inotify)

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

# Database initialization
python api/database.py
```

## Architecture

```
api/
├── public.py     # Public browsing API, serves /api/videos, /api/categories, HLS files, analytics
├── admin.py      # Upload/management API, multipart uploads, CRUD operations
├── database.py   # SQLAlchemy table definitions (categories, videos, video_qualities, analytics, transcoding_jobs, transcriptions)
└── schemas.py    # Pydantic models for request/response validation

worker/
└── transcoder.py # Event-driven (inotify) transcoder with checkpoint-based resumable processing

web/
├── public/       # Tailwind + Alpine.js frontend for browsing
└── admin/        # Admin UI for uploads and video management

cli/
└── vlog          # Argparse CLI, talks to admin API via httpx
```

### Key Flows

**Upload flow**: File goes to `UPLOADS_DIR/{video_id}.ext` -> worker detects via inotify (or fallback polling) -> ffmpeg transcodes to HLS in `VIDEOS_DIR/{slug}/` -> generates `master.m3u8` with quality variants -> marks video "ready"

**HLS output structure**: Each video gets `{slug}/master.m3u8` (adaptive playlist) + `{quality}.m3u8` + `{quality}_XXXX.ts` segments + `thumbnail.jpg`

**Quality ladder**: Only generates qualities <= source resolution. Presets defined in `config.py`: 2160p (15Mbps), 1440p (8Mbps), 1080p (5Mbps), 720p (2.5Mbps), 480p (1Mbps), 360p (600kbps)

**Transcoding recovery**: Jobs have per-quality checkpoints. On crash, worker detects stale jobs and resumes from last checkpoint. Completed qualities are preserved on retry.

**Transcription**: Optional auto-transcription using faster-whisper generates WebVTT subtitles. Configurable model size and language detection.

### Database Schema

Core tables: `categories`, `videos`, `video_qualities`
Analytics: `viewers`, `playback_sessions` (cookie-based viewer tracking, watch progress)
Transcoding: `transcoding_jobs`, `quality_progress` (checkpoint-based resumable transcoding)
Transcription: `transcriptions` (whisper-generated subtitles with VTT output)

## Important Configuration

- `pyproject.toml`: Package configuration with dependencies and CLI entry point
- `config.py`: Central config for paths, ports, quality presets, worker settings, transcription options
- NAS mount: `//10.0.10.84/MainPool` mounted at `/mnt/nas` via fstab
- systemd services use venv Python directly: `/home/damen/vlog/venv/bin/python`
- Package installed in development mode: `pip install -e .` makes `vlog` CLI available

## Python Version Note

Uses Python 3.9 - avoid `str | None` union syntax, use `Optional[str]` from typing instead.

## Package Structure

The project uses proper Python packaging via `pyproject.toml`:
- All modules (api, worker, cli, config) are installed as a package
- No sys.path manipulation needed
- CLI installed as console script entry point: `vlog` command
- Development install: `pip install -e .` from repository root