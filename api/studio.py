"""Studio API endpoints for broadcaster dashboard.

This module provides APIs for the studio/broadcaster dashboard:
- Stream management (list, get, update, end)
- Stream key handling (with password re-entry)
- Real-time metrics and viewer stats

Security decisions per Bruce's review:
- Stream key retrieval requires POST with password re-entry
- All endpoints require authentication
- Ownership checks enforce access control

Issue #524 - Broadcaster Dashboard
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter

from api.auth.middleware import get_current_user, require_auth
from api.auth.password import verify_password
from api.common import get_real_ip
from api.database import categories, live_streams
from api.db_retry import db_execute_with_retry, fetch_all_with_retry, fetch_one_with_retry
from api.live_auth import generate_stream_key, hash_stream_key, get_key_prefix, revoke_stream_key
from api.live_schemas import (
    ActiveViewerInfo,
    ActiveViewersResponse,
    MetricDataPoint,
    StreamKeyRequest,
    StreamKeyResponse,
    StreamMetricsResponse,
    StudioStreamListResponse,
    StudioStreamResponse,
    StudioStreamUpdateRequest,
    ViewerStatsResponse,
)
from config import (
    LIVE_ENABLED,
    RATE_LIMIT_ADMIN_DEFAULT,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
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


def _check_stream_ownership(stream: dict, user: dict) -> bool:
    """
    Check if user owns the stream or is admin.

    Args:
        stream: Stream record
        user: Current user

    Returns:
        True if user has access
    """
    if user.get("role") == "admin":
        return True
    return stream.get("owner_id") == user.get("id")


def _parse_qualities_json(qualities_str: Optional[str]) -> list:
    """Parse qualities JSON string to list."""
    if not qualities_str:
        return []
    try:
        import json
        return json.loads(qualities_str)
    except (json.JSONDecodeError, TypeError):
        return []


def _build_studio_stream_response(row: dict) -> StudioStreamResponse:
    """Build StudioStreamResponse from database row."""
    return StudioStreamResponse(
        id=row["id"],
        title=row["title"],
        slug=row["slug"],
        description=row.get("description") or "",
        status=row["status"],
        qualities=_parse_qualities_json(row.get("qualities")),
        category_id=row.get("category_id"),
        current_bitrate=row.get("current_bitrate"),
        connection_health=row.get("connection_health") or "unknown",
        viewer_count_current=row.get("viewer_count_current") or 0,
        viewer_count_peak=row.get("viewer_count_peak") or 0,
        viewer_count_total=row.get("viewer_count_total") or 0,
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        ended_at=row.get("ended_at"),
        last_segment_at=row.get("last_segment_at"),
        last_metric_at=row.get("last_metric_at"),
    )


@router.get("/streams")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_studio_streams(
    request: Request,
    current_user: dict = Depends(require_auth),
    status: Optional[str] = Query(None, description="Filter by status: idle, live, ending, ended"),
    limit: int = Query(50, ge=1, le=100),
) -> StudioStreamListResponse:
    """
    List streams accessible to the current user.

    Admins see all streams. Editors see only their own streams.
    """
    if not LIVE_ENABLED:
        return StudioStreamListResponse(streams=[], total=0)

    # Build query based on user role
    query = live_streams.select()

    if current_user.get("role") != "admin":
        # Non-admins only see their own streams
        query = query.where(live_streams.c.owner_id == current_user["id"])

    if status:
        query = query.where(live_streams.c.status == status)

    query = query.order_by(live_streams.c.created_at.desc()).limit(limit)

    rows = await fetch_all_with_retry(query)

    streams = [_build_studio_stream_response(dict(row)) for row in rows]

    return StudioStreamListResponse(streams=streams, total=len(streams))


@router.get("/streams/{slug}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_studio_stream(
    request: Request,
    slug: str,
    current_user: dict = Depends(require_auth),
) -> StudioStreamResponse:
    """
    Get stream details for studio dashboard.

    Includes health metrics and viewer counts.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    row = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not row:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Check ownership
    if not _check_stream_ownership(dict(row), current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    return _build_studio_stream_response(dict(row))


@router.patch("/streams/{slug}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def update_studio_stream(
    request: Request,
    slug: str,
    body: StudioStreamUpdateRequest,
    current_user: dict = Depends(require_auth),
) -> StudioStreamResponse:
    """
    Update stream metadata from studio.

    Allows updating title, description, and category mid-stream.
    Input is sanitized per Bruce's review.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    row = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not row:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Check ownership
    if not _check_stream_ownership(dict(row), current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    # Build update values (only non-None fields)
    update_values = {}
    if body.title is not None:
        update_values["title"] = body.title
    if body.description is not None:
        update_values["description"] = body.description
    if body.category_id is not None:
        # Validate category exists (per Bruce's review)
        category = await fetch_one_with_retry(
            categories.select().where(categories.c.id == body.category_id)
        )
        if not category:
            raise HTTPException(status_code=400, detail="Invalid category_id: category does not exist")
        update_values["category_id"] = body.category_id

    if not update_values:
        raise HTTPException(status_code=400, detail="No fields to update")

    await db_execute_with_retry(
        live_streams.update()
        .where(live_streams.c.id == row["id"])
        .values(**update_values)
    )

    # Fetch updated row
    updated_row = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == row["id"])
    )

    logger.info(f"Stream {slug} updated by user {current_user['id']}")

    return _build_studio_stream_response(dict(updated_row))


@router.post("/streams/{slug}/end")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def end_studio_stream(
    request: Request,
    slug: str,
    current_user: dict = Depends(require_auth),
) -> StudioStreamResponse:
    """
    End a live stream from studio.

    Revokes the stream key and triggers VOD recording if enabled.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    row = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not row:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Check ownership
    if not _check_stream_ownership(dict(row), current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    if row["status"] == "ended":
        raise HTTPException(status_code=400, detail="Stream has already ended")

    # Revoke the stream key (this ends the stream)
    await revoke_stream_key(row["id"])

    # Fetch updated row
    updated_row = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == row["id"])
    )

    logger.info(f"Stream {slug} ended by user {current_user['id']}")

    return _build_studio_stream_response(dict(updated_row))


