"""
Studio Moderation API.

Provides endpoints for stream moderation:
- Bans and timeouts
- Word filters with ReDoS protection
- Moderation logs

Related Issue: #530
"""

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter

from api.audit import AuditAction, log_audit
from api.auth.middleware import require_auth
from api.auth.permissions import Permission, Role, has_permission
from api.common import get_real_ip, get_request_id, require_valid_slug
from api.database import (
    database,
    live_streams,
    stream_bans,
    stream_word_filters,
    moderation_logs,
    stream_moderators,
    users,
)
from api.db_retry import db_execute_with_retry, fetch_one_with_retry, fetch_all_with_retry
from api.live_schemas import (
    BanType,
    FilterAction,
    StreamBanResponse,
    StreamBanListResponse,
    StreamBanCreate,
    WordFilterResponse,
    WordFilterListResponse,
    WordFilterCreate,
    WordFilterUpdate,
    ModerationLogResponse,
    ModerationLogListResponse,
)
from api.pubsub import publish_chat_user_action
from api.studio import require_csrf
from api.websocket_manager import websocket_manager
from config import (
    LIVE_ENABLED,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
)

logger = logging.getLogger(__name__)

# Create router for moderation API
router = APIRouter(prefix="/api/v1/studio/streams", tags=["Studio Moderation"])

# Initialize rate limiter
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)

# ReDoS protection: dangerous patterns to reject
DANGEROUS_REGEX_PATTERNS = [
    r"(a+)+",
    r"(a|a)+",
    r"(a|aa)+",
    r"(.*a){10,}",
    r"([a-zA-Z]+)*",
]

MAX_FILTERS_PER_STREAM = 50


def validate_regex_pattern(pattern: str) -> bool:
    """
    Validate a regex pattern is safe from ReDoS attacks.

    Returns True if safe, False if potentially dangerous.
    """
    # Check length
    if len(pattern) > 100:
        return False

    # Check for known dangerous patterns
    for dangerous in DANGEROUS_REGEX_PATTERNS:
        if dangerous in pattern:
            return False

    # Try to compile with timeout simulation
    # In production, use RE2 library for linear time guarantee
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


async def verify_stream_access(slug: str, user: dict) -> dict:
    """Verify user has access to a stream."""
    require_valid_slug(slug, "stream")

    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    role = Role(user["role"])
    is_owner = stream["owner_id"] == user["id"]
    has_manage_any = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    if not is_owner and not has_manage_any:
        raise HTTPException(status_code=404, detail="Stream not found")

    return dict(stream)


async def verify_stream_moderator(stream_id: int, user: dict, required_permission: str) -> bool:
    """
    Verify user is a moderator with specific permission.

    Stream owners and admins have all permissions.
    """
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

    # Check moderator record
    mod = await fetch_one_with_retry(
        stream_moderators.select().where(
            (stream_moderators.c.stream_id == stream_id)
            & (stream_moderators.c.user_id == user["id"])
        )
    )

    if not mod:
        return False

    perms = mod["permissions"]
    if isinstance(perms, str):
        try:
            perms = json.loads(perms)
        except json.JSONDecodeError:
            return False

    return required_permission in (perms or [])


async def require_moderator_permission(slug: str, user: dict, permission: str) -> dict:
    """Dependency: Verify user has moderator permission."""
    stream = await verify_stream_access(slug, user)

    if not await verify_stream_moderator(stream["id"], user, permission):
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")

    return stream


