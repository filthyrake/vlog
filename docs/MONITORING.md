# VLog Monitoring Guide

VLog exposes Prometheus metrics for comprehensive observability of the video platform.

## Metrics Endpoints

| Service | Endpoint | Port | Description |
|---------|----------|------|-------------|
| Admin API | `/metrics` | 9001 | Application metrics (videos, uploads, transcoding) |
| Worker API | `/api/metrics` | 9002 | Worker and job queue metrics |

## Quick Start

### 1. Verify Metrics Are Exposed

```bash
# Admin API metrics
curl -s http://localhost:9001/metrics | head -50

# Worker API metrics
curl -s http://localhost:9002/api/metrics | head -50
```

### 2. Configure Prometheus

Add VLog targets to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'vlog-admin'
    static_configs:
      - targets: ['your-vlog-server:9001']
    metrics_path: /metrics
    scrape_interval: 15s

  - job_name: 'vlog-worker-api'
    static_configs:
      - targets: ['your-vlog-server:9002']
    metrics_path: /api/metrics
    scrape_interval: 15s
```

### 3. Kubernetes ServiceMonitor (Optional)

If using Prometheus Operator in Kubernetes:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: vlog-monitor
  namespace: vlog
spec:
  selector:
    matchLabels:
      app: vlog
  endpoints:
  - port: admin
    path: /metrics
    interval: 15s
  - port: worker-api
    path: /api/metrics
    interval: 15s
```

---

## Available Metrics

### HTTP Request Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vlog_http_requests_total` | Counter | method, endpoint, status_code | Total HTTP requests |
| `vlog_http_request_duration_seconds` | Histogram | method, endpoint | Request latency distribution |

**Example queries:**
```promql
# Request rate by endpoint
rate(vlog_http_requests_total[5m])

# 95th percentile latency
histogram_quantile(0.95, rate(vlog_http_request_duration_seconds_bucket[5m]))

# Error rate
sum(rate(vlog_http_requests_total{status_code=~"5.."}[5m])) / sum(rate(vlog_http_requests_total[5m]))
```

### Video Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vlog_videos_total` | Gauge | status | Total videos by status (pending, processing, ready, failed) |
| `vlog_video_uploads_total` | Counter | result | Upload attempts (success, failed) |
| `vlog_video_views_total` | Counter | - | Total video views |

**Example queries:**
```promql
# Videos by status
vlog_videos_total

# Upload success rate
sum(rate(vlog_video_uploads_total{result="success"}[1h])) / sum(rate(vlog_video_uploads_total[1h]))

# Views per hour
rate(vlog_video_views_total[1h]) * 3600
```

### Transcoding Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vlog_transcoding_jobs_total` | Counter | status | Jobs by status (started, completed, failed, retried) |
| `vlog_transcoding_jobs_active` | Gauge | - | Currently active transcoding jobs |
| `vlog_transcoding_job_duration_seconds` | Histogram | quality | Job duration by quality level |
| `vlog_transcoding_queue_size` | Gauge | - | Jobs waiting in queue |

**Example queries:**
```promql
# Active jobs
vlog_transcoding_jobs_active

# Job failure rate
sum(rate(vlog_transcoding_jobs_total{status="failed"}[1h])) / sum(rate(vlog_transcoding_jobs_total{status="started"}[1h]))

# Average transcoding time by quality
histogram_quantile(0.5, rate(vlog_transcoding_job_duration_seconds_bucket[1h]))

# Queue depth
vlog_transcoding_queue_size
```

### Worker Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vlog_workers_total` | Gauge | status | Workers by status (online, offline) |
| `vlog_worker_heartbeat_total` | Counter | worker_id, result | Heartbeat attempts per worker |

**Example queries:**
```promql
# Online workers
vlog_workers_total{status="online"}

# Workers with failed heartbeats
increase(vlog_worker_heartbeat_total{result="failed"}[5m])
```

### Re-encode Queue Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vlog_reencode_queue_size` | Gauge | status | Re-encode jobs by status |
| `vlog_reencode_jobs_total` | Counter | status | Total re-encode jobs processed |

**Example queries:**
```promql
# Pending re-encode jobs
vlog_reencode_queue_size{status="pending"}

# Re-encode completion rate
rate(vlog_reencode_jobs_total{status="completed"}[1h])
```

