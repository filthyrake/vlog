"""
Public API - serves the video browsing interface.
Runs on port 9000.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import sqlalchemy as sa
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from api.analytics_cache import AnalyticsCache
from api.common import (
    HTTPMetricsMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    check_health,
    get_real_ip,
    get_storage_status,
    rate_limit_exceeded_handler,
    require_storage_available,
    validate_slug,
)
from api.database import (
    categories,
    chapters,
    configure_database,
    custom_field_definitions,
    database,
    playback_sessions,
    playlists,
    quality_progress,
    tags,
    transcoding_jobs,
    transcriptions,
    video_custom_fields,
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
from api.metrics import VIDEOS_WATCH_TIME_SECONDS_TOTAL
from api.pagination import encode_cursor, validate_cursor
from api.schemas import (
    CategoryResponse,
    ChapterInfo,
    PaginatedVideoListResponse,
    PlaybackEnd,
    PlaybackHeartbeat,
    PlaybackSessionCreate,
    PlaybackSessionResponse,
    PlaylistDetailResponse,
    PlaylistListResponse,
    PlaylistResponse,
    PlaylistVideoInfo,
    QualityProgressResponse,
    SpriteSheetInfo,
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
    DOWNLOADS_ALLOW_ORIGINAL,
    DOWNLOADS_ALLOW_TRANSCODED,
    DOWNLOADS_ENABLED,
    DOWNLOADS_MAX_CONCURRENT,
    DOWNLOADS_RATE_LIMIT_PER_HOUR,
    NAS_STORAGE,
    PUBLIC_PORT,
    QUALITY_NAMES,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_PUBLIC_ANALYTICS,
    RATE_LIMIT_PUBLIC_DEFAULT,
    RATE_LIMIT_PUBLIC_VIDEOS_LIST,
    RATE_LIMIT_STORAGE_URL,
    SECURE_COOKIES,
    SUPPORTED_VIDEO_EXTENSIONS,
    UPLOADS_DIR,
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

# Video list cache for performance (Issue #429)
# Caches video list query results for 30 seconds to reduce database load
_video_list_cache = AnalyticsCache(ttl_seconds=30, enabled=True, max_size=500)


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
    # Warn about in-memory rate limiting limitations (security issue #446)
    if RATE_LIMIT_ENABLED and RATE_LIMIT_STORAGE_URL == "memory://":
        logger.warning(
            "SECURITY: Rate limiting is using in-memory storage. "
            "With multiple API instances, attackers can bypass rate limits by distributing "
            "requests across instances. For production with load balancing, configure Redis: "
            "VLOG_RATE_LIMIT_STORAGE_URL=redis://localhost:6379 "
            "(or set VLOG_REDIS_URL which will be auto-detected)"
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

# HTTP metrics middleware (outermost - captures all requests including CORS preflight)
# Issue #207: Tracks requests in progress, duration, and total count
app.add_middleware(HTTPMetricsMiddleware, api_name="public")


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


async def get_video_chapters(video_ids: List[int], has_chapters_flags: Dict[int, bool] = None) -> dict:
    """
    Get chapters for a list of video IDs. Returns a dict of video_id -> list of chapters.

    Args:
        video_ids: List of video IDs to get chapters for
        has_chapters_flags: Optional dict mapping video_id -> has_chapters bool.
                           If provided, only queries for videos with has_chapters=True.
    """
    if not video_ids:
        return {}

    # Filter to only videos that have chapters (if flag info provided)
    if has_chapters_flags:
        video_ids = [vid for vid in video_ids if has_chapters_flags.get(vid, False)]

    if not video_ids:
        return {}

    query = (
        sa.select(
            chapters.c.video_id,
            chapters.c.id,
            chapters.c.title,
            chapters.c.start_time,
            chapters.c.end_time,
        )
        .where(chapters.c.video_id.in_(video_ids))
        .order_by(chapters.c.video_id, chapters.c.position)
    )

    rows = await fetch_all_with_retry(query)

    result = {}
    for row in rows:
        video_id = row["video_id"]
        if video_id not in result:
            result[video_id] = []
        result[video_id].append(
            ChapterInfo(
                id=row["id"],
                title=row["title"],
                start_time=row["start_time"],
                end_time=row["end_time"],
            )
        )

    return result


# =============================================================================
# Video List Query Helpers (Issue #437)
# =============================================================================


def build_base_videos_query() -> sa.Select:
    """
    Build the base query for listing videos with necessary joins.

    Returns a query that selects video fields, category name, and view count,
    filtered to only show published, non-deleted, ready videos.
    """
    return (
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
        .where(videos.c.deleted_at.is_(None))
        .where(videos.c.published_at.is_not(None))
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


def apply_category_filter(query: sa.Select, category: Optional[str]) -> sa.Select:
    """Apply category slug filter to the query."""
    if not category:
        return query
    return query.where(categories.c.slug == category)


def apply_tag_filter(query: sa.Select, tag: Optional[str]) -> sa.Select:
    """Apply tag slug filter using EXISTS for better performance."""
    if not tag:
        return query
    tag_exists = (
        sa.select(sa.literal_column("1"))
        .select_from(video_tags.join(tags, video_tags.c.tag_id == tags.c.id))
        .where(video_tags.c.video_id == videos.c.id)
        .where(tags.c.slug == tag)
        .exists()
    )
    return query.where(tag_exists)


def apply_search_filter(query: sa.Select, search: Optional[str]) -> sa.Select:
    """Apply text search filter on title and description."""
    if not search:
        return query
    search_term = f"%{search}%"
    return query.where(
        sa.or_(
            videos.c.title.ilike(search_term),
            videos.c.description.ilike(search_term),
        )
    )


def apply_duration_filter(query: sa.Select, duration: Optional[str]) -> sa.Select:
    """
    Apply duration filter to the query.

    Args:
        query: The current query
        duration: Comma-separated duration values (short, medium, long)

    Returns:
        Query with duration filter applied

    Raises:
        HTTPException: If invalid duration value is provided
    """
    if not duration:
        return query

    duration_filters = [d.strip().lower() for d in duration.split(",")]
    duration_conditions = []
    valid_durations = {DurationFilter.SHORT.value, DurationFilter.MEDIUM.value, DurationFilter.LONG.value}

    for df in duration_filters:
        if df not in valid_durations:
            raise HTTPException(
                status_code=400, detail=f"Invalid duration value: '{df}'. Valid values are: short, medium, long"
            )
        if df == DurationFilter.SHORT.value:
            duration_conditions.append(videos.c.duration < 300)  # < 5 minutes
        elif df == DurationFilter.MEDIUM.value:
            duration_conditions.append(sa.and_(videos.c.duration >= 300, videos.c.duration <= 1200))  # 5-20 minutes
        elif df == DurationFilter.LONG.value:
            duration_conditions.append(videos.c.duration > 1200)  # > 20 minutes

    if duration_conditions:
        query = query.where(sa.or_(*duration_conditions))
    return query


def apply_quality_filter(query: sa.Select, quality: Optional[str]) -> sa.Select:
    """Apply quality filter using EXISTS for better performance."""
    if not quality:
        return query

    quality_filters = [q.strip().lower() for q in quality.split(",")]
    valid_quality_filters = [q for q in quality_filters if q in QUALITY_NAMES]

    if not valid_quality_filters:
        return query

    quality_exists = (
        sa.select(sa.literal_column("1"))
        .where(video_qualities.c.video_id == videos.c.id)
        .where(video_qualities.c.quality.in_(valid_quality_filters))
        .exists()
    )
    return query.where(quality_exists)


def apply_date_range_filter(query: sa.Select, date_from: Optional[datetime], date_to: Optional[datetime]) -> sa.Select:
    """
    Apply date range filter to the query.

    Raises:
        HTTPException: If date_from is after date_to
    """
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Invalid date range: date_from must be before or equal to date_to")
    if date_from:
        query = query.where(videos.c.published_at >= date_from)
    if date_to:
        query = query.where(videos.c.published_at <= date_to)
    return query


def apply_transcription_filter(query: sa.Select, has_transcription: Optional[bool]) -> sa.Select:
    """Apply transcription availability filter using EXISTS for better performance."""
    if has_transcription is None:
        return query

    transcription_exists = (
        sa.select(sa.literal_column("1"))
        .where(transcriptions.c.video_id == videos.c.id)
        .where(transcriptions.c.status == TranscriptionStatus.COMPLETED)
        .exists()
    )

    if has_transcription:
        return query.where(transcription_exists)
    else:
        return query.where(~transcription_exists)


async def apply_custom_field_filters(query: sa.Select, custom_filters: Dict[str, str]) -> sa.Select:
    """
    Apply custom field filters to the query using EXISTS for better performance.

    This function uses guard clauses to handle edge cases early and keep
    the main logic flat (Issue #441).

    Args:
        query: The current query
        custom_filters: Dict mapping field slugs to filter values

    Returns:
        Query with custom field filters applied
    """
    if not custom_filters:
        return query

    # Fetch field definitions for all requested slugs in one query
    field_slugs = list(custom_filters.keys())
    field_query = sa.select(
        custom_field_definitions.c.id,
        custom_field_definitions.c.slug,
        custom_field_definitions.c.field_type,
    ).where(custom_field_definitions.c.slug.in_(field_slugs))
    field_rows = await fetch_all_with_retry(field_query)
    fields_by_slug = {row["slug"]: row for row in field_rows}

    # Apply filter for each custom field
    for field_slug, filter_value in custom_filters.items():
        # Guard clause: skip unknown field slugs
        field_def = fields_by_slug.get(field_slug)
        if not field_def:
            continue

        exists_clause = _build_custom_field_exists_clause(field_def, filter_value)
        query = query.where(exists_clause)

    return query


def _build_custom_field_exists_clause(field_def: Dict[str, Any], filter_value: str) -> sa.Exists:
    """
    Build an EXISTS clause for a single custom field filter.

    Args:
        field_def: Field definition with id, slug, and field_type
        filter_value: The value to filter by

    Returns:
        SQLAlchemy EXISTS clause for the filter
    """
    field_id = field_def["id"]
    field_type = field_def["field_type"]

    # Multi-select fields store JSON arrays - check if value is in the array
    if field_type == "multi_select":
        return (
            sa.select(sa.literal_column("1"))
            .where(video_custom_fields.c.video_id == videos.c.id)
            .where(video_custom_fields.c.field_id == field_id)
            .where(video_custom_fields.c.value.contains(f'"{filter_value}"'))
            .exists()
        )

    # Other types use exact JSON match
    json_value = json.dumps(filter_value)
    return (
        sa.select(sa.literal_column("1"))
        .where(video_custom_fields.c.video_id == videos.c.id)
        .where(video_custom_fields.c.field_id == field_id)
        .where(video_custom_fields.c.value == json_value)
        .exists()
    )


def parse_sort_parameters(sort: Optional[str], order: Optional[str], has_search: bool) -> tuple[SortBy, SortOrder]:
    """
    Parse and validate sort parameters.

    Args:
        sort: Sort field (relevance, date, duration, views, title)
        order: Sort order (asc, desc)
        has_search: Whether a search term is present (affects default sort)

    Returns:
        Tuple of (SortBy enum, SortOrder enum)

    Raises:
        HTTPException: If invalid sort or order value is provided
    """
    # Parse sort field
    if sort:
        try:
            sort_by = SortBy(sort.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort value: '{sort}'. Valid values are: relevance, date, duration, views, title",
            )
    else:
        sort_by = SortBy.RELEVANCE if has_search else SortBy.DATE

    # Parse sort order
    order_lower = (order or "desc").lower()
    if order_lower not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail=f"Invalid order value: '{order}'. Valid values are: asc, desc")
    sort_order = SortOrder.DESC if order_lower == "desc" else SortOrder.ASC

    return sort_by, sort_order


def apply_sorting(query: sa.Select, sort_by: SortBy, sort_order: SortOrder) -> sa.Select:
    """
    Apply sorting to the query.

    Args:
        query: The current query
        sort_by: The field to sort by
        sort_order: The sort direction

    Returns:
        Query with sorting applied
    """
    if sort_by == SortBy.DATE:
        order_col = videos.c.published_at.desc() if sort_order == SortOrder.DESC else videos.c.published_at.asc()
        return query.order_by(order_col)

    if sort_by == SortBy.DURATION:
        order_col = videos.c.duration.desc() if sort_order == SortOrder.DESC else videos.c.duration.asc()
        return query.order_by(order_col)

    if sort_by == SortBy.VIEWS:
        view_count_col = sa.literal_column("view_count")
        order_col = view_count_col.desc() if sort_order == SortOrder.DESC else view_count_col.asc()
        return query.order_by(order_col)

    if sort_by == SortBy.TITLE:
        title_lower = sa.func.lower(videos.c.title)
        order_col = title_lower.asc() if sort_order == SortOrder.ASC else title_lower.desc()
        return query.order_by(order_col)

    # SortBy.RELEVANCE and default: use published date descending
    return query.order_by(videos.c.published_at.desc())


def build_video_list_response(
    rows: List[Dict[str, Any]], video_tags_map: Dict[int, List[VideoTagInfo]]
) -> List[VideoListResponse]:
    """
    Build VideoListResponse objects from database rows.

    Args:
        rows: Database result rows
        video_tags_map: Map of video_id to list of tags

    Returns:
        List of VideoListResponse objects
    """

    def get_thumbnail_version(row: Dict[str, Any]) -> int:
        """Generate cache-busting version for thumbnail URL."""
        if row["thumbnail_timestamp"]:
            return int(row["thumbnail_timestamp"] * 1000)
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
            view_count=row["view_count"] if "view_count" in row._mapping else 0,  # Issue #413 Phase 3
        )
        for row in rows
    ]


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
    date_to: Optional[datetime] = Query(default=None, description="Filter videos published until this date (ISO 8601)"),
    has_transcription: Optional[bool] = Query(
        default=None, description="Filter by transcription availability (true/false)"
    ),
    featured: Optional[bool] = Query(
        default=None, description="Filter by featured status (true = only featured videos)"
    ),
    sort: Optional[str] = Query(default=None, description="Sort by: relevance, date, duration, views, title"),
    order: Optional[str] = Query(default="desc", description="Sort order: asc or desc"),
    limit: int = Query(default=50, ge=1, le=100, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip (deprecated, use cursor)"),
    cursor: Optional[str] = Query(
        default=None,
        description="Cursor for pagination (more efficient than offset for large datasets). "
        "Use next_cursor from previous response.",
    ),
    include_total: bool = Query(
        default=False, description="Include total count in response (expensive for large datasets)"
    ),
) -> PaginatedVideoListResponse:
    """
    List all published videos with advanced filtering and sorting.

    Pagination:
    - cursor: Use cursor-based pagination for efficient traversal of large datasets.
      Pass the next_cursor from the previous response to get the next page.
    - offset: Legacy offset-based pagination (deprecated, use cursor instead).
      When cursor is provided, offset is ignored.

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

    Note: Cursor-based pagination is recommended for large datasets (Issue #463).
    """
    # Parse custom field filters early for cache key inclusion (Issue #429)
    # Custom fields are query params like "custom.difficulty=beginner"
    custom_filters = {}
    for key, value in request.query_params.items():
        if key.startswith("custom."):
            field_slug = key[7:]  # Remove "custom." prefix
            if field_slug:
                custom_filters[field_slug] = value

    # Validate and decode cursor if provided
    cursor_data = validate_cursor(cursor)
    using_cursor = cursor_data is not None

    # Generate cache key from ALL query parameters including custom fields and cursor
    # Use a hash to avoid collisions from delimiter conflicts in parameter values
    # (e.g., search terms containing the delimiter character)
    custom_filters_key = "|".join(f"{k}={v}" for k, v in sorted(custom_filters.items()))
    pagination_key = f"cursor:{cursor}" if using_cursor else f"offset:{offset}"
    cache_key_raw = f"{category}|{tag}|{search}|{duration}|{quality}|{date_from}|{date_to}|{has_transcription}|{featured}|{sort}|{order}|{limit}|{pagination_key}|{include_total}|{custom_filters_key}"
    cache_key = f"videos:{hashlib.sha256(cache_key_raw.encode()).hexdigest()[:16]}"

    # Check cache first
    cached_result = _video_list_cache.get(cache_key)
    if cached_result is not None:
        return PaginatedVideoListResponse(**cached_result)

    # Build base query and apply all filters
    query = build_base_videos_query()
    query = apply_category_filter(query, category)
    query = apply_tag_filter(query, tag)
    query = apply_search_filter(query, search)
    query = apply_duration_filter(query, duration)
    query = apply_quality_filter(query, quality)
    query = apply_date_range_filter(query, date_from, date_to)
    query = apply_transcription_filter(query, has_transcription)
    # Issue #413 Phase 3: Featured video filter
    if featured is not None:
        query = query.where(videos.c.is_featured == featured)
    query = await apply_custom_field_filters(query, custom_filters)

    # Apply sorting - need to know sort direction for cursor pagination
    sort_by, sort_order = parse_sort_parameters(sort, order, has_search=bool(search))

    # Apply cursor-based pagination if cursor is provided (Issue #463)
    # Cursor pagination uses (published_at, id) for stable ordering
    if using_cursor:
        cursor_ts, cursor_id = cursor_data
        # For descending order: get items where (published_at, id) < cursor
        # For ascending order: get items where (published_at, id) > cursor
        if sort_order == SortOrder.DESC:
            query = query.where(
                sa.or_(
                    videos.c.published_at < cursor_ts,
                    sa.and_(videos.c.published_at == cursor_ts, videos.c.id < cursor_id),
                )
            )
        else:
            query = query.where(
                sa.or_(
                    videos.c.published_at > cursor_ts,
                    sa.and_(videos.c.published_at == cursor_ts, videos.c.id > cursor_id),
                )
            )

    # Apply sorting with secondary sort by id for stable cursor pagination
    query = apply_sorting(query, sort_by, sort_order)
    # Add secondary sort by id for deterministic ordering with same published_at
    if sort_order == SortOrder.DESC:
        query = query.order_by(videos.c.id.desc())
    else:
        query = query.order_by(videos.c.id.asc())

    # Apply pagination - fetch one extra to determine has_more
    if not using_cursor:
        query = query.offset(offset)
    query = query.limit(limit + 1)

    # Execute query and build response
    rows = await fetch_all_with_retry(query)

    # Determine if there are more results
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]  # Remove the extra row

    video_ids = [row["id"] for row in rows]
    video_tags_map = await get_video_tags(video_ids)
    video_list = build_video_list_response(rows, video_tags_map)

    # Generate next cursor from the last item
    next_cursor = None
    if has_more and rows:
        last_row = rows[-1]
        if last_row["published_at"]:
            next_cursor = encode_cursor(last_row["published_at"], last_row["id"])

    # Optionally get total count (expensive for large datasets)
    total_count = None
    if include_total:
        count_query = build_base_videos_query()
        count_query = apply_category_filter(count_query, category)
        count_query = apply_tag_filter(count_query, tag)
        count_query = apply_search_filter(count_query, search)
        count_query = apply_duration_filter(count_query, duration)
        count_query = apply_quality_filter(count_query, quality)
        count_query = apply_date_range_filter(count_query, date_from, date_to)
        count_query = apply_transcription_filter(count_query, has_transcription)
        count_query = await apply_custom_field_filters(count_query, custom_filters)
        # Wrap to count total
        count_query = sa.select(sa.func.count()).select_from(count_query.subquery())
        total_count = await fetch_val_with_retry(count_query)

    result = PaginatedVideoListResponse(
        videos=video_list,
        next_cursor=next_cursor,
        has_more=has_more,
        total_count=total_count,
    )

    # Cache the result as dict for serialization (Issue #429)
    _video_list_cache.set(cache_key, result.model_dump())

    return result


# Maximum videos per bulk request (Issue #413 Phase 3)
MAX_BULK_VIDEO_IDS = 20


@app.get("/api/videos/bulk")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_videos_bulk(
    request: Request,
    ids: str = Query(..., description="Comma-separated video IDs (max 20)"),
) -> List[VideoListResponse]:
    """
    Get multiple videos by ID in a single request.

    This endpoint is optimized for fetching multiple videos efficiently,
    useful for Continue Watching and Watch Later features.

    Args:
        ids: Comma-separated video IDs (max 20)

    Returns:
        List of VideoListResponse objects (same order as requested IDs, excluding missing/deleted)
    """
    # Parse and validate IDs
    try:
        id_list = []
        for id_str in ids.split(","):
            cleaned = id_str.strip()
            if cleaned:
                vid = int(cleaned)
                if vid <= 0:
                    raise HTTPException(
                        status_code=400, detail=f"Invalid video ID: '{cleaned}' must be a positive integer"
                    )
                id_list.append(vid)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid video ID format: '{cleaned}' is not a valid integer")

    if not id_list:
        return []

    # Deduplicate while preserving order
    id_list = list(dict.fromkeys(id_list))

    if len(id_list) > MAX_BULK_VIDEO_IDS:
        raise HTTPException(
            status_code=400, detail=f"Maximum {MAX_BULK_VIDEO_IDS} unique video IDs allowed per request"
        )

    # Build query for multiple videos
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
            videos.c.streaming_format,
            videos.c.primary_codec,
            categories.c.name.label("category_name"),
            sa.func.count(sa.distinct(playback_sessions.c.id)).label("view_count"),
        )
        .select_from(
            videos.outerjoin(categories, videos.c.category_id == categories.c.id).outerjoin(
                playback_sessions, videos.c.id == playback_sessions.c.video_id
            )
        )
        .where(videos.c.id.in_(id_list))
        .where(videos.c.status == "ready")
        .where(videos.c.deleted_at.is_(None))
        .where(videos.c.published_at.is_not(None))
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
            videos.c.thumbnail_source,
            videos.c.thumbnail_timestamp,
            videos.c.streaming_format,
            videos.c.primary_codec,
            categories.c.name,
        )
    )

    rows = await fetch_all_with_retry(query)

    # Get tags for these videos
    video_ids = [row["id"] for row in rows]
    video_tags_map = await get_video_tags(video_ids)

    # Build response preserving original request order
    row_map = {row["id"]: row for row in rows}
    ordered_rows = [row_map[vid] for vid in id_list if vid in row_map]
    return build_video_list_response(ordered_rows, video_tags_map)


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

    # Get chapters for this video (only if has_chapters is True - Issue #413 Phase 7A)
    chapter_list = []
    if row._mapping.get("has_chapters", False):
        video_chapters_map = await get_video_chapters([row["id"]])
        chapter_list = video_chapters_map.get(row["id"], [])

    # Build sprite sheet info if available (Issue #413 Phase 7B)
    sprite_sheet_info = None
    if row._mapping.get("sprite_sheet_status") == "ready" and row._mapping.get("sprite_sheet_count", 0) > 0:
        sprite_sheet_info = SpriteSheetInfo(
            base_url=f"/videos/{row['slug']}/sprites/sprite_",
            count=row["sprite_sheet_count"],
            interval=row["sprite_sheet_interval"],
            tile_size=row["sprite_sheet_tile_size"],
            frame_width=row["sprite_sheet_frame_width"],
            frame_height=row["sprite_sheet_frame_height"],
        )

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
            f"/videos/{row['slug']}/thumbnail.jpg?v={thumb_version}" if row["status"] == VideoStatus.READY else None
        ),
        thumbnail_source=row["thumbnail_source"] or "auto",
        thumbnail_timestamp=row["thumbnail_timestamp"],
        # Stream URLs use CDN if configured (Issue #222)
        stream_url=(
            f"{video_url_prefix}/videos/{row['slug']}/master.m3u8" if row["status"] == VideoStatus.READY else None
        ),
        # DASH URL only available for CMAF format videos
        dash_url=(
            f"{video_url_prefix}/videos/{row['slug']}/manifest.mpd"
            if row["status"] == VideoStatus.READY and row._mapping.get("streaming_format") == "cmaf"
            else None
        ),
        streaming_format=row._mapping.get("streaming_format", "hls_ts"),
        primary_codec=row._mapping.get("primary_codec", "h264"),
        captions_url=captions_url,
        transcription_status=transcription_status,
        qualities=qualities,
        tags=video_tag_list,
        chapters=chapter_list,
        sprite_sheet_info=sprite_sheet_info,
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


# =============================================================================
# Related Videos API (Issue #413 Phase 5)
# =============================================================================


async def _fetch_related_videos_tier(
    category_id: Optional[int],
    tag_ids: Optional[List[int]],
    exclude_ids: set,
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Fetch videos matching category and/or tags, excluding specified IDs.

    Tiers:
    - category_id + tag_ids: Videos with same category AND shared tags (highest relevance)
    - category_id only: Videos in the same category
    - tag_ids only: Videos with shared tags
    - neither: Recent published videos (fallback)

    Args:
        category_id: Optional category ID to filter by
        tag_ids: Optional list of tag IDs to match (uses EXISTS for any match)
        exclude_ids: Set of video IDs to exclude from results
        limit: Maximum number of videos to return

    Returns:
        List of video rows matching criteria
    """
    if limit <= 0:
        return []

    query = build_base_videos_query()

    # Exclude already-found videos
    if exclude_ids:
        query = query.where(videos.c.id.notin_(exclude_ids))

    # Apply category filter
    if category_id is not None:
        query = query.where(videos.c.category_id == category_id)

    # Apply tag filter using EXISTS (at least one matching tag)
    if tag_ids:
        tag_match_exists = (
            sa.select(sa.literal_column("1"))
            .select_from(video_tags)
            .where(video_tags.c.video_id == videos.c.id)
            .where(video_tags.c.tag_id.in_(tag_ids))
            .exists()
        )
        query = query.where(tag_match_exists)

    # Order by published date (most recent first)
    query = query.order_by(videos.c.published_at.desc())
    query = query.limit(limit)

    return await fetch_all_with_retry(query)


@app.get("/api/videos/{slug}/related")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_related_videos(
    request: Request,
    slug: str,
    limit: int = Query(default=12, ge=1, le=24, description="Maximum number of related videos to return"),
) -> List[VideoListResponse]:
    """
    Get related videos for a given video.

    Algorithm priority (with early termination when limit reached):
    1. Same category + shared tags (highest relevance)
    2. Same category only
    3. Shared tags only
    4. Recent videos (fallback)

    Results are cached for 30 seconds using the video list cache.

    Args:
        slug: The video slug to find related videos for
        limit: Maximum number of related videos (1-24, default 12)

    Returns:
        List of related videos sorted by relevance tier then recency
    """
    # Validate slug to prevent injection attacks
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="Invalid video slug")

    # Build cache key with SHA256 hash to prevent cache poisoning
    # High priority fix (Margo): Include schema version in cache key
    RELATED_VIDEOS_CACHE_VERSION = "v1"  # Increment on schema changes
    cache_key_raw = f"related:{RELATED_VIDEOS_CACHE_VERSION}:{slug}|{limit}"
    cache_key = f"related:{hashlib.sha256(cache_key_raw.encode()).hexdigest()[:16]}"

    # Check cache first
    cached = _video_list_cache.get(cache_key)
    if cached is not None:
        try:
            return [VideoListResponse(**v) for v in cached]
        except Exception as e:
            # Cache schema mismatch after deploy, invalidate and regenerate
            logger.warning(f"Cached related videos schema mismatch, invalidating: {e}")
            _video_list_cache.delete(cache_key)

    # Get the source video with its category
    video_query = (
        sa.select(videos.c.id, videos.c.category_id)
        .where(videos.c.slug == slug)
        .where(videos.c.status == VideoStatus.READY)
        .where(videos.c.deleted_at.is_(None))
        .where(videos.c.published_at.is_not(None))
    )
    video = await fetch_one_with_retry(video_query)

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video_id = video["id"]
    category_id = video["category_id"]

    # Get source video's tag IDs (limit to top 10 to avoid overly complex queries)
    tag_query = sa.select(video_tags.c.tag_id).where(video_tags.c.video_id == video_id).limit(10)
    tag_rows = await fetch_all_with_retry(tag_query)
    source_tag_ids = [r["tag_id"] for r in tag_rows] if tag_rows else []

    # Collect related videos using tiered algorithm with early termination
    related_videos: List[Dict[str, Any]] = []
    seen_ids: set = {video_id}  # Always exclude the source video

    # Tier 1: Same category + shared tags (highest relevance)
    if category_id is not None and source_tag_ids:
        tier1 = await _fetch_related_videos_tier(
            category_id=category_id,
            tag_ids=source_tag_ids,
            exclude_ids=seen_ids,
            limit=limit,
        )
        for v in tier1:
            if len(related_videos) < limit:
                related_videos.append(v)
                seen_ids.add(v["id"])

    # Tier 2: Same category only (if we need more)
    if len(related_videos) < limit and category_id is not None:
        remaining = limit - len(related_videos)
        tier2 = await _fetch_related_videos_tier(
            category_id=category_id,
            tag_ids=None,
            exclude_ids=seen_ids,
            limit=remaining,
        )
        for v in tier2:
            if len(related_videos) < limit:
                related_videos.append(v)
                seen_ids.add(v["id"])

    # Tier 3: Shared tags only (if we need more)
    if len(related_videos) < limit and source_tag_ids:
        remaining = limit - len(related_videos)
        tier3 = await _fetch_related_videos_tier(
            category_id=None,
            tag_ids=source_tag_ids,
            exclude_ids=seen_ids,
            limit=remaining,
        )
        for v in tier3:
            if len(related_videos) < limit:
                related_videos.append(v)
                seen_ids.add(v["id"])

    # Tier 4: Recent videos fallback (if we still need more)
    if len(related_videos) < limit:
        remaining = limit - len(related_videos)
        tier4 = await _fetch_related_videos_tier(
            category_id=None,
            tag_ids=None,
            exclude_ids=seen_ids,
            limit=remaining,
        )
        for v in tier4:
            if len(related_videos) < limit:
                related_videos.append(v)
                seen_ids.add(v["id"])

    # Get tags for all related videos
    video_ids = [v["id"] for v in related_videos]
    video_tags_map = await get_video_tags(video_ids)

    # Build response using existing helper
    result = build_video_list_response(related_videos, video_tags_map)

    # Cache the result
    _video_list_cache.set(cache_key, [v.model_dump() for v in result])

    return result


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
# Playlists
# ============================================================================


def _get_video_url_prefix() -> str:
    """Get the URL prefix for video assets."""
    return f"http://{NAS_STORAGE}/vlog-storage"


# Valid playlist types for filtering
VALID_PLAYLIST_TYPES = {"playlist", "collection", "series", "course"}


@app.get("/api/playlists")
@limiter.limit(RATE_LIMIT_PUBLIC_VIDEOS_LIST)
async def list_public_playlists(
    request: Request,
    playlist_type: Optional[str] = Query(default=None, description="Filter by type"),
    featured: Optional[bool] = Query(default=None, description="Filter by featured status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PlaylistListResponse:
    """List public playlists with video counts."""
    # Validate playlist_type if provided
    if playlist_type and playlist_type not in VALID_PLAYLIST_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Invalid playlist type. Valid options: {', '.join(sorted(VALID_PLAYLIST_TYPES))}"
        )

    # Build WHERE conditions
    conditions = ["p.deleted_at IS NULL", "p.visibility = 'public'"]
    filter_params: dict = {}

    if playlist_type:
        conditions.append("p.playlist_type = :playlist_type")
        filter_params["playlist_type"] = playlist_type

    if featured is not None:
        conditions.append("p.is_featured = :is_featured")
        filter_params["is_featured"] = featured

    where_clause = " AND ".join(conditions)

    # Count total (only uses filter params)
    count_query = sa.text(f"""
        SELECT COUNT(*) FROM playlists p WHERE {where_clause}
    """)
    if filter_params:
        count_query = count_query.bindparams(**filter_params)
    total_count = await fetch_val_with_retry(count_query)

    # Get playlists with video counts (uses filter + pagination params)
    all_params = {**filter_params, "limit": limit, "offset": offset}
    query = sa.text(f"""
        SELECT
            p.*,
            COUNT(DISTINCT CASE
                WHEN v.status = 'ready' AND v.deleted_at IS NULL
                AND v.published_at IS NOT NULL THEN pi.video_id
            END) as video_count,
            COALESCE(SUM(CASE
                WHEN v.status = 'ready' AND v.deleted_at IS NULL
                AND v.published_at IS NOT NULL THEN v.duration
            END), 0) as total_duration
        FROM playlists p
        LEFT JOIN playlist_items pi ON pi.playlist_id = p.id
        LEFT JOIN videos v ON v.id = pi.video_id
        WHERE {where_clause}
        GROUP BY p.id
        ORDER BY p.is_featured DESC, p.created_at DESC
        LIMIT :limit OFFSET :offset
    """).bindparams(**all_params)
    rows = await fetch_all_with_retry(query)

    playlist_list = []
    for row in rows:
        thumbnail_url = None
        if row.get("thumbnail_path"):
            thumbnail_url = f"{_get_video_url_prefix()}/{row['thumbnail_path']}"

        playlist_list.append(
            PlaylistResponse(
                id=row["id"],
                title=row["title"],
                slug=row["slug"],
                description=row.get("description"),
                thumbnail_url=thumbnail_url,
                visibility=row["visibility"],
                playlist_type=row["playlist_type"],
                is_featured=row["is_featured"],
                video_count=row["video_count"] or 0,
                total_duration=row["total_duration"] or 0,
                created_at=row["created_at"],
                updated_at=row.get("updated_at"),
            )
        )

    return PlaylistListResponse(playlists=playlist_list, total_count=total_count or 0)


@app.get("/api/playlists/{slug}")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_public_playlist(request: Request, slug: str) -> PlaylistDetailResponse:
    """Get a public playlist by slug with its videos."""
    # Validate slug
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="Invalid playlist slug")

    # Get playlist (only public or unlisted)
    playlist = await fetch_one_with_retry(
        playlists.select()
        .where(playlists.c.slug == slug)
        .where(playlists.c.deleted_at.is_(None))
        .where(playlists.c.visibility.in_(["public", "unlisted"]))
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    # Get videos (only ready, published, non-deleted)
    video_query = sa.text("""
        SELECT
            v.id, v.title, v.slug, v.duration, v.status,
            pi.position
        FROM playlist_items pi
        JOIN videos v ON v.id = pi.video_id
        WHERE pi.playlist_id = :playlist_id
          AND v.status = 'ready'
          AND v.deleted_at IS NULL
          AND v.published_at IS NOT NULL
        ORDER BY pi.position ASC
    """).bindparams(playlist_id=playlist["id"])
    video_rows = await fetch_all_with_retry(video_query)

    video_list = []
    total_duration = 0.0
    for vrow in video_rows:
        thumbnail_url = f"{_get_video_url_prefix()}/videos/{vrow['slug']}/thumbnail.jpg"
        video_list.append(
            PlaylistVideoInfo(
                id=vrow["id"],
                title=vrow["title"],
                slug=vrow["slug"],
                thumbnail_url=thumbnail_url,
                duration=vrow["duration"] or 0,
                position=vrow["position"],
                status=vrow["status"],
            )
        )
        total_duration += vrow["duration"] or 0

    # Build thumbnail URL
    thumbnail_url = None
    if playlist.get("thumbnail_path"):
        thumbnail_url = f"{_get_video_url_prefix()}/{playlist['thumbnail_path']}"

    return PlaylistDetailResponse(
        id=playlist["id"],
        title=playlist["title"],
        slug=playlist["slug"],
        description=playlist.get("description"),
        thumbnail_url=thumbnail_url,
        visibility=playlist["visibility"],
        playlist_type=playlist["playlist_type"],
        is_featured=playlist["is_featured"],
        video_count=len(video_list),
        total_duration=total_duration,
        created_at=playlist["created_at"],
        updated_at=playlist.get("updated_at"),
        videos=video_list,
    )


@app.get("/api/playlists/{slug}/videos")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_public_playlist_videos(request: Request, slug: str) -> List[PlaylistVideoInfo]:
    """Get videos in a public playlist."""
    # Validate slug
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="Invalid playlist slug")

    # Get playlist (only public or unlisted)
    playlist = await fetch_one_with_retry(
        playlists.select()
        .where(playlists.c.slug == slug)
        .where(playlists.c.deleted_at.is_(None))
        .where(playlists.c.visibility.in_(["public", "unlisted"]))
    )
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    # Get videos
    query = sa.text("""
        SELECT
            v.id, v.title, v.slug, v.duration, v.status,
            pi.position
        FROM playlist_items pi
        JOIN videos v ON v.id = pi.video_id
        WHERE pi.playlist_id = :playlist_id
          AND v.status = 'ready'
          AND v.deleted_at IS NULL
          AND v.published_at IS NOT NULL
        ORDER BY pi.position ASC
    """).bindparams(playlist_id=playlist["id"])
    rows = await fetch_all_with_retry(query)

    return [
        PlaylistVideoInfo(
            id=row["id"],
            title=row["title"],
            slug=row["slug"],
            thumbnail_url=f"{_get_video_url_prefix()}/videos/{row['slug']}/thumbnail.jpg",
            duration=row["duration"] or 0,
            position=row["position"],
            status=row["status"],
        )
        for row in rows
    ]


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
# Display Configuration
# ============================================================================

# Cached display settings (refreshed every 60 seconds)
_cached_display_settings: Dict[str, Any] = {}
_cached_display_settings_time: float = 0
_DISPLAY_SETTINGS_CACHE_TTL = 60  # seconds


async def get_display_settings() -> Dict[str, Any]:
    """
    Get display settings from database with caching.

    Returns dict with:
    - show_view_counts: bool (default True)
    - show_tagline: bool (default True)
    - tagline: str (default empty)
    """
    import time

    global _cached_display_settings, _cached_display_settings_time

    now = time.time()
    if _cached_display_settings and (now - _cached_display_settings_time) < _DISPLAY_SETTINGS_CACHE_TTL:
        return _cached_display_settings

    try:
        from api.settings_service import get_settings_service

        service = get_settings_service()

        settings = {
            "show_view_counts": await service.get("display.show_view_counts", True),
            "show_tagline": await service.get("display.show_tagline", True),
            "tagline": await service.get("display.tagline", ""),
        }

        _cached_display_settings = settings
        _cached_display_settings_time = now

    except Exception as e:
        logger.debug(f"Failed to get display settings from DB, using defaults: {e}")
        _cached_display_settings = {
            "show_view_counts": True,
            "show_tagline": True,
            "tagline": "",
        }
        _cached_display_settings_time = now

    return _cached_display_settings


@app.get("/api/config/display")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_display_config(request: Request):
    """
    Get display configuration for the public UI.

    Returns display settings like whether to show view counts.
    """
    settings = await get_display_settings()
    return settings


# ============================================================================
# Download Configuration (Issue #202)
# ============================================================================

# Cached download settings (refreshed every 60 seconds)
_cached_download_settings: Dict[str, Any] = {}
_cached_download_settings_time: float = 0
_DOWNLOAD_SETTINGS_CACHE_TTL = 60  # seconds
_download_settings_lock: Optional[asyncio.Lock] = None

# Concurrent download tracking per IP (in-memory, resets on restart)
_active_downloads_per_ip: Dict[str, int] = {}
_downloads_tracking_lock: Optional[asyncio.Lock] = None

# MIME type mapping for video files
_VIDEO_MIME_TYPES: Dict[str, str] = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
}

