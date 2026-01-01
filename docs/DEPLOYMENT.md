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
git clone https://github.com/filthyrake/vlog.git
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
export VLOG_STORAGE_PATH=$HOME/vlog-storage
```

### 3. Install and Configure PostgreSQL

```bash
# Install PostgreSQL
sudo dnf install postgresql-server postgresql  # RHEL/Rocky
# OR
sudo apt install postgresql postgresql-contrib  # Debian/Ubuntu

# Initialize and start PostgreSQL
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql

# Create database and user
sudo -u postgres psql << EOF
CREATE USER vlog WITH PASSWORD 'vlog_password';
CREATE DATABASE vlog OWNER vlog;
GRANT ALL PRIVILEGES ON DATABASE vlog TO vlog;
EOF

# Enable local password authentication (edit pg_hba.conf)
# Change 'ident' to 'md5' for local connections:
# local   all   all   md5
# host    all   all   127.0.0.1/32   md5
sudo vim /var/lib/pgsql/data/pg_hba.conf
sudo systemctl restart postgresql
```

### 4. Initialize Database Tables

```bash
python api/database.py
```

### 5. Start Development Servers

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
# /etc/fstab entry (replace <NAS_IP> and <YOUR_USER> with your values)
//<NAS_IP>/share/vlog-storage /mnt/nas/vlog-storage cifs credentials=/etc/samba/credentials,uid=<YOUR_USER>,gid=<YOUR_USER>,file_mode=0644,dir_mode=0755 0 0
```

#### Create Credentials File

```bash
sudo tee /etc/samba/credentials << EOF
username=your_nas_user
password=your_nas_password
EOF
sudo chmod 600 /etc/samba/credentials
```

#### PostgreSQL Setup

```bash
# Install and configure PostgreSQL (see Development Setup for details)
sudo dnf install postgresql-server postgresql
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql

# Create database
sudo -u postgres createuser vlog -P  # Enter password when prompted
sudo -u postgres createdb -O vlog vlog
```

#### Redis Setup (Optional)

Enable Redis for instant job dispatch and real-time progress updates.

**Option 1: Docker Container (recommended)**

Use the provided systemd service file which runs Redis in a Docker container with password authentication:

```bash
# Set up Redis password
sudo mkdir -p /etc/vlog
sudo cp systemd/vlog-redis.env.example /etc/vlog/redis.env
sudo chmod 600 /etc/vlog/redis.env

# Generate and set a strong password
REDIS_PASS=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
sudo sed -i "s/CHANGE_ME_TO_A_SECURE_PASSWORD/$REDIS_PASS/" /etc/vlog/redis.env
echo "Redis password: $REDIS_PASS"  # Save this!

# Install and start Redis container service
sudo cp systemd/vlog-redis.service.template /etc/systemd/system/vlog-redis.service
sudo systemctl daemon-reload
sudo systemctl enable --now vlog-redis

# Verify (use password from above)
docker exec vlog-redis redis-cli --no-auth-warning -a "$REDIS_PASS" ping  # Should return PONG

# Configure VLog to use Redis (include password in URL)
# VLOG_REDIS_URL=redis://:YOUR_REDIS_PASSWORD@localhost:6379
# VLOG_JOB_QUEUE_MODE=hybrid
```

**Option 2: System Redis**

```bash
# Install Redis
sudo dnf install redis  # RHEL/Rocky
# OR
sudo apt install redis-server  # Debian/Ubuntu

# Configure password authentication (edit config file directly)
# Find and uncomment/add the requirepass line:
sudo nano /etc/redis.conf  # or /etc/redis/redis.conf on Debian/Ubuntu
# Add or update: requirepass YOUR_STRONG_PASSWORD

# Enable and start
sudo systemctl restart redis

# Verify
redis-cli --no-auth-warning -a YOUR_STRONG_PASSWORD ping  # Should return PONG

# Configure VLog to use Redis (include password in URL)
# VLOG_REDIS_URL=redis://:YOUR_STRONG_PASSWORD@localhost:6379
# VLOG_JOB_QUEUE_MODE=hybrid
```

### 2. Systemd Service Files

Template service files are provided in the `systemd/` directory. Copy and customize them:

