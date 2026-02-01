"""
Studio Chat WebSocket Endpoint.

Provides real-time chat functionality via WebSocket:
- Connect to stream chat rooms
- Send and receive messages in real-time
- Receive moderation events
- Receive settings updates

Related Issue: #530
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import bleach
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.audit import AuditAction, log_audit
from api.auth.permissions import Permission, Role, has_permission
from api.common import require_valid_slug
from api.database import database, live_streams, chat_messages, stream_moderators, users
from api.db_retry import db_execute_with_retry, fetch_one_with_retry
from api.live_schemas import ChatSettingsResponse, WSMessageType
from api.pubsub import subscribe_to_stream_chat
from api.websocket_manager import (
    ManagedWebSocketConnection,
    ConnectionLimitError,
    OriginValidationError,
    authenticate_websocket,
    get_client_ip,
    websocket_manager,
)
from config import LIVE_ENABLED

logger = logging.getLogger(__name__)

# Create router for chat WebSocket
router = APIRouter(prefix="/api/v1/studio/streams", tags=["Studio Chat WebSocket"])

# XSS Prevention: Strip ALL HTML tags
ALLOWED_HTML_TAGS: list[str] = []


def sanitize_message(content: str) -> str:
    """Sanitize chat message content to prevent XSS."""
    return bleach.clean(content, tags=ALLOWED_HTML_TAGS, strip=True)


# Rate limiting for chat messages (in-memory, per connection)
# More sophisticated rate limiting could use Redis for cross-connection limits
class MessageRateLimiter:
    """Simple in-memory rate limiter for chat messages."""

    def __init__(self, max_messages: int = 60, window_seconds: int = 60):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []

    def is_allowed(self) -> tuple[bool, int]:
        """
        Check if a message is allowed.

        Returns:
            (allowed, retry_after_seconds)
        """
        now = datetime.now(timezone.utc).timestamp()
        window_start = now - self.window_seconds

        # Clean old timestamps
        self._timestamps = [t for t in self._timestamps if t > window_start]

        if len(self._timestamps) >= self.max_messages:
            # Calculate retry_after
            oldest_in_window = min(self._timestamps) if self._timestamps else now
            retry_after = int(oldest_in_window + self.window_seconds - now) + 1
            return False, max(1, retry_after)

        self._timestamps.append(now)
        return True, 0


async def verify_stream_exists(slug: str) -> Optional[dict]:
    """Verify stream exists and return stream data."""
    try:
        require_valid_slug(slug, "stream")
    except Exception:
        return None

    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if stream:
        return dict(stream)
    return None


async def is_user_moderator(stream_id: int, user: dict) -> bool:
    """Check if user is a moderator for the stream."""
    role = Role(user["role"])

    # Admin has all permissions
    if has_permission(role, Permission.LIVE_STREAM_MANAGE):
        return True

    # Check if stream owner
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )
    if stream and stream["owner_id"] == user["id"]:
        return True

    # Check if in moderators table
    mod = await fetch_one_with_retry(
        stream_moderators.select().where(
            (stream_moderators.c.stream_id == stream_id)
            & (stream_moderators.c.user_id == user["id"])
        )
    )
    return mod is not None


async def get_user_permissions(stream_id: int, user: dict) -> list[str]:
    """Get permissions for user on stream."""
    role = Role(user["role"])
    all_perms = ["delete_message", "timeout", "ban"]

    # Admin/owner has all permissions
    if has_permission(role, Permission.LIVE_STREAM_MANAGE):
        return all_perms

    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )
    if stream and stream["owner_id"] == user["id"]:
        return all_perms

    # Get from moderator record
    mod = await fetch_one_with_retry(
        stream_moderators.select().where(
            (stream_moderators.c.stream_id == stream_id)
            & (stream_moderators.c.user_id == user["id"])
        )
    )
    if mod:
        perms = mod["permissions"]
        if isinstance(perms, str):
            try:
                return json.loads(perms)
            except json.JSONDecodeError:
                return []
        return perms or []

    return []


def get_chat_settings_response(stream: dict) -> ChatSettingsResponse:
    """Build ChatSettingsResponse from stream dict."""
    return ChatSettingsResponse(
        stream_id=stream["id"],
        chat_enabled=stream.get("chat_enabled", True),
        chat_slow_mode_seconds=stream.get("chat_slow_mode_seconds", 0),
        chat_subscriber_only=stream.get("chat_subscriber_only", False),
        chat_follower_only=stream.get("chat_follower_only", False),
        chat_follower_min_minutes=stream.get("chat_follower_min_minutes", 0),
        chat_emote_only=stream.get("chat_emote_only", False),
        chat_links_allowed=stream.get("chat_links_allowed", True),
    )


async def handle_chat_message(
    conn: ManagedWebSocketConnection,
    stream: dict,
    user: dict,
    content: str,
    rate_limiter: MessageRateLimiter,
) -> None:
    """Handle incoming chat message from client."""
    # Check chat enabled
    if not stream.get("chat_enabled", True):
        await conn.send_error("chat_disabled", "Chat is disabled for this stream")
        return

    # Rate limit check
    allowed, retry_after = rate_limiter.is_allowed()
    if not allowed:
        await conn.send_error("rate_limited", "Slow down!", retry_after=retry_after)
        return

    # Check slow mode
    slow_mode = stream.get("chat_slow_mode_seconds", 0)
    if slow_mode > 0:
        # Update rate limiter for slow mode
        rate_limiter.max_messages = max(1, 60 // slow_mode)

    # Validate and sanitize content
    if not content or not content.strip():
        await conn.send_error("invalid_message", "Message cannot be empty")
        return

    if len(content) > 500:
        await conn.send_error("message_too_long", "Message exceeds 500 characters")
        return

    sanitized = sanitize_message(content)
    if not sanitized.strip():
        await conn.send_error("invalid_message", "Message cannot be empty after sanitization")
        return

    # Calculate stream offset for VOD sync
    stream_offset_ms = None
    if stream["status"] == "live" and stream.get("started_at"):
        now = datetime.now(timezone.utc)
        started = stream["started_at"]
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        stream_offset_ms = int((now - started).total_seconds() * 1000)

    # Insert message to database (use RETURNING for PostgreSQL compatibility)
    now = datetime.now(timezone.utc)
    insert_query = (
        chat_messages.insert()
        .values(
            stream_id=stream["id"],
            user_id=user["id"],
            content=sanitized,
            stream_offset_ms=stream_offset_ms,
            created_at=now,
        )
        .returning(chat_messages.c.id)
    )
    result = await fetch_one_with_retry(insert_query)
    message_id = result["id"]

    # Broadcast to all connections via WebSocket manager
    # Note: Also published to Redis via REST endpoint, but for WebSocket-sent messages,
    # we broadcast directly here for lower latency
    await websocket_manager.broadcast_to_stream(
        stream["id"],
        {
            "type": WSMessageType.CHAT_MESSAGE,
            "id": message_id,
            "user_id": user["id"],
            "username": user.get("username", ""),
            "content": sanitized,
            "timestamp": now.isoformat(),
        },
    )


async def handle_delete_message(
    conn: ManagedWebSocketConnection,
    stream: dict,
    user: dict,
    message_id: int,
    client_ip: Optional[str],
) -> None:
    """Handle message delete request from client."""
    # Check permissions
    perms = await get_user_permissions(stream["id"], user)
    if "delete_message" not in perms:
        await conn.send_error("forbidden", "You don't have permission to delete messages")
        return

    # Find the message
    msg = await fetch_one_with_retry(
        chat_messages.select().where(
            (chat_messages.c.id == message_id)
            & (chat_messages.c.stream_id == stream["id"])
            & (chat_messages.c.deleted_at.is_(None))
        )
    )

    if not msg:
        await conn.send_error("not_found", "Message not found")
        return

    # Soft delete
    now = datetime.now(timezone.utc)
    await db_execute_with_retry(
        chat_messages.update()
        .where(chat_messages.c.id == message_id)
        .values(deleted_at=now, deleted_by_id=user["id"])
    )

    # Audit log
    log_audit(
        action=AuditAction.CHAT_MESSAGE_DELETE,
        client_ip=client_ip,
        resource_type="chat_message",
        resource_id=message_id,
        details={
            "stream_id": stream["id"],
            "deleted_by": user["id"],
            "deleted_by_username": user.get("username"),
        },
    )

    # Broadcast deletion
    await websocket_manager.broadcast_to_stream(
        stream["id"],
        {
            "type": WSMessageType.MESSAGE_DELETED,
            "message_id": message_id,
            "deleted_by": user.get("username", ""),
        },
    )


async def pubsub_listener(
    stream_id: int,
    conn: ManagedWebSocketConnection,
    stop_event: asyncio.Event,
) -> None:
    """
    Listen to Redis pub/sub for chat events and forward to WebSocket.

    This handles messages sent via REST API or from other server instances.
    """
    subscriber = None
    try:
        subscriber = await subscribe_to_stream_chat(stream_id)
        async for message in subscriber.listen():
            if stop_event.is_set():
                break

            # Forward message to WebSocket
            try:
                await conn.send_json(message)
            except Exception as e:
                logger.debug(f"Error forwarding pubsub message: {e}")
                break
    except asyncio.CancelledError:
        # Expected during normal shutdown (e.g., WebSocket disconnect or server shutdown)
        pass
    except Exception as e:
        logger.warning(f"Pubsub listener error for stream {stream_id}: {e}")
    finally:
        if subscriber:
            await subscriber.close()


@router.websocket("/{slug}/chat")
async def chat_websocket(websocket: WebSocket, slug: str):
    """
    WebSocket endpoint for stream chat.

    Protocol:
    - Connect with session cookie for authentication
    - Server sends 'connected' message with user info and permissions
    - Client sends 'message' type to chat
    - Client sends 'delete' type to delete messages (requires permission)
    - Server broadcasts chat messages and moderation events
    - Server sends 'ping', client responds with 'pong'
    """
    if not LIVE_ENABLED:
        await websocket.close(code=1008, reason="Live streaming is not enabled")
        return

    # Verify stream exists
    stream = await verify_stream_exists(slug)
    if not stream:
        await websocket.close(code=1008, reason="Stream not found")
        return

    # Get client info for logging
    client_ip = get_client_ip(websocket)
    user_agent = websocket.headers.get("user-agent")

    # Authenticate user
    user = await authenticate_websocket(websocket, stream["id"])
    if not user:
        log_audit(
            AuditAction.WEBSOCKET_AUTH_FAILURE,
            client_ip=client_ip,
            user_agent=user_agent,
            resource_type="live_stream",
            resource_id=stream["id"],
            details={"reason": "no_session", "stream_slug": slug},
            success=False,
        )
        await websocket.close(code=4001, reason="Authentication required")
        return

    # Get session token for revalidation
    session_token = websocket.cookies.get("vlog_session", "")

    # Accept the WebSocket connection first
    await websocket.accept()

    try:
        # Register connection with manager
        conn_info = await websocket_manager.register_connection(
            websocket=websocket,
            stream_id=stream["id"],
            user=user,
            client_ip=client_ip,
            user_agent=user_agent,
        )
    except OriginValidationError as e:
        await websocket.send_json({
            "type": "error",
            "code": "origin_invalid",
            "message": str(e),
        })
        await websocket.close(code=4003, reason="Origin validation failed")
        return
    except ConnectionLimitError as e:
        await websocket.send_json({
            "type": "error",
            "code": f"limit_{e.limit_type}",
            "message": str(e),
        })
        await websocket.close(code=4029, reason="Connection limit exceeded")
        return
    except Exception as e:
        logger.error(f"Error registering WebSocket connection: {e}")
        await websocket.close(code=1011, reason="Internal error")
        return

    # Create rate limiter for this connection
    rate_limiter = MessageRateLimiter(max_messages=60, window_seconds=60)

    # Check moderator status
    is_moderator = await is_user_moderator(stream["id"], user)
    is_owner = stream["owner_id"] == user["id"]

    # Create managed connection context
    managed_conn = ManagedWebSocketConnection(
        manager=websocket_manager,
        conn_info=conn_info,
        session_token=session_token,
    )

    # Create stop event for pubsub listener
    stop_event = asyncio.Event()
    pubsub_task = None

    try:
        async with managed_conn as conn:
            # Send connected message
            await conn.send_json({
                "type": WSMessageType.CONNECTED,
                "user_id": user["id"],
                "username": user.get("username", ""),
                "is_moderator": is_moderator,
                "is_owner": is_owner,
                "settings": get_chat_settings_response(stream).model_dump(),
            })

            # Start pubsub listener in background
            pubsub_task = asyncio.create_task(
                pubsub_listener(stream["id"], conn, stop_event)
            )

            # Message handling loop
            async for message in conn.receive_messages():
                msg_type = message.get("type")

                if msg_type == "message":
                    content = message.get("content", "")
                    await handle_chat_message(conn, stream, user, content, rate_limiter)

                elif msg_type == "delete":
                    message_id = message.get("message_id")
                    if message_id:
                        await handle_delete_message(
                            conn, stream, user, message_id, client_ip
                        )
                    else:
                        await conn.send_error("invalid_request", "message_id required")

                elif msg_type == "pong":
                    # Handled internally by ManagedWebSocketConnection
                    pass

                else:
                    await conn.send_error("unknown_type", f"Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        logger.debug(f"WebSocket disconnected: {conn_info.connection_id}")
    except Exception as e:
        logger.error(f"WebSocket error for {conn_info.connection_id}: {e}", exc_info=True)
    finally:
        # Stop pubsub listener
        stop_event.set()
        if pubsub_task:
            pubsub_task.cancel()
            try:
                await pubsub_task
            except asyncio.CancelledError:
                # Expected when cancelling the pubsub task during cleanup
                pass