# Maximum file size for downloads (100GB sanity check)
_MAX_DOWNLOAD_FILE_SIZE = 100 * 1024 * 1024 * 1024


def _get_download_settings_lock() -> asyncio.Lock:
    """Get or create the download settings cache lock."""
    global _download_settings_lock
    if _download_settings_lock is None:
        _download_settings_lock = asyncio.Lock()
    return _download_settings_lock


def _get_downloads_tracking_lock() -> asyncio.Lock:
    """Get or create the concurrent downloads tracking lock."""
    global _downloads_tracking_lock
    if _downloads_tracking_lock is None:
        _downloads_tracking_lock = asyncio.Lock()
    return _downloads_tracking_lock


async def get_download_settings() -> Dict[str, Any]:
    """
    Get download settings from database with caching and env var fallback.

    Uses asyncio.Lock to prevent thundering herd on cache expiry.

    Returns dict with:
    - enabled: Whether downloads are enabled (default False)
    - allow_original: Whether original file downloads are allowed (default False)
    - allow_transcoded: Whether transcoded quality downloads are allowed (default True)
    - rate_limit_per_hour: Downloads per IP per hour (default 10)
    - max_concurrent: Max concurrent downloads per IP (default 2)

    Note: rate_limit_per_hour is configured at startup and requires restart to change.
    The database setting only affects the displayed config, not the actual rate limit.
    """
    global _cached_download_settings, _cached_download_settings_time

    now = time.time()
    # Fast path: cache is valid
    if _cached_download_settings and (now - _cached_download_settings_time) <= _DOWNLOAD_SETTINGS_CACHE_TTL:
        return _cached_download_settings

    # Slow path: acquire lock and refresh cache
    async with _get_download_settings_lock():
        # Double-check after acquiring lock (another request may have refreshed)
        now = time.time()
        if _cached_download_settings and (now - _cached_download_settings_time) <= _DOWNLOAD_SETTINGS_CACHE_TTL:
            return _cached_download_settings

        try:
            from api.settings_service import get_settings_service

            service = get_settings_service()

            settings = {
                "enabled": await service.get("downloads.enabled", DOWNLOADS_ENABLED),
                "allow_original": await service.get("downloads.allow_original", DOWNLOADS_ALLOW_ORIGINAL),
                "allow_transcoded": await service.get("downloads.allow_transcoded", DOWNLOADS_ALLOW_TRANSCODED),
                "rate_limit_per_hour": await service.get(
                    "downloads.rate_limit_per_hour", DOWNLOADS_RATE_LIMIT_PER_HOUR
                ),
                "max_concurrent": await service.get("downloads.max_concurrent", DOWNLOADS_MAX_CONCURRENT),
            }

            _cached_download_settings = settings
            _cached_download_settings_time = now

        except Exception as e:
            # Log at WARNING level - this is an operational issue that could hide config problems
            logger.warning(f"Failed to get download settings from DB, using env vars: {e}")
            _cached_download_settings = {
                "enabled": DOWNLOADS_ENABLED,
                "allow_original": DOWNLOADS_ALLOW_ORIGINAL,
                "allow_transcoded": DOWNLOADS_ALLOW_TRANSCODED,
                "rate_limit_per_hour": DOWNLOADS_RATE_LIMIT_PER_HOUR,
                "max_concurrent": DOWNLOADS_MAX_CONCURRENT,
            }
            _cached_download_settings_time = now

    return _cached_download_settings


