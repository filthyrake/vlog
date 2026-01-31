"""
Studio/Broadcaster Dashboard API.

Provides endpoints for broadcasters to manage their own live streams:
- List, create, update, end streams
- Stream key regeneration (with security controls)
- Ownership-based access control

Related Issue: #524
"""

import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request
from slowapi import Limiter
from slugify import slugify

from api.audit import AuditAction, log_audit
from api.auth.middleware import require_auth, SESSION_COOKIE_NAME
from api.auth.permissions import Permission, Role, has_permission
from api.common import get_real_ip, get_request_id, require_valid_slug
from api.database import database, live_streams
from api.db_retry import db_execute_with_retry, fetch_one_with_retry
from api.live_auth import generate_stream_key, get_key_prefix, hash_stream_key
from api.live_schemas import (
    StudioStreamCreate,
    StudioStreamCreatedResponse,
    StudioStreamKeyResponse,
    StudioStreamListResponse,
    StudioStreamResponse,
    StudioStreamUpdate,
)
from config import (
    LIVE_ENABLED,
    LIVE_RTMP_INGEST_URL,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
    SESSION_SECRET_KEY,
)

logger = logging.getLogger(__name__)

# Create router for studio API
router = APIRouter(prefix="/api/v1/studio", tags=["Studio"])

# Initialize rate limiter
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)


def _validate_csrf_token(session_token: str, csrf_token: str) -> bool:
    """
    Validate CSRF token for user session.

    CSRF token is derived from session token using HMAC-SHA256.
    """
    if not session_token or not csrf_token:
        return False
    if not SESSION_SECRET_KEY:
        logger.error("SESSION_SECRET_KEY not configured for CSRF validation")
        return False
    expected = hmac.new(
        SESSION_SECRET_KEY.encode(),
        session_token.encode(),
        "sha256",
    ).hexdigest()[:32]
    return hmac.compare_digest(csrf_token, expected)


async def require_csrf(
    session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(None, alias="X-CSRF-Token"),
) -> None:
    """
    Dependency to require valid CSRF token for state-changing requests.

    Validates the X-CSRF-Token header against the session cookie.

    Raises:
        HTTPException: 403 if CSRF token is invalid or missing
    """
    if not session_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not x_csrf_token:
        raise HTTPException(status_code=403, detail="CSRF token required")
    if not _validate_csrf_token(session_token, x_csrf_token):
        logger.warning("CSRF validation failed for studio request")
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


async def verify_stream_access(slug: str, user: dict) -> dict:
    """
    Verify user has access to a stream (ownership or admin permission).

    Args:
        slug: Stream slug
        user: Current authenticated user

    Returns:
        Stream record as dict

    Raises:
        HTTPException: 400 if slug is invalid
        HTTPException: 404 if stream not found or user doesn't have access
    """
    # Validate slug format to prevent enumeration/injection
    require_valid_slug(slug, "stream")

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
        # Return same error to prevent enumeration
        raise HTTPException(status_code=404, detail="Stream not found")

    return dict(stream)


def stream_to_response(stream: dict) -> StudioStreamResponse:
    """Convert stream record to response model."""
    qualities = []
    if stream["qualities"]:
        try:
            qualities = json.loads(stream["qualities"])
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse qualities JSON for stream {stream.get('id')}")

    return StudioStreamResponse(
        id=stream["id"],
        title=stream["title"],
        slug=stream["slug"],
        description=stream["description"] or "",
        status=stream["status"],
        qualities=qualities if qualities else None,
        category_id=stream["category_id"],
        dvr_enabled=stream["dvr_enabled"],
        dvr_window_seconds=stream["dvr_window_seconds"],
        auto_record_vod=stream["auto_record_vod"],
        segment_count=stream["segment_count"],
        vod_video_id=stream["vod_video_id"],
        created_at=stream["created_at"],
        started_at=stream["started_at"],
        ended_at=stream["ended_at"],
        last_segment_at=stream["last_segment_at"],
    )


