"""
Admin API - handles uploads and video management.
Runs on port 9001 (not exposed externally).
"""

import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlite3 import IntegrityError
from typing import List, Optional

import sqlalchemy as sa
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slugify import slugify
from starlette.middleware.base import BaseHTTPMiddleware

from api.database import (
    categories,
    configure_sqlite_pragmas,
    create_tables,
    database,
    playback_sessions,
    quality_progress,
    transcoding_jobs,
    transcriptions,
    video_qualities,
    videos,
)
from api.enums import TranscriptionStatus, VideoStatus
from api.errors import sanitize_error_message, sanitize_progress_error
from api.schemas import (
    AnalyticsOverview,
    CategoryCreate,
    CategoryResponse,
    DailyViews,
    QualityBreakdown,
    QualityProgressResponse,
    TranscodingProgressResponse,
    TranscriptionResponse,
    TranscriptionTrigger,
    TranscriptionUpdate,
    TrendDataPoint,
    TrendsResponse,
    VideoAnalyticsDetail,
    VideoAnalyticsListResponse,
    VideoAnalyticsSummary,
    VideoListResponse,
    VideoQualityResponse,
    VideoResponse,
)
from config import (
    ADMIN_CORS_ALLOWED_ORIGINS,
    ADMIN_PORT,
    ARCHIVE_DIR,
    MAX_UPLOAD_SIZE,
    RATE_LIMIT_ADMIN_DEFAULT,
    RATE_LIMIT_ADMIN_UPLOAD,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
    TRUSTED_PROXIES,
    UPLOAD_CHUNK_SIZE,
    UPLOADS_DIR,
    VIDEOS_DIR,
)


def _get_real_ip(request: Request) -> str:
    """
    Get the real client IP address, respecting X-Forwarded-For header only from trusted proxies.

    Security: X-Forwarded-For is only trusted when the direct client IP is in TRUSTED_PROXIES.
    This prevents attackers from spoofing the header to bypass rate limiting.
    Configure VLOG_TRUSTED_PROXIES with your proxy IPs (e.g., "127.0.0.1,10.0.0.1").
    """
    client_ip = get_remote_address(request)

    # Only trust X-Forwarded-For if request came from a trusted proxy
    if TRUSTED_PROXIES and client_ip in TRUSTED_PROXIES:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # X-Forwarded-For can contain multiple IPs: client, proxy1, proxy2, ...
            # The first one is the original client
            return forwarded.split(",")[0].strip()

    return client_ip


# Initialize rate limiter for admin API
limiter = Limiter(
    key_func=_get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handle rate limit exceeded errors with a proper JSON response."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded",
            "error": str(exc.detail),
        },
    )

# Allowed video file extensions
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}

# Input length limits
MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 5000


async def save_upload_with_size_limit(
    file: UploadFile, upload_path: Path, max_size: int = MAX_UPLOAD_SIZE
) -> int:
    """
    Stream upload to disk with size validation.
    Returns the total bytes written.
    Raises HTTPException if file exceeds max_size.
    """
    total_size = 0
    try:
        with open(upload_path, "wb") as f:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    # Clean up partial file
                    f.close()
                    upload_path.unlink(missing_ok=True)
                    max_size_gb = max_size / (1024 * 1024 * 1024)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum upload size is {max_size_gb:.0f} GB",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        # Clean up on any error
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

    return total_size


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    create_tables()
    await database.connect()
    await configure_sqlite_pragmas()
    yield
    await database.disconnect()


app = FastAPI(title="VLog Admin", description="Video management API", lifespan=lifespan)

# Register rate limiter with the app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # XSS protection for legacy browsers
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Permissions policy (disable unnecessary browser features)
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# Allow CORS for admin UI (internal-only, not exposed externally)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ADMIN_CORS_ALLOWED_ORIGINS,
    allow_credentials=True if ADMIN_CORS_ALLOWED_ORIGINS != ["*"] else False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve video files for preview
app.mount("/videos", StaticFiles(directory=str(VIDEOS_DIR)), name="videos")