async def log_moderation_action(
    stream_id: int,
    moderator_id: str,
    action: str,
    target_user_id: Optional[str] = None,
    target_message_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> int:
    """Log a moderation action and return the log ID."""
    insert_query = moderation_logs.insert().values(
        stream_id=stream_id,
        moderator_id=moderator_id,
        action=action,
        target_user_id=target_user_id,
        target_message_id=target_message_id,
        details=json.dumps(details) if details else None,
        created_at=datetime.now(timezone.utc),
    )
    result = await db_execute_with_retry(insert_query)
    return result.lastrowid


def is_ban_active(ban: dict) -> bool:
    """Check if a ban is currently active."""
    if ban["unbanned_at"]:
        return False
    if ban["expires_at"]:
        expires = ban["expires_at"]
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > datetime.now(timezone.utc)
    return True  # Permanent ban with no unban


# =============================================================================
# Bans Endpoints
# =============================================================================


@router.get(
    "/{slug}/bans",
    response_model=StreamBanListResponse,
    summary="List stream bans",
)
@limiter.limit("60/minute")
async def list_bans(
    request: Request,
    slug: str,
    active_only: bool = True,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(require_auth),
):
    """List bans for a stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Build query
    query = (
        sa.select(
            stream_bans,
            users.c.username.label("username"),
        )
        .select_from(
            stream_bans.outerjoin(users, stream_bans.c.user_id == users.c.id)
        )
        .where(stream_bans.c.stream_id == stream["id"])
        .order_by(stream_bans.c.created_at.desc())
        .limit(min(limit, 100))
        .offset(offset)
    )

    bans = await fetch_all_with_retry(query)

    # Get banned_by usernames
    ban_responses = []
    for ban in bans:
        is_active = is_ban_active(dict(ban))

        if active_only and not is_active:
            continue

        banned_by_username = None
        if ban["banned_by"]:
            banner = await fetch_one_with_retry(
                users.select().where(users.c.id == ban["banned_by"])
            )
            if banner:
                banned_by_username = banner["username"]

        ban_responses.append(
            StreamBanResponse(
                id=ban["id"],
                stream_id=ban["stream_id"],
                user_id=ban["user_id"],
                username=ban["username"],
                ban_type=BanType(ban["ban_type"]),
                duration_seconds=ban["duration_seconds"],
                reason=ban["reason"],
                banned_by_id=ban["banned_by"],
                banned_by_username=banned_by_username,
                created_at=ban["created_at"],
                expires_at=ban["expires_at"],
                unbanned_at=ban["unbanned_at"],
                is_active=is_active,
            )
        )

    # Get total count
    count_query = (
        sa.select(sa.func.count())
        .select_from(stream_bans)
        .where(stream_bans.c.stream_id == stream["id"])
    )
    total = await fetch_one_with_retry(count_query)
    total_count = total[0] if total else 0

    return StreamBanListResponse(
        bans=ban_responses,
        total=total_count,
        has_more=offset + len(ban_responses) < total_count,
    )


@router.post(
    "/{slug}/bans",
    response_model=StreamBanResponse,
    summary="Ban or timeout a user",
)
@limiter.limit("30/minute")
async def create_ban(
    request: Request,
    slug: str,
    ban_data: StreamBanCreate,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """
    Ban or timeout a user from stream chat.

    Requires 'ban' permission for permanent bans, 'timeout' for timeouts.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    required_perm = "ban" if ban_data.ban_type == BanType.PERMANENT else "timeout"
    stream = await require_moderator_permission(slug, user, required_perm)

    # Verify target user exists
    target_user = await fetch_one_with_retry(
        users.select().where(users.c.id == ban_data.user_id)
    )
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Don't allow banning stream owner or self
    if ban_data.user_id == stream["owner_id"]:
        raise HTTPException(status_code=400, detail="Cannot ban the stream owner")
    if ban_data.user_id == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot ban yourself")

    # Calculate expiration for timeouts
    now = datetime.now(timezone.utc)
    expires_at = None
    if ban_data.ban_type == BanType.TIMEOUT and ban_data.duration_seconds:
        expires_at = now + timedelta(seconds=ban_data.duration_seconds)

    # Insert ban
    insert_query = stream_bans.insert().values(
        stream_id=stream["id"],
        user_id=ban_data.user_id,
        ban_type=ban_data.ban_type.value,
        duration_seconds=ban_data.duration_seconds,
        reason=ban_data.reason,
        banned_by=user["id"],
        created_at=now,
        expires_at=expires_at,
    )
    result = await db_execute_with_retry(insert_query)
    ban_id = result.lastrowid

    # Log moderation action
    await log_moderation_action(
        stream_id=stream["id"],
        moderator_id=user["id"],
        action=ban_data.ban_type.value,
        target_user_id=ban_data.user_id,
        details={
            "duration_seconds": ban_data.duration_seconds,
            "reason": ban_data.reason,
        },
    )

    # Audit log
    audit_action = (
        AuditAction.CHAT_USER_BAN
        if ban_data.ban_type == BanType.PERMANENT
        else AuditAction.CHAT_USER_TIMEOUT
    )
    log_audit(
        action=audit_action,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="stream_ban",
        resource_id=ban_id,
        details={
            "stream_id": stream["id"],
            "stream_slug": slug,
            "target_user_id": ban_data.user_id,
            "target_username": target_user["username"],
            "ban_type": ban_data.ban_type.value,
            "duration_seconds": ban_data.duration_seconds,
        },
        request_id=get_request_id(request),
    )

    # Publish to WebSocket subscribers
    await publish_chat_user_action(
        stream_id=stream["id"],
        action=ban_data.ban_type.value,
        target_user_id=ban_data.user_id,
        target_username=target_user["username"],
        duration_seconds=ban_data.duration_seconds,
        reason=ban_data.reason,
        moderator_username=user.get("username"),
    )

    # Close user's WebSocket connections
    await websocket_manager.close_user_connections(
        stream_id=stream["id"],
        user_id=ban_data.user_id,
        code=4002,
        reason=f"You have been {'banned' if ban_data.ban_type == BanType.PERMANENT else 'timed out'}",
    )

    return StreamBanResponse(
        id=ban_id,
        stream_id=stream["id"],
        user_id=ban_data.user_id,
        username=target_user["username"],
        ban_type=ban_data.ban_type,
        duration_seconds=ban_data.duration_seconds,
        reason=ban_data.reason,
        banned_by_id=user["id"],
        banned_by_username=user.get("username"),
        created_at=now,
        expires_at=expires_at,
        unbanned_at=None,
        is_active=True,
    )


@router.delete(
    "/{slug}/bans/{ban_id}",
    summary="Unban a user",
)
@limiter.limit("30/minute")
async def unban_user(
    request: Request,
    slug: str,
    ban_id: int,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """Unban a user (lift an active ban)."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await require_moderator_permission(slug, user, "ban")

    # Find the ban
    ban = await fetch_one_with_retry(
        stream_bans.select().where(
            (stream_bans.c.id == ban_id)
            & (stream_bans.c.stream_id == stream["id"])
        )
    )

    if not ban:
        raise HTTPException(status_code=404, detail="Ban not found")

    if not is_ban_active(dict(ban)):
        raise HTTPException(status_code=400, detail="Ban is not active")

    # Update ban
    now = datetime.now(timezone.utc)
    await db_execute_with_retry(
        stream_bans.update()
        .where(stream_bans.c.id == ban_id)
        .values(unbanned_at=now, unbanned_by=user["id"])
    )

    # Get target username
    target_user = await fetch_one_with_retry(
        users.select().where(users.c.id == ban["user_id"])
    )

    # Log moderation action
    await log_moderation_action(
        stream_id=stream["id"],
        moderator_id=user["id"],
        action="unban",
        target_user_id=ban["user_id"],
    )

    # Audit log
    log_audit(
        action=AuditAction.CHAT_USER_UNBAN,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="stream_ban",
        resource_id=ban_id,
        details={
            "stream_id": stream["id"],
            "stream_slug": slug,
            "target_user_id": ban["user_id"],
            "target_username": target_user["username"] if target_user else None,
        },
        request_id=get_request_id(request),
    )

    # Publish to WebSocket
    await publish_chat_user_action(
        stream_id=stream["id"],
        action="unban",
        target_user_id=ban["user_id"],
        target_username=target_user["username"] if target_user else None,
        moderator_username=user.get("username"),
    )

    return {"unbanned": True, "ban_id": ban_id}


@router.get(
    "/{slug}/bans/check/{target_user_id}",
    summary="Check if user is banned",
)
@limiter.limit("60/minute")
async def check_user_ban(
    request: Request,
    slug: str,
    target_user_id: str,
    user: dict = Depends(require_auth),
):
    """Check if a specific user is currently banned from the stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Find active ban
    bans = await fetch_all_with_retry(
        stream_bans.select()
        .where(
            (stream_bans.c.stream_id == stream["id"])
            & (stream_bans.c.user_id == target_user_id)
        )
        .order_by(stream_bans.c.created_at.desc())
        .limit(1)
    )

    if not bans:
        return {"banned": False, "ban": None}

    ban = dict(bans[0])
    is_active = is_ban_active(ban)

    if not is_active:
        return {"banned": False, "ban": None}

    return {
        "banned": True,
        "ban": {
            "id": ban["id"],
            "ban_type": ban["ban_type"],
            "reason": ban["reason"],
            "expires_at": ban["expires_at"].isoformat() if ban["expires_at"] else None,
        },
    }


# =============================================================================
# Word Filters Endpoints
# =============================================================================


@router.get(
    "/{slug}/filters",
    response_model=WordFilterListResponse,
    summary="List word filters",
)
@limiter.limit("60/minute")
async def list_filters(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
):
    """List word filters for a stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    filters = await fetch_all_with_retry(
        sa.select(
            stream_word_filters,
            users.c.username.label("created_by_username"),
        )
        .select_from(
            stream_word_filters.outerjoin(
                users, stream_word_filters.c.created_by == users.c.id
            )
        )
        .where(stream_word_filters.c.stream_id == stream["id"])
        .order_by(stream_word_filters.c.created_at.desc())
    )

    return WordFilterListResponse(
        filters=[
            WordFilterResponse(
                id=f["id"],
                stream_id=f["stream_id"],
                pattern=f["pattern"],
                is_regex=f["is_regex"],
                action=FilterAction(f["action"]),
                timeout_seconds=f["timeout_seconds"],
                created_at=f["created_at"],
                created_by_id=f["created_by"],
                created_by_username=f["created_by_username"],
            )
            for f in filters
        ],
        total=len(filters),
    )


@router.post(
    "/{slug}/filters",
    response_model=WordFilterResponse,
    summary="Create a word filter",
)
@limiter.limit("30/minute")
async def create_filter(
    request: Request,
    slug: str,
    filter_data: WordFilterCreate,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """
    Create a word filter for automated moderation.

    Requires stream owner permission.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Only owner can manage filters
    role = Role(user["role"])
    is_owner = stream["owner_id"] == user["id"]
    is_admin = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Only the stream owner can manage filters")

    # Check filter count limit
    count = await fetch_one_with_retry(
        sa.select(sa.func.count())
        .select_from(stream_word_filters)
        .where(stream_word_filters.c.stream_id == stream["id"])
    )
    if count and count[0] >= MAX_FILTERS_PER_STREAM:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_FILTERS_PER_STREAM} filters per stream",
        )

    # Validate regex if needed
    if filter_data.is_regex and not validate_regex_pattern(filter_data.pattern):
        raise HTTPException(
            status_code=400,
            detail="Invalid or potentially dangerous regex pattern",
        )

    # Insert filter
    now = datetime.now(timezone.utc)
    insert_query = stream_word_filters.insert().values(
        stream_id=stream["id"],
        pattern=filter_data.pattern,
        is_regex=filter_data.is_regex,
        action=filter_data.action.value,
        timeout_seconds=filter_data.timeout_seconds,
        created_at=now,
        created_by=user["id"],
    )
    result = await db_execute_with_retry(insert_query)
    filter_id = result.lastrowid

    # Log moderation action
    await log_moderation_action(
        stream_id=stream["id"],
        moderator_id=user["id"],
        action="add_filter",
        details={
            "filter_id": filter_id,
            "pattern": filter_data.pattern,
            "is_regex": filter_data.is_regex,
            "action": filter_data.action.value,
        },
    )

    # Audit log
    log_audit(
        action=AuditAction.STREAM_FILTER_ADD,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="word_filter",
        resource_id=filter_id,
        details={
            "stream_id": stream["id"],
            "stream_slug": slug,
            "pattern": filter_data.pattern,
            "is_regex": filter_data.is_regex,
        },
        request_id=get_request_id(request),
    )

    return WordFilterResponse(
        id=filter_id,
        stream_id=stream["id"],
        pattern=filter_data.pattern,
        is_regex=filter_data.is_regex,
        action=filter_data.action,
        timeout_seconds=filter_data.timeout_seconds,
        created_at=now,
        created_by_id=user["id"],
        created_by_username=user.get("username"),
    )


