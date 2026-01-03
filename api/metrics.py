"""
Prometheus metrics for VLog API and workers.

Provides application metrics for monitoring and alerting.
Metrics are exposed at /metrics endpoint in Prometheus text format.

Related Issues: #414, #207
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from prometheus_client import Counter, Gauge, Histogram, Info, generate_latest

if TYPE_CHECKING:
    from databases import Database

logger = logging.getLogger(__name__)

# Configurable reconciliation interval (default 6 hours, can be reduced for more accuracy)
STORAGE_RECONCILIATION_INTERVAL_SECONDS = int(
    os.environ.get("VLOG_STORAGE_RECONCILIATION_INTERVAL", 6 * 60 * 60)
)

# Maximum files to scan during reconciliation (prevents runaway scans)
STORAGE_SCAN_MAX_FILES = int(os.environ.get("VLOG_STORAGE_SCAN_MAX_FILES", 5_000_000))

# Timeout for storage scan in seconds (default 30 minutes)
STORAGE_SCAN_TIMEOUT_SECONDS = int(os.environ.get("VLOG_STORAGE_SCAN_TIMEOUT", 1800))

# Application info
APP_INFO = Info("vlog", "VLog application information")

# =============================================================================
# API Metrics
# =============================================================================

# HTTP request metrics
HTTP_REQUESTS_TOTAL = Counter(
    "vlog_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code", "api"],  # api label for Grafana grouping
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

# =============================================================================
# Background Task Health Metrics (Issue #207 review feedback)
# =============================================================================

BACKGROUND_TASK_ERRORS_TOTAL = Counter(
    "vlog_background_task_errors_total",
    "Total background task errors",
    ["task_name"],  # heartbeat_metrics, storage_reconcile
)

BACKGROUND_TASK_LAST_SUCCESS = Gauge(
    "vlog_background_task_last_success_timestamp_seconds",
    "Timestamp of last successful background task run",
    ["task_name"],
)

BACKGROUND_TASK_DURATION_SECONDS = Histogram(
    "vlog_background_task_duration_seconds",
    "Background task execution duration in seconds",
    ["task_name"],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 600.0],
)

STORAGE_RECONCILIATION_STATUS = Gauge(
    "vlog_storage_reconciliation_status",
    "Status of last storage reconciliation (1=success, 0=failed, -1=partial)",
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
_DYNAMIC_SEGMENT_PARENTS = {"videos", "worker", "jobs", "workers", "playlists", "users", "reencode", "categories", "chapters", "files"}

# UUID pattern for fallback detection (e.g., 550e8400-e29b-41d4-a716-446655440000)
_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# Slug pattern - looks like a slug if it has hyphens and is long enough (likely dynamic content)
_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")  # At least 2 hyphens


def sanitize_label(value: str, max_len: int = 50) -> str:
    """
    Sanitize a value for use as a Prometheus label.

    Prevents label injection and limits cardinality by:
    - Removing non-alphanumeric characters (except underscore and hyphen)
    - Truncating to max length

    Args:
        value: The raw value to sanitize
        max_len: Maximum length for the label value

    Returns:
        Sanitized string safe for use as a Prometheus label
    """
    if not value:
        return "unknown"
    # Only allow alphanumeric, underscores, and hyphens
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
    return sanitized[:max_len] if sanitized else "unknown"


@lru_cache(maxsize=512)
def normalize_endpoint(path: str) -> str:
    """
    Normalize endpoint paths to reduce label cardinality.

    Converts dynamic path segments to placeholders:
    - /api/videos/my-video-slug -> /api/videos/{id}
    - /api/worker/123/progress -> /api/worker/{id}/progress
    - /api/users/550e8400-e29b-41d4-a716-446655440000 -> /api/users/{id}

    This prevents cardinality explosion in Prometheus metrics.

    Uses LRU cache to avoid repeated string allocations (95%+ cache hit rate
    expected since most apps have <100 unique endpoints).
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
        # Handle numeric-only segments (likely IDs)
        elif part.isdigit():
            normalized.append("{id}")
        # Handle UUID patterns (fallback for endpoints not in _DYNAMIC_SEGMENT_PARENTS)
        elif _UUID_PATTERN.match(part):
            normalized.append("{id}")
        # Handle slug-like patterns (3+ hyphenated segments, likely dynamic content)
        elif _SLUG_PATTERN.match(part) and len(part) > 20:
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