def reset_download_settings_cache() -> None:
    """Reset the cached download settings. Useful for testing."""
    global _cached_download_settings, _cached_download_settings_time
    _cached_download_settings = {}
    _cached_download_settings_time = 0


@app.get("/api/config/downloads")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_download_config(request: Request):
    """
    Get download configuration for the UI.

    Returns whether downloads are enabled and what options are available.
    This is used by the watch page to show/hide download buttons.
    """
    settings = await get_download_settings()

    if not settings["enabled"]:
        return {"enabled": False}

    return {
        "enabled": True,
        "allow_original": settings["allow_original"],
        "allow_transcoded": settings["allow_transcoded"],
    }


def _find_original_file(video_id: int) -> Optional[Path]:
    """
    Find the original uploaded file for a video with validation.

    Searches UPLOADS_DIR for files matching {video_id}.{ext} where ext
    is one of the supported video extensions. Validates that the file
    is readable, is a regular file, and has a reasonable size.

    Args:
        video_id: The video's database ID

    Returns:
        Path to the original file if found and valid, None otherwise

    Raises:
        OSError: If the uploads directory is inaccessible
    """
    try:
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            candidate = UPLOADS_DIR / f"{video_id}{ext}"

            if not candidate.exists():
                continue

            # Validate it's a regular file (not directory, symlink to unsafe location, etc.)
            if not candidate.is_file():
                logger.warning(f"Original file for video {video_id} is not a regular file: {candidate}")
                continue

            # Verify readable and get size
            try:
                stat_info = candidate.stat()

                # Check for empty files
                if stat_info.st_size == 0:
                    logger.warning(f"Original file for video {video_id} is empty: {candidate}")
                    continue

                # Sanity check on file size (100GB max)
                if stat_info.st_size > _MAX_DOWNLOAD_FILE_SIZE:
                    logger.error(
                        f"Original file for video {video_id} exceeds size limit: "
                        f"{stat_info.st_size / 1e9:.1f}GB > {_MAX_DOWNLOAD_FILE_SIZE / 1e9:.0f}GB"
                    )
                    continue

                # Verify we can read it
                if not os.access(candidate, os.R_OK):
                    logger.warning(f"Original file for video {video_id} is not readable: {candidate}")
                    continue

                return candidate

            except (OSError, PermissionError) as e:
                logger.warning(f"Cannot access original file {candidate}: {e}")
                continue

        return None

    except OSError as e:
        logger.error(f"Filesystem error searching for video {video_id} original: {e}")
        raise