@router.get("/streams")
@limiter.limit("60/minute")
async def list_streams(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    user: dict = Depends(require_auth),
) -> StudioStreamListResponse:
    """
    List streams owned by the current user.

    Admins see all streams.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 20

    offset = (page - 1) * page_size
    role = Role(user["role"])

    # Build query based on role
    query = live_streams.select()

    # Non-admins only see their own streams
    if not has_permission(role, Permission.LIVE_STREAM_MANAGE):
        query = query.where(live_streams.c.owner_id == user["id"])

    # Optional status filter
    if status and status in ("idle", "live", "ending", "ended"):
        query = query.where(live_streams.c.status == status)

    # Count total
    count_query = query.with_only_columns(sa.func.count())
    total = await database.fetch_val(count_query) or 0

    # Fetch page
    query = query.order_by(live_streams.c.created_at.desc())
    query = query.offset(offset).limit(page_size)
    rows = await database.fetch_all(query)

    streams = [stream_to_response(dict(row)) for row in rows]

    return StudioStreamListResponse(
        streams=streams,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(streams)) < total,
    )


@router.get("/streams/{slug}")
@limiter.limit("60/minute")
async def get_stream(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
) -> StudioStreamResponse:
    """Get a specific stream's details."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    stream = await verify_stream_access(slug, user)
    return stream_to_response(stream)


@router.post("/streams")
@limiter.limit("10/minute")
async def create_stream(
    request: Request,
    data: StudioStreamCreate,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
) -> StudioStreamCreatedResponse:
    """
    Create a new live stream.

    Returns the stream key ONCE in the response. The key is not stored
    in plaintext and cannot be retrieved again.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    # Check permission
    role = Role(user["role"])
    if not has_permission(role, Permission.LIVE_STREAM_CREATE):
        raise HTTPException(status_code=403, detail="Permission denied")

    # Generate unique slug
    base_slug = slugify(data.title, max_length=200)
    slug = base_slug
    counter = 1
    while True:
        existing = await database.fetch_one(
            live_streams.select().where(live_streams.c.slug == slug)
        )
        if not existing:
            break
        slug = f"{base_slug}-{counter}"
        counter += 1
        if counter > 100:
            raise HTTPException(status_code=400, detail="Could not generate unique slug")

    # Generate stream key (shown once)
    stream_key = generate_stream_key()
    key_hash = hash_stream_key(stream_key)
    key_prefix = get_key_prefix(stream_key)

    now = datetime.now(timezone.utc)

    # Insert stream
    result = await db_execute_with_retry(
        live_streams.insert().values(
            title=data.title,
            slug=slug,
            description=data.description,
            stream_key_hash=key_hash,
            stream_key_prefix=key_prefix,
            hash_version=2,  # argon2id
            status="idle",
            category_id=data.category_id,
            dvr_enabled=data.dvr_enabled,
            dvr_window_seconds=data.dvr_window_seconds,
            auto_record_vod=data.auto_record_vod,
            owner_id=user["id"],
            created_at=now,
            segment_count=0,
        )
    )

    stream_id = result

    # Audit log
    log_audit(
        AuditAction.STREAM_CREATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="live_stream",
        resource_id=stream_id,
        resource_name=slug,
        details={
            "title": data.title,
            "user_id": user["id"],
            "username": user.get("username"),
        },
        request_id=get_request_id(request),
    )

    logger.info(f"Stream {slug} created by user {user['id']}")

    return StudioStreamCreatedResponse(
        id=stream_id,
        title=data.title,
        slug=slug,
        description=data.description,
        status="idle",
        stream_key=stream_key,  # Only time this is returned
        rtmp_url=f"{LIVE_RTMP_INGEST_URL}/{slug}",
        category_id=data.category_id,
        dvr_enabled=data.dvr_enabled,
        dvr_window_seconds=data.dvr_window_seconds,
        auto_record_vod=data.auto_record_vod,
        created_at=now,
    )


@router.patch("/streams/{slug}")
@limiter.limit("30/minute")
async def update_stream(
    request: Request,
    slug: str,
    data: StudioStreamUpdate,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
) -> StudioStreamResponse:
    """Update a stream's metadata."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    stream = await verify_stream_access(slug, user)

    # Build update values - use model_fields_set to distinguish "not provided" vs "explicitly null"
    update_values = {}
    if data.title is not None:
        update_values["title"] = data.title
    if data.description is not None:
        update_values["description"] = data.description
    if "category_id" in data.model_fields_set:
        update_values["category_id"] = data.category_id
    if data.dvr_enabled is not None:
        update_values["dvr_enabled"] = data.dvr_enabled
    if data.dvr_window_seconds is not None:
        update_values["dvr_window_seconds"] = data.dvr_window_seconds
    if data.auto_record_vod is not None:
        update_values["auto_record_vod"] = data.auto_record_vod

    if not update_values:
        return stream_to_response(stream)

    await db_execute_with_retry(
        live_streams.update()
        .where(live_streams.c.id == stream["id"])
        .values(**update_values)
    )

    # Audit log
    log_audit(
        AuditAction.STREAM_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="live_stream",
        resource_id=stream["id"],
        resource_name=slug,
        details={
            "changes": update_values,
            "user_id": user["id"],
        },
        request_id=get_request_id(request),
    )

    # Fetch updated stream
    updated = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream["id"])
    )
    return stream_to_response(dict(updated))


