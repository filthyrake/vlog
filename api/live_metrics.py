"""
Live stream metrics computation and publishing.

Provides utilities for computing and publishing real-time stream metrics
for the studio/broadcaster dashboard.

Related Issue: #524
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from api.database import database, live_stream_segments, live_streams
from api.pubsub import publish_stream_metrics

logger = logging.getLogger(__name__)

# Bitrate estimation window (seconds)
BITRATE_WINDOW_SECONDS = 30

# Cache TTL for bitrate estimates (seconds) - prevents query amplification
BITRATE_CACHE_TTL_SECONDS = 3.0

# Maximum cache size to prevent unbounded memory growth (Issue #553)
BITRATE_CACHE_MAX_SIZE = 1000

# Number of sharded locks to reduce contention (Issue #545)
# Using 16 shards allows ~16x more concurrent access
BITRATE_CACHE_SHARD_COUNT = 16

# Simple in-memory cache for bitrate estimates
# Format: {stream_id: (bitrate_kbps, expiry_time)}
_bitrate_cache: Dict[int, Tuple[Optional[int], float]] = {}

# Sharded locks to reduce contention under high load (Issue #545)
# Each shard handles stream_ids where hash(stream_id) % SHARD_COUNT == shard_index
_cache_locks = [asyncio.Lock() for _ in range(BITRATE_CACHE_SHARD_COUNT)]

# Single lock to prevent concurrent eviction attempts (Issue #545/#553 review feedback)
# This avoids the need to acquire all shard locks during eviction
_eviction_lock = asyncio.Lock()


def _get_cache_lock(stream_id: int) -> asyncio.Lock:
    """Get the appropriate sharded lock for a stream_id."""
    return _cache_locks[stream_id % BITRATE_CACHE_SHARD_COUNT]


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
    Uses caching to prevent database query amplification when called
    frequently (e.g., on every segment upload).

    Args:
        stream_id: The stream ID
        window_seconds: Time window for bitrate estimation

    Returns:
        Estimated bitrate in kbps, or None if not enough data
    """
    import time
    current_time = time.monotonic()
    cache_lock = _get_cache_lock(stream_id)

    # Check cache first (with sharded lock to reduce contention - Issue #545)
    async with cache_lock:
        if stream_id in _bitrate_cache:
            cached_value, expiry_time = _bitrate_cache[stream_id]
            if current_time < expiry_time:
                return cached_value

    # Cache miss or expired - compute fresh value
    bitrate_kbps = await _compute_bitrate_uncached(stream_id, window_seconds)

    # Update cache (with sharded lock)
    async with cache_lock:
        _bitrate_cache[stream_id] = (bitrate_kbps, current_time + BITRATE_CACHE_TTL_SECONDS)

    # Enforce cache size limit (Issue #553)
    # Use a separate check to avoid holding lock during eviction scan
    if len(_bitrate_cache) > BITRATE_CACHE_MAX_SIZE:
        await _enforce_cache_size_limit(current_time)

    return bitrate_kbps


async def _enforce_cache_size_limit(current_time: float) -> None:
    """
    Enforce the cache size limit by evicting expired and oldest entries.

    Uses a single eviction lock to prevent concurrent evictions, then
    acquires per-shard locks only when deleting entries from that shard.
    This allows normal cache operations on other shards to continue during eviction.
    (Issue #545/#553 review feedback)
    """
    # Quick check without lock - if we're under limit, skip
    if len(_bitrate_cache) <= BITRATE_CACHE_MAX_SIZE:
        return

    # Use eviction lock to prevent concurrent evictions
    # This is non-blocking for normal cache operations
    if not _eviction_lock.locked():
        async with _eviction_lock:
            # Re-check under lock - another eviction may have already cleaned up
            if len(_bitrate_cache) <= BITRATE_CACHE_MAX_SIZE:
                return

            # Build list of keys to evict (without holding shard locks)
            keys_to_evict = []

            # First, collect expired entries
            for k, (_, expiry) in list(_bitrate_cache.items()):
                if current_time >= expiry:
                    keys_to_evict.append(k)

            # If still over limit after removing expired, find oldest entries
            remaining_after_expired = len(_bitrate_cache) - len(keys_to_evict)
            if remaining_after_expired > BITRATE_CACHE_MAX_SIZE:
                # Sort non-expired entries by expiry time (oldest first)
                non_expired = [
                    (k, v) for k, v in _bitrate_cache.items()
                    if k not in keys_to_evict
                ]
                non_expired.sort(key=lambda x: x[1][1])
                evict_count = remaining_after_expired - BITRATE_CACHE_MAX_SIZE
                keys_to_evict.extend(k for k, _ in non_expired[:evict_count])

            # Delete entries, acquiring only the relevant shard lock for each
            # Group by shard to minimize lock acquisitions
            by_shard: Dict[int, list] = {}
            for k in keys_to_evict:
                shard = k % BITRATE_CACHE_SHARD_COUNT
                if shard not in by_shard:
                    by_shard[shard] = []
                by_shard[shard].append(k)

            evicted = 0
            for shard, keys in by_shard.items():
                async with _cache_locks[shard]:
                    for k in keys:
                        if k in _bitrate_cache:
                            del _bitrate_cache[k]
                            evicted += 1

            if evicted > 0:
                logger.warning(
                    f"Bitrate cache exceeded max size ({BITRATE_CACHE_MAX_SIZE}), "
                    f"evicted {evicted} entries"
                )


async def _compute_bitrate_uncached(stream_id: int, window_seconds: int) -> Optional[int]:
    """
    Compute bitrate from database (uncached).

    Internal function - use estimate_bitrate() for cached access.
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