@router.delete(
    "/{slug}/filters/{filter_id}",
    summary="Delete a word filter",
)
@limiter.limit("30/minute")
async def delete_filter(
    request: Request,
    slug: str,
    filter_id: int,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
):
    """Delete a word filter."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Only owner can manage filters
    role = Role(user["role"])
    is_owner = stream["owner_id"] == user["id"]
    is_admin = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Only the stream owner can manage filters")

    # Find the filter
    f = await fetch_one_with_retry(
        stream_word_filters.select().where(
            (stream_word_filters.c.id == filter_id)
            & (stream_word_filters.c.stream_id == stream["id"])
        )
    )

    if not f:
        raise HTTPException(status_code=404, detail="Filter not found")

    # Delete
    await db_execute_with_retry(
        stream_word_filters.delete().where(stream_word_filters.c.id == filter_id)
    )

    # Log moderation action
    await log_moderation_action(
        stream_id=stream["id"],
        moderator_id=user["id"],
        action="remove_filter",
        details={"filter_id": filter_id, "pattern": f["pattern"]},
    )

    # Audit log
    log_audit(
        action=AuditAction.STREAM_FILTER_REMOVE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="word_filter",
        resource_id=filter_id,
        details={
            "stream_id": stream["id"],
            "stream_slug": slug,
            "pattern": f["pattern"],
        },
        request_id=get_request_id(request),
    )

    return {"deleted": True, "filter_id": filter_id}


# =============================================================================
# Moderation Logs Endpoints
# =============================================================================


@router.get(
    "/{slug}/moderation-logs",
    response_model=ModerationLogListResponse,
    summary="List moderation logs",
)
@limiter.limit("60/minute")
async def list_moderation_logs(
    request: Request,
    slug: str,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(require_auth),
):
    """List moderation logs for a stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is not enabled")

    stream = await verify_stream_access(slug, user)

    # Query logs with usernames
    logs = await fetch_all_with_retry(
        sa.select(
            moderation_logs,
            users.c.username.label("moderator_username"),
        )
        .select_from(
            moderation_logs.outerjoin(
                users, moderation_logs.c.moderator_id == users.c.id
            )
        )
        .where(moderation_logs.c.stream_id == stream["id"])
        .order_by(moderation_logs.c.created_at.desc())
        .limit(min(limit, 100))
        .offset(offset)
    )

    # Get target usernames
    log_responses = []
    for log in logs:
        target_username = None
        if log["target_user_id"]:
            target = await fetch_one_with_retry(
                users.select().where(users.c.id == log["target_user_id"])
            )
            if target:
                target_username = target["username"]

        details = None
        if log["details"]:
            try:
                details = json.loads(log["details"])
            except json.JSONDecodeError:
                details = None

        log_responses.append(
            ModerationLogResponse(
                id=log["id"],
                stream_id=log["stream_id"],
                moderator_id=log["moderator_id"],
                moderator_username=log["moderator_username"],
                action=log["action"],
                target_user_id=log["target_user_id"],
                target_username=target_username,
                target_message_id=log["target_message_id"],
                details=details,
                created_at=log["created_at"],
            )
        )

    # Get total count
    count = await fetch_one_with_retry(
        sa.select(sa.func.count())
        .select_from(moderation_logs)
        .where(moderation_logs.c.stream_id == stream["id"])
    )
    total_count = count[0] if count else 0

    return ModerationLogListResponse(
        logs=log_responses,
        total=total_count,
        has_more=offset + len(log_responses) < total_count,
    )
