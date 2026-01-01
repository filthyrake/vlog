# VLog Upgrade Guide

This guide covers upgrading VLog between versions.

---

## General Upgrade Procedure

### 1. Backup First

**Always backup before upgrading:**

```bash
# Backup database
pg_dump -U vlog -Fc vlog > /backup/vlog-pre-upgrade-$(date +%Y%m%d).dump

# Verify backup
pg_restore --list /backup/vlog-pre-upgrade-*.dump | head
```

### 2. Stop Services

```bash
sudo systemctl stop vlog.target
```

### 3. Pull Latest Code

```bash
cd /home/damen/vlog
git fetch origin
git checkout main
git pull origin main
```

### 4. Update Dependencies

```bash
source venv/bin/activate
pip install -e .
```

### 5. Run Database Migrations

```bash
alembic upgrade head
```

### 6. Update Kubernetes Workers (if applicable)

```bash
# Build new container image
docker build -f Dockerfile.worker.gpu -t vlog-worker-gpu:rocky10 .

# Push to registry or import to containerd
docker save vlog-worker-gpu:rocky10 | sudo k3s ctr images import -

# Rollout new version
kubectl rollout restart deployment/vlog-worker-nvidia -n vlog
kubectl rollout restart deployment/vlog-worker-intel -n vlog
kubectl rollout status deployment/vlog-worker-nvidia -n vlog
```

### 7. Start Services

```bash
sudo systemctl start vlog.target
```

### 8. Verify

```bash
# Health checks
curl http://localhost:9000/health
curl http://localhost:9001/health
curl http://localhost:9002/api/health

# Check workers
vlog worker status
```

---

## Version-Specific Upgrade Notes

### Upgrading to v0.0.3

This version adds significant new features: playlists, chapters, sprite sheets, advanced player features, and reliability improvements.

**New Features:**
- **Playlists & Collections** - Organize videos into playlists, collections, series, or courses
- **Video Chapters** - Timeline navigation with chapter markers (max 50 per video)
- **Sprite Sheets** - Thumbnail previews for timeline scrubbing
- **Featured Videos** - Mark videos as featured for homepage prominence
- **Display Settings** - Configurable public UI settings
- **Cursor-based Pagination** - Improved API performance for large datasets
- **Table Partitioning** - `playback_sessions` partitioned by month for analytics performance
- **Streaming Segment Upload** - Progressive upload during transcoding (optional)
- **Circuit Breaker** - Worker HTTP client resilience pattern

**Database Migrations:**

```bash
# Run all migrations (020-025)
alembic upgrade head

# Migrations included:
# 020 - playlists and playlist_items tables
# 021 - playback_sessions table partitioning
# 022 - is_featured column on videos
# 023 - playback_sessions composite index
# 024 - chapters table and has_chapters column
# 025 - sprite_queue table and sprite sheet columns
```

**New Environment Variables (all optional):**

```bash
# Display settings
VLOG_DISPLAY_SHOW_VIEW_COUNTS=true
VLOG_DISPLAY_SHOW_TAGLINE=true
VLOG_DISPLAY_TAGLINE="Your video platform"

# Circuit breaker
VLOG_HTTP_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
VLOG_HTTP_CIRCUIT_BREAKER_RECOVERY_TIMEOUT=30
VLOG_HTTP_CIRCUIT_BREAKER_SUCCESS_THRESHOLD=2

# Streaming segment upload (optional, default off)
VLOG_WORKER_STREAMING_UPLOAD=false

# Sprite sheet generation
VLOG_SPRITE_INTERVAL=10
VLOG_SPRITE_TILE_SIZE=10
VLOG_SPRITE_FRAME_WIDTH=160

# Worker version gating (optional)
VLOG_WORKER_VERSION_CHECK_ENABLED=false
VLOG_REQUIRED_WORKER_VERSION=0.0.3
```

**New API Endpoints:**

| Endpoint | Description |
|----------|-------------|
| `GET/POST /api/playlists` | Playlist management |
| `GET/POST /api/playlists/{id}/videos` | Playlist video management |
| `GET/POST /api/videos/{id}/chapters` | Chapter management |
| `GET/POST /api/videos/{id}/sprites` | Sprite sheet management |
| `GET /api/config/display` | Public display settings |

**Worker Container Update:**

```bash
# Rebuild worker containers with new dependencies
docker build -f Dockerfile.worker.gpu -t vlog-worker-gpu:rocky10 .

# Deploy to Kubernetes
kubectl rollout restart deployment/vlog-worker-nvidia -n vlog
kubectl rollout restart deployment/vlog-worker-intel -n vlog
```

**Post-Upgrade Steps:**

