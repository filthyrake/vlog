"""
Public API - serves the video browsing interface.
Runs on port 9000.
"""

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import sqlalchemy as sa
from fastapi import Cookie, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from api.common import (
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    check_health,
    get_real_ip,
    get_storage_status,
    rate_limit_exceeded_handler,
    validate_slug,
)
from api.database import (
    categories,
    configure_database,
    database,
    playback_sessions,
    quality_progress,
    tags,
    transcoding_jobs,
    transcriptions,
    video_qualities,
    video_tags,
    videos,
    viewers,
)
from api.db_retry import (
    DatabaseLockedError,
    db_execute_with_retry,
    fetch_all_with_retry,
    fetch_one_with_retry,
    fetch_val_with_retry,
)
from api.enums import DurationFilter, SortBy, SortOrder, TranscriptionStatus, VideoStatus
from api.errors import sanitize_error_message, sanitize_progress_error
from api.schemas import (
    CategoryResponse,
    PlaybackEnd,
    PlaybackHeartbeat,
    PlaybackSessionCreate,
    PlaybackSessionResponse,
    QualityProgressResponse,
    TagResponse,
    TranscodingProgressResponse,
    TranscriptionResponse,
    VideoListResponse,
    VideoQualityResponse,
    VideoResponse,
    VideoTagInfo,
)
from config import (
    CORS_ALLOWED_ORIGINS,
    NAS_STORAGE,
    PUBLIC_PORT,
    QUALITY_NAMES,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_PUBLIC_ANALYTICS,
    RATE_LIMIT_PUBLIC_DEFAULT,
    RATE_LIMIT_PUBLIC_VIDEOS_LIST,
    RATE_LIMIT_STORAGE_URL,
    SECURE_COOKIES,
    VIDEOS_DIR,
    WATERMARK_ENABLED,
    WATERMARK_IMAGE,
    WATERMARK_MAX_WIDTH_PERCENT,
    WATERMARK_OPACITY,
    WATERMARK_PADDING,
    WATERMARK_POSITION,
    WATERMARK_TEXT,
    WATERMARK_TEXT_COLOR,
    WATERMARK_TEXT_SIZE,
    WATERMARK_TYPE,
)

logger = logging.getLogger(__name__)

# Cached watermark settings (refreshed every 60 seconds)
_cached_watermark_settings: Dict[str, Any] = {}
_cached_watermark_settings_time: float = 0
_WATERMARK_SETTINGS_CACHE_TTL = 60  # Refresh every 60 seconds


async def get_watermark_settings() -> Dict[str, Any]:
    """
    Get watermark settings from database with caching and env var fallback.

    Settings are cached locally for 60 seconds to avoid database round-trips
    on every video page request. The cache is separate from the main
    SettingsService cache to minimize import overhead.

    Returns:
        Dict with keys:
        - enabled: Whether watermark is enabled
        - type: "image" or "text"
        - image: Path to watermark image (for image type)
        - text: Watermark text (for text type)
        - text_size: Font size for text watermark
        - text_color: Color for text watermark
        - position: Watermark position
        - opacity: Watermark opacity (0.0-1.0)
        - padding: Padding from edge in pixels
        - max_width_percent: Max width as percentage of video

    Falls back to environment variables (via config.py) if database is unavailable.
    """
    global _cached_watermark_settings, _cached_watermark_settings_time

    now = time.time()
    if _cached_watermark_settings and (now - _cached_watermark_settings_time) < _WATERMARK_SETTINGS_CACHE_TTL:
        return _cached_watermark_settings

    try:
        from api.settings_service import get_settings_service

        service = get_settings_service()

        # Fetch settings with fallback to config values
        settings = {
            "enabled": await service.get("watermark.enabled", WATERMARK_ENABLED),
            "type": await service.get("watermark.type", WATERMARK_TYPE),
            "image": await service.get("watermark.image", WATERMARK_IMAGE),
            "text": await service.get("watermark.text", WATERMARK_TEXT),
            "text_size": await service.get("watermark.text_size", WATERMARK_TEXT_SIZE),
            "text_color": await service.get("watermark.text_color", WATERMARK_TEXT_COLOR),
            "position": await service.get("watermark.position", WATERMARK_POSITION),
            "opacity": await service.get("watermark.opacity", WATERMARK_OPACITY),
            "padding": await service.get("watermark.padding", WATERMARK_PADDING),
            "max_width_percent": await service.get("watermark.max_width_percent", WATERMARK_MAX_WIDTH_PERCENT),
        }

        _cached_watermark_settings = settings
        _cached_watermark_settings_time = now
    except Exception as e:
        # Fall back to config values on error
        logger.debug(f"Failed to get watermark settings from DB, using env vars: {e}")
        _cached_watermark_settings = {
            "enabled": WATERMARK_ENABLED,
            "type": WATERMARK_TYPE,
            "image": WATERMARK_IMAGE,
            "text": WATERMARK_TEXT,
            "text_size": WATERMARK_TEXT_SIZE,
            "text_color": WATERMARK_TEXT_COLOR,
            "position": WATERMARK_POSITION,
            "opacity": WATERMARK_OPACITY,
            "padding": WATERMARK_PADDING,
            "max_width_percent": WATERMARK_MAX_WIDTH_PERCENT,
        }
        _cached_watermark_settings_time = now

    return _cached_watermark_settings


