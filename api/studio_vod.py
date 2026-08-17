"""
Studio VOD Management API.

Provides endpoints for broadcasters to manage their VOD recordings:
- List, view, update, delete VODs linked to their streams
- View analytics for VODs
- Download source files
- Upload custom thumbnails

Related Issue: #530
"""

import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from slowapi import Limiter

# Import PIL at module level and configure decompression bomb protection
# This must be done once at import time, not per-request, to avoid race conditions
try:
    from PIL import Image, ImageOps
    # Limit to 100 megapixels to prevent decompression bombs (CVE-2018-14618)
    # ~400MB uncompressed max, well within server memory limits
    Image.MAX_IMAGE_PIXELS = 100_000_000
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    Image = None  # type: ignore
    ImageOps = None  # type: ignore

from api.audit import AuditAction, log_audit
from api.auth.middleware import require_auth
from api.auth.permissions import Permission, Role, has_permission
from api.common import get_real_ip, get_request_id, require_valid_slug
from api.database import database, live_streams, playback_sessions, videos
from api.db_retry import db_execute_with_retry, fetch_all_with_retry, fetch_one_with_retry, fetch_val_with_retry
from api.live_schemas import (
    StudioVODAnalyticsResponse,
    StudioVODDownloadResponse,
    StudioVODListResponse,
    StudioVODResponse,
    StudioVODUpdate,
)
from api.studio import require_csrf
from config import (
    LIVE_ENABLED,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
    VIDEOS_DIR,
)

logger = logging.getLogger(__name__)

# Create router for studio VOD API
router = APIRouter(prefix="/api/v1/studio", tags=["Studio VOD"])

# Initialize rate limiter
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)


def _get_thumbnail_url(video_slug: str) -> str:
    """Get the thumbnail URL for a video."""
    return f"/videos/{video_slug}/thumbnail.jpg"


async def verify_vod_access(slug: str, user: dict) -> dict:
    """
    Verify user has access to a VOD (ownership or admin permission).

    A user has access if:
    1. They own the video directly, OR
    2. The video is linked to a stream they own, OR
    3. They have admin permissions

    Args:
        slug: Video slug
        user: Current authenticated user

    Returns:
        Video record as dict with stream info

    Raises:
        HTTPException: 400 if slug is invalid
        HTTPException: 404 if VOD not found or user doesn't have access
    """
    # Validate slug format
    require_valid_slug(slug, "vod")

    # Query video with stream info
    query = (
        sa.select(
            videos,
            live_streams.c.id.label("stream_id"),
            live_streams.c.slug.label("stream_slug"),
            live_streams.c.title.label("stream_title"),
            live_streams.c.owner_id.label("stream_owner_id"),
        )
        .select_from(
            videos.outerjoin(
                live_streams,
                live_streams.c.vod_video_id == videos.c.id
            )
        )
        .where(videos.c.slug == slug)
        .where(videos.c.deleted_at.is_(None))  # Exclude soft-deleted
    )

    video = await fetch_one_with_retry(query)

    if not video:
        raise HTTPException(status_code=404, detail="VOD not found")

    video_dict = dict(video)

    # Check access
    role = Role(user["role"])
    has_manage_any = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    # Check ownership: either video owner or stream owner
    is_video_owner = video_dict.get("owner_id") == user["id"]
    is_stream_owner = video_dict.get("stream_owner_id") == user["id"]
    is_owner = is_video_owner or is_stream_owner

    if not is_owner and not has_manage_any:
        # Return same error to prevent enumeration
        raise HTTPException(status_code=404, detail="VOD not found")

    return video_dict


def vod_to_response(video: dict) -> StudioVODResponse:
    """Convert video record to VOD response model."""
    thumbnail_url = None
    if video.get("slug"):
        # Check if thumbnail exists
        thumb_path = VIDEOS_DIR / video["slug"] / "thumbnail.jpg"
        if thumb_path.exists():
            thumbnail_url = _get_thumbnail_url(video["slug"])

    return StudioVODResponse(
        id=video["id"],
        title=video["title"],
        slug=video["slug"],
        description=video["description"] or "",
        status=video["status"],
        duration=video["duration"] or 0,
        source_width=video["source_width"] or 0,
        source_height=video["source_height"] or 0,
        category_id=video["category_id"],
        thumbnail_url=thumbnail_url,
        created_at=video["created_at"],
        published_at=video["published_at"],
        stream_id=video.get("stream_id"),
        stream_slug=video.get("stream_slug"),
        stream_title=video.get("stream_title"),
    )


