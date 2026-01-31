"""
Studio SSE (Server-Sent Events) endpoint for real-time stream metrics.

Provides real-time updates to the broadcaster dashboard including:
- Segment count
- Bitrate estimation
- Stream status changes

Security:
- Session validation on connection
- Session revalidation every 5 minutes
- Connection limits per stream (10 max)

Related Issue: #524
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Set

from fastapi import APIRouter, Cookie, HTTPException, Request
from slowapi import Limiter
from sse_starlette.sse import EventSourceResponse

from api.audit import AuditAction, log_audit
from api.auth.middleware import SESSION_COOKIE_NAME
from api.auth.permissions import Permission, Role, has_permission
from api.auth.sessions import validate_session_token
from api.common import get_real_ip, get_request_id
from api.database import database, live_streams
from api.db_retry import fetch_one_with_retry
from api.live_metrics import get_stream_metrics
from api.pubsub import subscribe_to_stream_metrics
from config import LIVE_ENABLED, RATE_LIMIT_ENABLED, RATE_LIMIT_STORAGE_URL

logger = logging.getLogger(__name__)

# Create router for studio SSE
router = APIRouter(prefix="/api/v1/studio", tags=["Studio SSE"])

# Initialize rate limiter for connection attempts
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)

# Maximum SSE clients per stream (broadcasters only)
MAX_SSE_CLIENTS_PER_STREAM = 10

# Session revalidation interval (5 minutes)
SESSION_REVALIDATION_SECONDS = 300

# Heartbeat interval for SSE keepalive
SSE_HEARTBEAT_SECONDS = 30

# Track active SSE connections per stream
_active_connections: Dict[int, Set[str]] = {}
_connection_lock = asyncio.Lock()


async def _add_connection(stream_id: int, connection_id: str) -> bool:
    """
    Add a connection to the tracking set.

    Returns True if added, False if limit reached.
    """
    async with _connection_lock:
        if stream_id not in _active_connections:
            _active_connections[stream_id] = set()

        if len(_active_connections[stream_id]) >= MAX_SSE_CLIENTS_PER_STREAM:
            return False

        _active_connections[stream_id].add(connection_id)
        return True


async def _remove_connection(stream_id: int, connection_id: str) -> None:
    """Remove a connection from the tracking set."""
    async with _connection_lock:
        if stream_id in _active_connections:
            _active_connections[stream_id].discard(connection_id)
            if not _active_connections[stream_id]:
                del _active_connections[stream_id]


async def get_sse_client_count(stream_id: int) -> int:
    """Get current SSE client count for a stream."""
    async with _connection_lock:
        return len(_active_connections.get(stream_id, set()))


async def verify_stream_access_for_sse(slug: str, user: dict) -> dict:
    """
    Verify user has access to a stream for SSE connection.

    Args:
        slug: Stream slug
        user: Current authenticated user

    Returns:
        Stream record as dict

    Raises:
        HTTPException: 404 if stream not found or user doesn't have access
    """
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Owner check OR admin permission
    role = Role(user["role"])
    is_owner = stream["owner_id"] == user["id"]
    has_manage_any = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    if not is_owner and not has_manage_any:
        raise HTTPException(status_code=404, detail="Stream not found")

    return dict(stream)


@router.get("/streams/{slug}/events")
@limiter.limit("30/minute")  # Rate limit connection attempts
async def stream_events(
    request: Request,
    slug: str,
    session_token: str = Cookie(None, alias=SESSION_COOKIE_NAME),
) -> EventSourceResponse:
    """
    SSE endpoint for real-time stream metrics.

    Sends metrics events whenever the stream state changes.
    Connection is closed if session becomes invalid.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    # Validate session
    if not session_token:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = await validate_session_token(session_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")

    # Verify stream access
    stream = await verify_stream_access_for_sse(slug, user)
    stream_id = stream["id"]

    # Generate connection ID
    import uuid
    connection_id = str(uuid.uuid4())[:8]

    # Check connection limit
    if not await _add_connection(stream_id, connection_id):
        raise HTTPException(
            status_code=429,
            detail="Too many connections to this stream",
        )

    # Audit log SSE connection
    log_audit(
        AuditAction.STUDIO_SSE_CONNECT,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="live_stream",
        resource_id=stream_id,
        resource_name=slug,
        details={
            "user_id": user["id"],
            "connection_id": connection_id,
        },
        request_id=get_request_id(request),
    )

    async def event_generator():
        subscriber = None
        last_session_check = datetime.now(timezone.utc)

        try:
            # Send initial metrics
            initial_metrics = await get_stream_metrics(stream_id)
            if initial_metrics:
                yield {
                    "event": "metrics",
                    "data": json.dumps(initial_metrics),
                }

            # Subscribe to metrics channel
            subscriber = await subscribe_to_stream_metrics(stream_id)

            # Create tasks for listening and heartbeat
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.debug(f"SSE client {connection_id} disconnected")
                    break

                # Check session validity periodically
                now = datetime.now(timezone.utc)
                if (now - last_session_check).total_seconds() >= SESSION_REVALIDATION_SECONDS:
                    revalidated_user = await validate_session_token(session_token)
                    if not revalidated_user:
                        yield {
                            "event": "session_expired",
                            "data": json.dumps({"message": "Session expired"}),
                        }
                        logger.info(f"SSE session expired for {connection_id}")
                        break
                    last_session_check = now

                # Listen for metrics with timeout for heartbeat
                try:
                    # Get next message with timeout
                    message = await asyncio.wait_for(
                        subscriber.get_message(),
                        timeout=SSE_HEARTBEAT_SECONDS,
                    )
                    if message and message.get("type") == "metrics":
                        yield {
                            "event": "metrics",
                            "data": json.dumps(message),
                        }
                except asyncio.TimeoutError:
                    # Send heartbeat on timeout
                    yield {
                        "event": "heartbeat",
                        "data": json.dumps({"timestamp": now.isoformat()}),
                    }

        except asyncio.CancelledError:
            logger.debug(f"SSE connection {connection_id} cancelled")
        except Exception as e:
            logger.warning(f"SSE error for {connection_id}: {e}")
        finally:
            # Cleanup
            await _remove_connection(stream_id, connection_id)
            if subscriber:
                await subscriber.close()
            logger.debug(f"SSE connection {connection_id} closed")

    return EventSourceResponse(event_generator())