def reset_watermark_settings_cache() -> None:
    """Reset the cached watermark settings. Useful for testing."""
    global _cached_watermark_settings, _cached_watermark_settings_time
    _cached_watermark_settings = {}
    _cached_watermark_settings_time = 0


# Cached CDN settings (refreshed every 60 seconds)
_cached_cdn_settings: Dict[str, Any] = {}
_cached_cdn_settings_time: float = 0
_CDN_SETTINGS_CACHE_TTL = 60  # Refresh every 60 seconds


async def get_cdn_settings() -> Dict[str, Any]:
    """
    Get CDN settings from database with caching.

    Settings are cached locally for 60 seconds to avoid database round-trips
    on every video request.

    Returns:
        Dict with keys:
        - enabled: Whether CDN is enabled for video streaming
        - base_url: CDN base URL (e.g., https://cdn.example.com)
    """
    global _cached_cdn_settings, _cached_cdn_settings_time

    now = time.time()
    if _cached_cdn_settings and (now - _cached_cdn_settings_time) < _CDN_SETTINGS_CACHE_TTL:
        return _cached_cdn_settings

    try:
        from api.settings_service import get_settings_service

        service = get_settings_service()

        settings = {
            "enabled": await service.get("cdn.enabled", False),
            "base_url": await service.get("cdn.base_url", ""),
        }

        _cached_cdn_settings = settings
        _cached_cdn_settings_time = now
    except Exception as e:
        logger.debug(f"Failed to get CDN settings from DB: {e}")
        _cached_cdn_settings = {"enabled": False, "base_url": ""}
        _cached_cdn_settings_time = now

    return _cached_cdn_settings


async def get_video_url_prefix() -> str:
    """
    Get the URL prefix for video streaming content.

    Returns CDN base URL if CDN is enabled and configured,
    otherwise returns empty string for relative URLs.

    Only video streaming content (manifests, segments) should use this.
    Thumbnails, captions, and other assets use direct origin URLs.

    Note: Trailing slashes are stripped to avoid double-slash URLs.
    """
    cdn_settings = await get_cdn_settings()
    if cdn_settings["enabled"] and cdn_settings["base_url"]:
        # Strip trailing slash to avoid double-slash in URLs
        return cdn_settings["base_url"].rstrip("/")
    return ""


def reset_cdn_settings_cache() -> None:
    """Reset the cached CDN settings. Useful for testing."""
    global _cached_cdn_settings, _cached_cdn_settings_time
    _cached_cdn_settings = {}
    _cached_cdn_settings_time = 0


# Initialize rate limiter
# Uses in-memory storage by default, can be configured to use Redis
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    # Warn about in-memory rate limiting limitations
    if RATE_LIMIT_ENABLED and RATE_LIMIT_STORAGE_URL == "memory://":
        logger.warning(
            "Rate limiting is using in-memory storage. "
            "For production deployments with multiple instances, configure Redis: "
            "VLOG_RATE_LIMIT_STORAGE_URL=redis://localhost:6379"
        )
    await database.connect()
    await configure_database()
    yield
    await database.disconnect()


app = FastAPI(title="VLog", description="Self-hosted video platform", lifespan=lifespan)

# Register rate limiter with the app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


@app.exception_handler(DatabaseLockedError)
async def database_locked_handler(request: Request, exc: DatabaseLockedError):
    """Handle database locked errors with a 503 response."""
    logger.warning(f"Database locked error: {exc}")
    return JSONResponse(
        status_code=503,
        content={"detail": "Database temporarily unavailable, please retry"},
        headers={"Retry-After": "1"},
    )


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)

# CORS middleware for HLS playback and analytics
# If CORS_ALLOWED_ORIGINS is empty, allow same-origin only (no CORS headers)
# Note: allow_credentials=True requires specific origins, not wildcards
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS if CORS_ALLOWED_ORIGINS else [],
    allow_credentials=bool(CORS_ALLOWED_ORIGINS),  # Only enable with explicit origins
    allow_methods=["GET", "HEAD", "OPTIONS", "POST"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges", "X-Request-ID"],
)