@router.get("/vods")
@limiter.limit("60/minute")
async def list_vods(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    user: dict = Depends(require_auth),
) -> StudioVODListResponse:
    """
    List VODs for streams owned by the current user.

    Includes videos that:
    - Are directly owned by the user
    - Are linked to streams owned by the user

    Admins see all VODs.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 20

    offset = (page - 1) * page_size
    role = Role(user["role"])
    has_manage_any = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    # Build query with stream join
    query = (
        sa.select(
            videos,
            live_streams.c.id.label("stream_id"),
            live_streams.c.slug.label("stream_slug"),
            live_streams.c.title.label("stream_title"),
            live_streams.c.owner_id.label("stream_owner_id"),
        )
        .select_from(
            videos.outerjoin(
                live_streams,
                live_streams.c.vod_video_id == videos.c.id
            )
        )
        .where(videos.c.deleted_at.is_(None))  # Exclude soft-deleted
    )

    # Non-admins only see VODs they own or linked to their streams
    if not has_manage_any:
        query = query.where(
            sa.or_(
                videos.c.owner_id == user["id"],
                live_streams.c.owner_id == user["id"],
            )
        )

    # Optional status filter
    if status and status in ("pending", "processing", "ready", "failed"):
        query = query.where(videos.c.status == status)

    # Add window function for total count (Issue #544)
    # This computes total in a single query, avoiding a separate COUNT query
    # Note: Requires index on (deleted_at, owner_id, created_at) for optimal performance
    query = query.add_columns(
        sa.func.count().over().label("total_count")
    )

    # Fetch page with total count included
    query = query.order_by(videos.c.created_at.desc())
    query = query.offset(offset).limit(page_size)
    rows = await fetch_all_with_retry(query)

    # Extract total from first row (all rows have same total_count)
    # If rows is empty (e.g., page past last page), fallback to COUNT query
    # to avoid returning incorrect total=0 (Copilot review feedback)
    if rows:
        total = rows[0]["total_count"]
    else:
        # Fallback COUNT for empty pages - only runs for invalid page requests
        count_query = (
            sa.select(sa.func.count())
            .select_from(
                videos.outerjoin(
                    live_streams,
                    live_streams.c.vod_video_id == videos.c.id
                )
            )
            .where(videos.c.deleted_at.is_(None))
        )
        if not has_manage_any:
            count_query = count_query.where(
                sa.or_(
                    videos.c.owner_id == user["id"],
                    live_streams.c.owner_id == user["id"],
                )
            )
        if status and status in ("pending", "processing", "ready", "failed"):
            count_query = count_query.where(videos.c.status == status)
        total = await fetch_val_with_retry(count_query) or 0

    vods = [vod_to_response(dict(row)) for row in rows]

    return StudioVODListResponse(
        vods=vods,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + len(vods)) < total,
    )


@router.get("/vods/{slug}")
@limiter.limit("60/minute")
async def get_vod(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
) -> StudioVODResponse:
    """Get details for a specific VOD."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    video = await verify_vod_access(slug, user)
    return vod_to_response(video)


@router.patch("/vods/{slug}")
@limiter.limit("30/minute")
async def update_vod(
    request: Request,
    slug: str,
    data: StudioVODUpdate,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
) -> StudioVODResponse:
    """Update a VOD's metadata."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    video = await verify_vod_access(slug, user)

    # Build update values
    update_values = {}
    if data.title is not None:
        update_values["title"] = data.title
    if data.description is not None:
        update_values["description"] = data.description
    if "category_id" in data.model_fields_set:
        update_values["category_id"] = data.category_id

    if not update_values:
        return vod_to_response(video)

    await db_execute_with_retry(
        videos.update()
        .where(videos.c.id == video["id"])
        .values(**update_values)
    )

    # Audit log
    log_audit(
        AuditAction.VOD_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="vod",
        resource_id=video["id"],
        resource_name=slug,
        details={
            "changes": update_values,
            "user_id": user["id"],
        },
        request_id=get_request_id(request),
    )

    # Fetch updated video
    updated = await verify_vod_access(slug, user)
    return vod_to_response(updated)


@router.delete("/vods/{slug}")
@limiter.limit("10/minute")
async def delete_vod(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
) -> dict:
    """
    Soft-delete a VOD.

    Sets deleted_at timestamp rather than removing the record.
    VOD can be restored by an admin if needed.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    video = await verify_vod_access(slug, user)

    now = datetime.now(timezone.utc)

    await db_execute_with_retry(
        videos.update()
        .where(videos.c.id == video["id"])
        .values(deleted_at=now)
    )

    # Also unlink from stream if linked
    if video.get("stream_id"):
        await db_execute_with_retry(
            live_streams.update()
            .where(live_streams.c.id == video["stream_id"])
            .values(vod_video_id=None)
        )

    # Audit log
    log_audit(
        AuditAction.VOD_DELETE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="vod",
        resource_id=video["id"],
        resource_name=slug,
        details={
            "user_id": user["id"],
            "stream_id": video.get("stream_id"),
        },
        request_id=get_request_id(request),
    )

    logger.info(f"VOD {slug} deleted by user {user['id']}")

    return {"deleted": True, "slug": slug}