```bash
# Copy template files
for f in systemd/*.template; do
  sudo cp "$f" "/etc/systemd/system/$(basename "$f" .template)"
done
sudo cp systemd/vlog.target /etc/systemd/system/

# Edit each service file to set your paths and username
sudo nano /etc/systemd/system/vlog-public.service
# ... repeat for other services

sudo systemctl daemon-reload
```

The service files include:
- **Security hardening** - PrivateTmp, ProtectSystem, NoNewPrivileges
- **Resource limits** - Memory caps, file descriptor limits
- **Restart policies** - Automatic restart on failure with rate limiting
- **Venv Python** - Uses the project's virtual environment Python directly

**Note:** The service files in `systemd/` use hardcoded paths. Before deploying, edit them to match your installation:
- Replace `/home/damen/vlog` with your installation path
- Replace `User=damen` and `Group=damen` with your user
- Replace `/mnt/nas/vlog-storage` with your storage path

#### vlog-public.service

```ini
[Unit]
Description=VLog Public API
After=network.target mnt-nas.mount
Wants=mnt-nas.mount

[Service]
Type=simple
User=<YOUR_USER>
Group=<YOUR_USER>
WorkingDirectory=/path/to/vlog
ExecStart=/path/to/vlog/venv/bin/python -m uvicorn api.public:app --host 0.0.0.0 --port 9000 --proxy-headers --forwarded-allow-ips='127.0.0.1,<PROXY_IP>'

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true
CapabilityBoundingSet=
AmbientCapabilities=

# Allowed paths
ReadWritePaths=/path/to/vlog /mnt/nas/vlog-storage

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
User=<YOUR_USER>
Group=<YOUR_USER>
WorkingDirectory=/path/to/vlog
ExecStart=/path/to/vlog/venv/bin/python -m uvicorn api.admin:app --host 0.0.0.0 --port 9001

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true

# Allowed paths
ReadWritePaths=/path/to/vlog /mnt/nas/vlog-storage

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
User=<YOUR_USER>
Group=<YOUR_USER>
WorkingDirectory=/path/to/vlog
Environment=PYTHONUNBUFFERED=1
ExecStart=/path/to/vlog/venv/bin/python worker/transcoder.py

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true

# Allowed paths
ReadWritePaths=/path/to/vlog /mnt/nas/vlog-storage

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
User=<YOUR_USER>
Group=<YOUR_USER>
WorkingDirectory=/path/to/vlog
Environment=PYTHONUNBUFFERED=1
ExecStart=/path/to/vlog/venv/bin/python worker/transcription.py

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true

# Allowed paths
ReadWritePaths=/path/to/vlog /mnt/nas/vlog-storage

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

#### vlog-worker-api.service

```ini
[Unit]
Description=VLog Worker API
After=network.target
Requires=network.target

[Service]
Type=simple
User=<YOUR_USER>
Group=<YOUR_USER>
WorkingDirectory=/path/to/vlog
ExecStart=/path/to/vlog/venv/bin/python -m uvicorn api.worker_api:app --host 0.0.0.0 --port 9002

# Security hardening
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
NoNewPrivileges=true

# Allowed paths
ReadWritePaths=/path/to/vlog /mnt/nas/vlog-storage

# Resource limits
LimitNOFILE=65535
MemoryMax=2G

# Restart policy
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### vlog.target

```ini
[Unit]
Description=VLog Video Platform
Wants=vlog-public.service vlog-admin.service vlog-worker.service vlog-transcription.service vlog-worker-api.service

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

# Worker API (for remote workers)
sudo firewall-cmd --permanent --add-port=9002/tcp

# Admin (only if needed externally - NOT recommended)
# sudo firewall-cmd --permanent --add-port=9001/tcp

sudo firewall-cmd --reload
```

---

## Kubernetes Distributed Transcoding

For horizontal scaling, deploy containerized GPU workers to Kubernetes.

### Container Images

The GPU worker container is based on **Rocky Linux 10**:
- FFmpeg 7.1.2 from RPM Fusion (nvenc, vaapi, qsv encoders pre-built)
- intel-media-driver 25.2.6 (Battlemage/Arc B580 support)
- Python 3.12

**Local registry:** `localhost:9003/vlog-worker-gpu:rocky10`

### 1. Build Worker Docker Image

