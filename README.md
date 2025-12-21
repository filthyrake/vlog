# VLog - Self-Hosted Video Platform

> **Note:** This project was built almost entirely with AI assistance (Claude Code). It's a "vibe-coded" project - functional and useful, but built through AI pair-programming rather than traditional development. Use accordingly, and contributions are welcome!

A lightweight, self-hosted video platform with 4K support, HLS adaptive streaming, auto-transcription, and a clean modern UI.

## Features

- **4K Video Support** - Transcode to 2160p, 1440p, 1080p, 720p, 480p, 360p (YouTube-style quality ladder)
- **HLS Streaming** - Adaptive bitrate for smooth playback on any connection
- **Distributed Transcoding** - Containerized workers in Kubernetes with automatic job distribution
- **Auto-Transcription** - Automatic subtitles using faster-whisper (WebVTT captions)
- **Event-Driven Processing** - Instant video detection via inotify (no polling delay)
- **Crash Recovery** - Checkpoint-based resumable transcoding
- **Soft-Delete** - Deleted videos go to archive with configurable retention period
- **Rate Limiting** - Configurable per-endpoint rate limits (memory or Redis storage)
- **Modern UI** - Clean, responsive Alpine.js + Tailwind CSS frontend
- **Playback Analytics** - Track views, watch time, completion rates
- **CLI + Web Upload** - Upload via command line or web interface
- **YouTube Migration** - Download and import videos directly from YouTube

## Requirements

- **Python 3.9+** (uses `Optional[]` typing syntax)
- **ffmpeg** with libx264 and aac support
- **yt-dlp** (optional, for YouTube downloads)
- **faster-whisper** (optional, for auto-transcription)

## Quick Start

```bash
# Clone and setup
git clone https://github.com/filthyrake/vlog.git
cd vlog

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install package in development mode
pip install -e .

# Initialize database
python api/database.py

# Start all services
./start.sh
```

Access:
- **Public Site:** http://localhost:9000
- **Admin Panel:** http://localhost:9001

## Services

| Service | Port | Description |
|---------|------|-------------|
| Public API | 9000 | Video browsing and HLS playback |
| Admin API | 9001 | Upload and management (internal only) |
| Worker API | 9002 | Remote worker registration and job distribution |
| Transcoding Worker | - | Local event-driven video processing |
| Remote Workers | - | Containerized transcoding in Kubernetes |
| Transcription Worker | - | Auto-captioning (faster-whisper) |

## CLI Usage

```bash
# The package installs a 'vlog' command automatically
# Make sure your venv is activated

# Upload a video
vlog upload video.mp4 -t "My Video Title" -c "Category Name"

# List videos
vlog list
vlog list -s processing  # Filter by status

# Manage categories
vlog categories                          # List all
vlog categories --create "Tutorials"     # Create new

# Download from YouTube
vlog download "https://youtube.com/watch?v=..." -c "Category"

# Delete a video
vlog delete 123

# Worker management (for distributed transcoding)
vlog worker register --name "k8s-worker-1"  # Get API key for new worker
vlog worker status                           # Show all workers and current jobs
vlog worker list                             # List registered workers
```

## Directory Structure

```
vlog/
├── pyproject.toml        # Package configuration
├── api/                  # FastAPI backend
│   ├── public.py         # Public API (port 9000)
│   ├── admin.py          # Admin API (port 9001)
│   ├── worker_api.py     # Worker API (port 9002)
│   ├── worker_auth.py    # API key authentication
│   ├── database.py       # SQLAlchemy schema
│   └── schemas.py        # Pydantic models
├── worker/
│   ├── transcoder.py     # Local HLS transcoding worker
│   ├── remote_transcoder.py  # Containerized remote worker
│   ├── hwaccel.py        # GPU detection and hardware encoder selection
│   ├── http_client.py    # Worker API client
│   └── transcription.py  # Whisper transcription worker
├── web/
│   ├── public/           # Public-facing frontend
│   └── admin/            # Admin interface
├── cli/
│   └── main.py           # Command-line tool (upload, worker mgmt)
├── k8s/                  # Kubernetes manifests for workers
├── systemd/              # Systemd service files
├── docs/                 # Documentation
├── Dockerfile.worker     # Container image for CPU-only workers
├── Dockerfile.worker.gpu # GPU-enabled container (Rocky Linux 10, NVENC/VAAPI)
├── config.py             # Central configuration (env var support)
├── vlog.db               # SQLite database (local)
└── start.sh              # Development startup script
```

## Storage Layout

```
/mnt/nas/vlog-storage/
├── uploads/              # Temporary upload storage
│   └── {video_id}.mp4
├── videos/               # HLS output
│   └── {slug}/
│       ├── master.m3u8   # Adaptive bitrate playlist
│       ├── 1080p.m3u8    # Quality-specific playlist
│       ├── 1080p_0000.ts # Video segments
│       ├── thumbnail.jpg
│       └── captions.vtt  # WebVTT subtitles
└── archive/              # Soft-deleted videos (moved here)
    └── {slug}/           # Same structure as videos/
```

## Quality Presets

