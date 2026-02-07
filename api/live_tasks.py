"""Background tasks for live streaming.

Handles:
- DVR window cleanup: Delete old segments beyond DVR window
- Stale stream detection: Mark streams as ending/ended when no segments received
- Playlist updates: Periodic refresh of HLS playlists
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import sqlalchemy as sa

from api.database import live_stream_segments, live_streams
from api.db_retry import db_execute_with_retry, fetch_all_with_retry, fetch_one_with_retry
from api.live_playlist import finalize_playlists_for_vod
from api.live_vod import trigger_vod_recording
from config import (
    LIVE_DVR_CLEANUP_BATCH_SIZE,
    LIVE_DVR_CLEANUP_INTERVAL,
    LIVE_STALE_GRACE_MULTIPLIER,
    LIVE_STALE_THRESHOLD,
    LIVE_STORAGE_PATH,
)

logger = logging.getLogger(__name__)

# Background task handles
_dvr_cleanup_task: Optional[asyncio.Task] = None
_stale_detection_task: Optional[asyncio.Task] = None
_running = False

# Prometheus-style metrics (counter values)
_dvr_segments_cleaned = 0
_stale_streams_detected = 0


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
        # Atomic UPDATE with WHERE conditions on status AND timestamp.
        # This eliminates the race condition where a segment arrives between
        # our SELECT and UPDATE — the UPDATE simply won't match any rows
        # if the stream is no longer stale. (Issue #551)
        result = await db_execute_with_retry(
            live_streams.update()
            .where(live_streams.c.id == stream["id"])
            .where(live_streams.c.status == "live")
            .where(
                sa.or_(
                    live_streams.c.last_segment_at < stale_cutoff,
                    live_streams.c.last_segment_at.is_(None),
                )
            )
            .values(status="ending")
        )

        if result > 0:
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
        # Atomic UPDATE with WHERE conditions on status AND timestamp.
        # Same race-condition fix as the live->ending transition. (Issue #551)
        result = await db_execute_with_retry(
            live_streams.update()
            .where(live_streams.c.id == stream["id"])
            .where(live_streams.c.status == "ending")
            .where(
                sa.or_(
                    live_streams.c.last_segment_at < final_cutoff,
                    live_streams.c.last_segment_at.is_(None),
                )
            )
            .values(status="ended", ended_at=now)
        )

        if result > 0:
            logger.info(f"Stream {stream['slug']} marked as ended (stale timeout)")

            # Re-fetch stream after update to verify state before VOD recording (Issue #552)
            updated_stream = await fetch_one_with_retry(
                live_streams.select().where(live_streams.c.id == stream["id"])
            )

            if updated_stream and updated_stream["status"] == "ended" and updated_stream["auto_record_vod"]:
                try:
                    await finalize_playlists_for_vod(stream["id"])
                    await trigger_vod_recording(stream["id"])
                except Exception as e:
                    logger.error(f"Failed to create VOD for stream {stream['slug']}: {e}")
            elif not updated_stream or updated_stream["status"] != "ended":
                logger.warning(
                    f"Stream {stream['slug']} status verification failed after update "
                    f"(expected 'ended', got '{updated_stream['status'] if updated_stream else 'missing'}'), "
                    f"skipping VOD recording"
                )

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


async def start_live_background_tasks():
    """Start all live streaming background tasks."""
    global _running, _dvr_cleanup_task, _stale_detection_task

    if _running:
        logger.warning("Live background tasks already running")
        return

    _running = True

    _dvr_cleanup_task = asyncio.create_task(_dvr_cleanup_loop())
    _stale_detection_task = asyncio.create_task(_stale_detection_loop())

    logger.info("Started live streaming background tasks")


async def stop_live_background_tasks(timeout: float = 10.0):
    """Stop all live streaming background tasks gracefully."""
    global _running, _dvr_cleanup_task, _stale_detection_task

    if not _running:
        return

    _running = False

    tasks = []
    if _dvr_cleanup_task:
        tasks.append(_dvr_cleanup_task)
    if _stale_detection_task:
        tasks.append(_stale_detection_task)

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

    logger.info("Stopped live streaming background tasks")


def get_live_task_metrics() -> dict:
    """Get metrics for live streaming background tasks."""
    return {
        "dvr_segments_cleaned_total": _dvr_segments_cleaned,
        "stale_streams_detected_total": _stale_streams_detected,
        "running": _running,
    }