```bash
cd /path/to/vlog

# Build the GPU-enabled image (Rocky Linux 10 based)
docker build -f Dockerfile.worker.gpu -t vlog-worker-gpu:rocky10 .

# Tag as latest
docker tag vlog-worker-gpu:rocky10 vlog-worker-gpu:latest

# Push to local registry (port 9003)
docker push localhost:9003/vlog-worker-gpu:rocky10

# For k3s with containerd, import directly
docker save vlog-worker-gpu:rocky10 | sudo k3s ctr images import -

# For multi-node clusters, import on each node
docker save vlog-worker-gpu:rocky10 | ssh node2 'sudo k3s ctr images import -'
```

### GPU Support Requirements

**NVIDIA NVENC:**
- nvidia-container-toolkit installed on nodes
- nvidia device plugin daemonset
- RuntimeClass `nvidia` configured

**Intel VAAPI (Arc/Battlemage):**
- Node Feature Discovery (NFD)
- Intel GPU device plugin

```bash
# Install Intel GPU support
kubectl apply -k 'https://github.com/intel/intel-device-plugins-for-kubernetes/deployments/nfd?ref=main'
kubectl apply -k 'https://github.com/intel/intel-device-plugins-for-kubernetes/deployments/gpu_plugin?ref=main'
```

### 2. Register Workers and Get API Keys

```bash
# Register a worker via CLI
vlog worker register --name "k8s-worker-1"
# Output: API Key: vlog_xxxxxxxx...
# Save this key - it cannot be retrieved again!

# Or via curl
curl -X POST http://localhost:9002/api/worker/register \
  -H "Content-Type: application/json" \
  -d '{"worker_name": "k8s-worker-1", "worker_type": "remote"}'
```

### 3. Create Kubernetes Resources

```bash
# Create namespace
kubectl apply -f k8s/namespace.yaml

# Create secret with API key
kubectl create secret generic vlog-worker-secret -n vlog \
  --from-literal=api-key='YOUR_API_KEY_HERE'

# Create configmap with API URL
kubectl create configmap vlog-worker-config -n vlog \
  --from-literal=api-url='http://YOUR_SERVER_IP:9002'

# Deploy workers
kubectl apply -f k8s/deployment.yaml
```

### 4. Example Kubernetes Manifests

See `k8s/` directory for full manifests. Key examples:

**NVIDIA GPU Worker (k8s/worker-deployment-nvidia.yaml):**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vlog-worker-nvidia
  namespace: vlog
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: vlog-worker
      app.kubernetes.io/component: nvidia
  template:
    spec:
      runtimeClassName: nvidia  # Required for GPU access
      containers:
      - name: worker
        image: vlog-worker-gpu:rocky10
        imagePullPolicy: Never
        env:
        - name: VLOG_WORKER_API_URL
          valueFrom:
            configMapKeyRef:
              name: vlog-worker-config
              key: api-url
        - name: VLOG_WORKER_API_KEY
          valueFrom:
            secretKeyRef:
              name: vlog-worker-secret
              key: nvidia-api-key
        - name: VLOG_HWACCEL_TYPE
          value: "nvidia"
        resources:
          limits:
            nvidia.com/gpu: 1
            memory: "4Gi"
```

**Intel Arc/Battlemage Worker (k8s/worker-deployment-intel.yaml):**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vlog-worker-intel
  namespace: vlog
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: vlog-worker
      app.kubernetes.io/component: intel
  template:
    spec:
      containers:
      - name: worker
        image: vlog-worker-gpu:rocky10
        imagePullPolicy: Never
        env:
        - name: VLOG_WORKER_API_URL
          valueFrom:
            configMapKeyRef:
              name: vlog-worker-config
              key: api-url
        - name: VLOG_WORKER_API_KEY
          valueFrom:
            secretKeyRef:
              name: vlog-worker-secret
              key: intel-api-key
        - name: VLOG_HWACCEL_TYPE
          value: "intel"
        resources:
          limits:
            gpu.intel.com/xe: 1
            memory: "4Gi"
```

**CPU-Only Worker (k8s/worker-deployment.yaml):**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vlog-worker
  namespace: vlog
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vlog-worker
  template:
    spec:
      containers:
      - name: worker
        image: vlog-worker-gpu:rocky10
        imagePullPolicy: Never
        env:
        - name: VLOG_WORKER_API_URL
          valueFrom:
            configMapKeyRef:
              name: vlog-worker-config
              key: api-url
        - name: VLOG_WORKER_API_KEY
          valueFrom:
            secretKeyRef:
              name: vlog-worker-secret
              key: api-key
        - name: VLOG_HWACCEL_TYPE
          value: "none"
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "4Gi"
            cpu: "4"
