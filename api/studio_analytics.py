"""
Studio Stream Analytics API.

Provides endpoints for broadcasters to view stream analytics:
- View analytics summary for a stream
- View viewer count history
- Trigger analytics recomputation

Related Issue: #530 (Phase 2D)
"""

import logging
from datetime import datetime, timezone
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.auth.middleware import require_auth
from api.auth.permissions import Permission, Role, has_permission
from api.common import get_real_ip, get_request_id, require_valid_slug
from api.database import (
    database,
    live_streams,
    stream_analytics_summary,
    stream_viewer_counts,
    chat_messages,
)
from api.db_retry import fetch_one_with_retry, fetch_all_with_retry, db_execute_with_retry
from api.live_schemas import (
    StreamAnalyticsSummaryResponse,
    StreamAnalyticsResponse,
    ViewerCountResponse,
    ViewerHistoryResponse,
)
from api.studio import require_csrf
from config import (
    LIVE_ENABLED,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
)

logger = logging.getLogger(__name__)

# Create router for studio analytics API
router = APIRouter(prefix="/api/v1/studio", tags=["Studio Analytics"])

# Initialize rate limiter
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)


async def verify_stream_analytics_access(stream_slug: str, user: dict) -> dict:
    """
    Verify user has access to view stream analytics.

    A user has access if:
    1. They own the stream, OR
    2. They have admin permissions

    Args:
        stream_slug: Stream slug
        user: Current authenticated user

    Returns:
        Stream record as dict

    Raises:
        HTTPException: 400 if slug is invalid
        HTTPException: 404 if stream not found or user doesn't have access
    """
    # Validate slug format
    require_valid_slug(stream_slug, "stream")

    # Query stream
    query = sa.select(live_streams).where(live_streams.c.slug == stream_slug)
    stream = await fetch_one_with_retry(query)

    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    stream_dict = dict(stream._mapping)

    # Check access: owner or admin
    role = Role(user["role"])
    if stream_dict["owner_id"] != user["id"] and not has_permission(
        role, Permission.LIVE_STREAM_MANAGE
    ):
        raise HTTPException(status_code=404, detail="Stream not found")

    return stream_dict


# =============================================================================
# Analytics Endpoints
# =============================================================================