@router.get("/vods/{slug}/analytics")
@limiter.limit("30/minute")
async def get_vod_analytics(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
) -> StudioVODAnalyticsResponse:
    """Get analytics for a specific VOD."""
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    video = await verify_vod_access(slug, user)
    video_id = video["id"]

    # Get total sessions and watch time
    stats_query = sa.select(
        sa.func.count(playback_sessions.c.id).label("total_views"),
        sa.func.sum(playback_sessions.c.duration_watched).label("total_watch_time"),
        sa.func.avg(playback_sessions.c.duration_watched).label("avg_watch_time"),
        sa.func.sum(
            sa.case((playback_sessions.c.completed == True, 1), else_=0)  # noqa: E712
        ).label("completions"),
    ).where(playback_sessions.c.video_id == video_id)

    stats = await fetch_one_with_retry(stats_query)

    total_views = stats["total_views"] or 0
    total_watch_time = float(stats["total_watch_time"] or 0)
    avg_watch_time = float(stats["avg_watch_time"] or 0)
    completions = stats["completions"] or 0

    # Calculate completion rate
    completion_rate = (completions / total_views * 100) if total_views > 0 else 0

    # Get unique viewers
    unique_query = (
        sa.select(sa.func.count(sa.distinct(playback_sessions.c.viewer_id)))
        .where(playback_sessions.c.video_id == video_id)
        .where(playback_sessions.c.viewer_id.isnot(None))
    )
    unique_viewers = await database.fetch_val(unique_query) or 0

    # Get daily view history (last 30 days)
    view_history_query = (
        sa.select(
            sa.func.date(playback_sessions.c.started_at).label("date"),
            sa.func.count(playback_sessions.c.id).label("views"),
        )
        .where(playback_sessions.c.video_id == video_id)
        .where(
            playback_sessions.c.started_at >= sa.func.now() - sa.text("INTERVAL '30 days'")
        )
        .group_by(sa.func.date(playback_sessions.c.started_at))
        .order_by(sa.func.date(playback_sessions.c.started_at))
    )

    history_rows = await fetch_all_with_retry(view_history_query)
    view_history = [
        {"date": str(row["date"]), "views": row["views"]}
        for row in history_rows
    ]

    return StudioVODAnalyticsResponse(
        vod_id=video_id,
        total_views=total_views,
        unique_viewers=unique_viewers,
        total_watch_time_seconds=total_watch_time,
        average_watch_time_seconds=avg_watch_time,
        completion_rate=round(completion_rate, 2),
        view_history=view_history,
    )