```

### 5. Monitor Workers

```bash
# Check worker status via CLI
vlog worker status

# View worker logs
kubectl logs -n vlog -l app=vlog-worker -f

# List registered workers
vlog worker list

# Check job status
kubectl exec -n vlog deployment/vlog-worker -- ps aux
```

### 6. Scaling

```bash
# Scale workers manually
kubectl scale deployment/vlog-worker -n vlog --replicas=4

# Or use HPA for auto-scaling
kubectl autoscale deployment/vlog-worker -n vlog \
  --min=1 --max=10 --cpu-percent=70
```

### 7. Troubleshooting Workers

```bash
# Check if workers are connecting
vlog worker status

# View detailed logs
kubectl logs -n vlog -l app=vlog-worker --tail=100

# Check for pending jobs
psql -U vlog -d vlog -c "SELECT id, video_id, current_step, worker_id FROM transcoding_jobs WHERE completed_at IS NULL"

# Reset stuck jobs
psql -U vlog -d vlog -c "UPDATE transcoding_jobs SET worker_id = NULL WHERE completed_at IS NULL"
psql -U vlog -d vlog -c "UPDATE videos SET status = 'pending' WHERE status = 'processing'"
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
sudo semanage port -a -t http_port_t -p tcp 9002
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
# Backup database
pg_dump -U vlog vlog > /backup/vlog-$(date +%Y%m%d).sql

# Backup with compression
pg_dump -U vlog -Fc vlog > /backup/vlog-$(date +%Y%m%d).dump

# Restore from backup
pg_restore -U vlog -d vlog /backup/vlog.dump
```

### Video Files

Video files on NAS should be backed up according to your NAS backup strategy.

---

## Upgrading

### Standard Upgrade

```bash
# Stop services
sudo systemctl stop vlog.target

# Backup database
pg_dump -U vlog vlog > /backup/vlog-pre-upgrade-$(date +%Y%m%d).sql

# Pull latest code
cd /home/damen/vlog
git pull origin main

# Update dependencies
source venv/bin/activate
pip install -e .

# Run database migrations
alembic upgrade head

# Start services
sudo systemctl start vlog.target
```

### Migrating to Database-Backed Settings

If upgrading from a version before the database-backed settings system:

1. **First startup will auto-seed**: On first startup after the upgrade, VLog automatically detects a fresh settings table and seeds it from your current environment variables.

2. **Or migrate manually**:

```bash
# After upgrade, migrate settings from environment to database
vlog settings migrate-from-env

# Verify settings were migrated
vlog settings list

# The command outputs which env vars are now "safe to remove"
# You can keep them as fallbacks or remove them from your environment
```

3. **Update configuration approach**:

**Before (environment variables):**
```bash
export VLOG_HLS_SEGMENT_DURATION=6
export VLOG_WATERMARK_ENABLED=true
```

**After (database via CLI):**
```bash
vlog settings set transcoding.hls_segment_duration 6
vlog settings set watermark.enabled true
```

**Or via Admin UI**: Navigate to Settings tab in the admin interface.

4. **Environment variables still work**: For backwards compatibility, environment variables continue to work as fallbacks if a setting isn't found in the database.

### What Changes After Migration

| Aspect | Before | After |
|--------|--------|-------|
| Configuration changes | Edit env vars, restart service | Update via UI/CLI, no restart |
| Settings visibility | Check `.env` files | View in admin UI |
| Audit trail | None | All changes logged |
| Per-setting control | All or nothing | Individual settings |
| Cache behavior | Immediate | Up to 60 seconds delay |

### Bootstrap Settings (Still Require Restart)

These settings cannot be changed at runtime and still require environment variables:
- `VLOG_DATABASE_URL`
- `VLOG_STORAGE_PATH`
- `VLOG_PUBLIC_PORT`, `VLOG_ADMIN_PORT`, `VLOG_WORKER_API_PORT`
- `VLOG_ADMIN_API_SECRET`, `VLOG_WORKER_ADMIN_SECRET`

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

### Database Connection Issues

PostgreSQL connection problems:

```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Check connections
psql -U vlog -d vlog -c "SELECT * FROM pg_stat_activity WHERE datname = 'vlog';"