# Custom static files handler with proper headers for HLS/DASH/CMAF streaming
class StreamingStaticFiles(StaticFiles):
    """
    Static files handler for video streaming content.

    Supports:
    - Legacy HLS with MPEG-TS segments (.ts)
    - Modern CMAF with fMP4 segments (.m4s, init.mp4)
    - HLS playlists (.m3u8)
    - DASH manifests (.mpd)

    Provides appropriate MIME types and cache headers for each file type.
    """

    async def get_response(self, path: str, scope) -> Response:
        try:
            response = await super().get_response(path, scope)

            # CORS headers for cross-origin playback (needed for some players)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Expose-Headers"] = "Content-Length,Content-Range"

            # MIME types and cache headers based on file type
            if path.endswith(".ts"):
                # Legacy MPEG-TS segments - cache aggressively (immutable)
                response.headers["Content-Type"] = "video/mp2t"
                response.headers["Cache-Control"] = "public, max-age=31536000"

            elif path.endswith(".m4s"):
                # CMAF media segments - cache aggressively (immutable)
                response.headers["Content-Type"] = "video/iso.segment"
                response.headers["Cache-Control"] = "public, max-age=31536000"

            elif path.endswith("init.mp4"):
                # CMAF initialization segments - cache aggressively
                response.headers["Content-Type"] = "video/mp4"
                response.headers["Cache-Control"] = "public, max-age=31536000"

            elif path.endswith(".m3u8"):
                # HLS playlists - no cache to allow live updates
                response.headers["Content-Type"] = "application/vnd.apple.mpegurl"
                response.headers["Cache-Control"] = "no-cache"

            elif path.endswith(".mpd"):
                # DASH manifests - no cache to allow live updates
                response.headers["Content-Type"] = "application/dash+xml"
                response.headers["Cache-Control"] = "no-cache"

            elif path.endswith("thumbnail.jpg") or "/frames/" in path:
                # Short cache for thumbnails and frame images
                response.headers["Cache-Control"] = "public, max-age=60, must-revalidate"

            return response

        except (OSError, PermissionError) as e:
            # Storage unavailable - return 503 with helpful message
            logger.warning(f"Storage unavailable for streaming file {path}: {e}")
            return JSONResponse(
                status_code=503,
                content={"detail": "Video storage temporarily unavailable. Please try again later."},
                headers={"Retry-After": "30"},
            )


# Backwards compatibility alias
HLSStaticFiles = StreamingStaticFiles


# Serve video files (HLS segments, playlists, thumbnails)
# Skip in test mode since CI doesn't have the storage directory
if not os.environ.get("VLOG_TEST_MODE"):
    app.mount("/videos", HLSStaticFiles(directory=str(VIDEOS_DIR)), name="videos")

