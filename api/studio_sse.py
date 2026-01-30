"""Studio SSE endpoint for real-time dashboard updates.

This module provides Server-Sent Events (SSE) for the studio dashboard:
- Stream metrics (bitrate, health) every 5 seconds
- Viewer count changes
- Stream state changes (live -> ending -> ended)

Connection limits per Cid's review:
- Max 5 SSE connections per user
- Max 20 SSE connections per stream
- Shared Redis subscription per stream (not per connection)

Issue #524 - Broadcaster Dashboard
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from api.auth.middleware import require_auth
from api.database import live_streams
from api.db_retry import fetch_one_with_retry
from api.pubsub import Subscriber, subscribe_to_live_stream
from config import (
    LIVE_ENABLED,
    LIVE_STUDIO_SSE_MAX_PER_STREAM,
    LIVE_STUDIO_SSE_MAX_PER_USER,
)

logger = logging.getLogger(__name__)

# Create router for studio SSE
router = APIRouter(prefix="/api/events", tags=["Studio SSE"])

# Connection tracking
# user_id -> set of stream_ids they're connected to
_user_connections: Dict[str, Set[int]] = defaultdict(set)

# stream_id -> number of active connections
_stream_connections: Dict[int, int] = defaultdict(int)

# Lock for thread-safe connection tracking
_connection_lock = asyncio.Lock()


async def _track_connection(user_id: str, stream_id: int) -> bool:
    """
    Track a new SSE connection.

    Returns False if connection limits exceeded.
    """
    async with _connection_lock:
        # Check user limit
        if len(_user_connections[user_id]) >= LIVE_STUDIO_SSE_MAX_PER_USER:
            # Allow if already connected to this stream
            if stream_id not in _user_connections[user_id]:
                return False

        # Check stream limit
        if _stream_connections[stream_id] >= LIVE_STUDIO_SSE_MAX_PER_STREAM:
            return False

        # Track connection
        _user_connections[user_id].add(stream_id)
        _stream_connections[stream_id] += 1

        logger.debug(
            f"SSE connection opened: user={user_id}, stream={stream_id}, "
            f"user_count={len(_user_connections[user_id])}, "
            f"stream_count={_stream_connections[stream_id]}"
        )

        return True


async def _untrack_connection(user_id: str, stream_id: int) -> None:
    """Remove connection from tracking."""
    async with _connection_lock:
        if user_id in _user_connections:
            _user_connections[user_id].discard(stream_id)
            if not _user_connections[user_id]:
                del _user_connections[user_id]

        if stream_id in _stream_connections:
            _stream_connections[stream_id] -= 1
            if _stream_connections[stream_id] <= 0:
                del _stream_connections[stream_id]

        logger.debug(f"SSE connection closed: user={user_id}, stream={stream_id}")


def _check_stream_ownership(stream: dict, user: dict) -> bool:
    """Check if user owns the stream or is admin."""
    if user.get("role") == "admin":
        return True
    return stream.get("owner_id") == user.get("id")


async def _event_generator(
    stream_id: int,
    user_id: str,
    initial_data: dict,
) -> AsyncIterator[dict]:
    """
    Generate SSE events for the studio dashboard.

    Yields:
        - Initial stream data
        - Metrics updates from Redis pub/sub
        - Viewer count updates
        - State changes
        - Periodic heartbeat (every 30s)
    """
    subscriber: Optional[Subscriber] = None

    try:
        # Send initial data
        yield {
            "event": "init",
            "data": json.dumps({
                "type": "init",
                "stream_id": stream_id,
                "current_bitrate": initial_data.get("current_bitrate"),
                "connection_health": initial_data.get("connection_health") or "unknown",
                "viewer_count_current": initial_data.get("viewer_count_current") or 0,
                "viewer_count_peak": initial_data.get("viewer_count_peak") or 0,
                "status": initial_data.get("status"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }),
        }

        # Subscribe to live stream channels
        subscriber = await subscribe_to_live_stream(stream_id)

        if not subscriber.is_active:
            logger.warning(f"Failed to subscribe to stream {stream_id}")
            yield {
                "event": "error",
                "data": json.dumps({"error": "Failed to connect to updates"}),
            }
            return

        # Heartbeat interval
        heartbeat_interval = 30.0
        last_heartbeat = asyncio.get_event_loop().time()

        # Listen for messages
        async for message in subscriber.listen():
            # Check for heartbeat
            current_time = asyncio.get_event_loop().time()
            if current_time - last_heartbeat >= heartbeat_interval:
                yield {
                    "event": "heartbeat",
                    "data": json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }),
                }
                last_heartbeat = current_time

            # Parse and forward message
            event_type = message.get("type", "unknown")

            if event_type == "metrics":
                yield {
                    "event": "metrics",
                    "data": json.dumps(message),
                }
            elif event_type == "viewers":
                yield {
                    "event": "viewers",
                    "data": json.dumps(message),
                }
            elif event_type == "state":
                yield {
                    "event": "state",
                    "data": json.dumps(message),
                }
                # If stream ended, close connection
                if message.get("status") == "ended":
                    yield {
                        "event": "close",
                        "data": json.dumps({
                            "type": "close",
                            "reason": "stream_ended",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }),
                    }
                    return

    except asyncio.CancelledError:
        logger.debug(f"SSE connection cancelled for stream {stream_id}")
        raise
    except Exception as e:
        logger.error(f"SSE error for stream {stream_id}: {e}")
        yield {
            "event": "error",
            "data": json.dumps({"error": "Connection error"}),
        }
    finally:
        if subscriber:
            await subscriber.close()
        await _untrack_connection(user_id, stream_id)


@router.get("/studio/{slug}")
async def studio_sse_endpoint(
    request: Request,
    slug: str,
    current_user: dict = Depends(require_auth),
) -> EventSourceResponse:
    """
    SSE endpoint for studio dashboard real-time updates.

    Streams:
    - init: Initial stream state
    - metrics: Bitrate, health, latency updates
    - viewers: Viewer count changes
    - state: Stream status changes (live -> ending -> ended)
    - heartbeat: Keep-alive every 30 seconds
    - error: Error notifications
    - close: Connection closing (e.g., stream ended)

    Connection limits:
    - Max 5 connections per user
    - Max 20 connections per stream
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    # Get stream
    row = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not row:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Check ownership
    if not _check_stream_ownership(dict(row), current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    user_id = current_user["id"]
    stream_id = row["id"]

    # Track connection (check limits)
    if not await _track_connection(user_id, stream_id):
        raise HTTPException(
            status_code=429,
            detail="Too many SSE connections. Close existing connections first.",
        )

    return EventSourceResponse(
        _event_generator(stream_id, user_id, dict(row)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


def get_sse_connection_stats() -> dict:
    """
    Get SSE connection statistics for monitoring.

    Returns:
        Dict with user_count, stream_count, and total_connections
    """
    return {
        "user_count": len(_user_connections),
        "stream_count": len(_stream_connections),
        "total_connections": sum(_stream_connections.values()),
        "per_user_limit": LIVE_STUDIO_SSE_MAX_PER_USER,
        "per_stream_limit": LIVE_STUDIO_SSE_MAX_PER_STREAM,
    }


# Alias for health check endpoint
get_sse_stats = get_sse_connection_stats