### Database Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vlog_db_connections_active` | Gauge | - | Active database connections |
| `vlog_db_query_retries_total` | Counter | - | Query retries due to transient errors |
| `vlog_db_query_duration_seconds` | Histogram | operation | Query latency by operation type |

**Example queries:**
```promql
# Connection pool usage
vlog_db_connections_active

# Slow queries (>100ms)
histogram_quantile(0.99, rate(vlog_db_query_duration_seconds_bucket[5m])) > 0.1

# Retry rate (indicates database issues)
rate(vlog_db_query_retries_total[5m])
```

### Redis Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vlog_redis_operations_total` | Counter | operation, result | Redis operations by type |
| `vlog_redis_circuit_breaker_state` | Gauge | - | Circuit breaker (0=closed, 1=open) |

**Example queries:**
```promql
# Redis availability
vlog_redis_circuit_breaker_state == 0

# Redis error rate
sum(rate(vlog_redis_operations_total{result="failed"}[5m])) / sum(rate(vlog_redis_operations_total[5m]))
```

### Storage Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vlog_storage_operations_total` | Counter | operation, result | Storage operations |
| `vlog_storage_bytes_written_total` | Counter | - | Total bytes written |

### Playback Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `vlog_playback_sessions_active` | Gauge | - | Active playback sessions |
| `vlog_video_views_total` | Counter | - | Total video views |

---

## Alerting Rules

### Example Prometheus Alert Rules

Create `vlog-alerts.yml`:

```yaml
groups:
- name: vlog
  rules:
  # High error rate on API
  - alert: VLogHighErrorRate
    expr: |
      sum(rate(vlog_http_requests_total{status_code=~"5.."}[5m]))
      / sum(rate(vlog_http_requests_total[5m])) > 0.05
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "VLog API error rate above 5%"
      description: "Error rate is {{ $value | humanizePercentage }}"

  # Transcoding queue backing up
  - alert: VLogTranscodingQueueBacklog
    expr: vlog_transcoding_queue_size > 10
    for: 15m
    labels:
      severity: warning
    annotations:
      summary: "Transcoding queue has {{ $value }} pending jobs"

  # No active workers
  - alert: VLogNoActiveWorkers
    expr: vlog_workers_total{status="online"} == 0
    for: 5m
    labels:
      severity: critical
    annotations:
      summary: "No VLog workers are online"

  # Worker heartbeat failures
  - alert: VLogWorkerHeartbeatFailures
    expr: increase(vlog_worker_heartbeat_total{result="failed"}[5m]) > 3
    labels:
      severity: warning
    annotations:
      summary: "Worker {{ $labels.worker_id }} has heartbeat failures"

  # Database connection issues
  - alert: VLogDatabaseRetries
    expr: rate(vlog_db_query_retries_total[5m]) > 0.1
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "Database experiencing transient errors"

  # Redis circuit breaker open
  - alert: VLogRedisDown
    expr: vlog_redis_circuit_breaker_state == 1
    for: 1m
    labels:
      severity: warning
    annotations:
      summary: "Redis circuit breaker is open"

  # High transcoding failure rate
  - alert: VLogTranscodingFailures
    expr: |
      sum(rate(vlog_transcoding_jobs_total{status="failed"}[1h]))
      / sum(rate(vlog_transcoding_jobs_total{status="started"}[1h])) > 0.1
    for: 30m
    labels:
      severity: warning
    annotations:
      summary: "Transcoding failure rate above 10%"

  # Slow API responses
  - alert: VLogSlowResponses
    expr: |
      histogram_quantile(0.95, rate(vlog_http_request_duration_seconds_bucket[5m])) > 2
    for: 10m
    labels:
      severity: warning
    annotations:
      summary: "95th percentile response time above 2 seconds"
```

---

## Grafana Dashboard

### Import Dashboard JSON

A sample Grafana dashboard is provided below. Import via Grafana UI (Dashboards > Import > Paste JSON).

<details>
<summary>Click to expand dashboard JSON</summary>

