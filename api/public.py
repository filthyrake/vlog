"""
Public API - serves the video browsing interface.
Runs on port 9000.
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

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
from api.enums import TranscriptionStatus, VideoStatus
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
    PUBLIC_PORT,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_PUBLIC_ANALYTICS,
    RATE_LIMIT_PUBLIC_DEFAULT,
    RATE_LIMIT_PUBLIC_VIDEOS_LIST,
    RATE_LIMIT_STORAGE_URL,
    SECURE_COOKIES,
    VIDEOS_DIR,
)

logger = logging.getLogger(__name__)

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


# Custom static files handler with proper headers for HLS
class HLSStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:
        try:
            response = await super().get_response(path, scope)
            # Fix MIME types and cache headers for HLS
            if path.endswith(".ts"):
                # CRITICAL: .ts files are MPEG Transport Stream, not TypeScript/Qt Linguist
                response.headers["Content-Type"] = "video/mp2t"
                response.headers["Cache-Control"] = "public, max-age=31536000"
            elif path.endswith(".m3u8"):
                response.headers["Content-Type"] = "application/vnd.apple.mpegurl"
                response.headers["Cache-Control"] = "no-cache"
            return response
        except (OSError, PermissionError) as e:
            # Storage unavailable - return 503 with helpful message
            logger.warning(f"Storage unavailable for HLS file {path}: {e}")
            return JSONResponse(
                status_code=503,
                content={"detail": "Video storage temporarily unavailable. Please try again later."},
                headers={"Retry-After": "30"},
            )


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
    limit: int = Query(default=50, ge=1, le=100, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
) -> List[VideoListResponse]:
    """List all published videos. Filter by category, tag, or search term."""
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
            categories.c.name.label("category_name"),
        )
        .select_from(videos.outerjoin(categories, videos.c.category_id == categories.c.id))
        .where(videos.c.status == VideoStatus.READY)
        .where(videos.c.deleted_at.is_(None))  # Exclude soft-deleted videos
        .where(videos.c.published_at.is_not(None))  # Only show published videos
        .order_by(videos.c.published_at.desc())
        .limit(limit)
        .offset(offset)
    )

    if category:
        query = query.where(categories.c.slug == category)

    if tag:
        # Filter by tag slug - join with video_tags and tags tables
        tag_subquery = (
            sa.select(video_tags.c.video_id)
            .select_from(video_tags.join(tags, video_tags.c.tag_id == tags.c.id))
            .where(tags.c.slug == tag)
        )
        query = query.where(videos.c.id.in_(tag_subquery))

    if search:
        search_term = f"%{search}%"
        query = query.where(
            sa.or_(
                videos.c.title.ilike(search_term),
                videos.c.description.ilike(search_term),
            )
        )

    rows = await fetch_all_with_retry(query)

    # Get tags for all videos in one query
    video_ids = [row["id"] for row in rows]
    video_tags_map = await get_video_tags(video_ids)

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
            thumbnail_url=f"/videos/{row['slug']}/thumbnail.jpg",
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
        thumbnail_url=f"/videos/{row['slug']}/thumbnail.jpg" if row["status"] == VideoStatus.READY else None,
        stream_url=f"/videos/{row['slug']}/master.m3u8" if row["status"] == VideoStatus.READY else None,
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
        LEFT JOIN videos v ON v.category_id = c.id AND v.status = 'ready' AND v.deleted_at IS NULL AND v.published_at IS NOT NULL
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
        LEFT JOIN videos v ON v.id = vt.video_id AND v.status = 'ready' AND v.deleted_at IS NULL AND v.published_at IS NOT NULL
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
