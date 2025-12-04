# VLog Deployment Guide

## Prerequisites

### System Requirements

- **OS:** Linux (tested on RHEL/CentOS 9)
- **Python:** 3.9+ (uses `Optional[]` syntax instead of `X | None`)
- **RAM:** 4GB minimum (8GB+ recommended for transcription)
- **Storage:** NAS or large local storage for video files

### Required Software

```bash
# Python 3.9+
python3 --version

# ffmpeg with libx264 and aac
ffmpeg -version

# Optional: yt-dlp for YouTube downloads
pip install yt-dlp
```

---

## Development Setup

### 1. Clone and Setup Virtual Environment

```bash
cd /home/damen
git clone <repo-url> vlog
cd vlog

python3 -m venv venv
source venv/bin/activate
pip install -e .  # Install package in development mode
```

### 2. Create Storage Directories

```bash
# For NAS setup
sudo mkdir -p /mnt/nas/vlog-storage/{videos,uploads,archive}
sudo chown $USER:$USER /mnt/nas/vlog-storage

# Or for local storage, set environment variable
export VLOG_STORAGE_PATH=/home/damen/vlog-storage
```

### 3. Initialize Database

```bash
python api/database.py
```

### 4. Start Development Servers

```bash
# Start all services
./start.sh

# Or start individually
./start-public.sh   # Port 9000
./start-admin.sh    # Port 9001
./start-worker.sh   # Transcoding
./start-transcription.sh  # Transcription (optional)
```

---

## Production Deployment

### 1. System Setup

#### NAS Mount (if using NAS)

```bash
# /etc/fstab entry
//10.0.10.84/MainPool/vlog-storage /mnt/nas/vlog-storage cifs credentials=/etc/samba/credentials,uid=damen,gid=damen,file_mode=0644,dir_mode=0755 0 0
```

#### Create Credentials File

```bash
sudo tee /etc/samba/credentials << EOF
username=your_nas_user
password=your_nas_password
EOF
sudo chmod 600 /etc/samba/credentials
```

### 2. Systemd Service Files

Service files are provided in the `systemd/` directory. Copy them to `/etc/systemd/system/`:

```bash
sudo cp systemd/*.service systemd/*.target /etc/systemd/system/
sudo systemctl daemon-reload
```

The service files include:
- **Security hardening** - PrivateTmp, ProtectSystem, NoNewPrivileges
- **Resource limits** - Memory caps, file descriptor limits
- **Restart policies** - Automatic restart on failure with rate limiting
- **Venv Python** - Uses `/home/damen/vlog/venv/bin/python` directly

#### vlog-public.service

```ini
[Unit]
Description=VLog Public API
After=network.target mnt-nas.mount
Wants=mnt-nas.mount

[Service]
Type=simple
User=damen
Group=damen
WorkingDirectory=/home/damen/vlog
ExecStart=/home/damen/vlog/venv/bin/python -m uvicorn api.public:app --host 0.0.0.0 --port 9000 --proxy-headers --forwarded-allow-ips='127.0.0.1,10.0.10.1'

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true
CapabilityBoundingSet=
AmbientCapabilities=

# Allowed paths
ReadWritePaths=/home/damen/vlog /mnt/nas/vlog-storage

# Resource limits
LimitNOFILE=65535
MemoryMax=2G

# Restart policy
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
```

#### vlog-admin.service

```ini
[Unit]
Description=VLog Admin API
After=network.target mnt-nas.mount
Wants=mnt-nas.mount

[Service]
Type=simple
User=damen
Group=damen
WorkingDirectory=/home/damen/vlog
ExecStart=/home/damen/vlog/venv/bin/python -m uvicorn api.admin:app --host 0.0.0.0 --port 9001

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true

# Allowed paths
ReadWritePaths=/home/damen/vlog /mnt/nas/vlog-storage

# Resource limits
LimitNOFILE=65535
MemoryMax=2G

# Restart policy
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### vlog-worker.service

```ini
[Unit]
Description=VLog Transcoding Worker
After=network.target mnt-nas.mount
Wants=mnt-nas.mount

[Service]
Type=simple
User=damen
Group=damen
WorkingDirectory=/home/damen/vlog
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/damen/vlog/venv/bin/python worker/transcoder.py

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true

# Allowed paths
ReadWritePaths=/home/damen/vlog /mnt/nas/vlog-storage

# Resource limits (higher for transcoding)
LimitNOFILE=65535
MemoryMax=8G

# Restart policy
Restart=on-failure
RestartSec=30

# Timeouts (longer for transcoding jobs)
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
```

#### vlog-transcription.service

```ini
[Unit]
Description=VLog Transcription Worker
After=network.target mnt-nas.mount
Wants=mnt-nas.mount