@router.post("/streams/{slug}/end")
@limiter.limit("10/minute")
async def end_stream(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
) -> StudioStreamResponse:
    """End a live stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    stream = await verify_stream_access(slug, user)

    if stream["status"] == "ended":
        raise HTTPException(status_code=400, detail="Stream already ended")

    now = datetime.now(timezone.utc)

    await db_execute_with_retry(
        live_streams.update()
        .where(live_streams.c.id == stream["id"])
        .values(status="ended", ended_at=now)
    )

    # Audit log
    log_audit(
        AuditAction.STREAM_END,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="live_stream",
        resource_id=stream["id"],
        resource_name=slug,
        details={
            "previous_status": stream["status"],
            "user_id": user["id"],
        },
        request_id=get_request_id(request),
    )

    logger.info(f"Stream {slug} ended by user {user['id']}")

    # Fetch updated stream
    updated = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream["id"])
    )
    return stream_to_response(dict(updated))


@router.post("/streams/{slug}/key/regenerate")
@limiter.limit("3/hour")  # Strict rate limit for key regeneration
async def regenerate_stream_key(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
) -> StudioStreamKeyResponse:
    """
    Regenerate the stream key.

    This invalidates the old key immediately. Cannot be done while streaming.
    Returns the new key ONCE - it cannot be retrieved again.

    Rate limited to 3 requests per hour.

    Uses atomic transaction with row lock to prevent race condition
    where stream goes live between status check and key update.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    # First verify basic access (slug validation + ownership)
    stream = await verify_stream_access(slug, user)
    stream_id = stream["id"]

    # Generate new key before transaction to minimize lock time
    new_key = generate_stream_key()
    key_hash = hash_stream_key(new_key)
    key_prefix = get_key_prefix(new_key)

    # Use transaction with row lock to prevent race condition
    async with database.transaction():
        # Lock the row and re-check status atomically
        locked_stream = await database.fetch_one(
            sa.text("""
                SELECT status FROM live_streams
                WHERE id = :id
                FOR UPDATE
            """),
            {"id": stream_id},
        )

        if not locked_stream:
            raise HTTPException(status_code=404, detail="Stream not found")

        if locked_stream["status"] == "live":
            raise HTTPException(
                status_code=409,
                detail="Cannot regenerate key while streaming. End the stream first.",
            )

        # Update key within same transaction (row is locked)
        await database.execute(
            live_streams.update()
            .where(live_streams.c.id == stream_id)
            .values(
                stream_key_hash=key_hash,
                stream_key_prefix=key_prefix,
                hash_version=2,
            )
        )

    # Audit log
    log_audit(
        AuditAction.STREAM_KEY_REGENERATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="live_stream",
        resource_id=stream["id"],
        resource_name=slug,
        details={
            "user_id": user["id"],
        },
        request_id=get_request_id(request),
    )

    logger.info(f"Stream key regenerated for {slug} by user {user['id']}")

    return StudioStreamKeyResponse(
        stream_key=new_key,
        rtmp_url=f"{LIVE_RTMP_INGEST_URL}/{slug}",
    )
