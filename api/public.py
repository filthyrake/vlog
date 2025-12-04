"""
Public API - serves the video browsing interface.
Runs on port 9000.
"""

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
    SecurityHeadersMiddleware,
    check_health,
    get_real_ip,
    rate_limit_exceeded_handler,
)
from api.database import (
    categories,
    configure_sqlite_pragmas,
    database,
    playback_sessions,
    quality_progress,
    transcoding_jobs,
    transcriptions,
    video_qualities,
    videos,
    viewers,
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
    TranscodingProgressResponse,
    TranscriptionResponse,
    VideoListResponse,
    VideoQualityResponse,
    VideoResponse,
)
from config import (
    CORS_ALLOWED_ORIGINS,
    PUBLIC_PORT,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_PUBLIC_ANALYTICS,
    RATE_LIMIT_PUBLIC_DEFAULT,
    RATE_LIMIT_PUBLIC_VIDEOS_LIST,
    RATE_LIMIT_STORAGE_URL,
    VIDEOS_DIR,
)

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
    await database.connect()
    await configure_sqlite_pragmas()
    yield
    await database.disconnect()


app = FastAPI(title="VLog", description="Self-hosted video platform", lifespan=lifespan)

# Register rate limiter with the app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.add_middleware(SecurityHeadersMiddleware)

# CORS middleware for HLS playback and analytics
# If CORS_ALLOWED_ORIGINS is empty, allow same-origin only (no CORS headers)
# Note: allow_credentials=True requires specific origins, not wildcards
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS if CORS_ALLOWED_ORIGINS else [],
    allow_credentials=bool(CORS_ALLOWED_ORIGINS),  # Only enable with explicit origins
    allow_methods=["GET", "HEAD", "OPTIONS", "POST"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)


# Custom static files handler with proper headers for HLS
class HLSStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:
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


# Serve video files (HLS segments, playlists, thumbnails)
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
    """Health check endpoint for monitoring and load balancers."""
    result = await check_health()
    return JSONResponse(
        status_code=result["status_code"],
        content={
            "status": "healthy" if result["healthy"] else "unhealthy",
            "checks": result["checks"],
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


@app.get("/api/videos")
@limiter.limit(RATE_LIMIT_PUBLIC_VIDEOS_LIST)
async def list_videos(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
) -> List[VideoListResponse]:
    """List all published videos."""
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
        .order_by(videos.c.published_at.desc())
        .limit(limit)
        .offset(offset)
    )

    if category:
        query = query.where(categories.c.slug == category)

    if search:
        search_term = f"%{search}%"
        query = query.where(
            sa.or_(
                videos.c.title.ilike(search_term),
                videos.c.description.ilike(search_term),
            )
        )

    rows = await database.fetch_all(query)

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
        )
        for row in rows
    ]


@app.get("/api/videos/{slug}")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_video(request: Request, slug: str) -> VideoResponse:
    """Get a single video by slug."""
    query = (
        sa.select(
            videos,
            categories.c.name.label("category_name"),
            categories.c.slug.label("category_slug"),
        )
        .select_from(videos.outerjoin(categories, videos.c.category_id == categories.c.id))
        .where(videos.c.slug == slug)
        .where(videos.c.deleted_at.is_(None))  # Exclude soft-deleted videos
    )

    row = await database.fetch_one(query)
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get quality variants
    quality_query = video_qualities.select().where(video_qualities.c.video_id == row["id"])
    quality_rows = await database.fetch_all(quality_query)

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
    transcription_row = await database.fetch_one(transcription_query)

    captions_url = None
    transcription_status = None

    if transcription_row:
        transcription_status = transcription_row["status"]
        if transcription_row["status"] == TranscriptionStatus.COMPLETED and transcription_row["vtt_path"]:
            captions_url = f"/videos/{row['slug']}/captions.vtt"

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
    )


@app.get("/api/videos/{slug}/progress")
@limiter.limit(RATE_LIMIT_PUBLIC_DEFAULT)
async def get_video_progress(request: Request, slug: str) -> TranscodingProgressResponse:
    """Get transcoding progress for a video."""
    # Get video by slug (exclude soft-deleted)
    video_query = videos.select().where(videos.c.slug == slug).where(videos.c.deleted_at.is_(None))
    video = await database.fetch_one(video_query)

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
    job = await database.fetch_one(job_query)

    if not job:
        return TranscodingProgressResponse(
            status=video["status"],
            progress_percent=0,
        )

    # Get quality progress
    quality_query = quality_progress.select().where(quality_progress.c.job_id == job["id"])
    quality_rows = await database.fetch_all(quality_query)

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
    # Get video by slug (exclude soft-deleted)
    video_query = videos.select().where(videos.c.slug == slug).where(videos.c.deleted_at.is_(None))
    video = await database.fetch_one(video_query)

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get transcription record
    transcription_query = transcriptions.select().where(transcriptions.c.video_id == video["id"])
    transcription = await database.fetch_one(transcription_query)

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
        LEFT JOIN videos v ON v.category_id = c.id AND v.status = 'ready' AND v.deleted_at IS NULL
        GROUP BY c.id
        ORDER BY c.name
    """)

    rows = await database.fetch_all(query)

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
    query = categories.select().where(categories.c.slug == slug)
    row = await database.fetch_one(query)
    if not row:
        raise HTTPException(status_code=404, detail="Category not found")

    # Get video count (exclude soft-deleted)
    count_query = (
        sa.select(sa.func.count())
        .select_from(videos)
        .where(
            sa.and_(
                videos.c.category_id == row["id"], videos.c.status == VideoStatus.READY, videos.c.deleted_at.is_(None)
            )
        )
    )
    count = await database.fetch_val(count_query)

    return CategoryResponse(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"] or "",
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
    video = await database.fetch_one(
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
        viewer = await database.fetch_one(viewers.select().where(viewers.c.session_id == vlog_viewer))
        if viewer:
            viewer_id = viewer["id"]
            # Update last_seen timestamp
            await database.execute(
                viewers.update().where(viewers.c.id == viewer_id).values(last_seen=datetime.now(timezone.utc))
            )

    # If no valid viewer cookie, create new viewer
    if viewer_id is None:
        new_viewer_session = str(uuid.uuid4())
        viewer_id = await database.execute(
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
        )

    # Create playback session linked to viewer
    await database.execute(
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
    session = await database.fetch_one(query)

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

    await database.execute(
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
    session = await database.fetch_one(query)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get video duration to determine if completed
    video_query = videos.select().where(videos.c.id == session["video_id"])
    video = await database.fetch_one(video_query)

    completed = data.completed
    if video and video["duration"] > 0:
        # Mark as completed if watched >= 90%
        percent_watched = data.position / video["duration"]
        if percent_watched >= 0.9:
            completed = True

    # Final update (handle None values from fresh sessions)
    current_max_position = session["max_position"] or 0.0
    await database.execute(
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
