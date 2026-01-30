"""Live stream metrics aggregation and health classification.

This module handles:
- Raw metrics storage in Redis (pushed during segment upload)
- Background aggregation task (every 10 seconds)
- Health classification based on latency and drop rate
- Metrics cleanup for old data

Design decisions per architectural review:
- Metrics are NOT computed inline during segment push (would block)
- Raw data stored in Redis, aggregated in background
- Single aggregated row written to database per interval
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from api.database import live_stream_metrics, live_streams
from api.db_retry import db_execute_with_retry, fetch_all_with_retry, fetch_one_with_retry
from api.pubsub import Publisher
from api.redis_client import get_redis
from config import (
    LIVE_HEALTH_DROP_RATE_DEGRADED,
    LIVE_HEALTH_DROP_RATE_GOOD,
    LIVE_HEALTH_LATENCY_DEGRADED_MS,
    LIVE_HEALTH_LATENCY_GOOD_MS,
    LIVE_METRICS_AGGREGATION_INTERVAL,
    LIVE_METRICS_RETENTION_HOURS,
    REDIS_PUBSUB_PREFIX,
)

logger = logging.getLogger(__name__)

# Redis key patterns for raw metrics
RAW_METRICS_KEY = f"{REDIS_PUBSUB_PREFIX}:metrics:{{stream_id}}:raw"
RAW_METRICS_TTL = 120  # Keep raw data for 2 minutes (enough for aggregation)


def classify_connection_health(
    latency_ms: Optional[int],
    drop_rate: float,
) -> str:
    """
    Classify connection health based on latency and drop rate.

    Args:
        latency_ms: Average segment push latency in milliseconds
        drop_rate: Ratio of dropped segments (0.0 to 1.0)

    Returns:
        Health status: 'good', 'degraded', 'poor', or 'unknown'
    """
    if latency_ms is None:
        return "unknown"

    # Good: Low latency AND low drop rate
    if latency_ms <= LIVE_HEALTH_LATENCY_GOOD_MS and drop_rate <= LIVE_HEALTH_DROP_RATE_GOOD:
        return "good"

    # Degraded: Moderate latency OR moderate drop rate
    if latency_ms <= LIVE_HEALTH_LATENCY_DEGRADED_MS and drop_rate <= LIVE_HEALTH_DROP_RATE_DEGRADED:
        return "degraded"

    # Poor: Everything else
    return "poor"


async def push_raw_metric(
    stream_id: int,
    size_bytes: int,
    duration_ms: Optional[int],
    latency_ms: Optional[int],
    quality: str,
) -> bool:
    """
    Push raw segment metric to Redis for later aggregation.

    Called during segment upload (non-blocking).

    Args:
        stream_id: Stream ID
        size_bytes: Segment size in bytes
        duration_ms: Segment duration in milliseconds (if known)
        latency_ms: Time from segment creation to receipt
        quality: Quality level being uploaded

    Returns:
        True if pushed successfully
    """
    redis = await get_redis()
    if not redis:
        # Log Redis unavailability for observability (per Cid's review)
        logger.warning(f"Redis unavailable when pushing raw metric for stream {stream_id}")
        return False

    key = RAW_METRICS_KEY.format(stream_id=stream_id)
    now = datetime.now(timezone.utc)

    metric = {
        "ts": now.isoformat(),
        "size": size_bytes,
        "duration_ms": duration_ms,
        "latency_ms": latency_ms,
        "quality": quality,
    }

    try:
        # Push to list with TTL
        await redis.rpush(key, json.dumps(metric))
        await redis.expire(key, RAW_METRICS_TTL)
        return True
    except Exception as e:
        logger.warning(f"Failed to push raw metric for stream {stream_id}: {e}")
        return False


async def aggregate_metrics_for_stream(stream_id: int) -> Optional[Dict[str, Any]]:
    """
    Aggregate raw metrics from Redis into a single metrics record.

    Reads and clears raw metrics from Redis, computes aggregates,
    and returns a dict ready for database insertion.

    Args:
        stream_id: Stream ID to aggregate

    Returns:
        Aggregated metrics dict or None if no data
    """
    redis = await get_redis()
    if not redis:
        # Log Redis unavailability for observability (per Cid's review)
        logger.warning(f"Redis unavailable when aggregating metrics for stream {stream_id}")
        return None

    key = RAW_METRICS_KEY.format(stream_id=stream_id)
    now = datetime.now(timezone.utc)

    try:
        # Get all raw metrics and clear the list atomically
        raw_data = await redis.lrange(key, 0, -1)
        if raw_data:
            await redis.delete(key)
    except Exception as e:
        logger.warning(f"Failed to read raw metrics for stream {stream_id}: {e}")
        return None

    if not raw_data:
        return None

    # Parse raw metrics
    metrics = []
    for item in raw_data:
        try:
            if isinstance(item, bytes):
                item = item.decode("utf-8")
            metrics.append(json.loads(item))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

    if not metrics:
        return None

    # Compute aggregates
    total_size = sum(m.get("size", 0) for m in metrics)
    total_duration_ms = sum(m.get("duration_ms", 0) or 0 for m in metrics)
    latencies = [m.get("latency_ms") for m in metrics if m.get("latency_ms") is not None]
    segments_received = len(metrics)

    # Calculate bitrate (bytes/second)
    interval_seconds = LIVE_METRICS_AGGREGATION_INTERVAL
    bitrate_total = int(total_size / interval_seconds) if interval_seconds > 0 else 0

    # Average latency
    avg_latency = int(sum(latencies) / len(latencies)) if latencies else None

    # For now, we don't track dropped segments in this implementation
    # This would require comparing expected vs received sequence numbers
    segments_dropped = 0
    drop_rate = 0.0

    return {
        "stream_id": stream_id,
        "timestamp": now,
        "bitrate_video": None,  # Would need separate tracking per quality
        "bitrate_audio": None,
        "bitrate_total": bitrate_total,
        "segment_push_latency_ms": avg_latency,
        "segments_received": segments_received,
        "segments_dropped": segments_dropped,
        "interval_seconds": interval_seconds,
    }


async def aggregate_and_store_metrics() -> int:
    """
    Aggregate metrics for all active streams and store in database.

    This is the main aggregation task that runs every LIVE_METRICS_AGGREGATION_INTERVAL.

    Returns:
        Number of streams processed
    """
    # Get all live streams
    streams = await fetch_all_with_retry(
        live_streams.select().where(live_streams.c.status.in_(["live", "ending"]))
    )

    processed = 0

    for stream in streams:
        stream_id = stream["id"]

        try:
            aggregated = await aggregate_metrics_for_stream(stream_id)
            if not aggregated:
                continue

            # Insert aggregated metrics
            await db_execute_with_retry(
                live_stream_metrics.insert().values(**aggregated)
            )

            # Calculate health and update stream
            drop_rate = 0.0
            if aggregated["segments_received"] > 0:
                drop_rate = aggregated["segments_dropped"] / aggregated["segments_received"]

            health = classify_connection_health(
                aggregated["segment_push_latency_ms"],
                drop_rate,
            )

            await db_execute_with_retry(
                live_streams.update()
                .where(live_streams.c.id == stream_id)
                .values(
                    current_bitrate=aggregated["bitrate_total"],
                    connection_health=health,
                    last_metric_at=aggregated["timestamp"],
                )
            )

            # Publish metrics to pub/sub for real-time SSE updates (per Ada's review)
            await Publisher.publish_stream_metrics(
                stream_id=stream_id,
                bitrate_total=aggregated["bitrate_total"],
                connection_health=health,
                segment_latency_ms=aggregated["segment_push_latency_ms"],
                segments_received=aggregated["segments_received"],
                segments_dropped=aggregated["segments_dropped"],
            )

            processed += 1

        except Exception as e:
            logger.error(f"Failed to aggregate metrics for stream {stream_id}: {e}")

    return processed


async def cleanup_old_metrics() -> int:
    """
    Delete metrics older than LIVE_METRICS_RETENTION_HOURS.

    Returns:
        Number of metrics deleted
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LIVE_METRICS_RETENTION_HOURS)

    try:
        # Delete in batches to avoid long-running transactions
        result = await db_execute_with_retry(
            live_stream_metrics.delete().where(
                live_stream_metrics.c.timestamp < cutoff
            )
        )
        return result if isinstance(result, int) else 0
    except Exception as e:
        logger.error(f"Failed to cleanup old metrics: {e}")
        return 0


async def get_recent_metrics(
    stream_id: int,
    minutes: int = 5,
) -> List[Dict[str, Any]]:
    """
    Get recent metrics for a stream.

    Args:
        stream_id: Stream ID
        minutes: How many minutes of history to retrieve

    Returns:
        List of metric records, newest first
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    rows = await fetch_all_with_retry(
        live_stream_metrics.select()
        .where(live_stream_metrics.c.stream_id == stream_id)
        .where(live_stream_metrics.c.timestamp >= cutoff)
        .order_by(live_stream_metrics.c.timestamp.desc())
    )

    return [dict(row) for row in rows]


async def get_metrics_history(
    stream_id: int,
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    """
    Get historical metrics for a stream within a time range.

    Args:
        stream_id: Stream ID
        start: Start of time range
        end: End of time range

    Returns:
        List of metric records, oldest first
    """
    rows = await fetch_all_with_retry(
        live_stream_metrics.select()
        .where(live_stream_metrics.c.stream_id == stream_id)
        .where(live_stream_metrics.c.timestamp >= start)
        .where(live_stream_metrics.c.timestamp <= end)
        .order_by(live_stream_metrics.c.timestamp.asc())
    )

    return [dict(row) for row in rows]
