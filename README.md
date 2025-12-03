# VLog - Self-Hosted Video Platform

A lightweight, self-hosted video platform with 4K support, HLS adaptive streaming, auto-transcription, and a clean modern UI.

## Features

- **4K Video Support** - Transcode to 2160p, 1440p, 1080p, 720p, 480p, 360p (YouTube-style quality ladder)
- **HLS Streaming** - Adaptive bitrate for smooth playback on any connection
- **Auto-Transcription** - Automatic subtitles using faster-whisper (WebVTT captions)
- **Event-Driven Processing** - Instant video detection via inotify (no polling delay)
- **Crash Recovery** - Checkpoint-based resumable transcoding
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
cd /home/damen
git clone <repo-url> vlog
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
| Transcoding Worker | - | Event-driven video processing |
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
```

## Directory Structure

```
vlog/
├── pyproject.toml        # Package configuration
├── api/                  # FastAPI backend
│   ├── __init__.py
│   ├── public.py         # Public API (port 9000)
│   ├── admin.py          # Admin API (port 9001)
│   ├── database.py       # SQLAlchemy schema
│   └── schemas.py        # Pydantic models
├── worker/
│   ├── __init__.py
│   ├── transcoder.py     # HLS transcoding worker
│   └── transcription.py  # Whisper transcription worker
├── web/
│   ├── public/           # Public-facing frontend
│   └── admin/            # Admin interface
├── cli/
│   ├── __init__.py
│   └── main.py           # Command-line tool
├── docs/                 # Documentation
├── config.py             # Central configuration
├── vlog.db               # SQLite database (local)
└── start.sh              # Development startup script
```

## Storage Layout

```
/mnt/nas/vlog-storage/
├── uploads/              # Temporary upload storage
│   └── {video_id}.mp4
└── videos/               # HLS output
    └── {slug}/
        ├── master.m3u8   # Adaptive bitrate playlist
        ├── 1080p.m3u8    # Quality-specific playlist
        ├── 1080p_0000.ts # Video segments
        ├── thumbnail.jpg
        └── captions.vtt  # WebVTT subtitles
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

## Documentation

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture and data flows |
| [API.md](docs/API.md) | Complete API reference |
| [DATABASE.md](docs/DATABASE.md) | Database schema documentation |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Configuration options |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production deployment guide |
| [CLAUDE.md](CLAUDE.md) | AI assistant project context |

## Configuration

Edit `config.py` to customize:

```python
# Storage paths
NAS_STORAGE = Path("/mnt/nas/vlog-storage")

# Server ports
PUBLIC_PORT = 9000
ADMIN_PORT = 9001

# Transcription
WHISPER_MODEL = "medium"  # tiny, base, small, medium, large-v3
TRANSCRIPTION_ENABLED = True

# Worker mode
WORKER_USE_FILESYSTEM_WATCHER = True  # inotify vs polling
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

## Tech Stack

- **Backend:** FastAPI + Uvicorn
- **Database:** SQLite + SQLAlchemy
- **Video Processing:** ffmpeg, ffprobe
- **Transcription:** faster-whisper
- **File Monitoring:** watchdog (inotify)
- **Frontend:** Alpine.js + Tailwind CSS v4
- **Video Player:** hls.js
- **Process Management:** systemd

## License

MIT License - See LICENSE file for details.
