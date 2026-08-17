"""
Public API - serves the video browsing interface.
Runs on port 9000.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import bleach
import sqlalchemy as sa
from fastapi import APIRouter, Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from api.analytics_cache import AnalyticsCache
from api.auth.middleware import get_current_user, require_auth, require_ownership_or_permission
from api.auth.permissions import Permission
from api.common import (
    HTTPMetricsMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    check_health,
    get_real_ip,
    get_storage_status,
    rate_limit_exceeded_handler,
    require_storage_available,
    require_valid_slug,
    validate_slug,
)
from api.database import (
    categories,
    chapters,
    comments,
    configure_database,
    custom_field_definitions,
    database,
    live_streams,
    playback_sessions,
    playlists,
    quality_progress,
    ratings,
    tags,
    transcoding_jobs,
    transcriptions,
    users,
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
from api.live_schemas import (
    PublicLiveStreamListResponse,
    PublicLiveStreamResponse,
)
from api.logging_config import setup_logging
from api.metrics import VIDEOS_WATCH_TIME_SECONDS_TOTAL
from api.pagination import encode_cursor, validate_cursor
from api.schemas import (
    CategoryResponse,
    ChapterInfo,
    CommentCreate,
    CommentListResponse,
    CommentResponse,
    CommentUpdate,
    CommentUserInfo,
    CommentWithReplies,
    EmbedCodeResponse,
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
    RatingCreate,
    RatingResponse,
    SpriteSheetInfo,
    TagResponse,
    TranscodingProgressResponse,
    TranscriptionResponse,
    VideoListResponse,
    VideoQualityResponse,
    VideoRatingAggregates,
    VideoResponse,
    VideoSocialStatus,
    VideoTagInfo,
)
from api.versioning import VersionHeaderMiddleware, configure_openapi_schema
from config import (
    API_INCLUDE_LEGACY_ROUTES,
    API_VERSION,
    AUTOPLAY_COUNTDOWN_SECONDS,
    AUTOPLAY_ENABLED,
    CORS_ALLOWED_ORIGINS,
    DOWNLOADS_ALLOW_ORIGINAL,
    DOWNLOADS_ALLOW_TRANSCODED,
    DOWNLOADS_ENABLED,
    DOWNLOADS_MAX_CONCURRENT,
    DOWNLOADS_RATE_LIMIT_PER_HOUR,
    EMBED_ALLOW_ALL_DOMAINS,
    EMBED_ALLOWED_DOMAINS,
    EMBED_DEFAULT_AUTOPLAY,
    EMBED_ENABLED,
    EMBED_MIN_PLAYBACK_FOR_VIEW,
    LIVE_ENABLED,
    LIVE_STORAGE_PATH,
    NAS_STORAGE,
    OPENAPI_DESCRIPTION,
    OPENAPI_TITLE,
    PUBLIC_PORT,
    QUALITY_NAMES,
    RATE_LIMIT_EMBED,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_PUBLIC_ANALYTICS,
    RATE_LIMIT_PUBLIC_DEFAULT,
    RATE_LIMIT_PUBLIC_VIDEOS_LIST,
    RATE_LIMIT_STORAGE_URL,
    SECURE_COOKIES,
    SUPPORTED_VIDEO_EXTENSIONS,
    UPLOADS_DIR,
    UPNEXT_ENABLED,
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

# Initialize structured logging (Issue #208) - must be before any getLogger() calls
setup_logging()

logger = logging.getLogger(__name__)

# Cached watermark settings
#
# Cache watermark settings for 60 seconds to balance freshness with database load.
# Rationale: At 60 seconds, settings changes appear within 1 minute while avoiding
# a database query on every video page load (can be 100+ requests/minute under load).
# Tradeoffs:
#   - Shorter TTL (30s): More responsive to changes, but 2x database queries
#   - Longer TTL (300s): Lower DB load, but 5 minute delay before changes appear
#   - 60s chosen as reasonable default for admin-controlled settings that rarely change
_cached_watermark_settings: Dict[str, Any] = {}
_cached_watermark_settings_time: float = 0
_WATERMARK_SETTINGS_CACHE_TTL_SECONDS = 60

# Video list cache for performance (Issue #429)
# Caches video list query results to reduce database load.
# Performance review (Issue #211): Increased default from 30s to 300s for reduced DB load.
# TTL is configurable via the VIDEO_LIST_CACHE_TTL environment variable (seconds).
_VIDEO_LIST_CACHE_TTL_DEFAULT = 300
_VIDEO_LIST_CACHE_TTL = int(os.getenv("VIDEO_LIST_CACHE_TTL", _VIDEO_LIST_CACHE_TTL_DEFAULT))
_video_list_cache = AnalyticsCache(ttl_seconds=_VIDEO_LIST_CACHE_TTL, enabled=True, max_size=500)


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
    if _cached_watermark_settings and (now - _cached_watermark_settings_time) < _WATERMARK_SETTINGS_CACHE_TTL_SECONDS:
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


# Cached CDN settings
#
# Same 60-second TTL rationale as watermark settings: balances freshness vs DB load.
# CDN settings change rarely (typically during initial setup or migrations), so
# the 1-minute delay is acceptable for the reduced database overhead.
_cached_cdn_settings: Dict[str, Any] = {}
_cached_cdn_settings_time: float = 0
_CDN_SETTINGS_CACHE_TTL_SECONDS = 60


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
    if _cached_cdn_settings and (now - _cached_cdn_settings_time) < _CDN_SETTINGS_CACHE_TTL_SECONDS:
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


# Cached embed settings
#
# Same 60-second TTL rationale: admin-controlled settings that change rarely.
# Embed settings (autoplay, domain restrictions) are typically set once during
# deployment and only adjusted when security posture changes.
_cached_embed_settings: Dict[str, Any] = {}
_cached_embed_settings_time: float = 0
_EMBED_SETTINGS_CACHE_TTL_SECONDS = 60


async def get_embed_settings() -> Dict[str, Any]:
    """
    Get embed settings from database with caching and env var fallback.

    Settings are cached locally for 60 seconds to avoid database round-trips
    on every embed page request.

    Returns:
        Dict with keys:
        - enabled: Whether embeds are enabled
        - allowed_domains: Domain whitelist for frame-ancestors CSP
        - allow_all_domains: Allow embedding on any domain
        - default_autoplay: Default autoplay behavior
        - min_playback_for_view: Seconds before counting as view
    """
    global _cached_embed_settings, _cached_embed_settings_time

    now = time.time()
    if _cached_embed_settings and (now - _cached_embed_settings_time) < _EMBED_SETTINGS_CACHE_TTL_SECONDS:
        return _cached_embed_settings

    try:
        from api.settings_service import get_settings_service

        service = get_settings_service()

        settings = {
            "enabled": await service.get("embed.enabled", EMBED_ENABLED),
            "allowed_domains": await service.get("embed.allowed_domains", EMBED_ALLOWED_DOMAINS),
            "allow_all_domains": await service.get("embed.allow_all_domains", EMBED_ALLOW_ALL_DOMAINS),
            "default_autoplay": await service.get("embed.default_autoplay", EMBED_DEFAULT_AUTOPLAY),
            "min_playback_for_view": await service.get("embed.min_playback_for_view", EMBED_MIN_PLAYBACK_FOR_VIEW),
        }

        _cached_embed_settings = settings
        _cached_embed_settings_time = now
    except Exception as e:
        logger.debug(f"Failed to get embed settings from DB, using env vars: {e}")
        _cached_embed_settings = {
            "enabled": EMBED_ENABLED,
            "allowed_domains": EMBED_ALLOWED_DOMAINS,
            "allow_all_domains": EMBED_ALLOW_ALL_DOMAINS,
            "default_autoplay": EMBED_DEFAULT_AUTOPLAY,
            "min_playback_for_view": EMBED_MIN_PLAYBACK_FOR_VIEW,
        }
        _cached_embed_settings_time = now

    return _cached_embed_settings


# Domain validation pattern for CSP frame-ancestors (Issue #210 security fix)
_CSP_DOMAIN_PATTERN = re.compile(
    r'^([a-z]+://)?[a-z0-9]+([\-\.][a-z0-9]+)*\.[a-z]{2,}(:[0-9]{1,5})?$',
    re.IGNORECASE
)


def _is_valid_csp_domain(domain: str) -> bool:
    """
    Validate that a domain is safe for CSP frame-ancestors.

    Args:
        domain: Domain string to validate (with or without protocol)

    Returns:
        True if domain is valid, False otherwise
    """
    # Remove protocol for validation if present
    domain_to_check = domain
    if domain.startswith("http://") or domain.startswith("https://"):
        domain_to_check = domain.split("://", 1)[1]

    return bool(_CSP_DOMAIN_PATTERN.match(domain_to_check))


def build_embed_csp_frame_ancestors(embed_settings: Dict[str, Any]) -> str:
    """
    Build the frame-ancestors CSP directive value based on embed settings.

    Uses the DB-backed settings so admin changes apply without restart.
    Security: Validates all domains before including in CSP header.

    Args:
        embed_settings: Dict from get_embed_settings() with keys:
            - allow_all_domains: bool
            - allowed_domains: str (comma-separated)

    Returns:
        CSP frame-ancestors value:
        - "*" if all domains allowed (public mode)
        - "'self'" if only same-origin allowed (default secure mode)
        - "'self' https://domain1.com ..." for domain whitelist
    """
    # Check for allow all domains first
    if embed_settings.get("allow_all_domains", False):
        return "*"

    # Parse allowed domains from configuration
    allowed = embed_settings.get("allowed_domains", "'self'").strip()

    # If 'self' only or empty, return self
    if not allowed or allowed == "'self'":
        return "'self'"

    # Parse comma-separated domains and build frame-ancestors value
    domains = [d.strip() for d in allowed.split(",") if d.strip()]
    if not domains:
        return "'self'"

    # Include 'self' and validate/normalize each domain
    parts = ["'self'"]
    for domain in domains:
        # Skip 'self' if explicitly listed (already included)
        if domain == "'self'":
            continue

        # Validate domain format to prevent CSP injection
        if domain.startswith("'"):
            # CSP keywords like 'none' - skip these in domain list
            logger.warning(f"Skipping CSP keyword in embed domains: {domain}")
            continue
        elif domain.startswith("http://") or domain.startswith("https://"):
            if _is_valid_csp_domain(domain):
                parts.append(domain)
            else:
                logger.warning(f"Invalid CSP domain format skipped: {domain}")
        else:
            if _is_valid_csp_domain(domain):
                parts.append(f"https://{domain}")
            else:
                logger.warning(f"Invalid CSP domain format skipped: {domain}")

    # If all domains were invalid, fall back to self-only
    if len(parts) == 1:
        logger.warning("All configured embed domains were invalid, using 'self' only")

    return " ".join(parts)


def reset_embed_settings_cache() -> None:
    """Reset the cached embed settings. Useful for testing."""
    global _cached_embed_settings, _cached_embed_settings_time
    _cached_embed_settings = {}
    _cached_embed_settings_time = 0


def validate_safe_path(base: Path, user_path: str) -> Path:
    """
    Validate that a path stays within the base directory to prevent path traversal.

    Args:
        base: The base directory that paths must be contained within
        user_path: The user-provided path to validate

    Returns:
        The resolved full path if valid

    Raises:
        HTTPException: If the path is invalid or traverses outside base
    """
    if not user_path:
        raise HTTPException(status_code=400, detail="Path cannot be empty")

    # Block obvious traversal attempts
    if ".." in user_path:
        logger.warning(f"Path traversal attempt blocked: {user_path}")
        raise HTTPException(status_code=400, detail="Invalid path")

    try:
        # Resolve both paths to catch symlink attacks
        full_path = (base / user_path).resolve()
        base_resolved = base.resolve()

        # Ensure the path is within the base directory
        full_path.relative_to(base_resolved)
        return full_path
    except (ValueError, OSError) as e:
        logger.warning(f"Path validation failed for {user_path}: {e}")
        raise HTTPException(status_code=400, detail="Invalid path")


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

    # Start live streaming background tasks
    if LIVE_ENABLED:
        from api.live_tasks import start_live_background_tasks
        await start_live_background_tasks()
        logger.info("Started live streaming background tasks")

    yield

    # Stop live streaming background tasks
    if LIVE_ENABLED:
        from api.live_ingest import stop_accepting_uploads, wait_for_active_uploads
        from api.live_tasks import stop_live_background_tasks
        stop_accepting_uploads()
        await wait_for_active_uploads(timeout=10.0)
        await stop_live_background_tasks(timeout=10.0)
        logger.info("Stopped live streaming background tasks")

    await database.disconnect()


app = FastAPI(
    title=OPENAPI_TITLE,
    description=OPENAPI_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)

# Create versioned API router
# All /api endpoints are registered on this router and mounted with version prefix
v1_router = APIRouter(tags=["API v1"])

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
app.add_middleware(VersionHeaderMiddleware)

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

# Serve live stream segments and playlists
if not os.environ.get("VLOG_TEST_MODE") and LIVE_ENABLED:
    try:
        LIVE_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
        app.mount("/live", HLSStaticFiles(directory=str(LIVE_STORAGE_PATH)), name="live")
        logger.info(f"Mounted live storage at /live from {LIVE_STORAGE_PATH}")
    except (PermissionError, OSError) as e:
        logger.warning(f"Could not mount live storage: {e}")

# Mount live ingest API (for segment push from FFmpeg)
if LIVE_ENABLED:
    from api.live_ingest import router as live_ingest_router
    app.include_router(live_ingest_router)
    logger.info("Mounted live ingest API at /api/live/ingest")

# Serve static web files
WEB_DIR = Path(__file__).parent.parent / "web" / "public"
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

# Serve studio web files (broadcaster dashboard)
# Requires built dist/ directory - run: cd web/studio && npm install && npm run build
STUDIO_SRC_DIR = Path(__file__).parent.parent / "web" / "studio"
STUDIO_DIST_DIR = STUDIO_SRC_DIR / "dist"

if STUDIO_DIST_DIR.exists():
    STUDIO_WEB_DIR = STUDIO_DIST_DIR
    app.mount("/studio/assets", StaticFiles(directory=str(STUDIO_DIST_DIR / "assets")), name="studio-assets")
    logger.info("Mounted studio dashboard at /studio")
else:
    STUDIO_WEB_DIR = None
    logger.warning(
        "Studio dist/ not found. Run 'cd web/studio && npm install && npm run build' to enable /studio"
    )


@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the main page."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/studio", response_class=HTMLResponse)
@app.get("/studio/", response_class=HTMLResponse)
async def studio_home():
    """Serve the studio broadcaster dashboard."""
    if STUDIO_WEB_DIR is None:
        return JSONResponse(
            status_code=503,
            content={"detail": "Studio not available. Run 'cd web/studio && npm install && npm run build' to enable."},
        )
    return FileResponse(STUDIO_WEB_DIR / "index.html")


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


# =============================================================================
# Video Embed Routes (Issue #210)
# =============================================================================


def _is_video_embeddable(video: dict) -> bool:
    """Check if video meets all requirements for embedding."""
    return (
        video["status"] == VideoStatus.READY
        and video["published_at"] is not None
        and video["deleted_at"] is None
    )


def _build_embed_error_response(
    reason: str,
    slug: str,
) -> FileResponse:
    """
    Build appropriate error response for embed requests.

    Args:
        reason: Error reason ('disabled', 'invalid_slug', 'not_found', 'not_embeddable', 'database_error')
        slug: Video slug for logging

    Returns:
        FileResponse with embed-error.html and appropriate headers
    """
    status_codes = {
        "disabled": 404,
        "invalid_slug": 400,
        "not_found": 404,
        "not_embeddable": 404,
        "database_error": 503,
    }

    headers = {"Content-Security-Policy": "frame-ancestors 'none'"}
    if reason == "database_error":
        headers["Retry-After"] = "30"

    return FileResponse(
        WEB_DIR / "embed-error.html",
        status_code=status_codes.get(reason, 500),
        headers=headers,
    )


@app.get("/embed/{slug}", response_class=HTMLResponse)
@limiter.limit(RATE_LIMIT_EMBED)
async def embed_page(request: Request, slug: str):
    """
    Serve the embed player page for a video.

    Returns a minimal video player designed for iframe embedding.
    Sets appropriate CSP frame-ancestors header based on configuration.

    Security:
    - Validates slug format before database query
    - Only serves videos with status='ready' and published_at set
    - CSP frame-ancestors controls which domains can embed
    - Does NOT set X-Frame-Options (CSP takes precedence)

    Query Parameters:
    - autoplay: 0 or 1 (default: from settings)
    - start: Start time in seconds (default: 0)
    - controls: 0 or 1 (default: 1)
    """
    # Validate slug format (security: prevents log injection, ensures valid input)
    if not validate_slug(slug):
        return _build_embed_error_response("invalid_slug", slug)

    # Check if embeds are enabled
    embed_settings = await get_embed_settings()
    if not embed_settings["enabled"]:
        return _build_embed_error_response("disabled", slug)

    # Validate video exists and is embeddable
    try:
        query = sa.select(
            videos.c.id,
            videos.c.slug,
            videos.c.status,
            videos.c.published_at,
            videos.c.deleted_at,
        ).where(videos.c.slug == slug)

        video = await fetch_one_with_retry(query)

        if not video:
            logger.debug(f"Embed: Video not found: {slug}")
            return _build_embed_error_response("not_found", slug)

        if not _is_video_embeddable(video):
            logger.debug(
                f"Embed: Video not embeddable: {slug} "
                f"(status={video['status']}, published={video['published_at'] is not None})"
            )
            return _build_embed_error_response("not_embeddable", slug)

    except Exception as e:
        logger.warning(f"Embed: Database error for {slug}: {e}")
        return _build_embed_error_response("database_error", slug)

    # Build CSP frame-ancestors directive from DB-backed settings
    frame_ancestors = build_embed_csp_frame_ancestors(embed_settings)

    # Return embed page with CSP header
    return FileResponse(
        WEB_DIR / "embed.html",
        headers={
            "Content-Security-Policy": f"frame-ancestors {frame_ancestors}",
        },
    )


@v1_router.get("/videos/{slug}/embed-code", response_model=EmbedCodeResponse)
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_embed_code(request: Request, slug: str):
    """
    Get the embed code for a video.

    Returns the iframe HTML and URL for embedding the video on external sites.

    Security:
    - Validates slug format before database query

    Returns 404 if:
    - Video doesn't exist
    - Video is not ready or not published
    - Embeds are disabled
    """
    # Validate slug format (security: prevents log injection, ensures valid input)
    require_valid_slug(slug, "video")

    # Check if embeds are enabled
    embed_settings = await get_embed_settings()
    if not embed_settings["enabled"]:
        raise HTTPException(status_code=404, detail="Video embedding is disabled")

    # Validate video exists and is embeddable
    query = sa.select(
        videos.c.id,
        videos.c.slug,
        videos.c.status,
        videos.c.published_at,
        videos.c.deleted_at,
    ).where(videos.c.slug == slug)

    video = await fetch_one_with_retry(query)

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not _is_video_embeddable(video):
        raise HTTPException(status_code=404, detail="Video not available for embedding")

    # Build embed URL
    # Use request's base URL for the embed URL
    base_url = str(request.base_url).rstrip("/")
    embed_url = f"{base_url}/embed/{slug}"

    # Standard dimensions for 16:9 aspect ratio
    width = 560
    height = 315

    # Build iframe HTML with proper attributes
    iframe_html = (
        f'<iframe src="{embed_url}" '
        f'width="{width}" height="{height}" '
        f'frameborder="0" '
        f'allow="autoplay; fullscreen; picture-in-picture" '
        f'allowfullscreen></iframe>'
    )

    return EmbedCodeResponse(
        embed_url=embed_url,
        iframe_html=iframe_html,
        width=width,
        height=height,
    )


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


@v1_router.get("/videos", summary="List videos", description="Get a paginated list of published videos with filtering and sorting options.")
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


@v1_router.get("/videos/bulk", summary="Bulk get videos", description="Get multiple videos by their slugs in a single request.")
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


@v1_router.get("/videos/{slug}", summary="Get video", description="Get detailed information about a specific video by its slug.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_video(request: Request, slug: str) -> VideoResponse:
    """Get a single video by slug."""
    # Validate slug to prevent path traversal attacks
    require_valid_slug(slug, "video")

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


@v1_router.get("/videos/{slug}/progress", summary="Get video progress", description="Get transcoding progress for a video.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_video_progress(request: Request, slug: str) -> TranscodingProgressResponse:
    """Get transcoding progress for a video."""
    # Validate slug to prevent path traversal attacks
    require_valid_slug(slug, "video")

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


@v1_router.get("/videos/{slug}/transcript", summary="Get video transcript", description="Get the transcription for a video if available.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_transcript(request: Request, slug: str) -> TranscriptionResponse:
    """Get transcription status and text for a video."""
    # Validate slug to prevent path traversal attacks
    require_valid_slug(slug, "video")

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


async def _find_related_videos_for_slug(
    slug: str,
    limit: int = 12,
    parallelize: bool = False,
) -> List[Dict[str, Any]]:
    """
    Shared helper to find related videos for a given video slug.

    Implements the tiered algorithm:
    1. Same category + shared tags (highest relevance)
    2. Same category only
    3. Shared tags only
    4. Recent videos (fallback)

    Args:
        slug: The source video slug
        limit: Maximum number of related videos to return
        parallelize: If True, run all tier queries in parallel (optimal for limit=1)

    Returns:
        List of related video row dicts, or empty list if source not found

    Raises:
        HTTPException: If video not found (404)
    """
    # Validate slug
    require_valid_slug(slug, "video")

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

    seen_ids: set = {video_id}  # Always exclude the source video

    if parallelize:
        # Performance optimization: Run all tiers in parallel for limit=1 (Issue #211)
        # This is optimal when we just need one result and don't know which tier will succeed
        # Build list of (priority, coroutine) tuples - only include applicable tiers
        tier_coros: List[Tuple[int, Any]] = []

        # Tier 1: Same category + shared tags (highest relevance)
        if category_id is not None and source_tag_ids:
            tier_coros.append((1, _fetch_related_videos_tier(
                category_id=category_id,
                tag_ids=source_tag_ids,
                exclude_ids=seen_ids,
                limit=limit,
            )))

        # Tier 2: Same category only
        if category_id is not None:
            tier_coros.append((2, _fetch_related_videos_tier(
                category_id=category_id,
                tag_ids=None,
                exclude_ids=seen_ids,
                limit=limit,
            )))

        # Tier 3: Shared tags only
        if source_tag_ids:
            tier_coros.append((3, _fetch_related_videos_tier(
                category_id=None,
                tag_ids=source_tag_ids,
                exclude_ids=seen_ids,
                limit=limit,
            )))

        # Tier 4: Recent videos fallback (always included)
        tier_coros.append((4, _fetch_related_videos_tier(
            category_id=None,
            tag_ids=None,
            exclude_ids=seen_ids,
            limit=limit,
        )))

        # Run all applicable tiers in parallel
        tier_results = await asyncio.gather(
            *[coro for _, coro in tier_coros],
            return_exceptions=True
        )

        # Match results back to priorities and find first non-empty by priority
        results_with_priority = list(zip([p for p, _ in tier_coros], tier_results))
        results_with_priority.sort(key=lambda x: x[0])  # Sort by priority

        for priority, tier_result in results_with_priority:
            if isinstance(tier_result, Exception):
                logger.warning(f"Tier {priority} query failed: {tier_result}")
                continue
            if tier_result:
                return tier_result[:limit]

        return []

    # Sequential execution with early termination (optimal for larger limits)
    related_videos: List[Dict[str, Any]] = []

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

    return related_videos


@v1_router.get("/videos/{slug}/related", summary="Get related videos", description="Get videos related to the specified video based on tags and category.")
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

    Results are cached for 300 seconds using the video list cache.

    Args:
        slug: The video slug to find related videos for
        limit: Maximum number of related videos (1-24, default 12)

    Returns:
        List of related videos sorted by relevance tier then recency
    """
    # Build cache key with SHA256 hash to prevent cache poisoning
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

    # Use shared helper for the tiered algorithm
    related_videos = await _find_related_videos_for_slug(slug, limit=limit, parallelize=False)

    # Get tags for all related videos
    video_ids = [v["id"] for v in related_videos]
    video_tags_map = await get_video_tags(video_ids)

    # Build response using existing helper
    result = build_video_list_response(related_videos, video_tags_map)

    # Cache the result
    _video_list_cache.set(cache_key, [v.model_dump() for v in result])

    return result