Videos are transcoded to all resolutions at or below the source:

| Quality | Bitrate | Audio |
|---------|---------|-------|
| 2160p (4K) | 15 Mbps | 192 kbps |
| 1440p | 8 Mbps | 192 kbps |
| 1080p | 5 Mbps | 128 kbps |
| 720p | 2.5 Mbps | 128 kbps |
| 480p | 1 Mbps | 96 kbps |
| 360p | 600 kbps | 96 kbps |

## Production Deployment

For production, use systemd to manage services:

```bash
# Enable and start all services
sudo systemctl enable vlog.target
sudo systemctl start vlog.target

# Check status
sudo systemctl status vlog-public vlog-admin vlog-worker

# View logs
sudo journalctl -u vlog-worker -f
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for full production setup including nginx configuration.

### Distributed Transcoding (Kubernetes)

For horizontal scaling, deploy containerized workers to Kubernetes:

```bash
# Register workers and get API keys
vlog worker register --name "k8s-worker-1"
# Save the returned API key

# Deploy to k8s
kubectl apply -f k8s/namespace.yaml
kubectl create secret generic vlog-worker-secret -n vlog \
  --from-literal=api-key='YOUR_API_KEY'
kubectl apply -f k8s/

# Check worker status
vlog worker status
kubectl logs -n vlog -l app=vlog-worker -f
```

See [k8s/README.md](k8s/README.md) for detailed Kubernetes deployment instructions.

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture and data flows |
| [API.md](docs/API.md) | Complete API reference |
| [DATABASE.md](docs/DATABASE.md) | Database schema documentation |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Configuration options |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production deployment guide |

## Configuration

All settings can be configured via environment variables (prefix: `VLOG_`) or by editing `config.py`:

```bash
# Storage paths
VLOG_STORAGE_PATH=/mnt/nas/vlog-storage

# Server ports
VLOG_PUBLIC_PORT=9000
VLOG_ADMIN_PORT=9001

# Transcription
VLOG_WHISPER_MODEL=medium  # tiny, base, small, medium, large-v3
VLOG_TRANSCRIPTION_ENABLED=true

# Rate limiting
VLOG_RATE_LIMIT_ENABLED=true
VLOG_RATE_LIMIT_PUBLIC_DEFAULT=100/minute
VLOG_RATE_LIMIT_STORAGE_URL=memory://  # or redis://localhost:6379

# Soft-delete retention
VLOG_ARCHIVE_RETENTION_DAYS=30

# Local worker mode
VLOG_WORKER_USE_FILESYSTEM_WATCHER=true  # inotify vs polling

# Remote workers (Kubernetes)
VLOG_WORKER_API_PORT=9002
VLOG_WORKER_API_URL=http://your-server:9002
VLOG_WORKER_API_KEY=your-api-key
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for all options.

## Troubleshooting

### Video stuck in "processing"

```bash
# Check worker logs
sudo journalctl -u vlog-worker -f

# Common causes:
# - ffmpeg not installed or missing codecs
# - Disk space full
# - NAS mount issues
```

### Upload fails

```bash
# Check admin logs
sudo journalctl -u vlog-admin -f

# Common causes:
# - uploads/ directory not writable
# - File too large (check nginx client_max_body_size)
```

### Playback issues

1. Verify HLS files exist: `ls /mnt/nas/vlog-storage/videos/{slug}/`
2. Check MIME types in browser dev tools (`.ts` should be `video/mp2t`)
3. Check CORS headers if using different domains

### Transcription not working

```bash
# Check transcription worker
sudo journalctl -u vlog-transcription -f

# Verify faster-whisper is installed
pip show faster-whisper
```

## Testing

VLog has comprehensive test coverage with unit, integration, and end-to-end tests.

```bash
# Install test dependencies
pip install pytest pytest-asyncio pytest-cov

# Run all tests with coverage
VLOG_TEST_MODE=1 pytest

# Run specific test types
VLOG_TEST_MODE=1 pytest -m integration  # Integration tests only
VLOG_TEST_MODE=1 pytest -m e2e          # End-to-end tests only

# Generate coverage report
VLOG_TEST_MODE=1 pytest --cov=api --cov=worker --cov=cli --cov-report=html
open htmlcov/index.html  # View coverage report
```

**Requirements:**
- PostgreSQL server (tests create temporary databases)
- Test environment variables (see `TESTING.md`)

**Test Coverage:**
- 37+ test files with 900+ test cases
- Unit tests for all major components
- Integration tests for workflows
- End-to-end tests for complete flows
- Database migration tests

See [TESTING.md](TESTING.md) for detailed testing guide.

## Tech Stack

- **Backend:** FastAPI + Uvicorn
- **Database:** SQLite + SQLAlchemy
- **Video Processing:** ffmpeg, ffprobe
- **Transcription:** faster-whisper
- **File Monitoring:** watchdog (inotify)
- **Rate Limiting:** slowapi (memory or Redis)
- **Frontend:** Alpine.js + Tailwind CSS v4
- **Video Player:** hls.js
- **Process Management:** systemd

## License

MIT License - See LICENSE file for details.