async def _acquire_download_slot(client_ip: str, max_concurrent: int) -> bool:
    """
    Try to acquire a download slot for the given IP.

    Args:
        client_ip: The client's IP address
        max_concurrent: Maximum concurrent downloads allowed per IP

    Returns:
        True if slot acquired, False if at limit
    """
    async with _get_downloads_tracking_lock():
        current = _active_downloads_per_ip.get(client_ip, 0)
        if current >= max_concurrent:
            return False
        _active_downloads_per_ip[client_ip] = current + 1
        return True


async def _release_download_slot(client_ip: str) -> None:
    """Release a download slot for the given IP."""
    async with _get_downloads_tracking_lock():
        current = _active_downloads_per_ip.get(client_ip, 0)
        if current <= 1:
            _active_downloads_per_ip.pop(client_ip, None)
        else:
            _active_downloads_per_ip[client_ip] = current - 1


@app.get("/api/videos/{slug}/download/original")
@limiter.limit(
    # Note: This rate limit is configured at startup from env vars.
    # Changing the database setting requires a restart to take effect.
    f"{DOWNLOADS_RATE_LIMIT_PER_HOUR}/hour" if DOWNLOADS_RATE_LIMIT_PER_HOUR > 0 else RATE_LIMIT_PUBLIC_DEFAULT
)
async def download_original(
    request: Request,
    slug: str,
    _storage=Depends(require_storage_available),
):
    """
    Download the original source file for a video.

    This endpoint serves the original file as uploaded, without any transcoding.
    The file is streamed to prevent loading large files into memory.

    Requirements:
    - Downloads must be enabled (VLOG_DOWNLOADS_ENABLED=true)
    - Original downloads must be allowed (VLOG_DOWNLOADS_ALLOW_ORIGINAL=true)
    - Storage must be available
    - Concurrent download limit per IP must not be exceeded

    Returns:
        FileResponse with the original video file
    """
    client_ip = get_real_ip(request)

    # Validate slug
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="Invalid video slug")

    # Check download settings
    settings = await get_download_settings()

    if not settings["enabled"]:
        raise HTTPException(status_code=403, detail="Downloads are disabled")

    if not settings["allow_original"]:
        raise HTTPException(status_code=403, detail="Original file downloads are disabled")

    # Check concurrent download limit
    max_concurrent = settings["max_concurrent"]
    if not await _acquire_download_slot(client_ip, max_concurrent):
        logger.warning(f"Download rate limit exceeded for {client_ip} (max {max_concurrent} concurrent)")
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent downloads. Maximum {max_concurrent} allowed per IP.",
        )

    try:
        # Get video from database
        video_query = (
            videos.select()
            .where(videos.c.slug == slug)
            .where(videos.c.status == VideoStatus.READY)
            .where(videos.c.deleted_at.is_(None))
            .where(videos.c.published_at.is_not(None))
        )
        video = await fetch_one_with_retry(video_query)

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        # Find the original file with validation
        try:
            original_file = _find_original_file(video["id"])
        except OSError:
            raise HTTPException(status_code=503, detail="Storage temporarily unavailable")

        if not original_file:
            raise HTTPException(
                status_code=404,
                detail="Original file not available. It may have been deleted after transcoding.",
            )

        # Final validation before serving (reduce TOCTOU window)
        try:
            if not original_file.exists():
                raise HTTPException(status_code=404, detail="Original file no longer available")
            file_size = original_file.stat().st_size
        except OSError as e:
            logger.error(f"Filesystem error accessing {original_file}: {e}")
            raise HTTPException(status_code=503, detail="Storage temporarily unavailable")

        # Generate a safe filename for the download
        # Use the video title with the original extension, limited to 200 chars
        safe_title = "".join(c for c in video["title"] if c.isalnum() or c in " -_").strip()
        safe_title = "_".join(safe_title.split())  # Replace spaces with underscores
        if not safe_title or len(safe_title.encode("utf-8")) > 200:
            safe_title = slug
        safe_title = safe_title[:200]  # Limit length

        # Validate extension is in allowed list
        ext = original_file.suffix.lower()
        if ext not in SUPPORTED_VIDEO_EXTENSIONS:
            logger.error(f"Invalid file extension {ext} for video {slug}")
            raise HTTPException(status_code=500, detail="Invalid file type")

        download_filename = f"{safe_title}{ext}"

        # RFC 5987 encoding for Content-Disposition with non-ASCII support
        encoded_filename = quote(download_filename)
        # Escape any quotes in the ASCII fallback filename
        ascii_filename = download_filename.replace('"', "_")

        # Determine correct MIME type based on extension
        media_type = _VIDEO_MIME_TYPES.get(ext, "application/octet-stream")

        file_size_mb = file_size / (1024 * 1024)
        logger.info(
            f"Serving original download: video={slug} (id={video['id']}), "
            f"file={original_file.name}, size={file_size_mb:.1f}MB, client={client_ip}"
        )

        # Note: We don't release the download slot here because FileResponse
        # streams the file asynchronously. The slot will be released when the
        # response completes or errors. For true slot tracking, we'd need to
        # wrap the response in a custom streaming response with cleanup.
        # For now, we accept this limitation - slots may leak on slow downloads.
        # TODO: Implement proper cleanup with background task or custom response

        return FileResponse(
            path=original_file,
            filename=download_filename,
            media_type=media_type,
            headers={
                # RFC 5987 encoded filename with ASCII fallback
                "Content-Disposition": f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{encoded_filename}",
                "Cache-Control": "private, max-age=3600",
            },
        )

    except HTTPException:
        # Release slot on HTTP errors (client won't download)
        await _release_download_slot(client_ip)
        raise
    except Exception:
        # Release slot on unexpected errors
        await _release_download_slot(client_ip)
        raise


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

    # Issue #207: Record watch time metric
    # duration_watched is accumulated in heartbeat endpoint - record the final value
    duration_watched = session["duration_watched"] or 0.0
    if 0 < duration_watched < 86400:  # Sanity check: 0 < watch time < 24 hours
        VIDEOS_WATCH_TIME_SECONDS_TOTAL.inc(duration_watched)

    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PUBLIC_PORT)
