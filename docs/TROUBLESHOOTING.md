# VLog Troubleshooting Guide

This guide covers common issues and their solutions.

---

## Quick Diagnostics

### Health Checks

```bash
# Check all services
curl -s http://localhost:9000/health  # Public API
curl -s http://localhost:9001/health  # Admin API
curl -s http://localhost:9002/api/health  # Worker API

# Check systemd services
sudo systemctl status vlog-public vlog-admin vlog-worker vlog-worker-api

# Check workers
vlog worker status
```

### Log Locations

| Service | Log Command |
|---------|-------------|
| Public API | `journalctl -u vlog-public -f` |
| Admin API | `journalctl -u vlog-admin -f` |
| Worker | `journalctl -u vlog-worker -f` |
| Worker API | `journalctl -u vlog-worker-api -f` |
| Transcription | `journalctl -u vlog-transcription -f` |
| Kubernetes workers | `kubectl logs -n vlog -l app=vlog-worker -f` |
| Audit logs | `tail -f /var/log/vlog/audit.log` |

---

## Video Processing Issues

### Video Stuck in "Pending"

**Symptom:** Video uploaded but status remains "pending"

**Causes and Solutions:**

1. **Worker not running**
   ```bash
   sudo systemctl status vlog-worker
   sudo systemctl start vlog-worker
   ```

2. **Redis job queue issue** (if using Redis)
   ```bash
   # Check Redis connection
   redis-cli ping

   # Check queue mode
   grep JOB_QUEUE_MODE /etc/systemd/system/vlog-worker.service
   ```

3. **Upload file missing**
   ```bash
   ls /mnt/nas/vlog-storage/uploads/
   # Should contain the video file
   ```

### Video Stuck in "Processing"

**Symptom:** Video shows "processing" but no progress

**Causes and Solutions:**

1. **FFmpeg crashed or hung**
   ```bash
   # Check worker logs for errors
   journalctl -u vlog-worker --since "1 hour ago" | grep -i error

   # Check for FFmpeg processes
   pgrep -f ffmpeg
   ```

2. **Disk space full**
   ```bash
   df -h /mnt/nas/vlog-storage
   df -h /tmp  # Work directory
   ```

3. **Stale job (worker crashed)**
   ```bash
   # Check job status in database
   psql -U vlog -d vlog -c "SELECT id, video_id, current_step, worker_id, last_checkpoint FROM transcoding_jobs WHERE completed_at IS NULL"

   # Reset stale jobs (use with caution)
   psql -U vlog -d vlog -c "UPDATE transcoding_jobs SET worker_id = NULL WHERE completed_at IS NULL AND last_checkpoint < NOW() - INTERVAL '30 minutes'"
   ```

4. **Kubernetes worker issues**
   ```bash
   kubectl get pods -n vlog
   kubectl describe pod -n vlog <pod-name>
   kubectl logs -n vlog <pod-name>
   ```

### Transcoding Failed

**Symptom:** Video status is "failed"

**Causes and Solutions:**

1. **Check error message**
   ```bash
   psql -U vlog -d vlog -c "SELECT id, title, error_message FROM videos WHERE status = 'failed'"
   ```

2. **Unsupported codec**
   - Check if source video uses a codec FFmpeg can decode
   - Try re-encoding the source with HandBrake

3. **Corrupt source file**
   - Re-upload the video
   - Test with `ffprobe /path/to/video.mp4`

4. **Retry failed job**
   - Via Admin UI: Click "Retry" on the video
   - Via API: `POST /api/videos/{id}/retry`

### GPU Encoding Issues

**Symptom:** GPU encoding fails, falls back to CPU

**Causes and Solutions:**

1. **NVIDIA issues**
   ```bash
   # Check GPU access
   nvidia-smi

   # Check container GPU access (K8s)
   kubectl exec -n vlog <pod> -- nvidia-smi

   # Verify runtime class
   kubectl get pods -n vlog -o yaml | grep runtimeClass
   ```

2. **Intel VAAPI issues**
   ```bash
   # Check for render device
   ls -la /dev/dri/

   # Test VAAPI
   vainfo

   # Container access (K8s)
   kubectl exec -n vlog <pod> -- vainfo
   ```

3. **Session limit reached** (NVIDIA consumer GPUs)
   - RTX 3090: 3 concurrent sessions
   - RTX 4090: 5 concurrent sessions
   - Reduce `VLOG_PARALLEL_QUALITIES` setting

