# VLog - Self-Hosted Video Platform

A lightweight, self-hosted video platform with 4K support, HLS streaming, and a clean modern UI.

## Features

- **4K Video Support** - Transcode to 2160p, 1440p, 1080p, 720p, 480p, 360p (YouTube-style quality ladder)
- **HLS Streaming** - Adaptive bitrate for smooth playback on any connection
- **Modern UI** - Clean, responsive design with dark theme
- **Categories** - Organize videos with categories and descriptions
- **CLI + Web Upload** - Upload via command line or web interface
- **YouTube Migration** - Download and import videos directly from YouTube

## Requirements

- Python 3.10+
- ffmpeg (with libx264 and aac support)
- yt-dlp (optional, for YouTube downloads)

## Quick Start

```bash
# Create and activate virtual environment
cd /home/damen/vlog
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start all services
./start.sh
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| Public Site | 9000 | Video browsing and playback |
| Admin Panel | 9001 | Upload and manage videos (internal only) |
| Worker | - | Background transcoding process |

## CLI Usage

```bash
# Add CLI to path (optional)
export PATH="$PATH:/home/damen/vlog/cli"

# Upload a video
./cli/vlog upload video.mp4 -t "My Video Title" -c "Category Name"

# List videos
./cli/vlog list
./cli/vlog list -s processing  # Filter by status

# Manage categories
./cli/vlog categories           # List all
./cli/vlog categories --create "Tutorials" -d "Tutorial videos"

# Download from YouTube
./cli/vlog download "https://youtube.com/watch?v=..." -c "Category"

# Delete a video
./cli/vlog delete 123
```

## Directory Structure

```
vlog/
├── api/              # FastAPI backend
├── cli/              # Command-line tools
├── web/
│   ├── public/       # Public-facing frontend
│   └── admin/        # Admin interface
├── videos/           # Transcoded HLS output
├── uploads/          # Temporary upload storage
├── worker/           # Transcoding worker
├── vlog.db           # SQLite database
└── start.sh          # Start all services
```

## Production Deployment

For production, run each service separately:

```bash
# In separate terminals/screens/tmux sessions:
./start-public.sh   # Public site (port 9000)
./start-admin.sh    # Admin panel (port 9001) - don't expose externally!
./start-worker.sh   # Transcoding worker
```

### nginx Reverse Proxy

Example nginx config for your subdomain:

```nginx
server {
    listen 80;
    server_name videos.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Increase limits for video uploads (if proxying admin)
    client_max_body_size 10G;
}
```

## Migrating from YouTube

1. Install yt-dlp: `pip install yt-dlp`
2. Use the CLI download command:
   ```bash
   ./cli/vlog download "https://youtube.com/watch?v=VIDEO_ID" \
       -t "Video Title" \
       -d "Description" \
       -c "Category"
   ```

For bulk migration, you can script it:
```bash
while read url; do
    ./cli/vlog download "$url" -c "My Category"
done < youtube_urls.txt
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

## Troubleshooting

**Video stuck in "processing"**
- Check if the worker is running
- Check worker output for ffmpeg errors
- Ensure ffmpeg is installed with required codecs

**Upload fails**
- Check disk space
- Ensure uploads/ directory is writable

**Playback issues**
- Verify HLS files exist in videos/{slug}/
- Check browser console for CORS errors
