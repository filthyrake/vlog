"""
Live stream metrics computation and publishing.

Provides utilities for computing and publishing real-time stream metrics
for the studio/broadcaster dashboard.

Related Issue: #524
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from api.database import database, live_stream_segments, live_streams
from api.pubsub import publish_stream_metrics

logger = logging.getLogger(__name__)

# Bitrate estimation window (seconds)
BITRATE_WINDOW_SECONDS = 30


async def compute_and_publish_metrics(stream_id: int) -> bool:
    """
    Compute and publish metrics for a stream.

    Fetches current stream state from database, computes metrics like
    estimated bitrate, and publishes to the pub/sub channel.

    Args:
        stream_id: The stream ID

    Returns:
        True if metrics were published successfully
    """
    # Fetch stream
    stream = await database.fetch_one(
        live_streams.select().where(live_streams.c.id == stream_id)
    )

    if not stream:
        logger.warning(f"Stream {stream_id} not found for metrics computation")
        return False

    # Parse qualities from JSON
    qualities = []
    if stream["qualities"]:
        try:
            qualities = json.loads(stream["qualities"])
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse qualities JSON for stream {stream_id}")

    # Compute bitrate estimate from recent segments
    bitrate_kbps = await estimate_bitrate(stream_id)

    # Format last_segment_at
    last_segment_at = None
    if stream["last_segment_at"]:
        ts = stream["last_segment_at"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        last_segment_at = ts.isoformat()

    # Publish metrics
    return await publish_stream_metrics(
        stream_id=stream_id,
        stream_slug=stream["slug"],
        status=stream["status"],
        segment_count=stream["segment_count"],
        qualities=qualities,
        bitrate_kbps=bitrate_kbps,
        last_segment_at=last_segment_at,
    )


async def estimate_bitrate(stream_id: int, window_seconds: int = BITRATE_WINDOW_SECONDS) -> Optional[int]:
    """
    Estimate the current bitrate of a stream based on recent segments.

    Computes bitrate from segments received in the last N seconds.

    Args:
        stream_id: The stream ID
        window_seconds: Time window for bitrate estimation

    Returns:
        Estimated bitrate in kbps, or None if not enough data
    """
    now = datetime.now(timezone.utc)

    # Query for recent segments
    query = """
        SELECT SUM(size_bytes) as total_bytes,
               COUNT(*) as segment_count,
               MIN(received_at) as first_received,
               MAX(received_at) as last_received
        FROM live_stream_segments
        WHERE stream_id = :stream_id
        AND received_at >= :window_start
    """

    # Calculate window start time
    window_start = now - timedelta(seconds=window_seconds)
    # Remove timezone for database compatibility if needed
    window_start_db = window_start.replace(tzinfo=None) if window_start.tzinfo else window_start

    result = await database.fetch_one(
        query,
        {
            "stream_id": stream_id,
            "window_start": window_start_db,
        },
    )

    if not result or not result["total_bytes"] or result["segment_count"] < 2:
        return None

    total_bytes = result["total_bytes"]
    first_received = result["first_received"]
    last_received = result["last_received"]

    if not first_received or not last_received:
        return None

    # Ensure timezone-aware
    if first_received.tzinfo is None:
        first_received = first_received.replace(tzinfo=timezone.utc)
    if last_received.tzinfo is None:
        last_received = last_received.replace(tzinfo=timezone.utc)

    # Calculate time span
    time_span_seconds = (last_received - first_received).total_seconds()
    if time_span_seconds <= 0:
        return None

    # Calculate bitrate: (bytes * 8) / seconds / 1000 = kbps
    bitrate_kbps = int((total_bytes * 8) / time_span_seconds / 1000)

    return bitrate_kbps


async def get_stream_metrics(stream_id: int) -> Optional[dict]:
    """
    Get current metrics for a stream.

    Args:
        stream_id: The stream ID

    Returns:
        Dict with metrics, or None if stream not found
    """
    stream = await database.fetch_one(
        live_streams.select().where(live_streams.c.id == stream_id)
    )

    if not stream:
        return None

    # Parse qualities
    qualities = []
    if stream["qualities"]:
        try:
            qualities = json.loads(stream["qualities"])
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse qualities JSON for stream {stream_id}")

    # Compute bitrate
    bitrate_kbps = await estimate_bitrate(stream_id)

    # Format timestamps
    last_segment_at = None
    if stream["last_segment_at"]:
        ts = stream["last_segment_at"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        last_segment_at = ts.isoformat()

    return {
        "stream_id": stream_id,
        "stream_slug": stream["slug"],
        "status": stream["status"],
        "segment_count": stream["segment_count"],
        "qualities": qualities,
        "bitrate_kbps": bitrate_kbps,
        "last_segment_at": last_segment_at,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