---

## Playback Issues

### Video Won't Play

**Symptom:** Video appears stuck or shows error in player

**Causes and Solutions:**

1. **Missing manifest**
   ```bash
   # Check files exist
   ls /mnt/nas/vlog-storage/videos/<slug>/
   # Should contain master.m3u8 (or manifest.mpd for CMAF)
   ```

2. **MIME type issues**
   - Check browser dev tools Network tab
   - `.m3u8` should be `application/vnd.apple.mpegurl`
   - `.ts` should be `video/mp2t`
   - `.m4s` should be `video/iso.segment`

3. **CORS issues**
   ```bash
   # Check CORS headers
   curl -I http://localhost:9000/videos/<slug>/master.m3u8
   ```

4. **CDN caching stale manifest**
   - Purge CDN cache for manifests
   - Check CDN TTL settings

### Quality Not Available

**Symptom:** Expected quality missing from player

**Causes and Solutions:**

1. **Source resolution too low**
   - VLog only generates qualities at or below source
   - Check source resolution: `ffprobe <source>`

2. **Transcoding incomplete**
   ```bash
   # Check quality progress
   psql -U vlog -d vlog -c "SELECT * FROM quality_progress WHERE job_id = <job_id>"
   ```

### Shaka Player / DASH Issues

**Symptom:** DASH playback fails, HLS works

**Causes and Solutions:**

1. **Missing manifest.mpd**
   ```bash
   ls /mnt/nas/vlog-storage/videos/<slug>/manifest.mpd
   ```

2. **Codec string issues**
   - Check browser console for codec errors
   - Verify HEVC/AV1 browser support

3. **Regenerate manifests**
   ```bash
   vlog manifests regenerate --slug <video-slug>
   ```

---

## Database Issues

### Connection Refused

**Symptom:** "Connection refused" errors

**Causes and Solutions:**

1. **PostgreSQL not running**
   ```bash
   sudo systemctl status postgresql
   sudo systemctl start postgresql
   ```

2. **Wrong connection URL**
   ```bash
   # Check environment
   grep DATABASE_URL /etc/systemd/system/vlog-*.service

   # Test connection
   psql -U vlog -d vlog -c "SELECT 1"
   ```

3. **pg_hba.conf authentication**
   ```bash
   sudo vim /var/lib/pgsql/data/pg_hba.conf
   # Ensure local connections use md5:
   # local  all  all  md5
   sudo systemctl restart postgresql
   ```

### Database Locked (SQLite only)

**Symptom:** "Database is locked" errors

**Solution:** Migrate to PostgreSQL. SQLite doesn't support concurrent writes.

### Query Timeout

**Symptom:** Slow queries or timeouts

**Causes and Solutions:**

1. **Missing indexes**
   ```sql
   -- Check slow queries
   SELECT * FROM pg_stat_statements ORDER BY total_time DESC LIMIT 10;
   ```

2. **Connection pool exhausted**
   - Check `vlog_db_connections_active` metric
   - Increase pool size if needed

---

## Redis Issues

### Connection Failed

**Symptom:** Redis connection errors, circuit breaker open

**Causes and Solutions:**

1. **Redis not running**
   ```bash
   sudo systemctl status redis
   # or
   docker ps | grep redis
   ```

2. **Wrong password**
   ```bash
   # Test connection
   redis-cli -a <password> ping
   ```

3. **Circuit breaker open**
   - Check `vlog_redis_circuit_breaker_state` metric
   - VLog falls back to database polling automatically

### SSE Updates Not Working

**Symptom:** Admin UI doesn't show real-time progress

**Causes and Solutions:**

1. **Redis required for SSE**
   ```bash
   # Enable Redis
   export VLOG_REDIS_URL=redis://:password@localhost:6379
   sudo systemctl restart vlog-admin
   ```

2. **Check Redis Pub/Sub**
   ```bash
   redis-cli -a <password> PUBSUB CHANNELS "vlog:*"
   ```

---

## Storage Issues

### NAS Mount Problems

**Symptom:** "No such file or directory" or permission errors

**Causes and Solutions:**

1. **Mount dropped**
   ```bash
   mount | grep vlog-storage
   # If not mounted:
   sudo mount -a
   ```

