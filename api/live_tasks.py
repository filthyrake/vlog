"""Background tasks for live streaming.

Handles:
- DVR window cleanup: Delete old segments beyond DVR window
- Stale stream detection: Mark streams as ending/ended when no segments received
- Playlist updates: Periodic refresh of HLS playlists
- Metrics aggregation: Compute aggregated metrics from raw Redis data (Issue #524)
- Metrics cleanup: Delete old metrics beyond retention period
- Viewer cleanup: Mark stale viewers as left
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlalchemy as sa

from api.database import live_stream_segments, live_streams, live_stream_viewers
from api.db_retry import db_execute_with_retry, fetch_all_with_retry, fetch_one_with_retry
from api.live_playlist import finalize_playlists_for_vod
from api.live_vod import trigger_vod_recording
from config import (
    LIVE_DVR_CLEANUP_BATCH_SIZE,
    LIVE_DVR_CLEANUP_INTERVAL,
    LIVE_METRICS_AGGREGATION_INTERVAL,
    LIVE_STALE_GRACE_MULTIPLIER,
    LIVE_STALE_THRESHOLD,
    LIVE_STORAGE_PATH,
    LIVE_VIEWER_CLEANUP_INTERVAL,
    LIVE_VIEWER_TIMEOUT,
)

logger = logging.getLogger(__name__)

# Background task handles
_dvr_cleanup_task: Optional[asyncio.Task] = None
_stale_detection_task: Optional[asyncio.Task] = None
_metrics_aggregation_task: Optional[asyncio.Task] = None
_metrics_cleanup_task: Optional[asyncio.Task] = None
_viewer_cleanup_task: Optional[asyncio.Task] = None
_running = False

# Prometheus-style metrics (counter values)
_dvr_segments_cleaned = 0
_stale_streams_detected = 0
_metrics_aggregated = 0
_metrics_cleaned = 0
_viewers_cleaned = 0

# Task health tracking (per Cid's review)
# Each task has its own expected interval for health threshold calculation
_task_health = {
    "metrics_aggregation": {"last_run": None, "run_count": 0, "error_count": 0, "interval_seconds": LIVE_METRICS_AGGREGATION_INTERVAL},
    "metrics_cleanup": {"last_run": None, "run_count": 0, "error_count": 0, "interval_seconds": 3600},  # Hourly
    "viewer_cleanup": {"last_run": None, "run_count": 0, "error_count": 0, "interval_seconds": LIVE_VIEWER_CLEANUP_INTERVAL},
}


async def cleanup_dvr_segments() -> int:
    """
    Delete segments older than DVR window for all active streams.

    Returns the number of segments deleted.
    """
    total_deleted = 0
    now = datetime.now(timezone.utc)

    # Get all live/ending streams
    streams = await fetch_all_with_retry(
        live_streams.select().where(live_streams.c.status.in_(["live", "ending"]))
    )

    for stream in streams:
        stream_id = stream["id"]
        slug = stream["slug"]
        dvr_window = stream["dvr_window_seconds"]

        if dvr_window <= 0:
            # DVR disabled or unlimited
            continue

        cutoff = now - timedelta(seconds=dvr_window)

        # Find segments to delete
        old_segments = await fetch_all_with_retry(
            live_stream_segments.select()
            .where(live_stream_segments.c.stream_id == stream_id)
            .where(live_stream_segments.c.received_at < cutoff)
            .order_by(live_stream_segments.c.received_at)
            .limit(LIVE_DVR_CLEANUP_BATCH_SIZE)
        )

        if not old_segments:
            continue

        # Delete segments in batch
        segment_ids = [seg["id"] for seg in old_segments]

        await db_execute_with_retry(
            live_stream_segments.delete().where(live_stream_segments.c.id.in_(segment_ids))
        )

        # Delete files asynchronously
        loop = asyncio.get_event_loop()
        for seg in old_segments:
            file_path = LIVE_STORAGE_PATH / slug / seg["quality"] / seg["filename"]
            try:
                await loop.run_in_executor(None, lambda p=file_path: p.unlink(missing_ok=True))
            except Exception as e:
                logger.debug(f"Failed to delete segment file {file_path}: {e}")

        total_deleted += len(segment_ids)
        logger.debug(f"Cleaned {len(segment_ids)} DVR segments for stream {slug}")

    return total_deleted


async def detect_stale_streams() -> int:
    """
    Detect streams that haven't received segments recently.

    Transitions:
    - live -> ending: No segments for LIVE_STALE_THRESHOLD seconds
    - ending -> ended: No segments for LIVE_STALE_THRESHOLD * GRACE_MULTIPLIER seconds

    Returns the number of streams that transitioned state.
    """
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=LIVE_STALE_THRESHOLD)
    final_cutoff = now - timedelta(seconds=LIVE_STALE_THRESHOLD * LIVE_STALE_GRACE_MULTIPLIER)

    transitions = 0

    # Check live streams for staleness
    live_streams_query = await fetch_all_with_retry(
        live_streams.select()
        .where(live_streams.c.status == "live")
        .where(
            sa.or_(
                live_streams.c.last_segment_at < stale_cutoff,
                live_streams.c.last_segment_at.is_(None),
            )
        )
    )

    for stream in live_streams_query:
        # Check if there's been a recent segment (race condition check)
        if stream["last_segment_at"] and stream["last_segment_at"] >= stale_cutoff:
            continue

        # Transition to ending
        await db_execute_with_retry(
            live_streams.update()
            .where(live_streams.c.id == stream["id"])
            .where(live_streams.c.status == "live")
            .values(status="ending")
        )

        logger.info(f"Stream {stream['slug']} marked as ending (no segments received)")
        transitions += 1

    # Check ending streams for final timeout
    ending_streams = await fetch_all_with_retry(
        live_streams.select()
        .where(live_streams.c.status == "ending")
        .where(
            sa.or_(
                live_streams.c.last_segment_at < final_cutoff,
                live_streams.c.last_segment_at.is_(None),
            )
        )
    )

    for stream in ending_streams:
        # Check if there's been a recent segment (grace period)
        if stream["last_segment_at"] and stream["last_segment_at"] >= final_cutoff:
            continue

        # Finalize stream
        await db_execute_with_retry(
            live_streams.update()
            .where(live_streams.c.id == stream["id"])
            .where(live_streams.c.status == "ending")
            .values(status="ended", ended_at=now)
        )

        logger.info(f"Stream {stream['slug']} marked as ended (stale timeout)")

        # Finalize playlists and trigger VOD recording
        if stream["auto_record_vod"]:
            try:
                await finalize_playlists_for_vod(stream["id"])
                await trigger_vod_recording(stream["id"])
            except Exception as e:
                logger.error(f"Failed to create VOD for stream {stream['slug']}: {e}")

        transitions += 1

    return transitions


async def _dvr_cleanup_loop():
    """Background loop for DVR cleanup."""
    global _dvr_segments_cleaned

    while _running:
        try:
            deleted = await cleanup_dvr_segments()
            if deleted > 0:
                _dvr_segments_cleaned += deleted
                logger.debug(f"DVR cleanup: deleted {deleted} segments (total: {_dvr_segments_cleaned})")
        except Exception as e:
            logger.error(f"DVR cleanup error: {e}")

        await asyncio.sleep(LIVE_DVR_CLEANUP_INTERVAL)


async def _stale_detection_loop():
    """Background loop for stale stream detection."""
    global _stale_streams_detected

    # Run slightly less often than stale threshold
    check_interval = max(10, LIVE_STALE_THRESHOLD // 2)

    while _running:
        try:
            transitions = await detect_stale_streams()
            if transitions > 0:
                _stale_streams_detected += transitions
                logger.debug(f"Stale detection: {transitions} transitions (total: {_stale_streams_detected})")
        except Exception as e:
            logger.error(f"Stale detection error: {e}")

        await asyncio.sleep(check_interval)


async def cleanup_stale_viewers() -> int:
    """
    Mark viewers as left if no heartbeat received within timeout.

    Also updates viewer counts on affected streams.

    Returns the number of viewers marked as left.
    """
    now = datetime.now(timezone.utc)
    timeout_cutoff = now - timedelta(seconds=LIVE_VIEWER_TIMEOUT)

    # Find stale viewers (not yet marked as left)
    stale_viewers = await fetch_all_with_retry(
        live_stream_viewers.select()
        .where(live_stream_viewers.c.last_heartbeat < timeout_cutoff)
        .where(live_stream_viewers.c.left_at.is_(None))
    )

    if not stale_viewers:
        return 0

    # Group by stream for efficient count updates
    stream_counts = {}
    viewer_ids = []

    for viewer in stale_viewers:
        viewer_ids.append(viewer["id"])
        stream_id = viewer["stream_id"]
        stream_counts[stream_id] = stream_counts.get(stream_id, 0) + 1

    # Mark viewers as left
    await db_execute_with_retry(
        live_stream_viewers.update()
        .where(live_stream_viewers.c.id.in_(viewer_ids))
        .values(left_at=now)
    )

    # Update viewer counts on affected streams and publish updates
    from api.pubsub import Publisher

    for stream_id, count in stream_counts.items():
        await db_execute_with_retry(
            live_streams.update()
            .where(live_streams.c.id == stream_id)
            .values(
                viewer_count_current=sa.func.greatest(
                    0, live_streams.c.viewer_count_current - count
                )
            )
        )

        # Publish updated viewer count (per Ada's review)
        stream = await fetch_one_with_retry(
            live_streams.select().where(live_streams.c.id == stream_id)
        )
        if stream:
            await Publisher.publish_viewer_count(
                stream_id=stream_id,
                current=stream["viewer_count_current"],
                peak=stream["viewer_count_peak"],
                total=stream["viewer_count_total"],
            )

    return len(viewer_ids)


async def _metrics_aggregation_loop():
    """Background loop for metrics aggregation."""
    global _metrics_aggregated

    # Import here to avoid circular imports
    from api.live_metrics import aggregate_and_store_metrics

    while _running:
        try:
            _task_health["metrics_aggregation"]["run_count"] += 1
            processed = await aggregate_and_store_metrics()
            if processed > 0:
                _metrics_aggregated += processed
                logger.debug(f"Metrics aggregation: {processed} streams (total: {_metrics_aggregated})")
            _task_health["metrics_aggregation"]["last_run"] = datetime.now(timezone.utc)
        except Exception as e:
            _task_health["metrics_aggregation"]["error_count"] += 1
            logger.error(f"Metrics aggregation error: {e}")

        await asyncio.sleep(LIVE_METRICS_AGGREGATION_INTERVAL)


async def _metrics_cleanup_loop():
    """Background loop for metrics cleanup (runs hourly)."""
    global _metrics_cleaned

    # Import here to avoid circular imports
    from api.live_metrics import cleanup_old_metrics

    while _running:
        try:
            _task_health["metrics_cleanup"]["run_count"] += 1
            deleted = await cleanup_old_metrics()
            if deleted > 0:
                _metrics_cleaned += deleted
                logger.info(f"Metrics cleanup: deleted {deleted} old metrics (total: {_metrics_cleaned})")
            _task_health["metrics_cleanup"]["last_run"] = datetime.now(timezone.utc)
        except Exception as e:
            _task_health["metrics_cleanup"]["error_count"] += 1
            logger.error(f"Metrics cleanup error: {e}")

        # Run every hour
        await asyncio.sleep(3600)


async def _viewer_cleanup_loop():
    """Background loop for viewer cleanup."""
    global _viewers_cleaned

    while _running:
        try:
            _task_health["viewer_cleanup"]["run_count"] += 1
            cleaned = await cleanup_stale_viewers()
            if cleaned > 0:
                _viewers_cleaned += cleaned
                logger.debug(f"Viewer cleanup: {cleaned} viewers (total: {_viewers_cleaned})")
            _task_health["viewer_cleanup"]["last_run"] = datetime.now(timezone.utc)
        except Exception as e:
            _task_health["viewer_cleanup"]["error_count"] += 1
            logger.error(f"Viewer cleanup error: {e}")

        await asyncio.sleep(LIVE_VIEWER_CLEANUP_INTERVAL)


async def start_live_background_tasks():
    """Start all live streaming background tasks."""
    global _running, _dvr_cleanup_task, _stale_detection_task
    global _metrics_aggregation_task, _metrics_cleanup_task, _viewer_cleanup_task

    if _running:
        logger.warning("Live background tasks already running")
        return

    _running = True

    _dvr_cleanup_task = asyncio.create_task(_dvr_cleanup_loop())
    _stale_detection_task = asyncio.create_task(_stale_detection_loop())
    _metrics_aggregation_task = asyncio.create_task(_metrics_aggregation_loop())
    _metrics_cleanup_task = asyncio.create_task(_metrics_cleanup_loop())
    _viewer_cleanup_task = asyncio.create_task(_viewer_cleanup_loop())

    logger.info("Started live streaming background tasks (including metrics and viewer tracking)")


async def stop_live_background_tasks(timeout: float = 10.0):
    """Stop all live streaming background tasks gracefully."""
    global _running, _dvr_cleanup_task, _stale_detection_task
    global _metrics_aggregation_task, _metrics_cleanup_task, _viewer_cleanup_task

    if not _running:
        return

    _running = False

    tasks = []
    if _dvr_cleanup_task:
        tasks.append(_dvr_cleanup_task)
    if _stale_detection_task:
        tasks.append(_stale_detection_task)
    if _metrics_aggregation_task:
        tasks.append(_metrics_aggregation_task)
    if _metrics_cleanup_task:
        tasks.append(_metrics_cleanup_task)
    if _viewer_cleanup_task:
        tasks.append(_viewer_cleanup_task)

    if tasks:
        # Wait for tasks to complete with timeout
        done, pending = await asyncio.wait(tasks, timeout=timeout)

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Task cancellation during shutdown is expected; suppress this error
                pass

    _dvr_cleanup_task = None
    _stale_detection_task = None
    _metrics_aggregation_task = None
    _metrics_cleanup_task = None
    _viewer_cleanup_task = None

    logger.info("Stopped live streaming background tasks")


def get_live_task_metrics() -> dict:
    """Get metrics for live streaming background tasks."""
    return {
        "dvr_segments_cleaned_total": _dvr_segments_cleaned,
        "stale_streams_detected_total": _stale_streams_detected,
        "metrics_aggregated_total": _metrics_aggregated,
        "metrics_cleaned_total": _metrics_cleaned,
        "viewers_cleaned_total": _viewers_cleaned,
        "running": _running,
        "task_health": _task_health,
    }


def get_task_health() -> dict:
    """
    Get health status for background tasks.

    Returns a dict suitable for health check endpoints (per Cid's review).
    Each task uses its own interval for health threshold (2x expected interval).
    """
    now = datetime.now(timezone.utc)
    health = {}

    for task_name, stats in _task_health.items():
        last_run = stats["last_run"]
        # Task is healthy if it ran within 2x its expected interval
        # This allows for some variance without false alarms
        interval_seconds = stats.get("interval_seconds", 120)  # Default 2 min
        threshold = timedelta(seconds=interval_seconds * 2)
        healthy = last_run is not None and (now - last_run) < threshold
        health[task_name] = {
            "healthy": healthy,
            "last_run": last_run.isoformat() if last_run else None,
            "run_count": stats["run_count"],
            "error_count": stats["error_count"],
            "expected_interval_seconds": interval_seconds,
        }

    return health