# Track known worker labels for selective cleanup (avoids _metrics.clear() race condition)
_known_worker_labels: set = set()


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

    # Start storage reconciliation task (configurable interval, default 6 hours)
    if _STORAGE_VIDEO_PATH and _STORAGE_VIDEO_PATH.exists():
        _storage_reconcile_task = asyncio.create_task(
            _storage_reconciliation_loop(),
            name="metrics_storage_reconcile",
        )
        logger.info(
            f"Started storage reconciliation task for {_STORAGE_VIDEO_PATH} "
            f"(interval: {STORAGE_RECONCILIATION_INTERVAL_SECONDS}s)"
        )


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
        start_time = time.perf_counter()
        try:
            await _update_worker_heartbeat_metrics(database, sa, workers)
            BACKGROUND_TASK_LAST_SUCCESS.labels(task_name="heartbeat_metrics").set(time.time())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            BACKGROUND_TASK_ERRORS_TOTAL.labels(task_name="heartbeat_metrics").inc()
            logger.warning(f"Failed to update worker heartbeat metrics: {e}")
        finally:
            duration = time.perf_counter() - start_time
            BACKGROUND_TASK_DURATION_SECONDS.labels(task_name="heartbeat_metrics").observe(duration)

        await asyncio.sleep(30)


async def _update_worker_heartbeat_metrics(database: "Database", sa, workers) -> None:
    """Update heartbeat age gauges for all active workers."""
    global _known_worker_labels

    from api.db_retry import fetch_all_with_retry

    now = datetime.now(timezone.utc)

    # Query with timeout and retry logic for transient errors
    query = sa.select(
        workers.c.worker_id,
        workers.c.worker_name,
        workers.c.last_heartbeat,
    ).where(workers.c.status != "disabled")

    try:
        async with asyncio.timeout(10):  # Increased timeout to accommodate retries
            rows = await fetch_all_with_retry(query)
    except asyncio.TimeoutError:
        logger.warning("Timeout querying workers for heartbeat metrics")
        return

    # Track current worker labels for selective cleanup
    current_labels = set()

    for worker in rows:
        # Use worker_name for lower cardinality, sanitize to prevent label injection
        raw_label = worker["worker_name"] or worker["worker_id"]
        worker_label = sanitize_label(raw_label)
        current_labels.add(worker_label)

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

    # Selectively remove stale labels (workers that no longer exist)
    # This avoids the race condition from clear() and reduces GC churn
    stale_labels = _known_worker_labels - current_labels
    for stale_label in stale_labels:
        try:
            WORKER_HEARTBEAT_AGE_SECONDS.remove(stale_label)
        except KeyError:
            pass  # Label already removed

    _known_worker_labels = current_labels


async def _storage_reconciliation_loop() -> None:
    """Background loop to reconcile storage bytes at configurable interval."""
    # Run initial reconciliation after a short delay
    await asyncio.sleep(60)  # Wait 1 minute after startup
    await reconcile_storage_bytes()

    # Then run at configured interval (default 6 hours)
    while True:
        await asyncio.sleep(STORAGE_RECONCILIATION_INTERVAL_SECONDS)

        start_time = time.perf_counter()
        try:
            await reconcile_storage_bytes()
            BACKGROUND_TASK_LAST_SUCCESS.labels(task_name="storage_reconcile").set(time.time())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            BACKGROUND_TASK_ERRORS_TOTAL.labels(task_name="storage_reconcile").inc()
            logger.warning(f"Failed to reconcile storage bytes: {e}")
        finally:
            duration = time.perf_counter() - start_time
            BACKGROUND_TASK_DURATION_SECONDS.labels(task_name="storage_reconcile").observe(duration)