# Serve static web files
WEB_DIR = Path(__file__).parent.parent / "web" / "public"
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the main page."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring and load balancers.

    Returns detailed status of database and storage health.
    Returns 503 if any critical component is unhealthy.
    """
    result = await check_health()
    storage_status = get_storage_status()

    return JSONResponse(
        status_code=result["status_code"],
        content={
            "status": "healthy" if result["healthy"] else "unhealthy",
            "checks": result["checks"],
            "storage": {
                "healthy": storage_status["healthy"],
                "last_check": storage_status["last_check"],
                "error": storage_status["last_error"],
            },
        },
    )


@app.get("/watch/{slug}", response_class=HTMLResponse)
async def watch_page(slug: str):
    """Serve the watch page."""
    return FileResponse(WEB_DIR / "watch.html")


@app.get("/category/{slug}", response_class=HTMLResponse)
async def category_page(slug: str):
    """Serve the category page."""
    return FileResponse(WEB_DIR / "category.html")


@app.get("/tag/{slug}", response_class=HTMLResponse)
async def tag_page(slug: str):
    """Serve the tag page."""
    return FileResponse(WEB_DIR / "tag.html")


async def get_video_tags(video_ids: List[int]) -> dict:
    """Get tags for a list of video IDs. Returns a dict of video_id -> list of tags."""
    if not video_ids:
        return {}

    query = (
        sa.select(
            video_tags.c.video_id,
            tags.c.id,
            tags.c.name,
            tags.c.slug,
        )
        .select_from(video_tags.join(tags, video_tags.c.tag_id == tags.c.id))
        .where(video_tags.c.video_id.in_(video_ids))
        .order_by(tags.c.name)
    )

    rows = await fetch_all_with_retry(query)

    result = {}
    for row in rows:
        video_id = row["video_id"]
        if video_id not in result:
            result[video_id] = []
        result[video_id].append(VideoTagInfo(id=row["id"], name=row["name"], slug=row["slug"]))

    return result


@app.get("/api/videos")
@limiter.limit(RATE_LIMIT_PUBLIC_VIDEOS_LIST)
async def list_videos(
    request: Request,
    category: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    duration: Optional[str] = Query(
        default=None, description="Filter by duration: short (<5min), medium (5-20min), long (>20min). Comma-separated."
    ),
    quality: Optional[str] = Query(
        default=None, description="Filter by available quality: 2160p, 1440p, 1080p, 720p, 480p, 360p. Comma-separated."
    ),
    date_from: Optional[datetime] = Query(
        default=None, description="Filter videos published from this date (ISO 8601)"
    ),
    date_to: Optional[datetime] = Query(
        default=None, description="Filter videos published until this date (ISO 8601)"
    ),
    has_transcription: Optional[bool] = Query(
        default=None, description="Filter by transcription availability (true/false)"
    ),
    sort: Optional[str] = Query(default=None, description="Sort by: relevance, date, duration, views, title"),
    order: Optional[str] = Query(default="desc", description="Sort order: asc or desc"),
    limit: int = Query(default=50, ge=1, le=100, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
) -> List[VideoListResponse]:
    """
    List all published videos with advanced filtering and sorting.

    Filters:
    - category: Filter by category slug
    - tag: Filter by tag slug
    - search: Full-text search in title and description
    - duration: short (<5min), medium (5-20min), long (>20min)
    - quality: Filter by available quality variants (e.g., 1080p, 2160p)
    - date_from/date_to: Filter by publication date range
    - has_transcription: Filter videos with/without transcriptions

    Sorting:
    - relevance (default for text searches), date, duration, views, title
    - order: asc (ascending) or desc (descending)
    """
    # Base query with view count for sorting
    query = (
        sa.select(
            videos.c.id,
            videos.c.title,
            videos.c.slug,
            videos.c.description,
            videos.c.category_id,
            videos.c.duration,
            videos.c.status,
            videos.c.created_at,
            videos.c.published_at,
            videos.c.thumbnail_source,
            videos.c.thumbnail_timestamp,
            categories.c.name.label("category_name"),
            sa.func.count(sa.distinct(playback_sessions.c.id)).label("view_count"),
        )
        .select_from(
            videos.outerjoin(categories, videos.c.category_id == categories.c.id).outerjoin(
                playback_sessions, videos.c.id == playback_sessions.c.video_id
            )
        )
        .where(videos.c.status == VideoStatus.READY)
        .where(videos.c.deleted_at.is_(None))  # Exclude soft-deleted videos
        .where(videos.c.published_at.is_not(None))  # Only show published videos
        .group_by(
            videos.c.id,
            videos.c.title,
            videos.c.slug,
            videos.c.description,
            videos.c.category_id,
            videos.c.duration,
            videos.c.status,
            videos.c.created_at,
            videos.c.published_at,
            categories.c.name,
        )
    )

    # Category filter
    if category:
        query = query.where(categories.c.slug == category)

    # Tag filter
    if tag:
        tag_subquery = (
            sa.select(video_tags.c.video_id)
            .select_from(video_tags.join(tags, video_tags.c.tag_id == tags.c.id))
            .where(tags.c.slug == tag)
        )
        query = query.where(videos.c.id.in_(tag_subquery))

    # Text search
    if search:
        search_term = f"%{search}%"
        query = query.where(
            sa.or_(
                videos.c.title.ilike(search_term),
                videos.c.description.ilike(search_term),
            )
        )

    # Duration filter
    if duration:
        duration_filters = [d.strip().lower() for d in duration.split(",")]
        duration_conditions = []
        valid_durations = {DurationFilter.SHORT.value, DurationFilter.MEDIUM.value, DurationFilter.LONG.value}
        for df in duration_filters:
            if df not in valid_durations:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid duration value: '{df}'. Valid values are: short, medium, long"
                )
            if df == DurationFilter.SHORT.value:
                duration_conditions.append(videos.c.duration < 300)  # < 5 minutes
            elif df == DurationFilter.MEDIUM.value:
                duration_conditions.append(sa.and_(videos.c.duration >= 300, videos.c.duration <= 1200))  # 5-20 minutes
            elif df == DurationFilter.LONG.value:
                duration_conditions.append(videos.c.duration > 1200)  # > 20 minutes
        if duration_conditions:
            query = query.where(sa.or_(*duration_conditions))

    # Quality filter
    if quality:
        quality_filters = [q.strip().lower() for q in quality.split(",")]
        # Validate quality values against allowed qualities
        valid_quality_filters = [q for q in quality_filters if q in QUALITY_NAMES]
        if valid_quality_filters:
            # Video must have at least one of the requested qualities
            quality_subquery = (
                sa.select(video_qualities.c.video_id)
                .where(video_qualities.c.quality.in_(valid_quality_filters))
                .distinct()
            )
            query = query.where(videos.c.id.in_(quality_subquery))

    # Date range filter
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=400,
            detail="Invalid date range: date_from must be before or equal to date_to"
        )
    if date_from:
        query = query.where(videos.c.published_at >= date_from)
    if date_to:
        query = query.where(videos.c.published_at <= date_to)

    # Transcription filter
    if has_transcription is not None:
        if has_transcription:
            # Has completed transcription
            transcription_subquery = (
                sa.select(transcriptions.c.video_id)
                .where(transcriptions.c.status == TranscriptionStatus.COMPLETED)
                .distinct()
            )
            query = query.where(videos.c.id.in_(transcription_subquery))
        else:
            # Does not have completed transcription
            transcription_subquery = (
                sa.select(transcriptions.c.video_id)
                .where(transcriptions.c.status == TranscriptionStatus.COMPLETED)
                .distinct()
            )
            query = query.where(videos.c.id.notin_(transcription_subquery))

    # Sorting
    # Validate and convert sort parameter to enum
    if sort:
        try:
            sort_by = SortBy(sort.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort value: '{sort}'. Valid values are: relevance, date, duration, views, title"
            )
    else:
        sort_by = SortBy.RELEVANCE if search else SortBy.DATE

    # Validate order parameter
    order_lower = order.lower()
    if order_lower not in ("asc", "desc"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order value: '{order}'. Valid values are: asc, desc"
        )
    sort_order = SortOrder.DESC if order_lower == "desc" else SortOrder.ASC

    if sort_by == SortBy.DATE:
        order_col = videos.c.published_at.desc() if sort_order == SortOrder.DESC else videos.c.published_at.asc()
        query = query.order_by(order_col)
    elif sort_by == SortBy.DURATION:
        order_col = videos.c.duration.desc() if sort_order == SortOrder.DESC else videos.c.duration.asc()
        query = query.order_by(order_col)
    elif sort_by == SortBy.VIEWS:
        # Use column label with desc()/asc() for type safety
        view_count_col = sa.literal_column("view_count")
        order_col = view_count_col.desc() if sort_order == SortOrder.DESC else view_count_col.asc()
        query = query.order_by(order_col)
    elif sort_by == SortBy.TITLE:
        # Case-insensitive sorting for better alphabetical ordering
        title_lower = sa.func.lower(videos.c.title)
        order_col = title_lower.asc() if sort_order == SortOrder.ASC else title_lower.desc()
        query = query.order_by(order_col)
    elif sort_by == SortBy.RELEVANCE:
        # For relevance, use published date as fallback (most recent first)
        query = query.order_by(videos.c.published_at.desc())
    else:
        # Default to date descending
        query = query.order_by(videos.c.published_at.desc())

    # Apply pagination after sorting
    query = query.limit(limit).offset(offset)

    rows = await fetch_all_with_retry(query)

    # Get tags for all videos in one query
    video_ids = [row["id"] for row in rows]
    video_tags_map = await get_video_tags(video_ids)

    def get_thumbnail_version(row):
        """Generate cache-busting version for thumbnail URL."""
        if row["thumbnail_timestamp"]:
            return int(row["thumbnail_timestamp"] * 1000)
        # Use hash of id + source for non-timestamp thumbnails
        source = row["thumbnail_source"] or "auto"
        return hash((row["id"], source)) % 1000000000

    return [
        VideoListResponse(
            id=row["id"],
            title=row["title"],
            slug=row["slug"],
            description=row["description"],
            category_id=row["category_id"],
            category_name=row["category_name"],
            duration=row["duration"],
            status=row["status"],
            created_at=row["created_at"],
            published_at=row["published_at"],
            thumbnail_url=f"/videos/{row['slug']}/thumbnail.jpg?v={get_thumbnail_version(row)}",
            thumbnail_source=row["thumbnail_source"] or "auto",
            thumbnail_timestamp=row["thumbnail_timestamp"],
            tags=video_tags_map.get(row["id"], []),
        )
        for row in rows
    ]


@app.get("/api/videos/{slug}")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_video(request: Request, slug: str) -> VideoResponse:
    """Get a single video by slug."""
    # Validate slug to prevent path traversal attacks
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="Invalid video slug")

    query = (
        sa.select(
            videos,
            categories.c.name.label("category_name"),
            categories.c.slug.label("category_slug"),
        )
        .select_from(videos.outerjoin(categories, videos.c.category_id == categories.c.id))
        .where(videos.c.slug == slug)
        .where(videos.c.deleted_at.is_(None))  # Exclude soft-deleted videos
        .where(videos.c.published_at.is_not(None))  # Only show published videos
    )

    row = await fetch_one_with_retry(query)
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get quality variants
    quality_query = video_qualities.select().where(video_qualities.c.video_id == row["id"])
    quality_rows = await fetch_all_with_retry(quality_query)

    qualities = [
        VideoQualityResponse(
            quality=q["quality"],
            width=q["width"],
            height=q["height"],
            bitrate=q["bitrate"],
        )
        for q in quality_rows
    ]

    # Get transcription status
    transcription_query = transcriptions.select().where(transcriptions.c.video_id == row["id"])
    transcription_row = await fetch_one_with_retry(transcription_query)

    captions_url = None
    transcription_status = None

    if transcription_row:
        transcription_status = transcription_row["status"]
        if transcription_row["status"] == TranscriptionStatus.COMPLETED and transcription_row["vtt_path"]:
            captions_url = f"/videos/{row['slug']}/captions.vtt"

    # Get tags for this video
    video_tags_map = await get_video_tags([row["id"]])
    video_tag_list = video_tags_map.get(row["id"], [])

    # Generate thumbnail version for cache busting
    thumb_version = None
    if row["status"] == VideoStatus.READY:
        if row["thumbnail_timestamp"]:
            thumb_version = int(row["thumbnail_timestamp"] * 1000)
        else:
            source = row["thumbnail_source"] or "auto"
            thumb_version = hash((row["id"], source)) % 1000000000

    # Get CDN URL prefix for video streaming content (Issue #222)
    video_url_prefix = await get_video_url_prefix()

    return VideoResponse(
        id=row["id"],
        title=row["title"],
        slug=row["slug"],
        description=row["description"],
        category_id=row["category_id"],
        category_name=row["category_name"],
        category_slug=row["category_slug"],
        duration=row["duration"],
        source_width=row["source_width"],
        source_height=row["source_height"],
        status=row["status"],
        error_message=sanitize_error_message(row["error_message"], context=f"video_slug={slug}"),
        created_at=row["created_at"],
        published_at=row["published_at"],
        thumbnail_url=(
            f"/videos/{row['slug']}/thumbnail.jpg?v={thumb_version}"
            if row["status"] == VideoStatus.READY
            else None
        ),
        thumbnail_source=row["thumbnail_source"] or "auto",
        thumbnail_timestamp=row["thumbnail_timestamp"],
        # Stream URLs use CDN if configured (Issue #222)
        stream_url=(
            f"{video_url_prefix}/videos/{row['slug']}/master.m3u8"
            if row["status"] == VideoStatus.READY
            else None
        ),
        # DASH URL only available for CMAF format videos
        dash_url=(
            f"{video_url_prefix}/videos/{row['slug']}/manifest.mpd"
            if row["status"] == VideoStatus.READY
            and row._mapping.get("streaming_format") == "cmaf"
            else None
        ),
        streaming_format=row._mapping.get("streaming_format", "hls_ts"),
        primary_codec=row._mapping.get("primary_codec", "h264"),
        captions_url=captions_url,
        transcription_status=transcription_status,
        qualities=qualities,
        tags=video_tag_list,
    )


@app.get("/api/videos/{slug}/progress")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_video_progress(request: Request, slug: str) -> TranscodingProgressResponse:
    """Get transcoding progress for a video."""
    # Validate slug to prevent path traversal attacks
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="Invalid video slug")

    # Get video by slug (exclude soft-deleted)
    video_query = videos.select().where(videos.c.slug == slug).where(videos.c.deleted_at.is_(None))
    video = await fetch_one_with_retry(video_query)

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # If video is ready or failed, return simple status
    if video["status"] in [VideoStatus.READY, VideoStatus.FAILED]:
        return TranscodingProgressResponse(
            status=video["status"],
            progress_percent=100 if video["status"] == VideoStatus.READY else 0,
            last_error=sanitize_progress_error(video["error_message"])
            if video["status"] == VideoStatus.FAILED
            else None,
        )

    # If pending, return basic pending status
    if video["status"] == VideoStatus.PENDING:
        return TranscodingProgressResponse(
            status=VideoStatus.PENDING,
            progress_percent=0,
        )

    # Get job info for processing videos
    job_query = transcoding_jobs.select().where(transcoding_jobs.c.video_id == video["id"])
    job = await fetch_one_with_retry(job_query)

    if not job:
        return TranscodingProgressResponse(
            status=video["status"],
            progress_percent=0,
        )

    # Get quality progress
    quality_query = quality_progress.select().where(quality_progress.c.job_id == job["id"])
    quality_rows = await fetch_all_with_retry(quality_query)

    qualities = [
        QualityProgressResponse(
            name=q["quality"],
            status=q["status"],
            progress=q["progress_percent"] or 0,
        )
        for q in quality_rows
    ]

    return TranscodingProgressResponse(
        status=video["status"],
        current_step=job["current_step"],
        progress_percent=job["progress_percent"] or 0,
        qualities=qualities,
        attempt=job["attempt_number"] or 1,
        max_attempts=job["max_attempts"] or 3,
        started_at=job["started_at"],
        last_error=sanitize_progress_error(job["last_error"]),
    )


@app.get("/api/videos/{slug}/transcript")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_transcript(request: Request, slug: str) -> TranscriptionResponse:
    """Get transcription status and text for a video."""
    # Validate slug to prevent path traversal attacks
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="Invalid video slug")

    # Get video by slug (exclude soft-deleted)
    video_query = videos.select().where(videos.c.slug == slug).where(videos.c.deleted_at.is_(None))
    video = await fetch_one_with_retry(video_query)

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get transcription record
    transcription_query = transcriptions.select().where(transcriptions.c.video_id == video["id"])
    transcription = await fetch_one_with_retry(transcription_query)

    if not transcription:
        return TranscriptionResponse(status=TranscriptionStatus.NONE)

    vtt_url = None
    if transcription["status"] == TranscriptionStatus.COMPLETED and transcription["vtt_path"]:
        vtt_url = f"/videos/{slug}/captions.vtt"

    return TranscriptionResponse(
        status=transcription["status"],
        language=transcription["language"],
        text=transcription["transcript_text"],
        vtt_url=vtt_url,
        word_count=transcription["word_count"],
        duration_seconds=transcription["duration_seconds"],
        started_at=transcription["started_at"],
        completed_at=transcription["completed_at"],
        error_message=sanitize_error_message(transcription["error_message"], context=f"video_slug={slug}"),
    )


@app.get("/api/categories")
@limiter.limit(RATE_LIMIT_PUBLIC_VIDEOS_LIST)
async def list_categories(request: Request) -> List[CategoryResponse]:
    """List all categories with video counts."""
    query = sa.text("""
        SELECT c.*, COUNT(v.id) as video_count
        FROM categories c
        LEFT JOIN videos v ON v.category_id = c.id
            AND v.status = 'ready'
            AND v.deleted_at IS NULL
            AND v.published_at IS NOT NULL
        GROUP BY c.id
        ORDER BY c.name
    """)

    rows = await fetch_all_with_retry(query)

    return [
        CategoryResponse(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            description=row["description"] or "",
            created_at=row["created_at"],
            video_count=row["video_count"],
        )
        for row in rows
    ]


@app.get("/api/categories/{slug}")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_category(request: Request, slug: str) -> CategoryResponse:
    """Get a single category by slug."""
    # Validate slug to prevent path traversal attacks
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="Invalid category slug")

    query = categories.select().where(categories.c.slug == slug)
    row = await fetch_one_with_retry(query)
    if not row:
        raise HTTPException(status_code=404, detail="Category not found")

    # Get video count (only published, non-deleted)
    count_query = (
        sa.select(sa.func.count())
        .select_from(videos)
        .where(
            sa.and_(
                videos.c.category_id == row["id"],
                videos.c.status == VideoStatus.READY,
                videos.c.deleted_at.is_(None),
                videos.c.published_at.is_not(None),
            )
        )
    )
    count = await fetch_val_with_retry(count_query)

    return CategoryResponse(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"] or "",
        created_at=row["created_at"],
        video_count=count or 0,
    )


@app.get("/api/tags")
@limiter.limit(RATE_LIMIT_PUBLIC_VIDEOS_LIST)
async def list_tags(request: Request) -> List[TagResponse]:
    """List all tags with video counts."""
    query = sa.text("""
        SELECT t.*, COUNT(vt.video_id) as video_count
        FROM tags t
        LEFT JOIN video_tags vt ON vt.tag_id = t.id
        LEFT JOIN videos v ON v.id = vt.video_id
            AND v.status = 'ready'
            AND v.deleted_at IS NULL
            AND v.published_at IS NOT NULL
        GROUP BY t.id
        ORDER BY t.name
    """)

    rows = await fetch_all_with_retry(query)

    return [
        TagResponse(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            created_at=row["created_at"],
            video_count=row["video_count"],
        )
        for row in rows
    ]


@app.get("/api/tags/{slug}")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_tag(request: Request, slug: str) -> TagResponse:
    """Get a single tag by slug."""
    # Validate slug to prevent path traversal attacks
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="Invalid tag slug")

    query = tags.select().where(tags.c.slug == slug)
    row = await fetch_one_with_retry(query)
    if not row:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Get video count (only count published, non-deleted videos)
    count_query = (
        sa.select(sa.func.count(sa.distinct(videos.c.id)))
        .select_from(video_tags.join(videos, videos.c.id == video_tags.c.video_id))
        .where(video_tags.c.tag_id == row["id"])
        .where(videos.c.status == VideoStatus.READY)
        .where(videos.c.deleted_at.is_(None))
        .where(videos.c.published_at.is_not(None))
    )
    count = await fetch_val_with_retry(count_query)

    return TagResponse(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        created_at=row["created_at"],
        video_count=count or 0,
    )


# ============================================================================
# Watermark Configuration
# ============================================================================


@app.get("/api/config/watermark")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_watermark_config(request: Request):
    """
    Get watermark configuration for client-side overlay.

    Returns watermark settings if enabled, or enabled=false if disabled.
    Supports two watermark types:
    - "image": Logo/image overlay (image_url points to /watermark/image)
    - "text": Text overlay with custom font size and color
    """
    # Get watermark settings from database with caching
    settings = await get_watermark_settings()

    if not settings["enabled"]:
        return {"enabled": False}

    # Check watermark type
    if settings["type"] == "text":
        # Text watermark
        if not settings["text"]:
            return {"enabled": False}

        return {
            "enabled": True,
            "type": "text",
            "text": settings["text"],
            "text_size": settings["text_size"],
            "text_color": settings["text_color"],
            "position": settings["position"],
            "opacity": settings["opacity"],
            "padding": settings["padding"],
        }
    else:
        # Image watermark (default)
        if not settings["image"]:
            return {"enabled": False}

        # Verify watermark image exists
        watermark_path = NAS_STORAGE / settings["image"]
        if not watermark_path.exists():
            logger.warning(f"Watermark image not found: {watermark_path}")
            return {"enabled": False}

        return {
            "enabled": True,
            "type": "image",
            "image_url": "/watermark/image",
            "position": settings["position"],
            "opacity": settings["opacity"],
            "padding": settings["padding"],
            "max_width_percent": settings["max_width_percent"],
        }


@app.get("/watermark/image")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_watermark_image(request: Request):
    """Serve the watermark image file."""
    # Get watermark settings from database with caching
    settings = await get_watermark_settings()

    if not settings["enabled"] or not settings["image"]:
        raise HTTPException(status_code=404, detail="Watermark not configured")

    watermark_path = NAS_STORAGE / settings["image"]
    if not watermark_path.exists():
        raise HTTPException(status_code=404, detail="Watermark image not found")

    # Determine content type from extension
    ext = watermark_path.suffix.lower()
    content_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".gif": "image/gif",
    }
    content_type = content_types.get(ext, "application/octet-stream")

    return FileResponse(
        watermark_path,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},  # Cache for 1 day
    )


# ============================================================================
# Analytics Endpoints
# ============================================================================


@app.post("/api/analytics/session")
@limiter.limit(RATE_LIMIT_PUBLIC_ANALYTICS)
async def start_analytics_session(
    request: Request,
    data: PlaybackSessionCreate,
    response: Response,
    vlog_viewer: Optional[str] = Cookie(default=None),
) -> PlaybackSessionResponse:
    """
    Start a new playback session for tracking.

    Uses a persistent viewer cookie to track unique visitors across sessions.
    Creates/updates viewer record and links playback session to viewer.
    """
    # Verify video exists and is accessible
    video = await fetch_one_with_retry(
        videos.select().where(
            videos.c.id == data.video_id,
            videos.c.status == VideoStatus.READY,
            videos.c.deleted_at.is_(None),
        )
    )
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    session_token = str(uuid.uuid4())
    viewer_id = None

    # Get or create viewer from cookie
    if vlog_viewer:
        # Look up existing viewer
        viewer = await fetch_one_with_retry(viewers.select().where(viewers.c.session_id == vlog_viewer))
        if viewer:
            viewer_id = viewer["id"]
            # Update last_seen timestamp
            await db_execute_with_retry(
                viewers.update().where(viewers.c.id == viewer_id).values(last_seen=datetime.now(timezone.utc))
            )

    # If no valid viewer cookie, create new viewer
    if viewer_id is None:
        new_viewer_session = str(uuid.uuid4())
        viewer_id = await db_execute_with_retry(
            viewers.insert().values(
                session_id=new_viewer_session,
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
            )
        )
        # Set viewer cookie (expires in 1 year)
        response.set_cookie(
            key="vlog_viewer",
            value=new_viewer_session,
            max_age=365 * 24 * 60 * 60,  # 1 year
            httponly=True,
            samesite="lax",
            secure=SECURE_COOKIES,
        )

    # Create playback session linked to viewer
    await db_execute_with_retry(
        playback_sessions.insert().values(
            video_id=data.video_id,
            viewer_id=viewer_id,
            session_token=session_token,
            started_at=datetime.now(timezone.utc),
            quality_used=data.quality,
        )
    )

    return PlaybackSessionResponse(session_token=session_token)


@app.post("/api/analytics/heartbeat")
@limiter.limit(RATE_LIMIT_PUBLIC_ANALYTICS)
async def analytics_heartbeat(request: Request, data: PlaybackHeartbeat):
    """Update playback session with current progress."""
    # Find the session
    query = playback_sessions.select().where(playback_sessions.c.session_token == data.session_token)
    session = await fetch_one_with_retry(query)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Calculate time since last update (heartbeats come every ~30s)
    duration_increment = 30.0 if data.playing else 0.0

    # Update session (handle None values from fresh sessions)
    current_duration = session["duration_watched"] or 0.0
    current_max_position = session["max_position"] or 0.0
    new_duration = current_duration + duration_increment
    new_max_position = max(current_max_position, data.position)

    update_values = {
        "duration_watched": new_duration,
        "max_position": new_max_position,
    }

    if data.quality:
        update_values["quality_used"] = data.quality

    await db_execute_with_retry(
        playback_sessions.update()
        .where(playback_sessions.c.session_token == data.session_token)
        .values(**update_values)
    )

    return {"status": "ok"}


@app.post("/api/analytics/end")
@limiter.limit(RATE_LIMIT_PUBLIC_ANALYTICS)
async def end_analytics_session(request: Request, data: PlaybackEnd):
    """End a playback session."""
    # Find the session
    query = playback_sessions.select().where(playback_sessions.c.session_token == data.session_token)
    session = await fetch_one_with_retry(query)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get video duration to determine if completed
    video_query = videos.select().where(videos.c.id == session["video_id"])
    video = await fetch_one_with_retry(video_query)

    completed = data.completed
    if video and video["duration"] > 0:
        # Mark as completed if watched >= 90%
        percent_watched = data.position / video["duration"]
        if percent_watched >= 0.9:
            completed = True

    # Final update (handle None values from fresh sessions)
    current_max_position = session["max_position"] or 0.0
    await db_execute_with_retry(
        playback_sessions.update()
        .where(playback_sessions.c.session_token == data.session_token)
        .values(
            ended_at=datetime.now(timezone.utc),
            max_position=max(current_max_position, data.position),
            completed=completed,
        )
    )

    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PUBLIC_PORT)
