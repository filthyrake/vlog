"""
Studio Moderation API.

Provides endpoints for stream moderation:
- Bans and timeouts
- Word filters with ReDoS protection
- Moderation logs

Related Issue: #530
"""

import atexit
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from multiprocessing import Process, Queue
from queue import Empty
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter

from api.audit import AuditAction, log_audit
from api.auth.middleware import require_auth
from api.auth.permissions import Permission, Role, has_permission
from api.common import ensure_utc, get_real_ip, get_request_id, verify_stream_access
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

MAX_FILTERS_PER_STREAM = 50

# Timeout in seconds for regex test execution
# 0.5s balances security (detect slow patterns) with UX (don't frustrate legitimate users)
REGEX_TEST_TIMEOUT_SECONDS = 0.5

# Track active regex test processes for cleanup
_active_regex_processes: list[Process] = []


def _cleanup_regex_processes() -> None:
    """Terminate any lingering regex test processes on shutdown."""
    for proc in _active_regex_processes:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=0.1)
    _active_regex_processes.clear()


# Register cleanup handler for graceful shutdown
atexit.register(_cleanup_regex_processes)


def _has_nested_quantifiers(pattern: str) -> bool:
    """Detect nested quantified groups like (a+)+ or (a*)* that cause exponential backtracking."""
    nested_quantifier_pattern = r'\([^)]*[+*][^)]*\)[+*?]|\([^)]*[+*][^)]*\)\{[0-9,]+\}'
    return bool(re.search(nested_quantifier_pattern, pattern))


def _has_overlapping_alternations(pattern: str) -> bool:
    """Detect alternations where branches can match the same input, like (a|aa)+."""
    overlapping_alt = r'\([^)]*\|[^)]*\)[+*]'
    if not re.search(overlapping_alt, pattern):
        return False

    alt_match = re.search(r'\(([^|)]+)\|([^)]+)\)[+*]', pattern)
    if alt_match:
        branch1, branch2 = alt_match.group(1), alt_match.group(2)
        # If one branch is prefix of another or they share starting character
        if branch1.startswith(branch2) or branch2.startswith(branch1):
            return True
        if branch1 and branch2 and branch1[0] == branch2[0]:
            return True
    return False


def _has_greedy_repetition(pattern: str) -> bool:
    """Detect .* or .+ followed by specific char, repeated: (.*a)+"""
    greedy_specific = r'\(\.\*[^)]+\)[+*]|\(\.\+[^)]+\)[+*]'
    return bool(re.search(greedy_specific, pattern))


def _has_excessive_nesting(pattern: str, max_depth: int = 3) -> bool:
    """Check for deeply nested groups that increase backtracking complexity."""
    depth = 0
    found_max_depth = 0
    for char in pattern:
        if char == '(':
            depth += 1
            found_max_depth = max(found_max_depth, depth)
        elif char == ')':
            depth -= 1
    return found_max_depth > max_depth


def _has_dangerous_regex_structure(pattern: str) -> bool:
    """
    Detect structurally dangerous regex patterns that could cause catastrophic backtracking.

    Returns True if any dangerous pattern is detected.
    """
    return (
        _has_nested_quantifiers(pattern) or
        _has_overlapping_alternations(pattern) or
        _has_greedy_repetition(pattern) or
        _has_excessive_nesting(pattern)
    )


def _regex_match_worker(pattern: str, test_input: str, result_queue: Queue) -> None:
    """Worker function that runs in a separate process to test regex matching."""
    try:
        compiled = re.compile(pattern)
        compiled.search(test_input)
        result_queue.put(True)
    except Exception:
        result_queue.put(False)


def _test_regex_with_timeout(pattern: str, test_input: str, timeout: float) -> bool:
    """
    Test a regex pattern against input with a hard timeout using process isolation.

    Unlike threads, processes can be forcibly terminated, ensuring the regex
    cannot consume CPU beyond the timeout period.

    Returns True if matching completes within timeout, False otherwise.
    """
    result_queue: Queue = Queue()
    proc = Process(target=_regex_match_worker, args=(pattern, test_input, result_queue))

    try:
        _active_regex_processes.append(proc)
        proc.start()
        proc.join(timeout=timeout)

        if proc.is_alive():
            # Timeout exceeded - forcibly terminate the process
            proc.terminate()
            proc.join(timeout=0.1)  # Brief wait for cleanup
            if proc.is_alive():
                proc.kill()  # Force kill if terminate didn't work
                proc.join(timeout=0.1)
            logger.warning(
                "Regex pattern timed out and was terminated",
                extra={"pattern": pattern[:50], "timeout": timeout}
            )
            return False

        # Process completed - get result from queue
        # Note: Queue.empty() is unreliable across processes, use get() with timeout instead
        try:
            return result_queue.get(timeout=0.1)
        except Empty:
            # No result in queue - worker failed silently
            return False

    except Exception as e:
        logger.error(f"Regex test process error: {e}")
        return False
    finally:
        # Clean up process reference
        if proc in _active_regex_processes:
            _active_regex_processes.remove(proc)
        # Ensure process is cleaned up
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=0.1)
        # Close queue to avoid resource leaks
        result_queue.close()
        result_queue.join_thread()