[Service]
Type=simple
User=damen
Group=damen
WorkingDirectory=/home/damen/vlog
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/damen/vlog/venv/bin/python worker/transcription.py

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true

# Allowed paths
ReadWritePaths=/home/damen/vlog /mnt/nas/vlog-storage

# Resource limits (higher for whisper model)
LimitNOFILE=65535
MemoryMax=8G

# Restart policy
Restart=on-failure
RestartSec=30

# Timeouts (longer for transcription jobs)
TimeoutStartSec=60
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
```

#### vlog.target

```ini
[Unit]
Description=VLog Video Platform
Wants=vlog-public.service vlog-admin.service vlog-worker.service vlog-transcription.service

[Install]
WantedBy=multi-user.target
```

### 3. Enable and Start Services

```bash
sudo systemctl daemon-reload
sudo systemctl enable vlog.target
sudo systemctl start vlog.target

# Check status
sudo systemctl status vlog-public vlog-admin vlog-worker vlog-transcription
```

### 4. Nginx Reverse Proxy

Create `/etc/nginx/conf.d/vlog.conf`:

```nginx
# Public site
server {
    listen 80;
    server_name videos.yourdomain.com;

    # Increase timeouts for long videos
    proxy_read_timeout 300;
    proxy_connect_timeout 300;
    proxy_send_timeout 300;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Video segments need larger buffer
    location /videos/ {
        proxy_pass http://127.0.0.1:9000;
        proxy_buffering off;
        proxy_set_header Host $host;
    }
}

# Admin panel (internal only - restrict access!)
server {
    listen 9001;
    listen [::]:9001;

    # Only allow internal IPs
    allow 10.0.0.0/8;
    allow 192.168.0.0/16;
    allow 127.0.0.1;
    deny all;

    client_max_body_size 50G;  # For large video uploads

    location / {
        proxy_pass http://127.0.0.1:9001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # Upload timeout for large files
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
    }
}
```

### 5. Firewall Configuration

```bash
# Public site
sudo firewall-cmd --permanent --add-port=9000/tcp

# Admin (only if needed externally - NOT recommended)
# sudo firewall-cmd --permanent --add-port=9001/tcp

sudo firewall-cmd --reload
```

---

## SELinux Configuration (RHEL/CentOS)

If SELinux is enforcing:

```bash
# Allow nginx to proxy
sudo setsebool -P httpd_can_network_connect 1

# Allow Python to bind to ports
sudo semanage port -a -t http_port_t -p tcp 9000
sudo semanage port -a -t http_port_t -p tcp 9001
```

---

## Log Management

### View Logs

```bash
# All services
sudo journalctl -u vlog-public -u vlog-admin -u vlog-worker -f

# Specific service
sudo journalctl -u vlog-worker -f

# Since last boot
sudo journalctl -u vlog-public -b
```

### Log Rotation

Logs are managed by journald. Configure retention in `/etc/systemd/journald.conf`:

```ini
[Journal]
SystemMaxUse=1G
MaxRetentionSec=30days
```

---

## Backup Strategy

### Database

```bash
# Backup
cp /home/damen/vlog/vlog.db /backup/vlog-$(date +%Y%m%d).db

# Or with sqlite3
sqlite3 /home/damen/vlog/vlog.db ".backup '/backup/vlog.db'"
```

### Video Files

Video files on NAS should be backed up according to your NAS backup strategy.

---

## Troubleshooting

### Service Won't Start

```bash
# Check status and logs
sudo systemctl status vlog-public
sudo journalctl -u vlog-public -n 50

# Common issues:
# - PYTHONPATH not set correctly
# - NAS not mounted
# - Port already in use
```

### Videos Stuck in Processing

```bash
# Check worker logs
sudo journalctl -u vlog-worker -f

# Common issues:
# - ffmpeg not installed or missing codecs
# - Disk space full
# - NAS connection issues
```

### Database Locked

SQLite can lock with concurrent access:

```bash
# Check for processes using the database
lsof /home/damen/vlog/vlog.db

# Restart services
sudo systemctl restart vlog.target
```

### HLS Playback Issues

1. Check MIME types in nginx (should be `video/mp2t` for `.ts` files)
2. Verify CORS headers in browser dev tools
3. Check that master.m3u8 exists and references correct files

---

## Monitoring

### Health Checks

```bash
# Public API
curl -s http://localhost:9000/api/videos | jq '.[:1]'

# Admin API
curl -s http://localhost:9001/api/categories | jq '.'

# Worker (check if processing)
sudo systemctl status vlog-worker
```

### Resource Monitoring

```bash
# CPU/Memory during transcoding
top -p $(pgrep -f transcoder)

# Disk usage
df -h /mnt/nas/vlog-storage
```
