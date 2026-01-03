"""
Prometheus metrics for VLog API and workers.

Provides application metrics for monitoring and alerting.
Metrics are exposed at /metrics endpoint in Prometheus text format.

Related Issues: #414, #207
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from prometheus_client import Counter, Gauge, Histogram, Info, generate_latest

if TYPE_CHECKING:
    from databases import Database

logger = logging.getLogger(__name__)

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

# Issue #207: HTTP requests in progress gauge (low-cardinality labels)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "vlog_http_requests_in_progress",
    "HTTP requests currently in progress",
    ["api"],  # "admin", "worker", "public" - only 3 values for low cardinality
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

# Issue #207: Jobs completed per worker (low-cardinality: worker_name not UUID)
WORKER_JOBS_COMPLETED_TOTAL = Counter(
    "vlog_worker_jobs_completed_total",
    "Total jobs completed per worker",
    ["worker_name"],  # Human-readable name for lower cardinality
)

# Issue #207: Heartbeat age per worker (updated by background task)
WORKER_HEARTBEAT_AGE_SECONDS = Gauge(
    "vlog_worker_heartbeat_age_seconds",
    "Seconds since last heartbeat per worker",
    ["worker_name"],  # Human-readable name for lower cardinality
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

# Issue #207: Total video storage used (tracked incrementally with periodic reconciliation)
STORAGE_VIDEOS_BYTES = Gauge(
    "vlog_storage_videos_bytes",
    "Total video storage used in bytes",
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

# Issue #207: Total watch time counter
VIDEOS_WATCH_TIME_SECONDS_TOTAL = Counter(
    "vlog_videos_watch_time_seconds_total",
    "Total video watch time in seconds",
)


def get_metrics() -> bytes:
    """Generate Prometheus metrics in text format."""
    return generate_latest()


def init_app_info(version: str = "0.1.0"):
    """Initialize application info metric."""
    APP_INFO.info({"version": version, "app": "vlog"})


# =============================================================================
# Issue #207: Helper Functions
# =============================================================================

# Pattern for normalizing dynamic path segments
_DYNAMIC_SEGMENT_PARENTS = {"videos", "worker", "jobs", "workers", "playlists", "users", "reencode"}


def normalize_endpoint(path: str) -> str:
    """
    Normalize endpoint paths to reduce label cardinality.

    Converts dynamic path segments to placeholders:
    - /api/videos/my-video-slug -> /api/videos/{id}
    - /api/worker/123/progress -> /api/worker/{id}/progress

    This prevents cardinality explosion in Prometheus metrics.
    """
    if not path:
        return "/"

    parts = path.split("/")
    normalized = []

    for i, part in enumerate(parts):
        if not part:
            normalized.append(part)
            continue

        # Check if previous segment indicates this is a dynamic ID
        if i > 0 and parts[i - 1] in _DYNAMIC_SEGMENT_PARENTS:
            normalized.append("{id}")
        # Also handle numeric-only segments (likely IDs)
        elif part.isdigit():
            normalized.append("{id}")
        else:
            normalized.append(part)

    return "/".join(normalized)


# =============================================================================
# Issue #207: Background Tasks for Dynamic Metrics
# =============================================================================

_metrics_update_task: Optional[asyncio.Task] = None
_storage_reconcile_task: Optional[asyncio.Task] = None

# Storage path for video files (configurable via environment)
_STORAGE_VIDEO_PATH: Optional[Path] = None


def set_storage_path(path: Path) -> None:
    """Set the storage path for video files."""
    global _STORAGE_VIDEO_PATH
    _STORAGE_VIDEO_PATH = path


async def start_metrics_background_tasks(database: "Database", storage_path: Optional[Path] = None) -> None:
    """
    Start background tasks to update dynamic metrics.

    Args:
        database: Database connection for querying worker heartbeats
        storage_path: Path to video storage directory (for reconciliation)
    """
    global _metrics_update_task, _storage_reconcile_task, _STORAGE_VIDEO_PATH

    if storage_path:
        _STORAGE_VIDEO_PATH = storage_path

    # Start worker heartbeat metrics task (runs every 30s)
    _metrics_update_task = asyncio.create_task(
        _worker_heartbeat_metrics_loop(database),
        name="metrics_heartbeat_update",
    )

    # Start storage reconciliation task (runs every 6 hours)
    if _STORAGE_VIDEO_PATH and _STORAGE_VIDEO_PATH.exists():
        _storage_reconcile_task = asyncio.create_task(
            _storage_reconciliation_loop(),
            name="metrics_storage_reconcile",
        )
        logger.info(f"Started storage reconciliation task for {_STORAGE_VIDEO_PATH}")


async def stop_metrics_background_tasks() -> None:
    """Stop all metrics background tasks."""
    global _metrics_update_task, _storage_reconcile_task

    if _metrics_update_task and not _metrics_update_task.done():
        _metrics_update_task.cancel()
        try:
            await _metrics_update_task
        except asyncio.CancelledError:
            pass
        _metrics_update_task = None

    if _storage_reconcile_task and not _storage_reconcile_task.done():
        _storage_reconcile_task.cancel()
        try:
            await _storage_reconcile_task
        except asyncio.CancelledError:
            pass
        _storage_reconcile_task = None


async def _worker_heartbeat_metrics_loop(database: "Database") -> None:
    """Background loop to update worker heartbeat age metrics every 30 seconds."""
    import sqlalchemy as sa

    from api.database import workers

    while True:
        try:
            await _update_worker_heartbeat_metrics(database, sa, workers)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Failed to update worker heartbeat metrics: {e}")

        await asyncio.sleep(30)


async def _update_worker_heartbeat_metrics(database: "Database", sa, workers) -> None:
    """Update heartbeat age gauges for all active workers."""
    now = datetime.now(timezone.utc)

    # Clear old labels before setting new ones (handles deleted workers)
    # This prevents unbounded memory growth from deleted worker labels
    WORKER_HEARTBEAT_AGE_SECONDS._metrics.clear()

    # Query with timeout to prevent blocking
    query = sa.select(workers).where(workers.c.status != "disabled")

    try:
        async with asyncio.timeout(5):  # 5 second timeout
            rows = await database.fetch_all(query)
    except asyncio.TimeoutError:
        logger.warning("Timeout querying workers for heartbeat metrics")
        return

    for worker in rows:
        # Use worker_name for lower cardinality (falls back to worker_id if no name)
        worker_label = worker["worker_name"] or worker["worker_id"]

        if worker["last_heartbeat"]:
            heartbeat_utc = worker["last_heartbeat"]
            # Ensure timezone-aware comparison
            if heartbeat_utc.tzinfo is None:
                heartbeat_utc = heartbeat_utc.replace(tzinfo=timezone.utc)
            age = (now - heartbeat_utc).total_seconds()
            WORKER_HEARTBEAT_AGE_SECONDS.labels(worker_name=worker_label).set(age)
        else:
            # Worker never sent heartbeat - set to very large value for alerting
            WORKER_HEARTBEAT_AGE_SECONDS.labels(worker_name=worker_label).set(float("inf"))


async def _storage_reconciliation_loop() -> None:
    """Background loop to reconcile storage bytes every 6 hours."""
    # Run initial reconciliation after a short delay
    await asyncio.sleep(60)  # Wait 1 minute after startup
    await reconcile_storage_bytes()

    # Then run every 6 hours
    while True:
        await asyncio.sleep(6 * 60 * 60)  # 6 hours

        try:
            await reconcile_storage_bytes()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Failed to reconcile storage bytes: {e}")


async def reconcile_storage_bytes() -> None:
    """
    Scan filesystem and reset storage gauge to accurate value.

    This corrects any drift from incremental tracking and provides
    an accurate baseline for the storage metric.
    """
    if not _STORAGE_VIDEO_PATH or not _STORAGE_VIDEO_PATH.exists():
        logger.debug("Storage path not configured or doesn't exist, skipping reconciliation")
        return

    try:
        total_bytes = await asyncio.to_thread(_scan_storage_size, _STORAGE_VIDEO_PATH)
        STORAGE_VIDEOS_BYTES.set(total_bytes)
        logger.info(f"Storage reconciliation complete: {total_bytes:,} bytes ({total_bytes / (1024**3):.2f} GB)")
    except Exception as e:
        logger.warning(f"Failed to scan storage for reconciliation: {e}")


def _scan_storage_size(storage_path: Path) -> int:
    """
    Blocking filesystem scan - run in thread.

    Scans all files in the storage directory and returns total size in bytes.
    """
    total = 0
    try:
        for f in storage_path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    # File may have been deleted during scan
                    pass
    except OSError as e:
        logger.warning(f"Error scanning storage directory: {e}")
    return total