1. Verify new tables exist:
   ```bash
   psql -U vlog -d vlog -c "\dt playlists; \dt chapters; \dt sprite_queue;"
   ```

2. Verify API endpoints work:
   ```bash
   curl http://localhost:9001/api/playlists
   curl http://localhost:9000/api/config/display
   ```

3. Optional: Configure display settings:
   ```bash
   vlog settings set display.show_view_counts true
   vlog settings set display.tagline "My Video Platform"
   ```

---

### Upgrading to v0.1.x (Database-Backed Settings)

This version introduces the database-backed settings system.

**What Changed:**
- Settings can now be stored in the database
- Runtime configuration changes without restart
- Admin UI settings tab

**Migration Steps:**

1. **First startup auto-seeds:** The database will be automatically populated with default settings on first startup.

2. **Or migrate manually:**
   ```bash
   vlog settings migrate-from-env
   ```

3. **Verify migration:**
   ```bash
   vlog settings list
   ```

**Environment Variables:**
- Environment variables still work as fallbacks
- You can keep them or remove them after migration
- Bootstrap settings (ports, secrets) still require env vars

### Upgrading to CMAF/DASH Support

This version adds CMAF streaming format with DASH support.

**What Changed:**
- New streaming format: CMAF with fMP4 segments
- DASH manifest generation (manifest.mpd)
- Shaka Player for DASH playback
- HEVC and AV1 codec support

**Migration Steps:**

1. **New videos use CMAF by default:**
   - No action needed for new uploads
   - They will automatically use CMAF format

2. **Existing videos (optional):**
   - Legacy HLS/TS videos continue to work
   - To upgrade: use the re-encode queue
   ```bash
   # Queue all legacy videos for re-encoding
   # Via Admin UI or API
   POST /api/reencode/queue-all
   ```

3. **Database migration:**
   ```bash
   alembic upgrade head
   # Adds streaming_format and primary_codec columns
   ```

**Configuration:**
```bash
VLOG_STREAMING_FORMAT=cmaf      # Default for new videos
VLOG_STREAMING_CODEC=hevc       # Default codec
VLOG_STREAMING_ENABLE_DASH=true # Generate DASH manifests
```

### Upgrading to Prometheus Metrics

This version adds Prometheus metrics endpoints.

**What Changed:**
- `/metrics` endpoint on Admin API (port 9001)
- `/api/metrics` endpoint on Worker API (port 9002)
- prometheus-client Python package dependency

**Migration Steps:**

1. **Dependencies:**
   ```bash
   pip install -e .  # prometheus-client is in requirements
   ```

2. **Configure Prometheus scraping:**
   ```yaml
   # prometheus.yml
   scrape_configs:
     - job_name: 'vlog'
       static_configs:
         - targets: ['your-server:9001', 'your-server:9002']
   ```

3. **No database migration required.**

### Upgrading to Automated Backups (Kubernetes)

This version adds automated PostgreSQL backups.

**What Changed:**
- New CronJob manifest: `k8s/backup-cronjob.yaml`
- Daily backups with 7-day retention

**Migration Steps:**

1. **Create backup secret:**
   ```bash
   kubectl create secret generic postgres-backup-credentials \
     --namespace vlog \
     --from-literal=PGHOST=your-postgres-host \
     --from-literal=PGPORT=5432 \
     --from-literal=PGDATABASE=vlog \
     --from-literal=PGUSER=vlog \
     --from-literal=PGPASSWORD=your-password
   ```

2. **Deploy CronJob:**
   ```bash
   kubectl apply -f k8s/backup-cronjob.yaml
   ```

3. **Verify:**
   ```bash
   kubectl get cronjob -n vlog
   ```

### Upgrading to Security Hardening

This version adds security improvements.

**What Changed:**
- Container security contexts (non-root, read-only fs, seccomp)
- NetworkPolicy for worker pods
- PodDisruptionBudgets
- CI/CD security scanning

**Migration Steps:**

1. **Update deployments:**
   ```bash
   kubectl apply -f k8s/worker-deployment-nvidia.yaml
   kubectl apply -f k8s/worker-deployment-intel.yaml
   ```

2. **Apply NetworkPolicy (optional but recommended):**
   ```bash
   # Configure Worker API address first
   vim k8s/networkpolicy.yaml
   kubectl apply -f k8s/networkpolicy.yaml
   ```

3. **Apply PodDisruptionBudgets:**
   ```bash
   kubectl apply -f k8s/worker-pdb.yaml
   kubectl apply -f k8s/worker-pdb-nvidia.yaml
   kubectl apply -f k8s/worker-pdb-intel.yaml
   ```

---

## Database Migrations