# Restart PostgreSQL if needed
sudo systemctl restart postgresql

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
curl -s http://localhost:9000/health

# Admin API
curl -s http://localhost:9001/health

# Worker API
curl -s http://localhost:9002/api/health

# Check all services
sudo systemctl status vlog-public vlog-admin vlog-worker vlog-worker-api vlog-transcription

# Check remote workers
vlog worker status
```

### Resource Monitoring

```bash
# CPU/Memory during transcoding
top -p $(pgrep -f transcoder)

# Disk usage
df -h /mnt/nas/vlog-storage
```

### Prometheus Metrics

VLog exposes Prometheus metrics for comprehensive monitoring:

```bash
# Admin API metrics
curl -s http://localhost:9001/metrics

# Worker API metrics
curl -s http://localhost:9002/api/metrics
```

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'vlog'
    static_configs:
      - targets: ['your-vlog-server:9001', 'your-vlog-server:9002']
    metrics_path: /metrics
    scrape_interval: 15s
```

See [MONITORING.md](MONITORING.md) for complete metrics documentation and Grafana dashboards.

---

## CDN Configuration

VLog supports serving video content through a CDN for improved performance and reduced origin server load.

### Enabling CDN

CDN settings are managed via the database-backed settings system:

```bash
# Enable CDN via CLI
vlog settings set cdn.enabled true
vlog settings set cdn.base_url https://cdn.yourdomain.com
```

Or via the Admin UI: Settings > CDN Configuration.

### CDN Requirements

Your CDN should be configured to:

1. **Origin:** Point to your VLog server (port 9000)
2. **Cache Rules:**
   - Cache video segments (`.ts`, `.m4s`) for long periods (1 year)
   - Cache manifests (`.m3u8`, `.mpd`) for short periods (10 seconds)
   - Cache thumbnails (`.jpg`) for medium periods (1 day)
3. **Headers:** Preserve CORS headers from origin

### Example CDN Configuration (Cloudflare)

```
Page Rule: cdn.yourdomain.com/videos/*.ts
  - Cache Level: Cache Everything
  - Edge Cache TTL: 1 month
  - Browser Cache TTL: 1 year

Page Rule: cdn.yourdomain.com/videos/*.m3u8
  - Cache Level: Cache Everything
  - Edge Cache TTL: 10 seconds
  - Browser Cache TTL: 10 seconds
```

### nginx CDN Proxy (Self-Hosted)

For a self-hosted CDN/caching layer:

```nginx
# /etc/nginx/conf.d/vlog-cdn.conf
proxy_cache_path /var/cache/nginx/vlog levels=1:2 keys_zone=vlog_cache:100m max_size=50g inactive=60d;

server {
    listen 80;
    server_name cdn.yourdomain.com;

    location /videos/ {
        proxy_pass http://127.0.0.1:9000;
        proxy_cache vlog_cache;

        # Cache video segments for 1 year
        proxy_cache_valid 200 365d;

        # Cache manifests for 10 seconds
        location ~ \.(m3u8|mpd)$ {
            proxy_pass http://127.0.0.1:9000;
            proxy_cache vlog_cache;
            proxy_cache_valid 200 10s;
        }

        add_header X-Cache-Status $upstream_cache_status;
    }
}
```

---

## Backup Strategy

### Database Backups

#### Manual Backup

```bash
# Create compressed backup
pg_dump -U vlog -Fc vlog > /backup/vlog-$(date +%Y%m%d).dump

# Verify backup
pg_restore --list /backup/vlog-*.dump | head

# Restore from backup
pg_restore -U vlog -d vlog --clean /backup/vlog.dump
```

#### Automated Backups (Kubernetes)

For Kubernetes deployments, use the provided CronJob:

```bash
# Create backup credentials secret
kubectl create secret generic postgres-backup-credentials \
  --namespace vlog \
  --from-literal=PGHOST=your-postgres-host \
  --from-literal=PGPORT=5432 \
  --from-literal=PGDATABASE=vlog \
  --from-literal=PGUSER=vlog \
  --from-literal=PGPASSWORD=your-password

# Deploy backup CronJob
kubectl apply -f k8s/backup-cronjob.yaml
```