@router.get(
    "/streams/{stream_slug}/analytics",
    response_model=StreamAnalyticsResponse,
    summary="Get stream analytics",
    description="Get analytics summary and viewer history for a stream.",
)
@limiter.limit("60/minute")
async def get_stream_analytics(
    request: Request,
    stream_slug: str,
    user: dict = Depends(require_auth),
):
    """Get complete analytics for a stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=404, detail="Live streaming not enabled")

    stream = await verify_stream_analytics_access(stream_slug, user)
    stream_id = stream["id"]

    # Fetch analytics summary
    summary_query = sa.select(stream_analytics_summary).where(
        stream_analytics_summary.c.stream_id == stream_id
    )
    summary_row = await fetch_one_with_retry(summary_query)

    if summary_row:
        summary = StreamAnalyticsSummaryResponse(
            stream_id=summary_row.stream_id,
            peak_viewers=summary_row.peak_viewers,
            average_viewers=summary_row.average_viewers,
            total_unique_viewers=summary_row.total_unique_viewers,
            total_chat_messages=summary_row.total_chat_messages,
            total_watch_minutes=summary_row.total_watch_minutes,
            average_watch_time_seconds=summary_row.average_watch_time_seconds,
            stream_duration_seconds=summary_row.stream_duration_seconds,
            computed_at=summary_row.computed_at,
        )
    else:
        # Return default/empty summary if not computed yet
        summary = StreamAnalyticsSummaryResponse(
            stream_id=stream_id,
            peak_viewers=0,
            average_viewers=0.0,
            total_unique_viewers=0,
            total_chat_messages=0,
            total_watch_minutes=0.0,
            average_watch_time_seconds=0.0,
            stream_duration_seconds=0,
            computed_at=None,
        )

    # Fetch viewer history
    viewer_query = (
        sa.select(stream_viewer_counts)
        .where(stream_viewer_counts.c.stream_id == stream_id)
        .order_by(stream_viewer_counts.c.recorded_at.asc())
    )
    viewer_rows = await fetch_all_with_retry(viewer_query)

    viewer_history = [
        ViewerCountResponse(
            recorded_at=row.recorded_at,
            viewer_count=row.viewer_count,
        )
        for row in viewer_rows
    ]

    return StreamAnalyticsResponse(
        summary=summary,
        viewer_history=viewer_history,
    )


@router.get(
    "/streams/{stream_slug}/analytics/summary",
    response_model=StreamAnalyticsSummaryResponse,
    summary="Get analytics summary",
    description="Get aggregated analytics summary for a stream.",
)
@limiter.limit("60/minute")
async def get_analytics_summary(
    request: Request,
    stream_slug: str,
    user: dict = Depends(require_auth),
):
    """Get analytics summary only."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=404, detail="Live streaming not enabled")

    stream = await verify_stream_analytics_access(stream_slug, user)
    stream_id = stream["id"]

    # Fetch analytics summary
    summary_query = sa.select(stream_analytics_summary).where(
        stream_analytics_summary.c.stream_id == stream_id
    )
    summary_row = await fetch_one_with_retry(summary_query)

    if summary_row:
        return StreamAnalyticsSummaryResponse(
            stream_id=summary_row.stream_id,
            peak_viewers=summary_row.peak_viewers,
            average_viewers=summary_row.average_viewers,
            total_unique_viewers=summary_row.total_unique_viewers,
            total_chat_messages=summary_row.total_chat_messages,
            total_watch_minutes=summary_row.total_watch_minutes,
            average_watch_time_seconds=summary_row.average_watch_time_seconds,
            stream_duration_seconds=summary_row.stream_duration_seconds,
            computed_at=summary_row.computed_at,
        )

    # Return default/empty summary if not computed yet
    return StreamAnalyticsSummaryResponse(
        stream_id=stream_id,
        peak_viewers=0,
        average_viewers=0.0,
        total_unique_viewers=0,
        total_chat_messages=0,
        total_watch_minutes=0.0,
        average_watch_time_seconds=0.0,
        stream_duration_seconds=0,
        computed_at=None,
    )


