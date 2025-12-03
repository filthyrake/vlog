"""
Public API - serves the video browsing interface.
Runs on port 9000.
"""
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import VIDEOS_DIR, PUBLIC_PORT
from api.database import database, videos, categories, video_qualities, playback_sessions, transcoding_jobs, quality_progress
from api.schemas import (
    VideoResponse, VideoListResponse, CategoryResponse, VideoQualityResponse,
    PlaybackSessionCreate, PlaybackHeartbeat, PlaybackEnd, PlaybackSessionResponse,
    TranscodingProgressResponse, QualityProgressResponse,
)
import sqlalchemy as sa
import uuid
from datetime import datetime

app = FastAPI(title="VLog", description="Self-hosted video platform")

# CORS middleware for HLS playback and analytics
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "OPTIONS", "POST"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)


# Custom static files handler with proper headers for HLS
class HLSStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        # Fix MIME types and cache headers for HLS
        if path.endswith('.ts'):
            # CRITICAL: .ts files are MPEG Transport Stream, not TypeScript/Qt Linguist
            response.headers["Content-Type"] = "video/mp2t"
            response.headers["Cache-Control"] = "public, max-age=31536000"
        elif path.endswith('.m3u8'):
            response.headers["Content-Type"] = "application/vnd.apple.mpegurl"
            response.headers["Cache-Control"] = "no-cache"
        return response


# Serve video files (HLS segments, playlists, thumbnails)
app.mount("/videos", HLSStaticFiles(directory=str(VIDEOS_DIR)), name="videos")

# Serve static web files
WEB_DIR = Path(__file__).parent.parent / "web" / "public"
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.on_event("startup")
async def startup():
    await database.connect()


@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()


@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the main page."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/watch/{slug}", response_class=HTMLResponse)
async def watch_page(slug: str):
    """Serve the watch page."""
    return FileResponse(WEB_DIR / "watch.html")


@app.get("/category/{slug}", response_class=HTMLResponse)
async def category_page(slug: str):
    """Serve the category page."""
    return FileResponse(WEB_DIR / "category.html")


@app.get("/api/videos")
async def list_videos(
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
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
        .where(videos.c.status == "ready")
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
async def get_video(slug: str) -> VideoResponse:
    """Get a single video by slug."""
    query = (
        sa.select(
            videos,
            categories.c.name.label("category_name"),
            categories.c.slug.label("category_slug"),
        )
        .select_from(videos.outerjoin(categories, videos.c.category_id == categories.c.id))
        .where(videos.c.slug == slug)
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
        error_message=row["error_message"],
        created_at=row["created_at"],
        published_at=row["published_at"],
        thumbnail_url=f"/videos/{row['slug']}/thumbnail.jpg" if row["status"] == "ready" else None,
        stream_url=f"/videos/{row['slug']}/master.m3u8" if row["status"] == "ready" else None,
        qualities=qualities,
    )


@app.get("/api/videos/{slug}/progress")
async def get_video_progress(slug: str) -> TranscodingProgressResponse:
    """Get transcoding progress for a video."""
    # Get video by slug
    video_query = videos.select().where(videos.c.slug == slug)
    video = await database.fetch_one(video_query)

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # If video is ready or failed, return simple status
    if video["status"] in ["ready", "failed"]:
        return TranscodingProgressResponse(
            status=video["status"],
            progress_percent=100 if video["status"] == "ready" else 0,
            last_error=video["error_message"] if video["status"] == "failed" else None,
        )

    # If pending, return basic pending status
    if video["status"] == "pending":
        return TranscodingProgressResponse(
            status="pending",
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
        last_error=job["last_error"],
    )


@app.get("/api/categories")
async def list_categories() -> List[CategoryResponse]:
    """List all categories with video counts."""
    query = sa.text("""
        SELECT c.*, COUNT(v.id) as video_count
        FROM categories c
        LEFT JOIN videos v ON v.category_id = c.id AND v.status = 'ready'
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
async def get_category(slug: str) -> CategoryResponse:
    """Get a single category by slug."""
    query = categories.select().where(categories.c.slug == slug)
    row = await database.fetch_one(query)
    if not row:
        raise HTTPException(status_code=404, detail="Category not found")

    # Get video count
    count_query = sa.select(sa.func.count()).select_from(videos).where(
        sa.and_(videos.c.category_id == row["id"], videos.c.status == "ready")
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
async def start_analytics_session(data: PlaybackSessionCreate) -> PlaybackSessionResponse:
    """Start a new playback session for tracking."""
    session_token = str(uuid.uuid4())

    await database.execute(
        playback_sessions.insert().values(
            video_id=data.video_id,
            session_token=session_token,
            started_at=datetime.utcnow(),
            quality_used=data.quality,
        )
    )

    return PlaybackSessionResponse(session_token=session_token)


@app.post("/api/analytics/heartbeat")
async def analytics_heartbeat(data: PlaybackHeartbeat):
    """Update playback session with current progress."""
    # Find the session
    query = playback_sessions.select().where(
        playback_sessions.c.session_token == data.session_token
    )
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
async def end_analytics_session(data: PlaybackEnd):
    """End a playback session."""
    # Find the session
    query = playback_sessions.select().where(
        playback_sessions.c.session_token == data.session_token
    )
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
            ended_at=datetime.utcnow(),
            max_position=max(current_max_position, data.position),
            completed=completed,
        )
    )

    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PUBLIC_PORT)