@router.post("/streams/{slug}/key")
@limiter.limit("5/minute")  # Stricter rate limit for sensitive operation
async def get_studio_stream_key(
    request: Request,
    slug: str,
    body: StreamKeyRequest,
    current_user: dict = Depends(require_auth),
) -> dict:
    """
    Stream key retrieval is not supported (keys are hashed).

    SECURITY: Stream keys are securely hashed and cannot be retrieved.
    Use the regenerate endpoint to get a new key.
    """
    raise HTTPException(
        status_code=400,
        detail="Stream keys cannot be retrieved after creation. Use /key/regenerate to generate a new key.",
    )


@router.post("/streams/{slug}/key/regenerate")
@limiter.limit("3/minute")  # Very strict rate limit
async def regenerate_studio_stream_key(
    request: Request,
    slug: str,
    body: StreamKeyRequest,
    current_user: dict = Depends(require_auth),
) -> StreamKeyResponse:
    """
    Regenerate the stream key (requires password re-entry).

    This invalidates the current key and generates a new one.
    Any active ingest will be disconnected.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    row = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not row:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Check ownership
    if not _check_stream_ownership(dict(row), current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    # Re-verify password
    if not verify_password(body.current_password, current_user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid password")

    # Generate new stream key
    new_key = generate_stream_key()
    key_hash = hash_stream_key(new_key)
    key_prefix = get_key_prefix(new_key)

    # Update stream with new key
    await db_execute_with_retry(
        live_streams.update()
        .where(live_streams.c.id == row["id"])
        .values(
            stream_key_hash=key_hash,
            stream_key_prefix=key_prefix,
        )
    )

    logger.info(f"Stream key regenerated for {slug} by user {current_user['id']}")

    return StreamKeyResponse(stream_key=new_key)


@router.get("/streams/{slug}/metrics")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_studio_stream_metrics(
    request: Request,
    slug: str,
    minutes: int = Query(5, ge=1, le=60),
    current_user: dict = Depends(require_auth),
) -> StreamMetricsResponse:
    """
    Get recent metrics for a stream.

    Returns bitrate, latency, and health data for the last N minutes.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    row = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not row:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Check ownership
    if not _check_stream_ownership(dict(row), current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    from api.live_metrics import get_recent_metrics

    metrics_data = await get_recent_metrics(row["id"], minutes=minutes)

    return StreamMetricsResponse(
        stream_id=row["id"],
        current_bitrate=row["current_bitrate"],
        connection_health=row["connection_health"] or "unknown",
        last_metric_at=row["last_metric_at"],
        metrics=[
            MetricDataPoint(
                timestamp=m["timestamp"],
                bitrate_video=m["bitrate_video"],
                bitrate_audio=m["bitrate_audio"],
                bitrate_total=m["bitrate_total"],
                segment_push_latency_ms=m["segment_push_latency_ms"],
                segments_received=m["segments_received"] or 0,
                segments_dropped=m["segments_dropped"] or 0,
                interval_seconds=m["interval_seconds"] or 10,
            )
            for m in metrics_data
        ],
    )


@router.get("/streams/{slug}/viewers")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_studio_stream_viewers(
    request: Request,
    slug: str,
    current_user: dict = Depends(require_auth),
) -> ViewerStatsResponse:
    """
    Get viewer statistics for a stream.

    Returns current, peak, and total viewer counts plus quality distribution.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    row = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not row:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Check ownership
    if not _check_stream_ownership(dict(row), current_user):
        raise HTTPException(status_code=403, detail="Access denied")

    from api.live_viewers import get_stream_viewer_stats

    stats = await get_stream_viewer_stats(row["id"])

    return ViewerStatsResponse(
        current=stats["current"],
        peak=stats["peak"],
        total=stats["total"],
        quality_distribution=stats["quality_distribution"],
    )