async def reconcile_storage_bytes() -> None:
    """
    Scan filesystem and reset storage gauge to accurate value.

    This corrects any drift from incremental tracking and provides
    an accurate baseline for the storage metric.

    Features:
    - Timeout protection (configurable, default 30 minutes)
    - Symlink escape protection
    - Partial failure detection (doesn't update metric on incomplete scan)
    - File count limit to prevent runaway scans
    """
    if not _STORAGE_VIDEO_PATH or not _STORAGE_VIDEO_PATH.exists():
        logger.debug("Storage path not configured or doesn't exist, skipping reconciliation")
        STORAGE_RECONCILIATION_STATUS.set(0)  # Failed
        return

    try:
        # Run scan with timeout
        async with asyncio.timeout(STORAGE_SCAN_TIMEOUT_SECONDS):
            result = await asyncio.to_thread(_scan_storage_size_safe, _STORAGE_VIDEO_PATH)

        if result["success"]:
            STORAGE_VIDEOS_BYTES.set(result["total_bytes"])
            STORAGE_RECONCILIATION_STATUS.set(1)  # Success
            logger.info(
                f"Storage reconciliation complete: {result['total_bytes']:,} bytes "
                f"({result['total_bytes'] / (1024**3):.2f} GB), "
                f"{result['files_scanned']:,} files scanned"
            )
        elif result["partial"]:
            # Partial scan - don't update metric with incomplete data
            STORAGE_RECONCILIATION_STATUS.set(-1)  # Partial
            logger.warning(
                f"Storage reconciliation incomplete: {result['error']}. "
                f"Scanned {result['files_scanned']:,} files, {result['total_bytes']:,} bytes partial. "
                "Metric NOT updated to avoid incorrect values."
            )
        else:
            STORAGE_RECONCILIATION_STATUS.set(0)  # Failed
            logger.error(f"Storage reconciliation failed: {result['error']}")

    except asyncio.TimeoutError:
        STORAGE_RECONCILIATION_STATUS.set(0)  # Failed
        BACKGROUND_TASK_ERRORS_TOTAL.labels(task_name="storage_reconcile").inc()
        logger.error(
            f"Storage reconciliation timed out after {STORAGE_SCAN_TIMEOUT_SECONDS} seconds. "
            "Consider increasing VLOG_STORAGE_SCAN_TIMEOUT or reducing storage size."
        )
    except Exception as e:
        STORAGE_RECONCILIATION_STATUS.set(0)  # Failed
        logger.warning(f"Failed to scan storage for reconciliation: {e}")


def _scan_storage_size_safe(storage_path: Path) -> dict:
    """
    Safe blocking filesystem scan with symlink protection and limits.

    Returns a dict with:
    - success: True if scan completed successfully
    - partial: True if scan was interrupted but has partial data
    - total_bytes: Total bytes scanned
    - files_scanned: Number of files scanned
    - error: Error message if any

    Security:
    - Prevents symlink escape attacks by verifying paths stay within storage_path
    - Limits file count to prevent runaway scans on unexpected directory structures
    """
    total = 0
    files_scanned = 0
    storage_path_resolved = storage_path.resolve()

    try:
        for f in storage_path.rglob("*"):
            # Check file count limit
            if files_scanned >= STORAGE_SCAN_MAX_FILES:
                return {
                    "success": False,
                    "partial": True,
                    "total_bytes": total,
                    "files_scanned": files_scanned,
                    "error": f"File count limit ({STORAGE_SCAN_MAX_FILES:,}) exceeded",
                }

            # Skip symlinks entirely to prevent escape attacks
            if f.is_symlink():
                continue

            if f.is_file():
                try:
                    # Verify file is within storage path (symlink escape protection)
                    file_resolved = f.resolve()
                    if not str(file_resolved).startswith(str(storage_path_resolved)):
                        logger.warning(f"Symlink escape detected, skipping: {f}")
                        continue

                    total += f.stat().st_size
                    files_scanned += 1
                except OSError:
                    # File may have been deleted during scan
                    pass

        return {
            "success": True,
            "partial": False,
            "total_bytes": total,
            "files_scanned": files_scanned,
            "error": None,
        }

    except OSError as e:
        # Directory-level error means partial data - don't use it
        return {
            "success": False,
            "partial": True,
            "total_bytes": total,
            "files_scanned": files_scanned,
            "error": str(e),
        }