2. **Stale NFS mount**
   ```bash
   # Force remount
   sudo umount -l /mnt/nas/vlog-storage
   sudo mount /mnt/nas/vlog-storage
   ```

3. **Permission issues**
   ```bash
   ls -la /mnt/nas/vlog-storage
   # Should be owned by vlog user
   ```

### Disk Space Full

**Symptom:** Upload or transcoding fails

**Causes and Solutions:**

1. **Check storage**
   ```bash
   df -h /mnt/nas/vlog-storage
   du -sh /mnt/nas/vlog-storage/*
   ```

2. **Clean up archive**
   ```bash
   # Check archived videos
   ls /mnt/nas/vlog-storage/archive/

   # Permanently delete old archives
   find /mnt/nas/vlog-storage/archive -type d -mtime +30 -exec rm -rf {} \;
   ```

3. **Clean up uploads**
   ```bash
   # Remove orphaned uploads
   ls /mnt/nas/vlog-storage/uploads/
   ```

---

## Worker Issues

### Workers Not Connecting

**Symptom:** `vlog worker status` shows no workers

**Causes and Solutions:**

1. **Worker API not running**
   ```bash
   sudo systemctl status vlog-worker-api
   curl http://localhost:9002/api/health
   ```

2. **Wrong API URL**
   ```bash
   # Check worker config
   kubectl get configmap vlog-worker-config -n vlog -o yaml
   ```

3. **API key invalid**
   ```bash
   # Check worker logs
   kubectl logs -n vlog -l app=vlog-worker | grep -i auth

   # Re-register worker if needed
   vlog worker register --name "new-worker"
   ```

4. **Firewall blocking**
   ```bash
   sudo firewall-cmd --list-ports
   # Should include 9002/tcp
   ```

### Workers Offline

**Symptom:** Workers showing "offline" status

**Causes and Solutions:**

1. **Heartbeat failing**
   ```bash
   # Check worker logs
   kubectl logs -n vlog <pod> | grep heartbeat
   ```

2. **Pod restarting**
   ```bash
   kubectl get pods -n vlog
   kubectl describe pod -n vlog <pod>
   ```

3. **Network policy blocking**
   ```bash
   kubectl get networkpolicy -n vlog
   ```

---

## Admin UI Issues

### Login Not Working

**Symptom:** Can't log into Admin UI

**Causes and Solutions:**

1. **Wrong secret**
   ```bash
   # Verify secret is set
   grep ADMIN_API_SECRET /etc/systemd/system/vlog-admin.service
   ```

2. **Cookie issues**
   - Clear browser cookies
   - Check secure cookie setting matches HTTPS usage

### Settings Not Saving

**Symptom:** Settings changes don't persist

**Causes and Solutions:**

1. **Database connection**
   ```bash
   # Check settings table
   psql -U vlog -d vlog -c "SELECT * FROM settings LIMIT 5"
   ```

2. **Cache delay**
   - Settings cache for 60 seconds
   - Wait and refresh

---

## Performance Issues

### Slow API Responses

**Causes and Solutions:**

1. **Check metrics**
   ```bash
   curl http://localhost:9001/metrics | grep http_request_duration
   ```

2. **Database slow**
   - Check `vlog_db_query_duration_seconds` metric
   - Add indexes if needed

3. **High load**
   - Check `vlog_transcoding_jobs_active` metric
   - Scale workers or reduce parallel qualities

### High Memory Usage

**Causes and Solutions:**

1. **Whisper model too large**
   ```bash
   # Use smaller model
   export VLOG_WHISPER_MODEL=small  # Instead of large-v3
   ```

2. **Too many parallel qualities**
   ```bash
   export VLOG_PARALLEL_QUALITIES=1
   ```

---

## Debug Mode

Enable debug logging for more information:

```bash
# Set log level
export VLOG_LOG_LEVEL=DEBUG

# Or in systemd service
Environment=VLOG_LOG_LEVEL=DEBUG
```

---

## Getting Help

If you can't resolve an issue:

1. **Check existing issues:** https://github.com/filthyrake/vlog/issues
2. **Collect logs:**
   ```bash
   journalctl -u vlog-* --since "1 hour ago" > vlog-logs.txt
   ```
3. **Open an issue** with:
   - VLog version
   - Error messages
   - Relevant logs
   - Steps to reproduce