@v1_router.get("/videos/{slug}/next", summary="Get next video", description="Get the next suggested video for autoplay.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_next_video(
    request: Request,
    slug: str,
) -> Optional[VideoListResponse]:
    """
    Get the next video suggestion for autoplay.

    Returns the single best related video for the "Up Next" feature.
    Uses the same tiered algorithm as related videos but returns only
    the top result. Parallelizes tier queries for optimal latency.

    Args:
        slug: The current video slug

    Returns:
        Single video suggestion, or null if no related videos found
    """
    # Build cache key with SHA256 hash
    NEXT_VIDEO_CACHE_VERSION = "v1"
    cache_key_raw = f"next:{NEXT_VIDEO_CACHE_VERSION}:{slug}"
    cache_key = f"next:{hashlib.sha256(cache_key_raw.encode()).hexdigest()[:16]}"

    # Check cache first
    cached = _video_list_cache.get(cache_key)
    if cached is not None:
        if cached == []:
            return None
        try:
            return VideoListResponse(**cached[0])
        except Exception as e:
            logger.warning(f"Cached next video schema mismatch, invalidating: {e}")
            _video_list_cache.delete(cache_key)

    # Use shared helper with parallelization for optimal latency (Issue #211)
    # For limit=1, running all tiers in parallel is faster than sequential with early termination
    related_videos = await _find_related_videos_for_slug(slug, limit=1, parallelize=True)

    if not related_videos:
        # Cache empty result to avoid repeated lookups
        _video_list_cache.set(cache_key, [])
        return None

    next_video = related_videos[0]

    # Get tags for the next video
    video_tags_map = await get_video_tags([next_video["id"]])

    # Build response
    result = build_video_list_response([next_video], video_tags_map)

    # Cache the result
    _video_list_cache.set(cache_key, [v.model_dump() for v in result])

    return result[0] if result else None


