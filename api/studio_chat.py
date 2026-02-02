"""
Studio Chat REST API.

Provides REST endpoints for chat functionality:
- List and send chat messages
- Delete messages (moderation)
- Manage chat settings
- Manage stream moderators

Related Issue: #530
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import bleach
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter

from api.audit import AuditAction, log_audit
from api.auth.middleware import require_auth
from api.auth.permissions import Permission, Role, has_permission
from api.common import (
    calculate_stream_offset_ms,
    get_real_ip,
    get_request_id,
    verify_stream_access,
)
from api.database import database, live_streams, chat_messages, stream_moderators, users
from api.db_retry import db_execute_with_retry, fetch_one_with_retry, fetch_all_with_retry
from api.live_schemas import (
    ChatMessageResponse,
    ChatMessageListResponse,
    ChatMessageSend,
    ChatSettingsResponse,
    ChatSettingsUpdate,
    StreamModeratorResponse,
    StreamModeratorListResponse,
    StreamModeratorAdd,
    StreamModeratorUpdate,
)
from api.pubsub import (
    publish_chat_message,
    publish_chat_message_deleted,
    publish_chat_settings_updated,
)
from api.studio import require_csrf
from config import (
    LIVE_ENABLED,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
)

logger = logging.getLogger(__name__)

# Create router for studio chat API
router = APIRouter(prefix="/api/v1/studio/streams", tags=["Studio Chat"])

# Initialize rate limiter
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)

# XSS Prevention: Strip ALL HTML tags from chat messages
ALLOWED_HTML_TAGS: list[str] = []  # No HTML allowed


def sanitize_message(content: str) -> str:
    """
    Sanitize chat message content to prevent XSS.

    Strips all HTML tags and escapes special characters.
    """
    return bleach.clean(content, tags=ALLOWED_HTML_TAGS, strip=True)


async def verify_stream_moderator(stream_id: int, user: dict) -> bool:
    """
    Verify user is a moderator for the specific stream.

    Checks:
    1. User is the stream owner
    2. User has global admin permissions
    3. User is listed in stream_moderators

    Returns True if user has moderator permissions.
    """
    # Check if user is admin
    role = Role(user["role"])
    if has_permission(role, Permission.LIVE_STREAM_MANAGE):
        return True

    # Check if user is stream owner
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )
    if stream and stream["owner_id"] == user["id"]:
        return True

    # Check if user is a moderator for this stream
    mod = await fetch_one_with_retry(
        stream_moderators.select().where(
            (stream_moderators.c.stream_id == stream_id)
            & (stream_moderators.c.user_id == user["id"])
        )
    )
    return mod is not None


async def get_moderator_permissions(stream_id: int, user: dict) -> list[str]:
    """
    Get the specific permissions for a moderator on a stream.

    Stream owners and admins have all permissions.
    """
    role = Role(user["role"])
    all_perms = ["delete_message", "timeout", "ban"]

    # Admins and owners have all permissions
    if has_permission(role, Permission.LIVE_STREAM_MANAGE):
        return all_perms

    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )
    if stream and stream["owner_id"] == user["id"]:
        return all_perms

    # Get moderator record for specific permissions
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


async def require_stream_moderator(
    slug: str,
    user: dict,
    required_permission: Optional[str] = None,
) -> dict:
    """
    Dependency: Verify user is a moderator for the stream with required permission.

    Args:
        slug: Stream slug
        user: Current user
        required_permission: Optional specific permission to check

    Returns:
        Stream dict if authorized

    Raises:
        HTTPException: 403 if not a moderator or missing permission
    """
    stream = await verify_stream_access(slug, user)

    if not await verify_stream_moderator(stream["id"], user):
        raise HTTPException(status_code=403, detail="Not a moderator for this stream")

    if required_permission:
        perms = await get_moderator_permissions(stream["id"], user)
        if required_permission not in perms:
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permission: {required_permission}",
            )

    return stream


# =============================================================================
# Chat Messages Endpoints
# =============================================================================


@router.get(
    "/{slug}/chat/messages",
    response_model=ChatMessageListResponse,
    summary="List chat messages",
)
@limiter.limit("60/minute")
async def list_chat_messages(
    request: Request,
    slug: str,
    before_id: Optional[int] = None,
    limit: int = 50,
    user: dict = Depends(require_auth),
):
    """
    List chat messages for a stream.

    Messages are returned in reverse chronological order (newest first).
    Use `before_id` for pagination.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Build query - fetch limit+1 to determine has_more efficiently (Issue #546)
    effective_limit = min(limit, 100)
    query = (
        sa.select(
            chat_messages,
            users.c.username.label("username"),
        )
        .select_from(
            chat_messages.outerjoin(users, chat_messages.c.user_id == users.c.id)
        )
        .where(chat_messages.c.stream_id == stream["id"])
        .where(chat_messages.c.deleted_at.is_(None))  # Exclude deleted messages
        .order_by(chat_messages.c.id.desc())
        .limit(effective_limit + 1)  # Fetch one extra to detect has_more
    )

    if before_id:
        query = query.where(chat_messages.c.id < before_id)

    messages = await fetch_all_with_retry(query)

    # Determine has_more by checking if we got more than the limit
    has_more = len(messages) > effective_limit
    if has_more:
        messages = messages[:effective_limit]  # Return only requested amount

    return ChatMessageListResponse(
        messages=[
            ChatMessageResponse(
                id=msg["id"],
                stream_id=msg["stream_id"],
                user_id=msg["user_id"],
                username=msg["username"],
                content=msg["content"],
                stream_offset_ms=msg["stream_offset_ms"],
                created_at=msg["created_at"],
            )
            for msg in messages
        ],
        # Note: 'total' is count of returned messages, NOT total in database.
        # This is intentional for cursor-based pagination - use 'has_more' for pagination.
        # (Issue #546 - removed expensive COUNT query for performance)
        total=len(messages),
        has_more=has_more,
        before_id=messages[-1]["id"] if messages else None,
    )


@router.post(
    "/{slug}/chat/messages",
    response_model=ChatMessageResponse,
    summary="Send a chat message (REST fallback)",
)
@limiter.limit("60/minute")
async def send_chat_message(
    request: Request,
    slug: str,
    message: ChatMessageSend,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """
    Send a chat message via REST API.

    This is a fallback for when WebSocket is unavailable.
    Prefer WebSocket for real-time chat.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Check if chat is enabled
    if not stream.get("chat_enabled", True):
        raise HTTPException(status_code=403, detail="Chat is disabled for this stream")

    # Sanitize message content (XSS prevention)
    sanitized_content = sanitize_message(message.content)
    if not sanitized_content.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty after sanitization")

    # Get current time for timestamp and offset calculation
    now = datetime.now(timezone.utc)

    # Calculate stream offset if stream is live (for VOD sync)
    # Wrapped in try/except to ensure chat messages are saved even if offset calculation fails
    stream_offset_ms = None
    if stream["status"] == "live":
        try:
            stream_offset_ms = calculate_stream_offset_ms(stream["started_at"], now)
        except (TypeError, ValueError, OverflowError) as e:
            logger.warning(f"Failed to calculate stream offset for stream {stream['id']}: {e}")
            stream_offset_ms = None

    # Insert message (use RETURNING for PostgreSQL compatibility)
    insert_query = (
        chat_messages.insert()
        .values(
            stream_id=stream["id"],
            user_id=user["id"],
            content=sanitized_content,
            stream_offset_ms=stream_offset_ms,
            created_at=now,
        )
        .returning(chat_messages.c.id)
    )
    result = await fetch_one_with_retry(insert_query)
    message_id = result["id"]

    # Fetch the inserted message with username
    msg = await fetch_one_with_retry(
        sa.select(
            chat_messages,
            users.c.username.label("username"),
        )
        .select_from(
            chat_messages.outerjoin(users, chat_messages.c.user_id == users.c.id)
        )
        .where(chat_messages.c.id == message_id)
    )

    # Check if user is a moderator for this stream
    mod_perms = await get_moderator_permissions(stream["id"], user)
    is_moderator = bool(mod_perms)

    # Publish to Redis for WebSocket subscribers
    await publish_chat_message(
        stream_id=stream["id"],
        message_id=message_id,
        user_id=user["id"],
        username=user.get("username", ""),
        display_name=user.get("display_name"),
        content=sanitized_content,
        timestamp=now.isoformat(),
        user_role=user.get("role"),
        is_moderator=is_moderator,
        is_broadcaster=(stream["owner_id"] == user["id"]),
    )

    return ChatMessageResponse(
        id=msg["id"],
        stream_id=msg["stream_id"],
        user_id=msg["user_id"],
        username=msg["username"],
        content=msg["content"],
        stream_offset_ms=msg["stream_offset_ms"],
        created_at=msg["created_at"],
    )


@router.delete(
    "/{slug}/chat/messages/{message_id}",
    summary="Delete a chat message",
)
@limiter.limit("120/minute")
async def delete_chat_message(
    request: Request,
    slug: str,
    message_id: int,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """
    Delete (soft-delete) a chat message.

    Requires moderator permission: delete_message
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    # Verify user is moderator with delete_message permission
    stream = await require_stream_moderator(slug, user, required_permission="delete_message")

    # Find the message
    msg = await fetch_one_with_retry(
        chat_messages.select().where(
            (chat_messages.c.id == message_id)
            & (chat_messages.c.stream_id == stream["id"])
            & (chat_messages.c.deleted_at.is_(None))
        )
    )

    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

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
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="chat_message",
        resource_id=message_id,
        details={
            "stream_id": stream["id"],
            "stream_slug": slug,
            "deleted_by": user["id"],
            "original_user_id": msg["user_id"],
        },
        request_id=get_request_id(request),
    )

    # Publish deletion to WebSocket subscribers
    await publish_chat_message_deleted(
        stream_id=stream["id"],
        message_id=message_id,
        deleted_by_id=user["id"],
        deleted_by_username=user.get("username", ""),
    )

    return {"deleted": True, "message_id": message_id}


# =============================================================================
# Chat Settings Endpoints
# =============================================================================


@router.get(
    "/{slug}/chat/settings",
    response_model=ChatSettingsResponse,
    summary="Get chat settings",
)
@limiter.limit("60/minute")
async def get_chat_settings(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
):
    """Get chat settings for a stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

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


@router.patch(
    "/{slug}/chat/settings",
    response_model=ChatSettingsResponse,
    summary="Update chat settings",
)
@limiter.limit("30/minute")
async def update_chat_settings(
    request: Request,
    slug: str,
    settings: ChatSettingsUpdate,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """
    Update chat settings for a stream.

    Only the stream owner or admin can update settings.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Only owner or admin can change settings (not moderators)
    role = Role(user["role"])
    is_owner = stream["owner_id"] == user["id"]
    is_admin = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Only the stream owner can update settings")

    # Build update values
    update_values = {}
    if settings.chat_enabled is not None:
        update_values["chat_enabled"] = settings.chat_enabled
    if settings.chat_slow_mode_seconds is not None:
        update_values["chat_slow_mode_seconds"] = settings.chat_slow_mode_seconds
    if settings.chat_subscriber_only is not None:
        update_values["chat_subscriber_only"] = settings.chat_subscriber_only
    if settings.chat_follower_only is not None:
        update_values["chat_follower_only"] = settings.chat_follower_only
    if settings.chat_follower_min_minutes is not None:
        update_values["chat_follower_min_minutes"] = settings.chat_follower_min_minutes
    if settings.chat_emote_only is not None:
        update_values["chat_emote_only"] = settings.chat_emote_only
    if settings.chat_links_allowed is not None:
        update_values["chat_links_allowed"] = settings.chat_links_allowed

    if update_values:
        await db_execute_with_retry(
            live_streams.update()
            .where(live_streams.c.id == stream["id"])
            .values(**update_values)
        )

    # Fetch updated stream
    updated = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream["id"])
    )

    # Audit log
    log_audit(
        action=AuditAction.CHAT_SETTINGS_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="stream",
        resource_id=stream["id"],
        resource_name=slug,
        details={"changes": update_values},
        request_id=get_request_id(request),
    )

    response = ChatSettingsResponse(
        stream_id=updated["id"],
        chat_enabled=updated.get("chat_enabled", True),
        chat_slow_mode_seconds=updated.get("chat_slow_mode_seconds", 0),
        chat_subscriber_only=updated.get("chat_subscriber_only", False),
        chat_follower_only=updated.get("chat_follower_only", False),
        chat_follower_min_minutes=updated.get("chat_follower_min_minutes", 0),
        chat_emote_only=updated.get("chat_emote_only", False),
        chat_links_allowed=updated.get("chat_links_allowed", True),
    )

    # Publish settings update to WebSocket subscribers
    await publish_chat_settings_updated(
        stream_id=stream["id"],
        settings={
            "chat_enabled": response.chat_enabled,
            "chat_slow_mode_seconds": response.chat_slow_mode_seconds,
            "chat_subscriber_only": response.chat_subscriber_only,
            "chat_follower_only": response.chat_follower_only,
            "chat_follower_min_minutes": response.chat_follower_min_minutes,
            "chat_emote_only": response.chat_emote_only,
            "chat_links_allowed": response.chat_links_allowed,
        },
        updated_by_id=user["id"],
        updated_by_username=user.get("username", ""),
    )

    return response