VLog uses Alembic for database migrations.

### Running Migrations

```bash
source venv/bin/activate
alembic upgrade head
```

### Checking Current Version

```bash
alembic current
```

### Viewing Migration History

```bash
alembic history --verbose
```

### Rolling Back

```bash
# Roll back one migration
alembic downgrade -1

# Roll back to specific version
alembic downgrade <revision>
```

---

## Rollback Procedure

If an upgrade fails:

### 1. Stop Services

```bash
sudo systemctl stop vlog.target
```

### 2. Restore Database

```bash
# Drop and recreate database
psql -U postgres -c "DROP DATABASE vlog"
psql -U postgres -c "CREATE DATABASE vlog OWNER vlog"

# Restore from backup
pg_restore -U vlog -d vlog /backup/vlog-pre-upgrade-*.dump
```

### 3. Revert Code

```bash
git checkout <previous-tag>
pip install -e .
```

### 4. Start Services

```bash
sudo systemctl start vlog.target
```

### 5. Rollback Kubernetes Workers

```bash
kubectl rollout undo deployment/vlog-worker-nvidia -n vlog
kubectl rollout undo deployment/vlog-worker-intel -n vlog
```

---

## Breaking Changes Log

### Database Schema Changes

| Version | Migration | Description |
|---------|-----------|-------------|
| 0.0.3 | 020_add_playlists | playlists and playlist_items tables |
| 0.0.3 | 021_partition_playback_sessions | Partitioned playback_sessions by month |
| 0.0.3 | 022_add_featured_video_columns | is_featured column on videos |
| 0.0.3 | 023_add_playback_sessions_composite_index | Composite index optimization |
| 0.0.3 | 024_add_chapters | chapters table, has_chapters on videos |
| 0.0.3 | 025_add_sprite_sheets | sprite_queue table, sprite columns on videos |
| 0.1.x | 012_add_settings | Database-backed settings system |
| 0.1.x | 013_add_streaming_format | streaming_format, primary_codec columns |
| 0.1.x | 014_add_reencode_queue | Re-encode queue table |
| 0.1.x | 015_extend_video_qualities | segment_format column |

### API Changes

| Version | Endpoint | Change |
|---------|----------|--------|
| 0.0.3 | `/api/playlists` | Added (Public & Admin) |
| 0.0.3 | `/api/playlists/{id}/videos` | Added (Admin) |
| 0.0.3 | `/api/videos/{id}/chapters` | Added (Admin) |
| 0.0.3 | `/api/videos/{id}/sprites` | Added (Admin) |
| 0.0.3 | `/api/config/display` | Added (Public) |
| 0.0.3 | `/api/videos?featured=` | Added featured filter |
| 0.1.x | `/metrics` | Added (Admin API) |
| 0.1.x | `/api/metrics` | Added (Worker API) |
| 0.1.x | `/api/reencode/*` | Added (Admin API) |
| 0.1.x | `/api/settings/*` | Added (Admin API) |

### Configuration Changes

| Version | Setting | Change |
|---------|---------|--------|
| 0.0.3 | `VLOG_DISPLAY_*` | Added (display settings) |
| 0.0.3 | `VLOG_HTTP_CIRCUIT_BREAKER_*` | Added (circuit breaker) |
| 0.0.3 | `VLOG_WORKER_STREAMING_UPLOAD` | Added (streaming upload) |
| 0.0.3 | `VLOG_SPRITE_*` | Added (sprite sheet settings) |
| 0.0.3 | `VLOG_WORKER_VERSION_CHECK_*` | Added (version gating) |
| 0.1.x | `VLOG_STREAMING_FORMAT` | Added (default: cmaf) |
| 0.1.x | `VLOG_STREAMING_CODEC` | Added (default: hevc) |
| 0.1.x | `VLOG_CDN_ENABLED` | Added |
| 0.1.x | `VLOG_CDN_BASE_URL` | Added |

---

## Upgrade Checklist

Before upgrading:

- [ ] Read release notes
- [ ] Backup database
- [ ] Check disk space
- [ ] Plan maintenance window
- [ ] Notify users (if applicable)

After upgrading:

- [ ] Run database migrations
- [ ] Check health endpoints
- [ ] Verify workers connected
- [ ] Test video upload
- [ ] Test video playback
- [ ] Check metrics endpoint
- [ ] Review logs for errors

---

## Getting Help

If you encounter issues during upgrade:

1. **Check logs:**
   ```bash
   journalctl -u vlog-* --since "30 minutes ago"
   ```

2. **Check migration status:**
   ```bash
   alembic current
   ```

3. **Open an issue:** https://github.com/filthyrake/vlog/issues