# Serve admin web files
WEB_DIR = Path(__file__).parent.parent / "web" / "admin"
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def admin_home():
    """Serve the admin page."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring and load balancers."""
    checks = {
        "database": False,
        "storage": False,
    }

    # Check database connectivity
    try:
        await database.fetch_one("SELECT 1")
        checks["database"] = True
    except Exception:
        pass

    # Check storage accessibility (NAS mount)
    try:
        checks["storage"] = VIDEOS_DIR.exists() and UPLOADS_DIR.exists()
    except Exception:
        pass

    healthy = all(checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "unhealthy",
            "checks": checks,
        },
    )


# ============ Categories ============


@app.get("/api/categories")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_categories(request: Request) -> List[CategoryResponse]:
    """List all categories."""
    query = sa.text("""
        SELECT c.*, COUNT(v.id) as video_count
        FROM categories c
        LEFT JOIN videos v ON v.category_id = c.id AND v.deleted_at IS NULL
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


@app.post("/api/categories")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def create_category(request: Request, data: CategoryCreate) -> CategoryResponse:
    """Create a new category."""
    slug = slugify(data.name)

    # Check for duplicate slug
    existing = await database.fetch_one(categories.select().where(categories.c.slug == slug))
    if existing:
        raise HTTPException(status_code=400, detail="Category with this name already exists")

    query = categories.insert().values(
        name=data.name,
        slug=slug,
        description=data.description,
        created_at=datetime.now(timezone.utc),
    )
    category_id = await database.execute(query)

    return CategoryResponse(
        id=category_id,
        name=data.name,
        slug=slug,
        description=data.description,
        created_at=datetime.now(timezone.utc),
        video_count=0,
    )


@app.delete("/api/categories/{category_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def delete_category(request: Request, category_id: int):
    """Delete a category."""
    # Verify category exists
    existing = await database.fetch_one(categories.select().where(categories.c.id == category_id))
    if not existing:
        raise HTTPException(status_code=404, detail="Category not found")

    # Use transaction to ensure atomicity
    async with database.transaction():
        # Set videos in this category to uncategorized
        await database.execute(videos.update().where(videos.c.category_id == category_id).values(category_id=None))
        await database.execute(categories.delete().where(categories.c.id == category_id))

    return {"status": "ok"}


# ============ Videos ============


@app.get("/api/videos")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_all_videos(
    request: Request,
    status: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
) -> List[VideoListResponse]:
    """List all videos (including non-ready ones for admin)."""
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
        .where(videos.c.deleted_at.is_(None))  # Exclude soft-deleted videos
        .order_by(videos.c.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    if status:
        query = query.where(videos.c.status == status)

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
            thumbnail_url=f"/videos/{row['slug']}/thumbnail.jpg" if row["status"] == VideoStatus.READY else None,
        )
        for row in rows
    ]


@app.get("/api/videos/{video_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_video(request: Request, video_id: int) -> VideoResponse:
    """Get video details."""
    query = (
        sa.select(
            videos,
            categories.c.name.label("category_name"),
        )
        .select_from(videos.outerjoin(categories, videos.c.category_id == categories.c.id))
        .where(videos.c.id == video_id)
    )

    row = await database.fetch_one(query)
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    quality_rows = await database.fetch_all(video_qualities.select().where(video_qualities.c.video_id == video_id))

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
        duration=row["duration"],
        source_width=row["source_width"],
        source_height=row["source_height"],
        status=row["status"],
        error_message=sanitize_error_message(row["error_message"], context=f"video_id={video_id}"),
        created_at=row["created_at"],
        published_at=row["published_at"],
        thumbnail_url=f"/videos/{row['slug']}/thumbnail.jpg" if row["status"] == VideoStatus.READY else None,
        stream_url=f"/videos/{row['slug']}/master.m3u8" if row["status"] == VideoStatus.READY else None,
        qualities=qualities,
    )


@app.post("/api/videos")
@limiter.limit(RATE_LIMIT_ADMIN_UPLOAD)
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    category_id: Optional[int] = Form(None),
):
    """Upload a new video for processing."""
    # Validate file extension
    file_ext = Path(file.filename).suffix.lower() if file.filename else ""
    if not file_ext:
        file_ext = ".mp4"  # Default extension
    if file_ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{file_ext}'. Allowed: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}",
        )

    # Validate input lengths
    if not title or len(title.strip()) == 0:
        raise HTTPException(status_code=400, detail="Title is required")
    if len(title) > MAX_TITLE_LENGTH:
        raise HTTPException(status_code=400, detail=f"Title must be {MAX_TITLE_LENGTH} characters or less")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise HTTPException(status_code=400, detail=f"Description must be {MAX_DESCRIPTION_LENGTH} characters or less")

    # Generate slug with race condition handling
    base_slug = slugify(title.strip())
    slug = base_slug
    counter = 0
    max_attempts = 100  # Prevent infinite loop

    # Try to insert with retry on slug collision
    video_id = None
    while video_id is None and counter < max_attempts:
        try:
            query = videos.insert().values(
                title=title,
                slug=slug,
                description=description,
                category_id=category_id if category_id else None,
                status=VideoStatus.PENDING,
                created_at=datetime.now(timezone.utc),
            )
            video_id = await database.execute(query)
        except IntegrityError:
            # Slug collision - try with incremented counter
            counter += 1
            slug = f"{base_slug}-{counter}"
        except Exception as e:
            # Check if it's a wrapped IntegrityError (databases library wraps exceptions)
            if "UNIQUE constraint failed" in str(e) and "slug" in str(e):
                counter += 1
                slug = f"{base_slug}-{counter}"
            else:
                raise

    if video_id is None:
        raise HTTPException(status_code=500, detail="Failed to generate unique slug")

    # Save uploaded file with size validation (file_ext already validated above)
    upload_path = UPLOADS_DIR / f"{video_id}{file_ext}"
    await save_upload_with_size_limit(file, upload_path)

    # Create output directory
    (VIDEOS_DIR / slug).mkdir(parents=True, exist_ok=True)

    return {
        "status": "ok",
        "video_id": video_id,
        "slug": slug,
        "message": "Video queued for processing",
    }


@app.put("/api/videos/{video_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def update_video(
    request: Request,
    video_id: int,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    category_id: Optional[int] = Form(None),
    published_at: Optional[str] = Form(None),
):
    """Update video metadata."""
    update_data = {}
    if title is not None:
        update_data["title"] = title
    if description is not None:
        update_data["description"] = description
    if category_id is not None:
        if category_id > 0:
            # Validate category exists
            existing_category = await database.fetch_one(categories.select().where(categories.c.id == category_id))
            if not existing_category:
                raise HTTPException(status_code=400, detail=f"Category with ID {category_id} does not exist")
            update_data["category_id"] = category_id
        else:
            # category_id <= 0 means uncategorize
            update_data["category_id"] = None
    if published_at is not None:
        if published_at == "":
            update_data["published_at"] = None
        else:
            try:
                # Parse ISO format datetime (e.g., "2024-01-15T14:30")
                update_data["published_at"] = datetime.fromisoformat(published_at)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use ISO format (YYYY-MM-DDTHH:MM)")

    if update_data:
        await database.execute(videos.update().where(videos.c.id == video_id).values(**update_data))

    return {"status": "ok"}


@app.delete("/api/videos/{video_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def delete_video(request: Request, video_id: int, permanent: bool = False):
    """
    Soft-delete a video (moves to archive) or permanently delete if permanent=True.

    Soft-delete:
    - Moves video files to archive directory
    - Sets deleted_at timestamp
    - Video can be restored within retention period

    Permanent delete:
    - Removes all files permanently
    - Deletes all database records
    - Cannot be undone
    """
    # Get video info
    row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    if permanent:
        # PERMANENT DELETE - remove everything
        # First, delete all database records atomically
        async with database.transaction():
            # Get job ID for quality_progress cleanup
            job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
            if job:
                await database.execute(quality_progress.delete().where(quality_progress.c.job_id == job["id"]))
            await database.execute(transcoding_jobs.delete().where(transcoding_jobs.c.video_id == video_id))
            await database.execute(playback_sessions.delete().where(playback_sessions.c.video_id == video_id))
            await database.execute(transcriptions.delete().where(transcriptions.c.video_id == video_id))
            await database.execute(video_qualities.delete().where(video_qualities.c.video_id == video_id))
            # Delete video record last (foreign key dependencies)
            await database.execute(videos.delete().where(videos.c.id == video_id))

        # Delete files AFTER successful transaction (file ops can't be rolled back)
        video_dir = VIDEOS_DIR / row["slug"]
        if video_dir.exists():
            shutil.rmtree(video_dir)

        # Delete archived files if any
        archive_dir = ARCHIVE_DIR / row["slug"]
        if archive_dir.exists():
            shutil.rmtree(archive_dir)

        # Delete source file from uploads if still there
        for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi"]:
            upload_file = UPLOADS_DIR / f"{video_id}{ext}"
            if upload_file.exists():
                upload_file.unlink()

        return {"status": "ok", "message": "Video permanently deleted"}

    else:
        # SOFT DELETE - move to archive
        # Update database FIRST to avoid inconsistent state if file ops fail
        await database.execute(
            videos.update().where(videos.c.id == video_id).values(deleted_at=datetime.now(timezone.utc))
        )

        video_dir = VIDEOS_DIR / row["slug"]
        archive_video_dir = ARCHIVE_DIR / row["slug"]
        moved_files = []  # Track what we moved for rollback

        try:
            # Move video files to archive
            if video_dir.exists():
                archive_video_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(video_dir), str(archive_video_dir))
                moved_files.append(("dir", archive_video_dir, video_dir))

            # Move source file to archive if still in uploads
            for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi"]:
                upload_file = UPLOADS_DIR / f"{video_id}{ext}"
                if upload_file.exists():
                    archive_upload = ARCHIVE_DIR / f"uploads/{video_id}{ext}"
                    archive_upload.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(upload_file), str(archive_upload))
                    moved_files.append(("file", archive_upload, upload_file))
        except Exception as e:
            # Rollback: restore files that were moved
            for item_type, src, dst in reversed(moved_files):
                try:
                    shutil.move(str(src), str(dst))
                except Exception:
                    pass  # Best effort rollback
            # Rollback database change
            await database.execute(
                videos.update().where(videos.c.id == video_id).values(deleted_at=None)
            )
            raise HTTPException(status_code=500, detail=f"Failed to archive files: {e}")

        return {"status": "ok", "message": "Video moved to archive"}


@app.post("/api/videos/{video_id}/restore")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def restore_video(request: Request, video_id: int):
    """Restore a soft-deleted video from archive."""
    row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    if not row["deleted_at"]:
        raise HTTPException(status_code=400, detail="Video is not deleted")

    # Store original deleted_at for potential rollback
    original_deleted_at = row["deleted_at"]

    # Update database FIRST to avoid inconsistent state if file ops fail
    await database.execute(videos.update().where(videos.c.id == video_id).values(deleted_at=None))

    archive_video_dir = ARCHIVE_DIR / row["slug"]
    video_dir = VIDEOS_DIR / row["slug"]
    moved_files = []  # Track what we moved for rollback

    try:
        # Move video files back from archive
        if archive_video_dir.exists():
            shutil.move(str(archive_video_dir), str(video_dir))
            moved_files.append(("dir", video_dir, archive_video_dir))

        # Move source file back if archived
        for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi"]:
            archive_upload = ARCHIVE_DIR / f"uploads/{video_id}{ext}"
            if archive_upload.exists():
                upload_file = UPLOADS_DIR / f"{video_id}{ext}"
                shutil.move(str(archive_upload), str(upload_file))
                moved_files.append(("file", upload_file, archive_upload))
    except Exception as e:
        # Rollback: restore files that were moved
        for item_type, src, dst in reversed(moved_files):
            try:
                shutil.move(str(src), str(dst))
            except Exception:
                pass  # Best effort rollback
        # Rollback database change
        await database.execute(
            videos.update().where(videos.c.id == video_id).values(deleted_at=original_deleted_at)
        )
        raise HTTPException(status_code=500, detail=f"Failed to restore files: {e}")

    return {"status": "ok", "message": "Video restored from archive"}


@app.get("/api/videos/archived")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_archived_videos(request: Request):
    """List all soft-deleted videos in archive."""
    query = videos.select().where(videos.c.deleted_at.is_not(None)).order_by(videos.c.deleted_at.desc())
    rows = await database.fetch_all(query)

    return {
        "videos": [
            {
                "id": row["id"],
                "title": row["title"],
                "slug": row["slug"],
                "deleted_at": row["deleted_at"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    }


@app.post("/api/videos/{video_id}/retry")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def retry_video(request: Request, video_id: int):
    """Retry processing a failed video."""
    row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    if row["status"] != VideoStatus.FAILED:
        raise HTTPException(status_code=400, detail="Video is not in failed state")

    # Check if source file exists
    source_exists = False
    for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi"]:
        if (UPLOADS_DIR / f"{video_id}{ext}").exists():
            source_exists = True
            break

    if not source_exists:
        raise HTTPException(status_code=400, detail="Source file no longer exists")

    # Reset status to pending
    await database.execute(
        videos.update()
        .where(videos.c.id == video_id)
        .values(
            status=VideoStatus.PENDING,
            error_message=None,
        )
    )

    return {"status": "ok", "message": "Video queued for retry"}


@app.post("/api/videos/{video_id}/re-upload")
@limiter.limit(RATE_LIMIT_ADMIN_UPLOAD)
async def re_upload_video(
    request: Request,
    video_id: int,
    file: UploadFile = File(...),
):
    """
    Re-upload a video file, replacing the existing transcoded content.

    This will:
    - Delete all existing transcoded files (HLS segments, playlists, thumbnail)
    - Delete video_qualities, transcoding_jobs, quality_progress, transcriptions
    - Save the new file and queue for reprocessing
    - Preserve: title, description, category, published_at, created_at, slug
    """
    # Validate file extension
    file_ext = Path(file.filename).suffix.lower() if file.filename else ""
    if not file_ext:
        file_ext = ".mp4"
    if file_ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{file_ext}'. Allowed: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}",
        )

    # Get video info
    row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    if row["deleted_at"]:
        raise HTTPException(status_code=400, detail="Cannot re-upload a deleted video")

    if row["status"] == VideoStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="Cannot re-upload while video is processing")

    slug = row["slug"]
    video_dir = VIDEOS_DIR / slug

    # === CLEANUP PHASE ===

    # 1. Delete all files in video directory (keep directory structure)
    if video_dir.exists():
        for item in video_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
    else:
        # Create the directory if it doesn't exist
        video_dir.mkdir(parents=True, exist_ok=True)

    # 2. Delete old source file from uploads
    for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi"]:
        upload_file = UPLOADS_DIR / f"{video_id}{ext}"
        if upload_file.exists():
            upload_file.unlink()

    # === DATABASE CLEANUP (atomic) ===
    async with database.transaction():
        # Delete transcoding job and quality_progress
        job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
        if job:
            await database.execute(quality_progress.delete().where(quality_progress.c.job_id == job["id"]))
        await database.execute(transcoding_jobs.delete().where(transcoding_jobs.c.video_id == video_id))

        # Delete transcriptions
        await database.execute(transcriptions.delete().where(transcriptions.c.video_id == video_id))

        # Delete video_qualities
        await database.execute(video_qualities.delete().where(video_qualities.c.video_id == video_id))

        # Reset video state
        await database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(
                status=VideoStatus.PENDING,
                duration=0,
                source_width=0,
                source_height=0,
                error_message=None,
            )
        )

    # === UPLOAD NEW FILE === (file_ext already validated above)
    # Done after transaction so DB state is consistent even if upload fails
    upload_path = UPLOADS_DIR / f"{video_id}{file_ext}"
    await save_upload_with_size_limit(file, upload_path)

    return {
        "status": "ok",
        "video_id": video_id,
        "slug": slug,
        "message": "Video queued for reprocessing",
    }


@app.get("/api/videos/{video_id}/progress")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_video_progress(request: Request, video_id: int) -> TranscodingProgressResponse:
    """Get transcoding progress for a video."""
    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))

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
    job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))

    if not job:
        return TranscodingProgressResponse(
            status=video["status"],
            progress_percent=0,
        )

    # Get quality progress
    quality_rows = await database.fetch_all(quality_progress.select().where(quality_progress.c.job_id == job["id"]))

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


# ============ Transcription ============


@app.get("/api/videos/{video_id}/transcript")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_video_transcript(request: Request, video_id: int) -> TranscriptionResponse:
    """Get transcription status and text for a video."""
    # Get video
    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get transcription record
    transcription = await database.fetch_one(transcriptions.select().where(transcriptions.c.video_id == video_id))

    if not transcription:
        return TranscriptionResponse(status=TranscriptionStatus.NONE)

    vtt_url = None
    if transcription["status"] == TranscriptionStatus.COMPLETED and transcription["vtt_path"]:
        vtt_url = f"/videos/{video['slug']}/captions.vtt"

    return TranscriptionResponse(
        status=transcription["status"],
        language=transcription["language"],
        text=transcription["transcript_text"],
        vtt_url=vtt_url,
        word_count=transcription["word_count"],
        duration_seconds=transcription["duration_seconds"],
        started_at=transcription["started_at"],
        completed_at=transcription["completed_at"],
        error_message=sanitize_error_message(transcription["error_message"], context=f"video_id={video_id}"),
    )


@app.post("/api/videos/{video_id}/transcribe")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def trigger_transcription(request: Request, video_id: int, data: TranscriptionTrigger = None):
    """Manually trigger transcription for a video."""
    # Get video
    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video["status"] != VideoStatus.READY:
        raise HTTPException(status_code=400, detail="Video must be ready before transcription")

    # Check if transcription already exists
    existing = await database.fetch_one(transcriptions.select().where(transcriptions.c.video_id == video_id))

    if existing:
        if existing["status"] == TranscriptionStatus.PROCESSING:
            raise HTTPException(status_code=400, detail="Transcription already in progress")

        # Reset to pending for re-transcription
        await database.execute(
            transcriptions.update()
            .where(transcriptions.c.video_id == video_id)
            .values(
                status=TranscriptionStatus.PENDING,
                language=data.language if data else None,
                started_at=None,
                completed_at=None,
                duration_seconds=None,
                transcript_text=None,
                vtt_path=None,
                word_count=None,
                error_message=None,
            )
        )
        return {"status": "ok", "message": "Transcription queued for retry"}

    # Create new transcription record
    await database.execute(
        transcriptions.insert().values(
            video_id=video_id,
            status=TranscriptionStatus.PENDING,
            language=data.language if data else None,
        )
    )

    return {"status": "ok", "message": "Transcription queued"}


@app.put("/api/videos/{video_id}/transcript")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def update_transcript(request: Request, video_id: int, data: TranscriptionUpdate):
    """Manually edit/correct transcript text and regenerate VTT."""
    # Get video
    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get transcription
    transcription = await database.fetch_one(transcriptions.select().where(transcriptions.c.video_id == video_id))

    if not transcription:
        raise HTTPException(status_code=404, detail="No transcription found for this video")

    # Update transcript text
    word_count = len(data.text.split())
    await database.execute(
        transcriptions.update()
        .where(transcriptions.c.video_id == video_id)
        .values(
            transcript_text=data.text,
            word_count=word_count,
        )
    )

    return {"status": "ok", "message": "Transcript updated", "word_count": word_count}


@app.delete("/api/videos/{video_id}/transcript")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def delete_transcript(request: Request, video_id: int):
    """Delete transcription and VTT file for a video."""
    # Get video
    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get transcription
    transcription = await database.fetch_one(transcriptions.select().where(transcriptions.c.video_id == video_id))

    if not transcription:
        raise HTTPException(status_code=404, detail="No transcription found for this video")

    # Delete VTT file if exists
    vtt_path = VIDEOS_DIR / video["slug"] / "captions.vtt"
    if vtt_path.exists():
        vtt_path.unlink()

    # Delete transcription record
    await database.execute(transcriptions.delete().where(transcriptions.c.video_id == video_id))

    return {"status": "ok", "message": "Transcription deleted"}


# ============ Analytics ============


@app.get("/api/analytics/overview")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def analytics_overview(request: Request) -> AnalyticsOverview:
    """Get global analytics overview."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)

    # Total views
    total_views = await database.fetch_val(sa.select(sa.func.count()).select_from(playback_sessions)) or 0

    # Unique viewers
    unique_viewers = (
        await database.fetch_val(
            sa.select(sa.func.count(sa.distinct(playback_sessions.c.viewer_id)))
            .select_from(playback_sessions)
            .where(playback_sessions.c.viewer_id.isnot(None))
        )
        or 0
    )

    # Total watch time
    total_watch_seconds = (
        await database.fetch_val(
            sa.select(sa.func.sum(playback_sessions.c.duration_watched)).select_from(playback_sessions)
        )
        or 0
    )
    total_watch_time_hours = total_watch_seconds / 3600

    # Completion rate
    completed_count = (
        await database.fetch_val(
            sa.select(sa.func.count()).select_from(playback_sessions).where(playback_sessions.c.completed.is_(True))
        )
        or 0
    )
    completion_rate = completed_count / total_views if total_views > 0 else 0

    # Average watch duration
    avg_watch = (
        await database.fetch_val(
            sa.select(sa.func.avg(playback_sessions.c.duration_watched)).select_from(playback_sessions)
        )
        or 0
    )

    # Views today
    views_today = (
        await database.fetch_val(
            sa.select(sa.func.count())
            .select_from(playback_sessions)
            .where(playback_sessions.c.started_at >= today_start)
        )
        or 0
    )

    # Views this week
    views_week = (
        await database.fetch_val(
            sa.select(sa.func.count())
            .select_from(playback_sessions)
            .where(playback_sessions.c.started_at >= week_start)
        )
        or 0
    )

    # Views this month
    views_month = (
        await database.fetch_val(
            sa.select(sa.func.count())
            .select_from(playback_sessions)
            .where(playback_sessions.c.started_at >= month_start)
        )
        or 0
    )

    return AnalyticsOverview(
        total_views=total_views,
        unique_viewers=unique_viewers,
        total_watch_time_hours=round(total_watch_time_hours, 1),
        completion_rate=round(completion_rate, 2),
        avg_watch_duration_seconds=round(avg_watch, 1),
        views_today=views_today,
        views_this_week=views_week,
        views_this_month=views_month,
    )


@app.get("/api/analytics/videos")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def analytics_videos(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    sort_by: str = "views",
    period: str = "all",
) -> VideoAnalyticsListResponse:
    """Get per-video analytics."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    period_filter = None
    if period == "day":
        period_filter = today_start
    elif period == "week":
        period_filter = today_start - timedelta(days=7)
    elif period == "month":
        period_filter = today_start - timedelta(days=30)

    # Build query with aggregations - use parameterized queries
    period_clause = "AND ps.started_at >= :period_filter" if period_filter else ""

    # Validate sort_by to prevent SQL injection (whitelist approach)
    valid_sort_columns = {
        "views": "total_views DESC",
        "watch_time": "total_watch_time_seconds DESC",
        "completion_rate": "completion_rate DESC",
    }
    order_clause = valid_sort_columns.get(sort_by, "total_views DESC")

    base_query = f"""
        SELECT
            v.id as video_id,
            v.title,
            v.slug,
            COUNT(ps.id) as total_views,
            COUNT(DISTINCT ps.viewer_id) as unique_viewers,
            COALESCE(SUM(ps.duration_watched), 0) as total_watch_time_seconds,
            COALESCE(AVG(ps.duration_watched), 0) as avg_watch_duration_seconds,
            COALESCE(SUM(CASE WHEN ps.completed THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(ps.id), 0), 0) as completion_rate,
            (SELECT quality_used FROM playback_sessions WHERE video_id = v.id GROUP BY quality_used ORDER BY COUNT(*) DESC LIMIT 1) as peak_quality
        FROM videos v
        LEFT JOIN playback_sessions ps ON v.id = ps.video_id {period_clause}
        WHERE v.status = 'ready'
        GROUP BY v.id
        ORDER BY {order_clause}
        LIMIT :limit OFFSET :offset
    """

    params = {"limit": limit, "offset": offset}
    if period_filter:
        params["period_filter"] = period_filter.isoformat()

    rows = await database.fetch_all(sa.text(base_query), params)

    # Get total count
    count_result = await database.fetch_val(
        sa.select(sa.func.count()).select_from(videos).where(videos.c.status == VideoStatus.READY)
    )

    video_stats = []
    for row in rows:
        video_stats.append(
            VideoAnalyticsSummary(
                video_id=row["video_id"],
                title=row["title"],
                slug=row["slug"],
                thumbnail_url=f"/videos/{row['slug']}/thumbnail.jpg",
                total_views=row["total_views"] or 0,
                unique_viewers=row["unique_viewers"] or 0,
                total_watch_time_seconds=row["total_watch_time_seconds"] or 0,
                avg_watch_duration_seconds=round(row["avg_watch_duration_seconds"] or 0, 1),
                completion_rate=round(row["completion_rate"] or 0, 2),
                peak_quality=row["peak_quality"],
            )
        )

    return VideoAnalyticsListResponse(
        videos=video_stats,
        total_count=count_result or 0,
    )


@app.get("/api/analytics/videos/{video_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def analytics_video_detail(request: Request, video_id: int) -> VideoAnalyticsDetail:
    """Get detailed analytics for a specific video."""
    # Get video info
    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get aggregated stats
    stats_query = """
        SELECT
            COUNT(*) as total_views,
            COUNT(DISTINCT viewer_id) as unique_viewers,
            COALESCE(SUM(duration_watched), 0) as total_watch_time_seconds,
            COALESCE(AVG(duration_watched), 0) as avg_watch_duration_seconds,
            COALESCE(SUM(CASE WHEN completed THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0), 0) as completion_rate,
            COALESCE(AVG(max_position / NULLIF(:duration, 0)), 0) as avg_percent_watched
        FROM playback_sessions
        WHERE video_id = :video_id
    """
    stats = await database.fetch_one(sa.text(stats_query), {"video_id": video_id, "duration": video["duration"] or 1})

    # Quality breakdown
    quality_query = """
        SELECT
            quality_used as quality,
            COUNT(*) * 1.0 / (SELECT COUNT(*) FROM playback_sessions WHERE video_id = :video_id AND quality_used IS NOT NULL) as percentage
        FROM playback_sessions
        WHERE video_id = :video_id AND quality_used IS NOT NULL
        GROUP BY quality_used
        ORDER BY percentage DESC
    """
    quality_rows = await database.fetch_all(sa.text(quality_query), {"video_id": video_id})

    quality_breakdown = (
        [QualityBreakdown(quality=q["quality"], percentage=round(q["percentage"], 2)) for q in quality_rows]
        if quality_rows
        else []
    )

    # Views over time (last 30 days)
    views_query = """
        SELECT
            DATE(started_at) as date,
            COUNT(*) as views
        FROM playback_sessions
        WHERE video_id = :video_id
            AND started_at >= DATE('now', '-30 days')
        GROUP BY DATE(started_at)
        ORDER BY date
    """
    views_rows = await database.fetch_all(sa.text(views_query), {"video_id": video_id})

    views_over_time = [DailyViews(date=str(v["date"]), views=v["views"]) for v in views_rows] if views_rows else []

    return VideoAnalyticsDetail(
        video_id=video_id,
        title=video["title"],
        duration=video["duration"] or 0,
        total_views=stats["total_views"] or 0,
        unique_viewers=stats["unique_viewers"] or 0,
        total_watch_time_seconds=stats["total_watch_time_seconds"] or 0,
        avg_watch_duration_seconds=round(stats["avg_watch_duration_seconds"] or 0, 1),
        completion_rate=round(stats["completion_rate"] or 0, 2),
        avg_percent_watched=round(stats["avg_percent_watched"] or 0, 2),
        quality_breakdown=quality_breakdown,
        views_over_time=views_over_time,
    )


@app.get("/api/analytics/trends")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def analytics_trends(
    request: Request,
    period: str = "30d",
    video_id: Optional[int] = None,
) -> TrendsResponse:
    """Get time-series analytics data."""
    # Validate period to prevent SQL injection (whitelist approach)
    valid_periods = {"7d": 7, "30d": 30, "90d": 90}
    days = valid_periods.get(period, 30)

    # Build query with parameterized values
    video_clause = "AND video_id = :video_id" if video_id else ""

    base_query = f"""
        SELECT
            DATE(started_at) as date,
            COUNT(*) as views,
            COUNT(DISTINCT viewer_id) as unique_viewers,
            COALESCE(SUM(duration_watched), 0) / 3600.0 as watch_time_hours
        FROM playback_sessions
        WHERE started_at >= DATE('now', :days_offset)
        {video_clause}
        GROUP BY DATE(started_at)
        ORDER BY date
    """

    params = {"days_offset": f"-{days} days"}
    if video_id:
        params["video_id"] = video_id

    rows = await database.fetch_all(sa.text(base_query), params)

    data = [
        TrendDataPoint(
            date=str(r["date"]),
            views=r["views"],
            unique_viewers=r["unique_viewers"] or 0,
            watch_time_hours=round(r["watch_time_hours"], 2),
        )
        for r in rows
    ]

    return TrendsResponse(period=period, data=data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=ADMIN_PORT)