The CronJob:
- Runs daily at 2:00 AM UTC
- Creates compressed dumps using `pg_dump --format=custom`
- Verifies backup integrity
- Retains 7 days of backups
- Stores backups on NAS (`/mnt/nas/vlog-storage/backups/`)

#### Automated Backups (Systemd)

For systemd deployments, create a backup script:

```bash
#!/bin/bash
# /usr/local/bin/vlog-backup.sh

BACKUP_DIR=/mnt/nas/vlog-storage/backups
RETENTION_DAYS=7
DATE=$(date +%Y-%m-%d-%H%M%S)

# Create backup
pg_dump -U vlog -Fc vlog > "${BACKUP_DIR}/vlog-${DATE}.dump"

# Verify backup
if ! pg_restore --list "${BACKUP_DIR}/vlog-${DATE}.dump" > /dev/null 2>&1; then
    echo "Backup verification failed!"
    rm -f "${BACKUP_DIR}/vlog-${DATE}.dump"
    exit 1
fi

# Clean old backups
find "${BACKUP_DIR}" -name "vlog-*.dump" -mtime +${RETENTION_DAYS} -delete

echo "Backup completed: vlog-${DATE}.dump"
```

Add to crontab:
```bash
0 2 * * * /usr/local/bin/vlog-backup.sh >> /var/log/vlog-backup.log 2>&1
```

### Video File Backups

Video files on NAS should be backed up using your NAS's backup features:
- RAID for redundancy
- Periodic snapshots
- Off-site replication for disaster recovery

**Important:** Video files can be regenerated from source files, but source files in `uploads/` are deleted after transcoding. Consider keeping source files if re-encoding might be needed.

---

## Audit Logging

VLog logs security-relevant operations for compliance and troubleshooting.

### Audit Log Location

By default: `/var/log/vlog/audit.log`

Configure via environment variables:
```bash
VLOG_AUDIT_LOG_ENABLED=true
VLOG_AUDIT_LOG_PATH=/var/log/vlog/audit.log
VLOG_AUDIT_LOG_LEVEL=INFO
```

### Audited Events

| Event | Description |
|-------|-------------|
| `auth.login` | Admin login attempts |
| `auth.logout` | Admin logout |
| `video.upload` | Video upload initiated |
| `video.delete` | Video deleted |
| `video.restore` | Video restored from archive |
| `settings.update` | Runtime setting changed |
| `worker.register` | New worker registered |
| `worker.revoke` | Worker API key revoked |

### Log Format

```json
{
  "timestamp": "2025-12-27T10:30:00Z",
  "event": "video.delete",
  "user": "admin",
  "ip": "192.168.1.100",
  "details": {
    "video_id": 123,
    "video_title": "Example Video"
  }
}
```

### Log Rotation

Audit logs use `RotatingFileHandler` with automatic rotation:

```bash
VLOG_AUDIT_LOG_MAX_BYTES=10485760    # 10 MB per file
VLOG_AUDIT_LOG_BACKUP_COUNT=5        # Keep 5 backup files
```

For systemd/journald, logs are automatically managed. Configure retention in `/etc/systemd/journald.conf`:

```ini
[Journal]
SystemMaxUse=1G
MaxRetentionSec=90days
```

### Viewing Audit Logs

```bash
# View recent audit entries
tail -f /var/log/vlog/audit.log | jq .

# Search for specific events
grep '"event":"settings.update"' /var/log/vlog/audit.log | jq .

# Filter by date
grep '2025-12-27' /var/log/vlog/audit.log | jq .
```

---

## Production Checklist

Before going to production, verify:

### Security
- [ ] Admin API (9001) not exposed to internet
- [ ] `VLOG_ADMIN_API_SECRET` is set
- [ ] `VLOG_WORKER_ADMIN_SECRET` is set
- [ ] HTTPS enabled via reverse proxy
- [ ] Rate limiting enabled
- [ ] Firewall rules configured

### Reliability
- [ ] PostgreSQL backups configured
- [ ] Log rotation configured
- [ ] Health checks responding
- [ ] Monitoring/alerting set up

### Performance
- [ ] Redis enabled for job queue
- [ ] CDN configured (if needed)
- [ ] GPU workers deployed (if available)
- [ ] NAS storage adequate

### Operations
- [ ] Systemd services enabled
- [ ] Prometheus scraping configured
- [ ] Runbooks documented
- [ ] On-call procedures defined