@v1_router.get("/categories", summary="List categories", description="Get all video categories.")
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


@v1_router.get("/categories/{slug}", summary="Get category", description="Get a category by its slug.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_category(request: Request, slug: str) -> CategoryResponse:
    """Get a single category by slug."""
    # Validate slug to prevent path traversal attacks
    require_valid_slug(slug, "category")

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


@v1_router.get("/tags", summary="List tags", description="Get all video tags.")
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


@v1_router.get("/tags/{slug}", summary="Get tag", description="Get a tag by its slug.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_tag(request: Request, slug: str) -> TagResponse:
    """Get a single tag by slug."""
    # Validate slug to prevent path traversal attacks
    require_valid_slug(slug, "tag")

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


@v1_router.get("/playlists", summary="List playlists", description="Get all public playlists.")
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


@v1_router.get("/playlists/{slug}", summary="Get playlist", description="Get a playlist by its slug.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_public_playlist(request: Request, slug: str) -> PlaylistDetailResponse:
    """Get a public playlist by slug with its videos."""
    # Validate slug
    require_valid_slug(slug, "playlist")

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


@v1_router.get("/playlists/{slug}/videos", summary="Get playlist videos", description="Get videos in a playlist.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_public_playlist_videos(request: Request, slug: str) -> List[PlaylistVideoInfo]:
    """Get videos in a public playlist."""
    # Validate slug
    require_valid_slug(slug, "playlist")

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
# Live Streaming Public API
# ============================================================================


def _parse_qualities_json(qualities_str: Optional[str]) -> List[str]:
    """Parse qualities JSON string to list."""
    if not qualities_str:
        return []
    try:
        return json.loads(qualities_str)
    except json.JSONDecodeError:
        return []


@v1_router.get("/live/streams", summary="List live streams", description="Get all public live streams.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def list_public_live_streams(request: Request) -> PublicLiveStreamListResponse:
    """
    List live streams that are currently live or recently ended.

    Only returns streams with status 'live' or 'ending'.
    """
    if not LIVE_ENABLED:
        return PublicLiveStreamListResponse(streams=[], total=0)

    rows = await fetch_all_with_retry(
        live_streams.select()
        .where(live_streams.c.status.in_(["live", "ending"]))
        .order_by(live_streams.c.started_at.desc())
    )

    streams = []
    for row in rows:
        streams.append(
            PublicLiveStreamResponse(
                title=row["title"],
                slug=row["slug"],
                description=row["description"] or "",
                status=row["status"],
                qualities=_parse_qualities_json(row["qualities"]),
                category_id=row["category_id"],
                started_at=row["started_at"],
                dvr_enabled=row["dvr_enabled"],
                dvr_window_seconds=row["dvr_window_seconds"],
            )
        )

    return PublicLiveStreamListResponse(streams=streams, total=len(streams))


@v1_router.get("/live/streams/{slug}", summary="Get live stream", description="Get information about a live stream.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_public_live_stream(request: Request, slug: str) -> PublicLiveStreamResponse:
    """
    Get public information about a live stream.

    Returns stream info if it exists and is live or ending.
    Returns 404 if stream doesn't exist or is not active.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    row = await fetch_one_with_retry(
        live_streams.select()
        .where(live_streams.c.slug == slug)
        .where(live_streams.c.status.in_(["live", "ending"]))
    )

    if not row:
        raise HTTPException(status_code=404, detail="Live stream not found")

    return PublicLiveStreamResponse(
        title=row["title"],
        slug=row["slug"],
        description=row["description"] or "",
        status=row["status"],
        qualities=_parse_qualities_json(row["qualities"]),
        category_id=row["category_id"],
        started_at=row["started_at"],
        dvr_enabled=row["dvr_enabled"],
        dvr_window_seconds=row["dvr_window_seconds"],
    )


# ============================================================================
# Watermark Configuration
# ============================================================================


@v1_router.get("/config/watermark", summary="Get watermark config", description="Get watermark configuration for video overlay.")
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

# Cached display settings
#
# Same 60-second TTL rationale: balances freshness vs DB load for admin settings.
# Display settings (view counts visibility, tagline) are cosmetic and rarely change.
_cached_display_settings: Dict[str, Any] = {}
_cached_display_settings_time: float = 0
_DISPLAY_SETTINGS_CACHE_TTL_SECONDS = 60


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
    if _cached_display_settings and (now - _cached_display_settings_time) < _DISPLAY_SETTINGS_CACHE_TTL_SECONDS:
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


@v1_router.get("/config/display", summary="Get display config", description="Get display configuration for the video player.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_display_config(request: Request):
    """
    Get display configuration for the public UI.

    Returns display settings like whether to show view counts.
    """
    settings = await get_display_settings()
    return settings


# ============================================================================
# Theme/Branding Configuration (Issue #214)
# ============================================================================

# Cached theme settings
#
# Same 60-second TTL rationale: theme/branding settings change rarely.
# Theme colors and branding are typically set during initial deployment.
_cached_theme_settings: Dict[str, Any] = {}
_cached_theme_settings_time: float = 0
_THEME_SETTINGS_CACHE_TTL_SECONDS = 60


async def get_theme_settings() -> Dict[str, Any]:
    """
    Get theme and branding settings from database with caching.

    Returns dict with branding, theme colors, and layout configuration.
    """
    import time

    global _cached_theme_settings, _cached_theme_settings_time

    now = time.time()
    if _cached_theme_settings and (now - _cached_theme_settings_time) < _THEME_SETTINGS_CACHE_TTL_SECONDS:
        return _cached_theme_settings

    try:
        from api.settings_service import get_settings_service

        service = get_settings_service()

        settings = {
            # Branding
            "site_name": await service.get("branding.site_name", "VLog"),
            "logo_path": await service.get("branding.logo_path", None),
            "favicon_path": await service.get("branding.favicon_path", None),
            "footer_text": await service.get("branding.footer_text", None),
            "footer_links": await service.get("branding.footer_links", []),
            # Theme colors
            "primary_color": await service.get("theme.primary_color", "#3B82F6"),
            "secondary_color": await service.get("theme.secondary_color", "#1E40AF"),
            "accent_color": await service.get("theme.accent_color", "#60A5FA"),
            "mode": await service.get("theme.mode", "auto"),
            "custom_css": await service.get("theme.custom_css", None),
            # Layout
            "homepage_style": await service.get("layout.homepage_style", "grid"),
            "videos_per_page": await service.get("layout.videos_per_page", 24),
            "grid_columns": await service.get("layout.grid_columns", 4),
            "show_sidebar": await service.get("layout.show_sidebar", True),
            "show_related_videos": await service.get("layout.show_related_videos", True),
        }

        _cached_theme_settings = settings
        _cached_theme_settings_time = now

    except Exception as e:
        logger.debug(f"Failed to get theme settings from DB, using defaults: {e}")
        _cached_theme_settings = {
            "site_name": "VLog",
            "logo_path": None,
            "favicon_path": None,
            "footer_text": None,
            "footer_links": [],
            "primary_color": "#3B82F6",
            "secondary_color": "#1E40AF",
            "accent_color": "#60A5FA",
            "mode": "auto",
            "custom_css": None,
            "homepage_style": "grid",
            "videos_per_page": 24,
            "grid_columns": 4,
            "show_sidebar": True,
            "show_related_videos": True,
        }
        _cached_theme_settings_time = now

    return _cached_theme_settings


def reset_theme_settings_cache() -> None:
    """
    Reset the cached theme settings.

    Should be called when branding/theme settings are updated via admin API
    to ensure the public API reflects changes immediately.
    """
    global _cached_theme_settings, _cached_theme_settings_time
    _cached_theme_settings = {}
    _cached_theme_settings_time = 0


@v1_router.get("/config/theme", summary="Get theme config", description="Get theme and branding configuration.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_theme_config(request: Request):
    """
    Get theme and branding configuration for the public UI.

    Returns branding settings (site name, logo, footer), theme colors,
    and layout preferences.
    """
    settings = await get_theme_settings()
    return settings


@v1_router.get("/branding/logo", summary="Get logo image", description="Serve the site logo image.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_public_logo(request: Request):
    """Serve the logo image for public display."""
    settings = await get_theme_settings()

    if not settings.get("logo_path"):
        raise HTTPException(status_code=404, detail="No logo configured")

    # Validate path to prevent traversal attacks
    logo_path = validate_safe_path(NAS_STORAGE, settings["logo_path"])
    if not logo_path.exists():
        raise HTTPException(status_code=404, detail="Logo image not found")

    ext = logo_path.suffix.lower()
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
        logo_path,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",  # Cache for 1 day
            "X-Content-Type-Options": "nosniff",
        },
    )


@v1_router.get("/branding/favicon", summary="Get favicon", description="Serve the site favicon.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_public_favicon(request: Request):
    """Serve the favicon for public display."""
    settings = await get_theme_settings()

    if not settings.get("favicon_path"):
        raise HTTPException(status_code=404, detail="No favicon configured")

    # Validate path to prevent traversal attacks
    favicon_path = validate_safe_path(NAS_STORAGE, settings["favicon_path"])
    if not favicon_path.exists():
        raise HTTPException(status_code=404, detail="Favicon not found")

    ext = favicon_path.suffix.lower()
    content_types = {
        ".ico": "image/x-icon",
        ".png": "image/png",
        ".svg": "image/svg+xml",
    }
    content_type = content_types.get(ext, "application/octet-stream")

    return FileResponse(
        favicon_path,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",  # Cache for 1 day
            "X-Content-Type-Options": "nosniff",
        },
    )


# ============================================================================
# Download Configuration (Issue #202)
# ============================================================================

# Cached download settings
#
# Same 60-second TTL rationale: download permissions change rarely.
# Download settings (enabled, rate limits) are security-related and
# typically only change when operational policies are updated.
_cached_download_settings: Dict[str, Any] = {}
_cached_download_settings_time: float = 0
_DOWNLOAD_SETTINGS_CACHE_TTL_SECONDS = 60
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
    if _cached_download_settings and (now - _cached_download_settings_time) <= _DOWNLOAD_SETTINGS_CACHE_TTL_SECONDS:
        return _cached_download_settings

    # Slow path: acquire lock and refresh cache
    async with _get_download_settings_lock():
        # Double-check after acquiring lock (another request may have refreshed)
        now = time.time()
        if _cached_download_settings and (now - _cached_download_settings_time) <= _DOWNLOAD_SETTINGS_CACHE_TTL_SECONDS:
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


@v1_router.get("/config/downloads", summary="Get downloads config", description="Get download configuration and availability.")
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


# ============================================================================
# Playback Configuration (Issue #211)
# ============================================================================

# Cached playback settings
#
# Same 60-second TTL rationale: playback settings (autoplay, up-next) change rarely.
# These user-experience settings are typically configured during initial setup.
_cached_playback_settings: Dict[str, Any] = {}
_cached_playback_settings_time: float = 0
_PLAYBACK_SETTINGS_CACHE_TTL_SECONDS = 60


async def get_playback_settings() -> Dict[str, Any]:
    """
    Get playback settings from database with caching.

    Returns dict with:
    - autoplay_enabled: bool (default True)
    - upnext_enabled: bool (default True)
    - autoplay_countdown_seconds: int (default 10)

    Note: No locking needed - dict reads are atomic and stale data
    for 60s is acceptable for these settings.
    """
    global _cached_playback_settings, _cached_playback_settings_time

    now = time.time()
    if _cached_playback_settings and (now - _cached_playback_settings_time) < _PLAYBACK_SETTINGS_CACHE_TTL_SECONDS:
        return _cached_playback_settings

    try:
        from api.settings_service import get_settings_service

        service = get_settings_service()

        settings = {
            "autoplay_enabled": await service.get("playback.autoplay_enabled", AUTOPLAY_ENABLED),
            "upnext_enabled": await service.get("playback.upnext_enabled", UPNEXT_ENABLED),
            "autoplay_countdown_seconds": await service.get(
                "playback.autoplay_countdown_seconds", AUTOPLAY_COUNTDOWN_SECONDS
            ),
        }

        _cached_playback_settings = settings
        _cached_playback_settings_time = now

    except Exception as e:
        logger.warning(f"Failed to get playback settings from DB, using defaults: {e}")
        _cached_playback_settings = {
            "autoplay_enabled": AUTOPLAY_ENABLED,
            "upnext_enabled": UPNEXT_ENABLED,
            "autoplay_countdown_seconds": AUTOPLAY_COUNTDOWN_SECONDS,
        }
        _cached_playback_settings_time = now

    return _cached_playback_settings


@v1_router.get("/config/playback", summary="Get playback config", description="Get playback configuration for autoplay and up-next features.")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_playback_config(request: Request):
    """
    Get playback configuration for the UI.

    Returns autoplay and up-next settings that control video player behavior.
    This is used by the watch page to enable/disable autoplay features.
    """
    settings = await get_playback_settings()

    return {
        "autoplay_enabled": settings["autoplay_enabled"],
        "upnext_enabled": settings["upnext_enabled"],
        "autoplay_countdown_seconds": settings["autoplay_countdown_seconds"],
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


@v1_router.get("/videos/{slug}/download/original", summary="Download original video", description="Download the original video file if downloads are enabled.")
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
    require_valid_slug(slug, "video")

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


@v1_router.post("/analytics/session", summary="Create analytics session", description="Start a new playback analytics session.")
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


@v1_router.post("/analytics/heartbeat", summary="Send analytics heartbeat", description="Send periodic heartbeat during video playback.")
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


@v1_router.post("/analytics/end", summary="End analytics session", description="End a playback analytics session.")
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


# =============================================================================
# Comments and Ratings API (Issue #213)
# Social engagement features with threading support
# =============================================================================

# Rate limiting for comments and ratings
RATE_LIMIT_COMMENTS = "5/minute"
RATE_LIMIT_COMMENTS_HOURLY = "20/hour"
RATE_LIMIT_RATINGS = "10/minute"


def sanitize_comment_content(content: str) -> str:
    """
    Sanitize comment content to prevent XSS.

    Strips all HTML tags and attributes, returning plain text only.
    Uses bleach library as recommended by security review.
    """
    return bleach.clean(content, tags=[], strip=True)


async def get_video_by_slug(slug: str) -> dict:
    """
    Get a video by slug with validation.

    Returns the video row or raises 404.
    """
    require_valid_slug(slug, "video")

    video_query = (
        videos.select()
        .where(videos.c.slug == slug)
        .where(videos.c.deleted_at.is_(None))
        .where(videos.c.published_at.is_not(None))
    )
    video = await fetch_one_with_retry(video_query)

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    return video


async def _build_social_settings(video: dict) -> dict:
    """
    Build resolved social settings from a video record.

    Internal helper that inherits from global settings if per-video settings are NULL.
    """
    from api.settings_service import get_settings_service

    service = get_settings_service()

    # Get global settings
    global_comments = await service.get("social.comments_enabled", True)
    global_ratings = await service.get("social.ratings_enabled", True)
    ratings_type = await service.get("social.ratings_type", "stars")
    require_approval = await service.get("social.comments_require_approval", False)
    max_length = await service.get("social.comments_max_length", 5000)
    max_depth = await service.get("social.comments_max_depth", 5)

    # Per-video settings override global if not NULL
    comments_enabled = video["comments_enabled"] if video["comments_enabled"] is not None else global_comments
    ratings_enabled = video["ratings_enabled"] if video["ratings_enabled"] is not None else global_ratings

    return {
        "comments_enabled": comments_enabled,
        "ratings_enabled": ratings_enabled,
        "ratings_type": ratings_type,
        "require_approval": require_approval,
        "max_length": max_length,
        "max_depth": max_depth,
        "video": video,
    }


async def get_social_settings(slug: str) -> dict:
    """
    Get resolved social feature settings for a video by slug.

    Inherits from global settings if per-video settings are NULL.
    """
    video = await get_video_by_slug(slug)
    return await _build_social_settings(video)


async def get_social_settings_by_video_id(video_id: int) -> dict:
    """
    Get resolved social feature settings for a video by ID.

    Used internally when we already have the video_id from a comment record.
    Inherits from global settings if per-video settings are NULL.
    """
    video_query = (
        videos.select()
        .where(videos.c.id == video_id)
        .where(videos.c.deleted_at.is_(None))
    )
    video = await fetch_one_with_retry(video_query)

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    return await _build_social_settings(video)


def build_comment_response(comment: dict, user: dict, reply_count: int = 0) -> CommentResponse:
    """Build a comment response from database records."""
    return CommentResponse(
        id=comment["id"],
        video_id=comment["video_id"],
        user=CommentUserInfo(
            id=user["id"],
            username=user["username"],
            display_name=user.get("display_name"),
            avatar_url=user.get("avatar_url"),
        ),
        content=comment["content"],
        video_timestamp=float(comment["video_timestamp"]) if comment["video_timestamp"] else None,
        status=comment["status"],
        depth=comment["depth"],
        parent_id=comment["parent_id"],
        path=comment["path"],
        created_at=comment["created_at"],
        updated_at=comment["updated_at"],
        is_edited=comment["updated_at"] is not None,
        reply_count=reply_count,
    )


@v1_router.get(
    "/videos/{slug}/comments",
    response_model=CommentListResponse,
    summary="Get video comments",
    description="Get paginated comments for a video with threaded replies.",
)
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_video_comments(
    request: Request,
    slug: str,
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(50, ge=1, le=100, description="Maximum comments to return"),
    include_replies: bool = Query(True, description="Include nested replies"),
):
    """
    Get comments for a video with optional threaded replies.

    Comments are returned with nested replies (up to max depth).
    Only approved, non-deleted comments are returned.
    """
    settings = await get_social_settings(slug)
    video_id = settings["video"]["id"]
    if not settings["comments_enabled"]:
        raise HTTPException(status_code=403, detail="Comments are disabled for this video")

    # Build base query for root-level comments
    base_query = (
        comments.select()
        .where(comments.c.video_id == video_id)
        .where(comments.c.status == "approved")
        .where(comments.c.deleted_at.is_(None))
        .where(comments.c.depth == 1)  # Root comments only
        .order_by(comments.c.created_at.desc())
    )

    # Apply cursor pagination if provided
    if cursor:
        try:
            cursor_data = validate_cursor(cursor)
            cursor_time = datetime.fromisoformat(cursor_data["created_at"])
            cursor_id = cursor_data["id"]
            base_query = base_query.where(
                sa.or_(
                    comments.c.created_at < cursor_time,
                    sa.and_(
                        comments.c.created_at == cursor_time,
                        comments.c.id < cursor_id,
                    ),
                )
            )
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    # Fetch one more than requested to check if there are more
    root_comments = await fetch_all_with_retry(base_query.limit(limit + 1))

    has_more = len(root_comments) > limit
    root_comments = root_comments[:limit]

    # Get total count
    count_query = sa.select(sa.func.count()).select_from(comments).where(
        comments.c.video_id == video_id,
        comments.c.status == "approved",
        comments.c.deleted_at.is_(None),
    )
    total_count = await fetch_val_with_retry(count_query) or 0

    # Fetch all user IDs we need
    user_ids = set()
    for c in root_comments:
        user_ids.add(c["user_id"])

    # If including replies, fetch all replies for these root comments
    replies_by_parent: Dict[int, List[dict]] = {}
    if include_replies and root_comments:
        root_ids = [c["id"] for c in root_comments]
        # Fetch all descendants using path prefix
        # For PostgreSQL ltree, we'd use: path <@ 'root_path'
        # Since we're using text paths, we use LIKE with pattern
        reply_conditions = []
        for root_id in root_ids:
            reply_conditions.append(comments.c.path.like(f"{root_id}.%"))

        if reply_conditions:
            replies_query = (
                comments.select()
                .where(comments.c.video_id == video_id)
                .where(comments.c.status == "approved")
                .where(comments.c.deleted_at.is_(None))
                .where(sa.or_(*reply_conditions))
                .order_by(comments.c.path, comments.c.created_at)
            )
            replies = await fetch_all_with_retry(replies_query)

            for reply in replies:
                user_ids.add(reply["user_id"])
                parent_id = reply["parent_id"]
                if parent_id not in replies_by_parent:
                    replies_by_parent[parent_id] = []
                replies_by_parent[parent_id].append(reply)

    # Fetch all users at once
    users_dict = {}
    if user_ids:
        users_query = users.select().where(users.c.id.in_(user_ids))
        users_list = await fetch_all_with_retry(users_query)
        users_dict = {u["id"]: dict(u) for u in users_list}

    # Build nested comment structure
    def build_nested(comment_row: dict, all_replies: Dict[int, List[dict]]) -> CommentWithReplies:
        comment_id = comment_row["id"]
        user = users_dict.get(comment_row["user_id"], {})
        direct_replies = all_replies.get(comment_id, [])
        reply_count = len(direct_replies)

        nested_replies = []
        for reply in direct_replies:
            nested_replies.append(build_nested(reply, all_replies))

        base_response = build_comment_response(comment_row, user, reply_count)
        return CommentWithReplies(
            **base_response.model_dump(),
            replies=nested_replies,
        )

    result_comments = []
    for root in root_comments:
        result_comments.append(build_nested(root, replies_by_parent))

    # Build next cursor
    next_cursor = None
    if has_more and root_comments:
        last = root_comments[-1]
        next_cursor = encode_cursor({
            "created_at": last["created_at"].isoformat(),
            "id": last["id"],
        })

    return CommentListResponse(
        comments=result_comments,
        total_count=total_count,
        next_cursor=next_cursor,
        has_more=has_more,
    )


@v1_router.post(
    "/videos/{slug}/comments",
    response_model=CommentResponse,
    summary="Create comment",
    description="Create a new comment on a video. Requires authentication.",
)
@limiter.limit(RATE_LIMIT_COMMENTS)
async def create_comment(
    request: Request,
    slug: str,
    data: CommentCreate,
    current_user: dict = Depends(require_auth),
):
    """
    Create a new comment on a video.

    Content is sanitized to prevent XSS.
    If comments_require_approval is enabled, comment starts in 'pending' status.
    """
    settings = await get_social_settings(slug)
    video_id = settings["video"]["id"]
    if not settings["comments_enabled"]:
        raise HTTPException(status_code=403, detail="Comments are disabled for this video")

    # Check max length
    if len(data.content) > settings["max_length"]:
        raise HTTPException(
            status_code=400,
            detail=f"Comment exceeds maximum length of {settings['max_length']} characters",
        )

    # Sanitize content
    sanitized_content = sanitize_comment_content(data.content)
    if not sanitized_content.strip():
        raise HTTPException(status_code=400, detail="Comment content cannot be empty after sanitization")

    # Handle replies
    parent_comment = None
    depth = 1
    path = ""

    if data.parent_id:
        # Fetch parent comment
        parent_query = comments.select().where(
            comments.c.id == data.parent_id,
            comments.c.video_id == video_id,  # Must be same video
            comments.c.deleted_at.is_(None),
        )
        parent_comment = await fetch_one_with_retry(parent_query)

        if not parent_comment:
            raise HTTPException(status_code=400, detail="Parent comment not found or belongs to different video")

        # Check depth limit
        parent_depth = parent_comment["depth"]
        if parent_depth >= settings["max_depth"]:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum reply depth of {settings['max_depth']} reached",
            )

        depth = parent_depth + 1

    # Determine initial status
    status = "pending" if settings["require_approval"] else "approved"

    # Insert comment
    now = datetime.now(timezone.utc)
    insert_query = comments.insert().values(
        video_id=video_id,
        user_id=current_user["id"],
        path="",  # Will be updated after we get the ID
        depth=depth,
        parent_id=data.parent_id,
        content=sanitized_content,
        video_timestamp=data.video_timestamp,
        status=status,
        created_at=now,
    )

    result = await db_execute_with_retry(insert_query)
    comment_id = result

    # Update path with actual ID
    if parent_comment:
        path = f"{parent_comment['path']}.{comment_id}"
    else:
        path = str(comment_id)

    await db_execute_with_retry(
        comments.update()
        .where(comments.c.id == comment_id)
        .values(path=path)
    )

    # Fetch the created comment
    new_comment = await fetch_one_with_retry(
        comments.select().where(comments.c.id == comment_id)
    )

    return build_comment_response(new_comment, current_user, 0)


@v1_router.put(
    "/comments/{comment_id}",
    response_model=CommentResponse,
    summary="Update comment",
    description="Update your own comment. Requires authentication.",
)
@limiter.limit(RATE_LIMIT_COMMENTS)
async def update_comment(
    request: Request,
    comment_id: int,
    data: CommentUpdate,
    current_user: dict = Depends(require_auth),
):
    """
    Update an existing comment.

    Only the comment author can update their own comment.
    Admins can update any comment via the admin API.
    """
    # Fetch comment
    comment = await fetch_one_with_retry(
        comments.select().where(comments.c.id == comment_id)
    )

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment["deleted_at"]:
        raise HTTPException(status_code=404, detail="Comment has been deleted")

    # Check ownership
    await require_ownership_or_permission(
        comment["user_id"],
        Permission.COMMENT_UPDATE,
        Permission.COMMENT_UPDATE_ANY,
        current_user,
    )

    # Get settings for content length validation
    settings = await get_social_settings_by_video_id(comment["video_id"])

    if len(data.content) > settings["max_length"]:
        raise HTTPException(
            status_code=400,
            detail=f"Comment exceeds maximum length of {settings['max_length']} characters",
        )

    # Sanitize content
    sanitized_content = sanitize_comment_content(data.content)
    if not sanitized_content.strip():
        raise HTTPException(status_code=400, detail="Comment content cannot be empty after sanitization")

    # Update comment
    await db_execute_with_retry(
        comments.update()
        .where(comments.c.id == comment_id)
        .values(
            content=sanitized_content,
            updated_at=datetime.now(timezone.utc),
        )
    )

    # Fetch updated comment
    updated_comment = await fetch_one_with_retry(
        comments.select().where(comments.c.id == comment_id)
    )

    # Get reply count
    reply_count_query = sa.select(sa.func.count()).select_from(comments).where(
        comments.c.parent_id == comment_id,
        comments.c.deleted_at.is_(None),
    )
    reply_count = await fetch_val_with_retry(reply_count_query) or 0

    return build_comment_response(updated_comment, current_user, reply_count)


@v1_router.delete(
    "/comments/{comment_id}",
    summary="Delete comment",
    description="Soft-delete your own comment. Requires authentication.",
)
@limiter.limit(RATE_LIMIT_COMMENTS)
async def delete_comment(
    request: Request,
    comment_id: int,
    current_user: dict = Depends(require_auth),
):
    """
    Soft-delete a comment.

    Only the comment author can delete their own comment.
    Admins can hard-delete via the admin API.
    """
    # Fetch comment
    comment = await fetch_one_with_retry(
        comments.select().where(comments.c.id == comment_id)
    )

    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment["deleted_at"]:
        raise HTTPException(status_code=404, detail="Comment already deleted")

    # Check ownership
    await require_ownership_or_permission(
        comment["user_id"],
        Permission.COMMENT_DELETE,
        Permission.COMMENT_DELETE_ANY,
        current_user,
    )

    # Soft delete
    await db_execute_with_retry(
        comments.update()
        .where(comments.c.id == comment_id)
        .values(deleted_at=datetime.now(timezone.utc))
    )

    return {"status": "deleted", "comment_id": comment_id}


# Ratings endpoints

@v1_router.get(
    "/videos/{slug}/rating",
    response_model=VideoRatingAggregates,
    summary="Get video rating",
    description="Get rating aggregates and the current user's rating for a video.",
)
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_video_rating(
    request: Request,
    slug: str,
    current_user: Optional[dict] = Depends(get_current_user),
):
    """
    Get rating aggregates for a video.

    Returns average rating, distribution, and the current user's rating if authenticated.
    """
    settings = await get_social_settings(slug)
    video = settings["video"]
    video_id = video["id"]

    user_rating = None
    if current_user:
        user_rating_row = await fetch_one_with_retry(
            ratings.select().where(
                ratings.c.video_id == video_id,
                ratings.c.user_id == current_user["id"],
            )
        )
        if user_rating_row:
            user_rating = user_rating_row["rating_value"]

    # Parse rating distribution from JSON
    distribution = None
    if video["rating_distribution"]:
        try:
            distribution = json.loads(video["rating_distribution"])
        except (json.JSONDecodeError, TypeError):
            distribution = {}

    return VideoRatingAggregates(
        video_id=video_id,
        rating_type=settings["ratings_type"],
        rating_count=video["rating_count"] or 0,
        rating_avg=float(video["rating_avg"]) if video["rating_avg"] else None,
        rating_distribution=distribution,
        likes_count=video["likes_count"] or 0,
        dislikes_count=video["dislikes_count"] or 0,
        user_rating=user_rating,
    )


@v1_router.post(
    "/videos/{slug}/rating",
    response_model=RatingResponse,
    summary="Rate video",
    description="Rate a video (upsert - creates or updates existing rating). Requires authentication.",
)
@limiter.limit(RATE_LIMIT_RATINGS)
async def rate_video(
    request: Request,
    slug: str,
    data: RatingCreate,
    current_user: dict = Depends(require_auth),
):
    """
    Rate a video.

    For stars mode: value must be 1-5
    For thumbs mode: value must be 1 (like) or -1 (dislike)

    This is an upsert operation - creates a new rating or updates existing.
    """
    settings = await get_social_settings(slug)
    video_id = settings["video"]["id"]
    if not settings["ratings_enabled"]:
        raise HTTPException(status_code=403, detail="Ratings are disabled for this video")

    # Validate rating value based on type
    if settings["ratings_type"] == "stars":
        if data.value < 1 or data.value > 5:
            raise HTTPException(status_code=400, detail="Star rating must be between 1 and 5")
    else:  # thumbs
        if data.value not in (1, -1):
            raise HTTPException(status_code=400, detail="Thumbs rating must be 1 (like) or -1 (dislike)")

    now = datetime.now(timezone.utc)

    # Check if user already rated
    existing = await fetch_one_with_retry(
        ratings.select().where(
            ratings.c.video_id == video_id,
            ratings.c.user_id == current_user["id"],
        )
    )

    if existing:
        # Update existing rating
        await db_execute_with_retry(
            ratings.update()
            .where(ratings.c.video_id == video_id)
            .where(ratings.c.user_id == current_user["id"])
            .values(
                rating_value=data.value,
                updated_at=now,
            )
        )
    else:
        # Create new rating
        await db_execute_with_retry(
            ratings.insert().values(
                video_id=video_id,
                user_id=current_user["id"],
                rating_value=data.value,
                created_at=now,
            )
        )

    return RatingResponse(
        video_id=video_id,
        user_rating=data.value,
        rating_type=settings["ratings_type"],
    )


@v1_router.delete(
    "/videos/{slug}/rating",
    summary="Remove rating",
    description="Remove your rating from a video. Requires authentication.",
)
@limiter.limit(RATE_LIMIT_RATINGS)
async def delete_rating(
    request: Request,
    slug: str,
    current_user: dict = Depends(require_auth),
):
    """
    Remove the current user's rating from a video.
    """
    # Get video by slug to validate it exists and get video_id
    video = await get_video_by_slug(slug)
    video_id = video["id"]

    # Check if rating exists
    existing = await fetch_one_with_retry(
        ratings.select().where(
            ratings.c.video_id == video_id,
            ratings.c.user_id == current_user["id"],
        )
    )

    if not existing:
        raise HTTPException(status_code=404, detail="Rating not found")

    # Delete rating
    await db_execute_with_retry(
        ratings.delete().where(
            ratings.c.video_id == video_id,
            ratings.c.user_id == current_user["id"],
        )
    )

    return {"status": "deleted", "video_id": video_id}


@v1_router.get(
    "/videos/{slug}/social",
    response_model=VideoSocialStatus,
    summary="Get video social status",
    description="Get resolved social feature status and aggregates for a video.",
)
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_video_social_status(
    request: Request,
    slug: str,
):
    """
    Get social feature status for a video.

    Returns whether comments/ratings are enabled and current aggregates.
    """
    settings = await get_social_settings(slug)
    video = settings["video"]

    return VideoSocialStatus(
        comments_enabled=settings["comments_enabled"],
        ratings_enabled=settings["ratings_enabled"],
        ratings_type=settings["ratings_type"],
        comment_count=video["comment_count"] or 0,
        rating_count=video["rating_count"] or 0,
        rating_avg=float(video["rating_avg"]) if video["rating_avg"] else None,
        likes_count=video["likes_count"] or 0,
        dislikes_count=video["dislikes_count"] or 0,
    )


# =============================================================================
# API Router Mounting (Issue #218)
# Mount versioned routers and configure OpenAPI documentation
# =============================================================================

# Mount v1 router at /api/v1
app.include_router(v1_router, prefix="/api/v1")
logger.info("Mounted API v1 at /api/v1")

# Mount legacy routes at /api for backwards compatibility (if enabled)
if API_INCLUDE_LEGACY_ROUTES:
    app.include_router(v1_router, prefix="/api", include_in_schema=False)
    logger.info("Mounted legacy routes at /api (aliased to v1)")

# Include studio module routers for broadcaster dashboard
# These have their own /api/v1/studio prefix.
# Imported here rather than at the top of the file so the studio modules load
# after `app` and the v1 router are fully configured above.
from api import (  # noqa: E402
    studio,
    studio_analytics,
    studio_chat,
    studio_chat_ws,
    studio_moderation,
    studio_sse,
    studio_vod,
)

app.include_router(studio.router)
app.include_router(studio_sse.router)
app.include_router(studio_vod.router)
app.include_router(studio_chat.router)
app.include_router(studio_chat_ws.router)
app.include_router(studio_moderation.router)
app.include_router(studio_analytics.router)
logger.info("Mounted studio dashboard routers at /api/v1/studio")


# Configure custom OpenAPI schema
def custom_openapi():
    return configure_openapi_schema(app)


app.openapi = custom_openapi


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PUBLIC_PORT)