@router.get("/vods/{slug}/download")
@limiter.limit("10/minute")
async def get_vod_download_url(
    request: Request,
    slug: str,
    user: dict = Depends(require_auth),
) -> StudioVODDownloadResponse:
    """
    Get a download URL for the VOD source file.

    Returns a time-limited signed URL for downloading the original video file.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    video = await verify_vod_access(slug, user)

    # Find the source file
    video_dir = VIDEOS_DIR / video["slug"]
    source_file = None

    # Look for original.* files
    for ext in [".mp4", ".mkv", ".webm", ".mov"]:
        candidate = video_dir / f"original{ext}"
        if candidate.exists():
            source_file = candidate
            break

    if not source_file:
        raise HTTPException(status_code=404, detail="Source file not found")

    # For now, return a direct path. In production, this could be a
    # signed URL with expiration for CDN/S3.
    # The download endpoint would verify the token server-side.
    expires_at = datetime.now(timezone.utc).replace(
        second=0, microsecond=0
    )
    # Expire in 1 hour
    from datetime import timedelta
    expires_at = expires_at + timedelta(hours=1)

    return StudioVODDownloadResponse(
        download_url=f"/videos/{video['slug']}/{source_file.name}",
        filename=f"{video['slug']}{source_file.suffix}",
        expires_at=expires_at,
    )


@router.post("/vods/{slug}/thumbnail")
@limiter.limit("10/minute")
async def upload_vod_thumbnail(
    request: Request,
    slug: str,
    file: UploadFile = File(...),
    user: dict = Depends(require_auth),
    _csrf: None = Depends(require_csrf),
) -> StudioVODResponse:
    """
    Upload a custom thumbnail for a VOD.

    Accepts JPEG or PNG images. Maximum size 5MB.
    Image will be resized to 1280x720 if larger.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    video = await verify_vod_access(slug, user)

    # Validate file type
    content_type = file.content_type or ""
    if content_type not in ("image/jpeg", "image/png"):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only JPEG and PNG images are accepted."
        )

    # Validate file size (5MB max)
    max_size = 5 * 1024 * 1024
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail="File too large. Maximum size is 5MB."
        )

    # Require PIL for secure image processing - fail closed if unavailable
    if not PIL_AVAILABLE:
        logger.error("PIL not available - cannot safely process thumbnail uploads")
        raise HTTPException(
            status_code=503,
            detail="Image processing unavailable. Please contact administrator."
        )

    # Save thumbnail with atomic write (temp file + rename)
    video_dir = VIDEOS_DIR / video["slug"]
    if not video_dir.exists():
        video_dir.mkdir(parents=True, exist_ok=True)

    thumbnail_path = video_dir / "thumbnail.jpg"
    thumbnail_temp = video_dir / f"thumbnail.{uuid.uuid4().hex}.tmp"

    # Maximum allowed dimensions - 5000x5000 is generous for thumbnails
    # Reduces worst-case memory from 300MB to 75MB per request
    MAX_IMAGE_DIMENSION = 5000

    try:
        img = Image.open(io.BytesIO(content))

        # Check dimensions before any pixel access to catch bombs early
        if img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION:
            raise HTTPException(
                status_code=400,
                detail=f"Image dimensions too large. Maximum is {MAX_IMAGE_DIMENSION}x{MAX_IMAGE_DIMENSION} pixels."
            )

        # Apply EXIF orientation before any other processing
        # This ensures the image displays correctly after EXIF metadata is stripped during re-encoding
        img = ImageOps.exif_transpose(img)

        # Convert to RGB if needed (for PNG with alpha or palette)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Resize if larger than target thumbnail size
        if img.width > 1280 or img.height > 720:
            img.thumbnail((1280, 720), Image.Resampling.LANCZOS)

        # Save to temp file first
        img.save(thumbnail_temp, "JPEG", quality=85)

        # Atomic rename (POSIX guarantees atomicity for same-filesystem renames)
        thumbnail_temp.rename(thumbnail_path)

    except HTTPException:
        raise
    except Image.DecompressionBombError:
        raise HTTPException(
            status_code=400,
            detail="Image file is too large (potential decompression bomb)."
        )
    except Exception as e:
        logger.error(f"Failed to process thumbnail for {slug}: {e}")
        raise HTTPException(status_code=500, detail="Failed to process thumbnail")
    finally:
        # Clean up temp file if it still exists (failed before rename)
        if thumbnail_temp.exists():
            try:
                thumbnail_temp.unlink()
            except Exception:
                pass  # Best effort cleanup

    # Update video to mark as custom thumbnail
    await db_execute_with_retry(
        videos.update()
        .where(videos.c.id == video["id"])
        .values(thumbnail_source="custom")
    )

    # Audit log
    log_audit(
        AuditAction.VOD_THUMBNAIL_UPLOAD,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="vod",
        resource_id=video["id"],
        resource_name=slug,
        details={
            "user_id": user["id"],
            "file_size": len(content),
            "content_type": content_type,
        },
        request_id=get_request_id(request),
    )

    logger.info(f"Custom thumbnail uploaded for VOD {slug} by user {user['id']}")

    # Return updated VOD
    updated = await verify_vod_access(slug, user)
    return vod_to_response(updated)
