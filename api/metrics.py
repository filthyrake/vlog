"""
Prometheus metrics for VLog API and workers.

Provides application metrics for monitoring and alerting.
Metrics are exposed at /metrics endpoint in Prometheus text format.

Related Issue: #414
"""

from prometheus_client import Counter, Gauge, Histogram, Info, generate_latest

# Application info
APP_INFO = Info("vlog", "VLog application information")

# =============================================================================
# API Metrics
# =============================================================================

# HTTP request metrics
HTTP_REQUESTS_TOTAL = Counter(
    "vlog_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "vlog_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# Video metrics
VIDEOS_TOTAL = Gauge(
    "vlog_videos_total",
    "Total number of videos",
    ["status"],
)

VIDEO_UPLOADS_TOTAL = Counter(
    "vlog_video_uploads_total",
    "Total video uploads",
    ["result"],  # success, failed
)

# =============================================================================
# Transcoding Metrics
# =============================================================================

TRANSCODING_JOBS_TOTAL = Counter(
    "vlog_transcoding_jobs_total",
    "Total transcoding jobs",
    ["status"],  # started, completed, failed, retried
)

TRANSCODING_JOBS_ACTIVE = Gauge(
    "vlog_transcoding_jobs_active",
    "Number of active transcoding jobs",
)

TRANSCODING_JOB_DURATION_SECONDS = Histogram(
    "vlog_transcoding_job_duration_seconds",
    "Transcoding job duration in seconds",
    ["quality"],
    buckets=[30, 60, 120, 300, 600, 1200, 1800, 3600, 7200],
)

TRANSCODING_QUEUE_SIZE = Gauge(
    "vlog_transcoding_queue_size",
    "Number of jobs in transcoding queue",
)

# =============================================================================
# Worker Metrics
# =============================================================================

WORKERS_TOTAL = Gauge(
    "vlog_workers_total",
    "Total number of registered workers",
    ["status"],  # online, offline
)

WORKER_HEARTBEAT_TOTAL = Counter(
    "vlog_worker_heartbeat_total",
    "Total worker heartbeats",
    ["worker_id", "result"],  # success, failed
)

# =============================================================================
# Re-encode Queue Metrics
# =============================================================================

REENCODE_QUEUE_SIZE = Gauge(
    "vlog_reencode_queue_size",
    "Number of videos in re-encode queue",
    ["status"],  # pending, processing, completed, failed
)

REENCODE_JOBS_TOTAL = Counter(
    "vlog_reencode_jobs_total",
    "Total re-encode jobs processed",
    ["status"],  # completed, failed
)

# =============================================================================
# Database Metrics
# =============================================================================

DB_CONNECTIONS_ACTIVE = Gauge(
    "vlog_db_connections_active",
    "Number of active database connections",
)

DB_QUERY_RETRIES_TOTAL = Counter(
    "vlog_db_query_retries_total",
    "Total database query retries due to transient errors",
)

DB_QUERY_DURATION_SECONDS = Histogram(
    "vlog_db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"],  # select, insert, update, delete
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

# =============================================================================
# Redis Metrics
# =============================================================================

REDIS_OPERATIONS_TOTAL = Counter(
    "vlog_redis_operations_total",
    "Total Redis operations",
    ["operation", "result"],  # operation: publish, subscribe, etc. result: success, failed
)

REDIS_CIRCUIT_BREAKER_STATE = Gauge(
    "vlog_redis_circuit_breaker_state",
    "Redis circuit breaker state (0=closed, 1=open)",
)

# =============================================================================
# Storage Metrics
# =============================================================================

STORAGE_OPERATIONS_TOTAL = Counter(
    "vlog_storage_operations_total",
    "Total storage operations",
    ["operation", "result"],  # operation: read, write. result: success, failed
)

STORAGE_BYTES_WRITTEN = Counter(
    "vlog_storage_bytes_written_total",
    "Total bytes written to storage",
)

# =============================================================================
# Playback Metrics
# =============================================================================

PLAYBACK_SESSIONS_ACTIVE = Gauge(
    "vlog_playback_sessions_active",
    "Number of active playback sessions",
)

VIDEO_VIEWS_TOTAL = Counter(
    "vlog_video_views_total",
    "Total video views",
)


def get_metrics() -> bytes:
    """Generate Prometheus metrics in text format."""
    return generate_latest()


def init_app_info(version: str = "0.1.0"):
    """Initialize application info metric."""
    APP_INFO.info({"version": version, "app": "vlog"})