def validate_regex_pattern(pattern: str) -> bool:
    """
    Validate a regex pattern is safe from ReDoS attacks.

    Uses multiple layers of protection:
    1. Length limit
    2. Structural analysis for known dangerous patterns
    3. Timeout-limited test execution against adversarial input

    Returns True if safe, False if potentially dangerous.
    """
    # Check length - limit complexity surface
    if len(pattern) > 100:
        return False

    # Structural analysis for known dangerous patterns
    if _has_dangerous_regex_structure(pattern):
        return False

    # Try to compile
    try:
        re.compile(pattern)
    except re.error:
        return False

    # Test against adversarial input that triggers backtracking
    # This string is designed to maximize backtracking for vulnerable patterns
    adversarial_input = "a" * 30 + "!"

    if not _test_regex_with_timeout(pattern, adversarial_input, REGEX_TEST_TIMEOUT_SECONDS):
        return False

    return True


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
    # Use RETURNING for PostgreSQL compatibility
    insert_query = (
        moderation_logs.insert()
        .values(
            stream_id=stream_id,
            moderator_id=moderator_id,
            action=action,
            target_user_id=target_user_id,
            target_message_id=target_message_id,
            details=json.dumps(details) if details else None,
            created_at=datetime.now(timezone.utc),
        )
        .returning(moderation_logs.c.id)
    )
    result = await fetch_one_with_retry(insert_query)
    return result["id"]


def is_ban_active(ban: dict) -> bool:
    """Check if a ban is currently active."""
    if ban["unbanned_at"]:
        return False
    if ban["expires_at"]:
        expires = ensure_utc(ban["expires_at"])
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

    # Build query with JOINs for both banned user and moderator who banned
    banned_by_users = users.alias("banned_by_users")
    query = (
        sa.select(
            stream_bans,
            users.c.username.label("username"),
            banned_by_users.c.username.label("banned_by_username"),
        )
        .select_from(
            stream_bans
            .outerjoin(users, stream_bans.c.user_id == users.c.id)
            .outerjoin(banned_by_users, stream_bans.c.banned_by == banned_by_users.c.id)
        )
        .where(stream_bans.c.stream_id == stream["id"])
        .order_by(stream_bans.c.created_at.desc())
        .limit(min(limit, 100))
        .offset(offset)
    )

    bans = await fetch_all_with_retry(query)

    # Build response objects
    ban_responses = []
    for ban in bans:
        is_active = is_ban_active(dict(ban))

        if active_only and not is_active:
            continue

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
                banned_by_username=ban["banned_by_username"],
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

    # Insert ban (use RETURNING for PostgreSQL compatibility)
    insert_query = (
        stream_bans.insert()
        .values(
            stream_id=stream["id"],
            user_id=ban_data.user_id,
            ban_type=ban_data.ban_type.value,
            duration_seconds=ban_data.duration_seconds,
            reason=ban_data.reason,
            banned_by=user["id"],
            created_at=now,
            expires_at=expires_at,
        )
        .returning(stream_bans.c.id)
    )
    result = await fetch_one_with_retry(insert_query)
    ban_id = result["id"]

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
        moderator_id=user["id"],
        moderator_username=user.get("username", ""),
        duration_seconds=ban_data.duration_seconds,
        reason=ban_data.reason,
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
        target_username=target_user["username"] if target_user else "",
        moderator_id=user["id"],
        moderator_username=user.get("username", ""),
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

    # Insert filter (use RETURNING for PostgreSQL compatibility)
    now = datetime.now(timezone.utc)
    insert_query = (
        stream_word_filters.insert()
        .values(
            stream_id=stream["id"],
            pattern=filter_data.pattern,
            is_regex=filter_data.is_regex,
            action=filter_data.action.value,
            timeout_seconds=filter_data.timeout_seconds,
            created_at=now,
            created_by=user["id"],
        )
        .returning(stream_word_filters.c.id)
    )
    result = await fetch_one_with_retry(insert_query)
    filter_id = result["id"]

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

    # Batch-fetch all target usernames (avoid N+1 queries)
    target_ids = {log["target_user_id"] for log in logs if log["target_user_id"]}
    target_map: dict[str, str] = {}
    if target_ids:
        targets = await fetch_all_with_retry(
            users.select().where(users.c.id.in_(target_ids))
        )
        target_map = {t["id"]: t["username"] for t in targets}

    # Build response list
    log_responses = []
    for log in logs:
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
                target_username=target_map.get(log["target_user_id"]) if log["target_user_id"] else None,
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