# =============================================================================
# Stream Moderators Endpoints
# =============================================================================


@router.get(
    "/{slug}/moderators",
    response_model=StreamModeratorListResponse,
    summary="List stream moderators",
)
@limiter.limit("60/minute")
async def list_moderators(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
):
    """List moderators for a stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Fetch moderators with user info
    mods = await fetch_all_with_retry(
        sa.select(
            stream_moderators,
            users.c.username.label("username"),
        )
        .select_from(
            stream_moderators.join(users, stream_moderators.c.user_id == users.c.id)
        )
        .where(stream_moderators.c.stream_id == stream["id"])
        .order_by(stream_moderators.c.granted_at.desc())
    )

    # Batch-fetch all granter usernames (avoid N+1 queries)
    granter_ids = {mod["granted_by"] for mod in mods if mod["granted_by"]}
    granter_map: dict[str, str] = {}
    if granter_ids:
        granters = await fetch_all_with_retry(
            users.select().where(users.c.id.in_(granter_ids))
        )
        granter_map = {g["id"]: g["username"] for g in granters}

    # Build response list
    mod_responses = []
    for mod in mods:
        perms = mod["permissions"]
        if isinstance(perms, str):
            try:
                perms = json.loads(perms)
            except json.JSONDecodeError:
                perms = []

        mod_responses.append(
            StreamModeratorResponse(
                id=mod["id"],
                stream_id=mod["stream_id"],
                user_id=mod["user_id"],
                username=mod["username"],
                permissions=perms,
                granted_by_id=mod["granted_by"],
                granted_by_username=granter_map.get(mod["granted_by"]) if mod["granted_by"] else None,
                granted_at=mod["granted_at"],
            )
        )

    return StreamModeratorListResponse(
        moderators=mod_responses,
        total=len(mod_responses),
    )


@router.post(
    "/{slug}/moderators",
    response_model=StreamModeratorResponse,
    summary="Add a stream moderator",
)
@limiter.limit("30/minute")
async def add_moderator(
    request: Request,
    slug: str,
    mod_add: StreamModeratorAdd,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """
    Add a moderator to a stream.

    Only the stream owner or admin can add moderators.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Only owner or admin can add moderators
    role = Role(user["role"])
    is_owner = stream["owner_id"] == user["id"]
    is_admin = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Only the stream owner can add moderators")

    # Verify the target user exists
    target_user = await fetch_one_with_retry(
        users.select().where(users.c.id == mod_add.user_id)
    )
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a moderator
    existing = await fetch_one_with_retry(
        stream_moderators.select().where(
            (stream_moderators.c.stream_id == stream["id"])
            & (stream_moderators.c.user_id == mod_add.user_id)
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="User is already a moderator")

    # Insert moderator
    now = datetime.now(timezone.utc)
    perms_json = json.dumps(mod_add.permissions)

    await db_execute_with_retry(
        stream_moderators.insert().values(
            stream_id=stream["id"],
            user_id=mod_add.user_id,
            permissions=perms_json,
            granted_by=user["id"],
            granted_at=now,
        )
    )

    # Fetch the inserted record
    mod = await fetch_one_with_retry(
        stream_moderators.select().where(
            (stream_moderators.c.stream_id == stream["id"])
            & (stream_moderators.c.user_id == mod_add.user_id)
        )
    )

    # Audit log
    log_audit(
        action=AuditAction.STREAM_MODERATOR_ADD,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="stream_moderator",
        resource_id=mod["id"],
        details={
            "stream_id": stream["id"],
            "stream_slug": slug,
            "user_id": mod_add.user_id,
            "username": target_user["username"],
            "permissions": mod_add.permissions,
        },
        request_id=get_request_id(request),
    )

    return StreamModeratorResponse(
        id=mod["id"],
        stream_id=mod["stream_id"],
        user_id=mod["user_id"],
        username=target_user["username"],
        permissions=mod_add.permissions,
        granted_by_id=user["id"],
        granted_by_username=user.get("username"),
        granted_at=now,
    )


@router.patch(
    "/{slug}/moderators/{moderator_user_id}",
    response_model=StreamModeratorResponse,
    summary="Update moderator permissions",
)
@limiter.limit("30/minute")
async def update_moderator(
    request: Request,
    slug: str,
    moderator_user_id: str,
    mod_update: StreamModeratorUpdate,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """
    Update a moderator's permissions.

    Only the stream owner or admin can update moderator permissions.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Only owner or admin can update moderators
    role = Role(user["role"])
    is_owner = stream["owner_id"] == user["id"]
    is_admin = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Only the stream owner can update moderators")

    # Find the moderator
    mod = await fetch_one_with_retry(
        stream_moderators.select().where(
            (stream_moderators.c.stream_id == stream["id"])
            & (stream_moderators.c.user_id == moderator_user_id)
        )
    )
    if not mod:
        raise HTTPException(status_code=404, detail="Moderator not found")

    # Update permissions
    perms_json = json.dumps(mod_update.permissions)
    await db_execute_with_retry(
        stream_moderators.update()
        .where(stream_moderators.c.id == mod["id"])
        .values(permissions=perms_json)
    )

    # Get moderator's username
    target_user = await fetch_one_with_retry(
        users.select().where(users.c.id == moderator_user_id)
    )

    # Audit log
    log_audit(
        action=AuditAction.STREAM_MODERATOR_ADD,  # Reuse for permission update
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="stream_moderator",
        resource_id=mod["id"],
        details={
            "stream_id": stream["id"],
            "stream_slug": slug,
            "user_id": moderator_user_id,
            "old_permissions": mod["permissions"],
            "new_permissions": mod_update.permissions,
        },
        request_id=get_request_id(request),
    )

    # Get granted_by username
    granted_by_username = None
    if mod["granted_by"]:
        granter = await fetch_one_with_retry(
            users.select().where(users.c.id == mod["granted_by"])
        )
        if granter:
            granted_by_username = granter["username"]

    return StreamModeratorResponse(
        id=mod["id"],
        stream_id=mod["stream_id"],
        user_id=mod["user_id"],
        username=target_user["username"] if target_user else "",
        permissions=mod_update.permissions,
        granted_by_id=mod["granted_by"],
        granted_by_username=granted_by_username,
        granted_at=mod["granted_at"],
    )


@router.delete(
    "/{slug}/moderators/{moderator_user_id}",
    summary="Remove a stream moderator",
)
@limiter.limit("30/minute")
async def remove_moderator(
    request: Request,
    slug: str,
    moderator_user_id: str,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """
    Remove a moderator from a stream.

    Only the stream owner or admin can remove moderators.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Only owner or admin can remove moderators
    role = Role(user["role"])
    is_owner = stream["owner_id"] == user["id"]
    is_admin = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Only the stream owner can remove moderators")

    # Find the moderator
    mod = await fetch_one_with_retry(
        stream_moderators.select().where(
            (stream_moderators.c.stream_id == stream["id"])
            & (stream_moderators.c.user_id == moderator_user_id)
        )
    )
    if not mod:
        raise HTTPException(status_code=404, detail="Moderator not found")

    # Get moderator's username for audit log
    target_user = await fetch_one_with_retry(
        users.select().where(users.c.id == moderator_user_id)
    )

    # Delete the moderator record
    await db_execute_with_retry(
        stream_moderators.delete().where(stream_moderators.c.id == mod["id"])
    )

    # Audit log
    log_audit(
        action=AuditAction.STREAM_MODERATOR_REMOVE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="stream_moderator",
        resource_id=mod["id"],
        details={
            "stream_id": stream["id"],
            "stream_slug": slug,
            "user_id": moderator_user_id,
            "username": target_user["username"] if target_user else None,
        },
        request_id=get_request_id(request),
    )

    return {"removed": True, "user_id": moderator_user_id}