@router.get(
    "/streams/{stream_slug}/analytics/viewers",
    response_model=ViewerHistoryResponse,
    summary="Get viewer history",
    description="Get viewer count history for a stream.",
)
@limiter.limit("60/minute")
async def get_viewer_history(
    request: Request,
    stream_slug: str,
    limit: int = 1000,
    user: dict = Depends(require_auth),
):
    """Get viewer count time series for a stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=404, detail="Live streaming not enabled")

    # Clamp limit
    limit = min(max(1, limit), 10000)

    stream = await verify_stream_analytics_access(stream_slug, user)
    stream_id = stream["id"]

    # Fetch viewer counts
    viewer_query = (
        sa.select(stream_viewer_counts)
        .where(stream_viewer_counts.c.stream_id == stream_id)
        .order_by(stream_viewer_counts.c.recorded_at.asc())
        .limit(limit)
    )
    viewer_rows = await fetch_all_with_retry(viewer_query)

    data_points = [
        ViewerCountResponse(
            recorded_at=row.recorded_at,
            viewer_count=row.viewer_count,
        )
        for row in viewer_rows
    ]

    return ViewerHistoryResponse(
        stream_id=stream_id,
        data_points=data_points,
        total_points=len(data_points),
    )


@router.post(
    "/streams/{stream_slug}/analytics/recompute",
    response_model=StreamAnalyticsSummaryResponse,
    summary="Recompute analytics",
    description="Trigger recomputation of analytics for a stream.",
    dependencies=[Depends(require_csrf)],
)
@limiter.limit("10/minute")
async def recompute_analytics(
    request: Request,
    stream_slug: str,
    user: dict = Depends(require_auth),
):
    """Recompute analytics for a stream."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=404, detail="Live streaming not enabled")

    stream = await verify_stream_analytics_access(stream_slug, user)
    stream_id = stream["id"]

    # Compute analytics from raw data
    now = datetime.now(timezone.utc)

    # Calculate stream duration
    stream_duration_seconds = 0
    if stream.get("started_at") and stream.get("ended_at"):
        start = stream["started_at"]
        end = stream["ended_at"]
        stream_duration_seconds = int((end - start).total_seconds())
    elif stream.get("started_at") and stream.get("status") == "live":
        # Still live, calculate from start to now
        start = stream["started_at"]
        stream_duration_seconds = int((now - start).total_seconds())

    # Get viewer count statistics
    viewer_stats_query = sa.select(
        sa.func.max(stream_viewer_counts.c.viewer_count).label("peak_viewers"),
        sa.func.avg(stream_viewer_counts.c.viewer_count).label("average_viewers"),
        sa.func.count(stream_viewer_counts.c.id).label("data_points"),
    ).where(stream_viewer_counts.c.stream_id == stream_id)
    viewer_stats = await fetch_one_with_retry(viewer_stats_query)

    peak_viewers = viewer_stats.peak_viewers or 0
    average_viewers = float(viewer_stats.average_viewers or 0)

    # Count chat messages
    chat_count_query = sa.select(sa.func.count(chat_messages.c.id)).where(
        chat_messages.c.stream_id == stream_id,
        chat_messages.c.deleted_at.is_(None),
    )
    chat_count = await database.fetch_val(chat_count_query)
    total_chat_messages = chat_count or 0

    # Note: unique viewers and watch time would require playback session tracking
    # For now, we estimate based on viewer counts
    total_unique_viewers = 0  # Would need session tracking
    total_watch_minutes = 0.0  # Would need session tracking
    average_watch_time_seconds = 0.0  # Would need session tracking

    # Atomic upsert using PostgreSQL INSERT ... ON CONFLICT DO UPDATE.
    # This eliminates the TOCTOU race condition where concurrent requests could
    # both see "not exists" and attempt inserts — the database handles
    # serialization at the row level regardless of isolation level. (Issue #550)
    analytics_values = dict(
        peak_viewers=peak_viewers,
        average_viewers=average_viewers,
        total_unique_viewers=total_unique_viewers,
        total_chat_messages=total_chat_messages,
        total_watch_minutes=total_watch_minutes,
        average_watch_time_seconds=average_watch_time_seconds,
        stream_duration_seconds=stream_duration_seconds,
        computed_at=now,
    )

    upsert_stmt = pg_insert(stream_analytics_summary).values(
        stream_id=stream_id,
        **analytics_values,
    ).on_conflict_do_update(
        index_elements=[stream_analytics_summary.c.stream_id],
        set_=analytics_values,
    )
    await db_execute_with_retry(upsert_stmt)

    logger.info(
        f"Recomputed analytics for stream {stream_slug}",
        extra={
            "request_id": get_request_id(request),
            "stream_id": stream_id,
            "peak_viewers": peak_viewers,
            "average_viewers": average_viewers,
            "total_chat_messages": total_chat_messages,
        },
    )

    return StreamAnalyticsSummaryResponse(
        stream_id=stream_id,
        peak_viewers=peak_viewers,
        average_viewers=average_viewers,
        total_unique_viewers=total_unique_viewers,
        total_chat_messages=total_chat_messages,
        total_watch_minutes=total_watch_minutes,
        average_watch_time_seconds=average_watch_time_seconds,
        stream_duration_seconds=stream_duration_seconds,
        computed_at=now,
    )


# =============================================================================
# Viewer Count Recording (Internal)
# =============================================================================


async def record_viewer_count(stream_id: int, viewer_count: int) -> None:
    """
    Record a viewer count snapshot for a stream.

    Called periodically (e.g., every minute) during live streams
    to build viewer history for analytics.

    Args:
        stream_id: Stream ID
        viewer_count: Current viewer count
    """
    insert_query = stream_viewer_counts.insert().values(
        stream_id=stream_id,
        viewer_count=viewer_count,
        recorded_at=datetime.now(timezone.utc),
    )
    await db_execute_with_retry(insert_query)