```json
{
  "title": "VLog Overview",
  "uid": "vlog-overview",
  "panels": [
    {
      "title": "Request Rate",
      "type": "graph",
      "targets": [
        {
          "expr": "sum(rate(vlog_http_requests_total[5m])) by (endpoint)",
          "legendFormat": "{{ endpoint }}"
        }
      ]
    },
    {
      "title": "Videos by Status",
      "type": "piechart",
      "targets": [
        {
          "expr": "vlog_videos_total",
          "legendFormat": "{{ status }}"
        }
      ]
    },
    {
      "title": "Active Transcoding Jobs",
      "type": "stat",
      "targets": [
        {
          "expr": "vlog_transcoding_jobs_active"
        }
      ]
    },
    {
      "title": "Queue Depth",
      "type": "stat",
      "targets": [
        {
          "expr": "vlog_transcoding_queue_size"
        }
      ]
    },
    {
      "title": "Online Workers",
      "type": "stat",
      "targets": [
        {
          "expr": "vlog_workers_total{status=\"online\"}"
        }
      ]
    },
    {
      "title": "Transcoding Duration (p95)",
      "type": "graph",
      "targets": [
        {
          "expr": "histogram_quantile(0.95, rate(vlog_transcoding_job_duration_seconds_bucket[1h])) by (quality)",
          "legendFormat": "{{ quality }}"
        }
      ]
    },
    {
      "title": "Error Rate",
      "type": "graph",
      "targets": [
        {
          "expr": "sum(rate(vlog_http_requests_total{status_code=~\"5..\"}[5m])) / sum(rate(vlog_http_requests_total[5m]))",
          "legendFormat": "Error Rate"
        }
      ]
    },
    {
      "title": "Database Query Latency (p95)",
      "type": "graph",
      "targets": [
        {
          "expr": "histogram_quantile(0.95, rate(vlog_db_query_duration_seconds_bucket[5m])) by (operation)",
          "legendFormat": "{{ operation }}"
        }
      ]
    }
  ]
}
```

</details>

---

## Health Check Endpoints

In addition to metrics, VLog exposes health check endpoints:

| Service | Endpoint | Description |
|---------|----------|-------------|
| Public API | `/health` | Returns 200 if service is up |
| Admin API | `/health` | Returns 200 with DB and storage status |
| Worker API | `/api/health` | Returns 200 if service is up |
| Worker Pod | `/health` (port 8080) | Kubernetes liveness probe |
| Worker Pod | `/ready` (port 8080) | Kubernetes readiness probe |

### Health Check Example

```bash
# Full health check
curl -s http://localhost:9001/health | jq .

# Response:
{
  "status": "healthy",
  "database": "connected",
  "storage": "accessible"
}
```

---

## Best Practices

### Scrape Intervals

| Use Case | Recommended Interval |
|----------|---------------------|
| Real-time dashboards | 15s |
| General monitoring | 30s |
| Long-term trending | 60s |

### Retention

- **Short-term (high resolution):** 15 days at 15s intervals
- **Medium-term:** 90 days at 1m downsampled
- **Long-term:** 1 year at 5m downsampled

### Labels to Avoid

Avoid high-cardinality labels that can cause metric explosion:
- Video IDs (use aggregated metrics instead)
- User IDs
- Request paths with variable segments

### Dashboard Tips

1. **Start with the Golden Signals:**
   - Latency: `vlog_http_request_duration_seconds`
   - Traffic: `vlog_http_requests_total`
   - Errors: `vlog_http_requests_total{status_code=~"5.."}`
   - Saturation: `vlog_transcoding_queue_size`, `vlog_db_connections_active`

2. **Include business metrics:**
   - Videos uploaded per day
   - Transcoding throughput
   - Views per hour

3. **Add annotations for deployments:**
   - Mark deployment times on graphs
   - Correlate performance changes with releases

---

## Troubleshooting

### Metrics Not Appearing

1. **Verify endpoint is accessible:**
   ```bash
   curl -v http://localhost:9001/metrics
   ```

2. **Check Prometheus targets:**
   - Navigate to Prometheus UI > Status > Targets
   - Verify VLog targets are "UP"

3. **Check firewall rules:**
   ```bash
   sudo firewall-cmd --list-ports
   # Should include 9001/tcp and 9002/tcp
   ```

### High Cardinality Warnings

If Prometheus warns about high cardinality:
- Check for high-cardinality labels
- Consider aggregating metrics
- Adjust retention policies

### Missing Worker Metrics

1. **Verify Worker API is running:**
   ```bash
   systemctl status vlog-worker-api
   ```

2. **Check if workers are registered:**
   ```bash
   vlog worker list
   ```
