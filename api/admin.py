"""
Admin API - handles uploads and video management.
Runs on port 9001 (not exposed externally).
"""

import asyncio
import hmac
import json
import logging
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Note: IntegrityError handling is done via exception message inspection
# to support both SQLite and PostgreSQL backends
from typing import List, Optional

import sqlalchemy as sa
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slugify import slugify
from sse_starlette.sse import EventSourceResponse

from api.analytics_cache import create_analytics_cache
from api.audit import AuditAction, log_audit
from api.common import (
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    check_health,
    check_storage_available,
    get_real_ip,
    get_storage_status,
    rate_limit_exceeded_handler,
)
from api.database import (
    admin_sessions,
    categories,
    configure_database,
    create_tables,
    database,
    playback_sessions,
    quality_progress,
    tags,
    transcoding_jobs,
    transcriptions,
    video_qualities,
    video_tags,
    videos,
    worker_api_keys,
    workers,
)
from api.db_retry import (
    DatabaseLockedError,
    db_execute_with_retry,
    fetch_all_with_retry,
    fetch_one_with_retry,
    fetch_val_with_retry,
)
from api.enums import TranscriptionStatus, VideoStatus
from api.errors import is_unique_violation, sanitize_error_message, sanitize_progress_error
from api.job_queue import JobDispatch, get_job_queue
from api.pubsub import subscribe_to_progress, subscribe_to_workers
from api.redis_client import is_redis_available
from api.schemas import (
    ActiveJobsResponse,
    ActiveJobWithWorker,
    AnalyticsOverview,
    BulkDeleteRequest,
    BulkDeleteResponse,
    BulkOperationResult,
    BulkRestoreRequest,
    BulkRestoreResponse,
    BulkRetranscodeRequest,
    BulkRetranscodeResponse,
    BulkUpdateRequest,
    BulkUpdateResponse,
    CategoryCreate,
    CategoryResponse,
    DailyViews,
    QualityBreakdown,
    QualityProgressResponse,
    RetranscodeRequest,
    RetranscodeResponse,
    SettingCreate,
    SettingResponse,
    SettingsByCategoryResponse,
    SettingsCategoryResponse,
    SettingsExport,
    SettingsImport,
    SettingUpdate,
    TagCreate,
    TagResponse,
    TagUpdate,
    ThumbnailFrame,
    ThumbnailFramesResponse,
    ThumbnailInfoResponse,
    ThumbnailResponse,
    TranscodingProgressResponse,
    TranscriptionResponse,
    TranscriptionTrigger,
    TranscriptionUpdate,
    TrendDataPoint,
    TrendsResponse,
    VideoAnalyticsDetail,
    VideoAnalyticsListResponse,
    VideoAnalyticsSummary,
    VideoExportItem,
    VideoExportResponse,
    VideoListResponse,
    VideoQualitiesResponse,
    VideoQualityInfo,
    VideoQualityResponse,
    VideoResponse,
    VideoTagInfo,
    VideoTagsUpdate,
    WorkerDashboardResponse,
    WorkerDashboardStatus,
    WorkerDetailResponse,
    WorkerJobHistory,
)
from api.settings_service import SettingsValidationError, get_settings_service
from config import (
    ADMIN_API_SECRET,
    ADMIN_CORS_ALLOWED_ORIGINS,
    ADMIN_PORT,
    ADMIN_SESSION_EXPIRY_HOURS,
    ANALYTICS_CACHE_ENABLED,
    ANALYTICS_CACHE_STORAGE_URL,
    ANALYTICS_CACHE_TTL,
    ANALYTICS_CLIENT_CACHE_MAX_AGE,
    ARCHIVE_DIR,
    JOB_QUEUE_MODE,
    MAX_THUMBNAIL_UPLOAD_SIZE,
    MAX_UPLOAD_SIZE,
    NAS_STORAGE,
    QUALITY_PRESETS,
    RATE_LIMIT_ADMIN_DEFAULT,
    RATE_LIMIT_ADMIN_UPLOAD,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
    SECURE_COOKIES,
    SSE_HEARTBEAT_INTERVAL,
    SSE_RECONNECT_TIMEOUT_MS,
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    THUMBNAIL_FRAME_PERCENTAGES,
    THUMBNAIL_WIDTH,
    UPLOAD_CHUNK_SIZE,
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
    WORKER_OFFLINE_THRESHOLD_MINUTES,
)
from worker.transcoder import generate_thumbnail, get_video_info

logger = logging.getLogger(__name__)

# Initialize rate limiter for admin API
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)

# Initialize analytics cache (uses Redis if ANALYTICS_CACHE_STORAGE_URL is set to redis://)
analytics_cache = create_analytics_cache(
    storage_url=ANALYTICS_CACHE_STORAGE_URL,
    ttl_seconds=ANALYTICS_CACHE_TTL,
    enabled=ANALYTICS_CACHE_ENABLED,
)

# Use centralized video extensions from config
ALLOWED_VIDEO_EXTENSIONS = SUPPORTED_VIDEO_EXTENSIONS

# Input length limits
MAX_TITLE_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 5000

# Security event logger for authentication events
security_logger = logging.getLogger("security.admin_auth")

# Session cookie name
ADMIN_SESSION_COOKIE = "vlog_admin_session"


async def validate_session_token(session_token: str) -> bool:
    """
    Validate a session token against the database.
    Returns True if valid, False if invalid or expired.
    Updates last_used_at timestamp for valid sessions.
    """
    if not session_token:
        return False

    now = datetime.now(timezone.utc)
    query = admin_sessions.select().where(
        admin_sessions.c.session_token == session_token,
        admin_sessions.c.expires_at > now,
    )
    session = await database.fetch_one(query)

    if not session:
        return False

    # Update last_used_at
    update_query = (
        admin_sessions.update()
        .where(admin_sessions.c.session_token == session_token)
        .values(last_used_at=now)
    )
    await database.execute(update_query)

    return True


async def create_admin_session(
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> str:
    """
    Create a new admin session and return the session token.
    """
    session_token = secrets.token_urlsafe(48)  # 64 chars base64
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=ADMIN_SESSION_EXPIRY_HOURS)

    query = admin_sessions.insert().values(
        session_token=session_token,
        created_at=now,
        expires_at=expires_at,
        last_used_at=now,
        ip_address=ip_address[:45] if ip_address else None,
        user_agent=user_agent[:512] if user_agent else None,
    )
    await database.execute(query)

    security_logger.info(
        "Admin session created",
        extra={"event": "session_created", "client_ip": ip_address, "expires_at": expires_at.isoformat()},
    )

    return session_token


async def delete_admin_session(session_token: str) -> None:
    """
    Delete an admin session if it exists.
    """
    query = admin_sessions.delete().where(admin_sessions.c.session_token == session_token)
    await database.execute(query)


async def cleanup_expired_sessions() -> int:
    """
    Delete expired sessions. Returns the number of sessions deleted.
    Called periodically to clean up stale sessions.
    """
    now = datetime.now(timezone.utc)
    query = admin_sessions.delete().where(admin_sessions.c.expires_at < now)
    return await database.execute(query)


class AdminAuthMiddleware:
    """
    Middleware to protect Admin API endpoints with authentication.

    Supports two authentication methods:
    1. X-Admin-Secret header - for API clients and CLI tools
    2. Session cookie - for browser-based UI (HTTP-only, secure)

    When ADMIN_API_SECRET is configured:
    - All /api/* paths (except /api/auth/*) require authentication
    - Authentication can be via X-Admin-Secret header OR valid session cookie
    - Returns 401 if neither is provided or valid

    When ADMIN_API_SECRET is not configured (empty):
    - All requests are allowed (backwards compatible)

    Paths that are always allowed (no auth required):
    - / (admin HTML page)
    - /health (monitoring)
    - /static/* (static files)
    - /videos/* (video file serving for preview)
    - /api/auth/* (login, logout, check endpoints)
    """

    def __init__(self, app):
        self.app = app

    def _parse_cookies(self, cookie_header: bytes) -> dict:
        """Parse Cookie header into a dict."""
        cookies = {}
        if cookie_header:
            cookie_str = cookie_header.decode("utf-8", errors="ignore")
            for item in cookie_str.split(";"):
                item = item.strip()
                if "=" in item:
                    key, value = item.split("=", 1)
                    cookies[key.strip()] = value.strip()
        return cookies

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")

        # Skip auth for non-API paths
        if not path.startswith("/api"):
            await self.app(scope, receive, send)
            return

        # Skip auth for OPTIONS (CORS preflight) requests
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # Skip auth for auth endpoints (login, logout, check)
        if path.startswith("/api/auth/"):
            await self.app(scope, receive, send)
            return

        # If ADMIN_API_SECRET is not configured, allow all requests (backwards compatible)
        if not ADMIN_API_SECRET:
            await self.app(scope, receive, send)
            return

        # Extract client IP for logging
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        # Extract headers
        headers = dict(scope.get("headers", []))

        # Method 1: Check X-Admin-Secret header (for API clients/CLI)
        admin_secret = headers.get(b"x-admin-secret", b"").decode("utf-8", errors="ignore")
        if admin_secret:
            if hmac.compare_digest(admin_secret, ADMIN_API_SECRET):
                # Header auth successful
                security_logger.info(
                    "Admin API auth successful via header",
                    extra={"event": "auth_success", "method": "header", "path": path, "client_ip": client_ip},
                )
                await self.app(scope, receive, send)
                return
            else:
                security_logger.warning(
                    "Admin API auth failed: invalid secret header",
                    extra={"event": "auth_failure", "reason": "invalid_secret", "path": path, "client_ip": client_ip},
                )
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "Invalid admin secret"},
                )
                await response(scope, receive, send)
                return

        # Method 2: Check session cookie (for browser UI)
        cookie_header = headers.get(b"cookie", b"")
        cookies = self._parse_cookies(cookie_header)
        session_token = cookies.get(ADMIN_SESSION_COOKIE, "")

        if session_token:
            # Validate session against database
            is_valid = await validate_session_token(session_token)
            if is_valid:
                # Cookie auth successful
                security_logger.info(
                    "Admin API auth successful via session cookie",
                    extra={"event": "auth_success", "method": "cookie", "path": path, "client_ip": client_ip},
                )
                await self.app(scope, receive, send)
                return
            else:
                security_logger.warning(
                    "Admin API auth failed: invalid or expired session",
                    extra={"event": "auth_failure", "reason": "invalid_session", "path": path, "client_ip": client_ip},
                )

        # No valid authentication provided
        security_logger.warning(
            "Admin API auth failed: no valid credentials",
            extra={"event": "auth_failure", "reason": "no_credentials", "path": path, "client_ip": client_ip},
        )
        response = JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
        )
        await response(scope, receive, send)


async def delete_video_and_job(video_id: int) -> None:
    """
    Delete a video and all its related records safely.

    IMPORTANT: Always use this instead of videos.delete() directly!
    SQLite foreign key CASCADE is unreliable with the async databases library
    because foreign_keys pragma is per-connection and connections are pooled.

    This explicitly deletes related records to prevent orphaned data.
    Deletes: quality_progress, transcoding_jobs, playback_sessions,
    transcriptions, video_qualities, video_tags, and the video itself.
    """
    # Get job_id first (if exists) for quality_progress cleanup
    job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
    if job:
        # Delete quality_progress entries first (FK to transcoding_jobs)
        await database.execute(quality_progress.delete().where(quality_progress.c.job_id == job["id"]))
        # Delete transcoding job
        await database.execute(transcoding_jobs.delete().where(transcoding_jobs.c.id == job["id"]))
    # Delete all related records
    await database.execute(playback_sessions.delete().where(playback_sessions.c.video_id == video_id))
    await database.execute(transcriptions.delete().where(transcriptions.c.video_id == video_id))
    await database.execute(video_qualities.delete().where(video_qualities.c.video_id == video_id))
    await database.execute(video_tags.delete().where(video_tags.c.video_id == video_id))
    # Delete video record last (after all FK dependencies are removed)
    await database.execute(videos.delete().where(videos.c.id == video_id))


def validate_content_length(request: Request) -> None:
    """
    Validate Content-Length header against MAX_UPLOAD_SIZE.

    This provides early rejection of oversized uploads before the transfer starts,
    saving bandwidth for both client and server.

    Raises:
        HTTPException: 413 if Content-Length exceeds MAX_UPLOAD_SIZE
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_SIZE:
                max_size_gb = MAX_UPLOAD_SIZE / (1024 * 1024 * 1024)
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Maximum upload size is {max_size_gb:.0f} GB",
                )
        except ValueError:
            pass  # Invalid Content-Length header, continue with streaming validation


async def save_upload_with_size_limit(file: UploadFile, upload_path: Path, max_size: int = MAX_UPLOAD_SIZE) -> int:
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
    except (OSError, IOError, PermissionError) as e:
        # Storage-related errors - clean up and return 503
        upload_path.unlink(missing_ok=True)
        logger.warning(f"Storage error during upload to {upload_path}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Video storage temporarily unavailable. Please try again later.",
            headers={"Retry-After": "30"},
        )
    except Exception as e:
        # Clean up on any other error
        upload_path.unlink(missing_ok=True)
        logger.exception(f"Unexpected error during file upload: {e}")
        raise HTTPException(status_code=500, detail="Upload failed")

    return total_size


async def cleanup_orphaned_jobs() -> int:
    """
    Remove transcoding jobs that reference non-existent videos.

    This can happen when CASCADE deletes fail due to SQLite foreign_keys
    pragma not being enabled on all connections (a limitation of the
    async databases library with connection pooling).

    Returns the number of orphaned jobs deleted.
    """
    # Find orphaned jobs (video_id doesn't exist in videos table)
    orphaned = await database.fetch_all(
        sa.text("""
            SELECT tj.id, tj.video_id
            FROM transcoding_jobs tj
            LEFT JOIN videos v ON tj.video_id = v.id
            WHERE v.id IS NULL
        """)
    )

    if not orphaned:
        return 0

    for job in orphaned:
        # Delete quality_progress first
        await database.execute(quality_progress.delete().where(quality_progress.c.job_id == job["id"]))
        # Delete the orphaned job
        await database.execute(transcoding_jobs.delete().where(transcoding_jobs.c.id == job["id"]))

    logger.warning(f"Cleaned up {len(orphaned)} orphaned transcoding job(s)")
    return len(orphaned)


async def create_or_reset_transcoding_job(video_id: int, priority: str = "normal") -> None:
    """
    Create a new transcoding job or reset an existing one for a video.

    Uses ON CONFLICT DO UPDATE for PostgreSQL to handle the case where a job
    already exists (e.g., due to upload retry, duplicate submission, or
    re-transcode of a video with existing job). For SQLite (tests), catches
    IntegrityError and updates the existing job.

    This prevents HTTP 500 errors from unique constraint violations (issue #270).

    If Redis is configured, also publishes the job to the Redis Streams queue
    for instant dispatch to workers.

    Args:
        video_id: The video ID to create/reset a job for
        priority: Job priority ("high", "normal", "low")
    """
    # Validate priority (defense-in-depth beyond Pydantic validation)
    if priority not in ("high", "normal", "low"):
        priority = "normal"

    db_url = str(database.url)
    is_postgresql = db_url.startswith("postgresql")

    if is_postgresql:
        # PostgreSQL: Use INSERT ON CONFLICT DO UPDATE to atomically
        # create or reset the job in a single statement
        await db_execute_with_retry(
            sa.text("""
                INSERT INTO transcoding_jobs (video_id, current_step, progress_percent, attempt_number, max_attempts)
                VALUES (:video_id, 'pending', 0, 1, 3)
                ON CONFLICT (video_id) DO UPDATE SET
                    current_step = 'pending',
                    progress_percent = 0,
                    attempt_number = transcoding_jobs.attempt_number + 1,
                    worker_id = NULL,
                    claimed_at = NULL,
                    claim_expires_at = NULL,
                    started_at = NULL,
                    completed_at = NULL,
                    last_error = NULL
            """).bindparams(video_id=video_id)
        )
    else:
        # SQLite: Try insert, catch IntegrityError and update instead
        try:
            await db_execute_with_retry(
                transcoding_jobs.insert().values(
                    video_id=video_id,
                    current_step="pending",
                    progress_percent=0,
                    attempt_number=1,
                    max_attempts=3,
                )
            )
        except Exception as e:
            # Check if it's a unique constraint violation
            error_str = str(e).lower()
            if "unique constraint" in error_str or "unique" in error_str:
                # Job already exists - reset it
                await db_execute_with_retry(
                    transcoding_jobs.update()
                    .where(transcoding_jobs.c.video_id == video_id)
                    .values(
                        current_step="pending",
                        progress_percent=0,
                        attempt_number=transcoding_jobs.c.attempt_number + 1,
                        worker_id=None,
                        claimed_at=None,
                        claim_expires_at=None,
                        started_at=None,
                        completed_at=None,
                        last_error=None,
                    )
                )
            else:
                # Re-raise other errors (retryable errors are handled by db_execute_with_retry)
                raise

    # Publish job to Redis Streams for instant dispatch (if configured)
    if JOB_QUEUE_MODE in ("redis", "hybrid"):
        try:
            # Get video and job info for the dispatch message
            video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
            job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))

            if video and job:
                job_dispatch = JobDispatch(
                    job_id=job["id"],
                    video_id=video_id,
                    video_slug=video["slug"],
                    source_width=video["source_width"],
                    source_height=video["source_height"],
                    duration=video["duration"],
                    priority=priority,
                )

                job_queue = await get_job_queue()
                published = await job_queue.publish_job(job_dispatch)
                if published:
                    logger.debug(f"Published job {job['id']} to Redis queue (priority: {priority})")
        except Exception as e:
            # Redis publish failure is not critical - workers will poll database
            logger.warning(f"Failed to publish job to Redis: {e}")


# Background task for periodic session cleanup
_session_cleanup_task: Optional[asyncio.Task] = None


async def _periodic_session_cleanup():
    """Background task to periodically clean up expired sessions."""
    while True:
        try:
            # Run cleanup every hour
            await asyncio.sleep(3600)
            deleted = await cleanup_expired_sessions()
            if deleted:
                logger.info(f"Cleaned up {deleted} expired admin sessions")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error during session cleanup: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    global _session_cleanup_task

    # Warn about in-memory rate limiting limitations
    if RATE_LIMIT_ENABLED and RATE_LIMIT_STORAGE_URL == "memory://":
        logger.warning(
            "Rate limiting is using in-memory storage. "
            "For production deployments with multiple instances, configure Redis: "
            "VLOG_RATE_LIMIT_STORAGE_URL=redis://localhost:6379"
        )
    create_tables()
    await database.connect()
    await configure_database()

    # Clean up any orphaned transcoding jobs from previous crashes/bugs
    await cleanup_orphaned_jobs()

    # Clean up expired sessions on startup
    expired_count = await cleanup_expired_sessions()
    if expired_count:
        logger.info(f"Cleaned up {expired_count} expired admin sessions on startup")

    # Start background task for periodic session cleanup
    _session_cleanup_task = asyncio.create_task(_periodic_session_cleanup())

    yield

    # Cancel background cleanup task
    if _session_cleanup_task:
        _session_cleanup_task.cancel()
        try:
            await _session_cleanup_task
        except asyncio.CancelledError:
            pass

    await database.disconnect()


app = FastAPI(title="VLog Admin", description="Video management API", lifespan=lifespan)

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

# Admin API authentication middleware (see AdminAuthMiddleware class)
# Only active when VLOG_ADMIN_API_SECRET is configured
app.add_middleware(AdminAuthMiddleware)

# Allow CORS for admin UI (internal-only, not exposed externally)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ADMIN_CORS_ALLOWED_ORIGINS,
    allow_credentials=True if ADMIN_CORS_ALLOWED_ORIGINS != ["*"] else False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
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


# ============ Authentication ============
# Server-side session management for secure browser authentication.
# Fixes XSS vulnerability where admin secret was stored in sessionStorage.
# See: https://github.com/filthyrake/vlog/issues/324


@app.post("/api/auth/login")
@limiter.limit("10/minute")
async def auth_login(request: Request, response: Response):
    """
    Authenticate with admin secret and create a session.

    Validates the admin secret and creates a server-side session.
    Sets an HTTP-only, Secure cookie for subsequent requests.

    Request body: {"secret": "your-admin-secret"}
    """
    # If auth is not configured, sessions aren't needed
    if not ADMIN_API_SECRET:
        return {"authenticated": True, "message": "Authentication not required"}

    try:
        body = await request.json()
        secret = body.get("secret", "")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    if not secret:
        raise HTTPException(status_code=400, detail="Secret is required")

    # Validate the secret
    if not hmac.compare_digest(secret, ADMIN_API_SECRET):
        client_ip = get_real_ip(request)
        security_logger.warning(
            "Admin login failed: invalid secret",
            extra={"event": "login_failure", "client_ip": client_ip},
        )
        raise HTTPException(status_code=403, detail="Invalid admin secret")

    # Create session
    client_ip = get_real_ip(request)
    user_agent = request.headers.get("user-agent", "")
    session_token = await create_admin_session(ip_address=client_ip, user_agent=user_agent)

    # Set HTTP-only cookie
    # SameSite=Lax allows the cookie to be sent with top-level navigations
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=session_token,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        max_age=ADMIN_SESSION_EXPIRY_HOURS * 3600,
        path="/",
    )

    security_logger.info(
        "Admin login successful",
        extra={"event": "login_success", "client_ip": client_ip},
    )

    return {"authenticated": True, "message": "Login successful"}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    """
    Log out and destroy the current session.

    Deletes the server-side session and clears the cookie.
    """
    # Get session token from cookie
    session_token = request.cookies.get(ADMIN_SESSION_COOKIE, "")

    if session_token:
        await delete_admin_session(session_token)
        client_ip = get_real_ip(request)
        security_logger.info(
            "Admin logout",
            extra={"event": "logout", "client_ip": client_ip},
        )

    # Clear the cookie
    response.delete_cookie(
        key=ADMIN_SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
    )

    return {"authenticated": False, "message": "Logged out"}


@app.get("/api/auth/check")
async def auth_check(request: Request):
    """
    Check if the current session is authenticated.

    Returns authentication status without requiring credentials.
    Used by the UI to determine if login is needed.
    """
    # If auth is not configured, always authenticated
    if not ADMIN_API_SECRET:
        return {"authenticated": True, "auth_required": False}

    # Check for X-Admin-Secret header (for API clients)
    admin_secret = request.headers.get("x-admin-secret", "")
    if admin_secret and hmac.compare_digest(admin_secret, ADMIN_API_SECRET):
        return {"authenticated": True, "auth_required": True}

    # Check session cookie
    session_token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    if session_token:
        is_valid = await validate_session_token(session_token)
        if is_valid:
            return {"authenticated": True, "auth_required": True}

    return {"authenticated": False, "auth_required": True}


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


@app.post("/api/categories")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def create_category(request: Request, data: CategoryCreate) -> CategoryResponse:
    """Create a new category."""
    slug = slugify(data.name)

    # Check for duplicate slug
    existing = await fetch_one_with_retry(categories.select().where(categories.c.slug == slug))
    if existing:
        raise HTTPException(status_code=400, detail="Category with this name already exists")

    query = categories.insert().values(
        name=data.name,
        slug=slug,
        description=data.description,
        created_at=datetime.now(timezone.utc),
    )
    category_id = await db_execute_with_retry(query)

    # Audit log
    log_audit(
        AuditAction.CATEGORY_CREATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="category",
        resource_id=category_id,
        resource_name=slug,
        details={"name": data.name},
    )

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
    existing = await fetch_one_with_retry(categories.select().where(categories.c.id == category_id))
    if not existing:
        raise HTTPException(status_code=404, detail="Category not found")

    # Use transaction to ensure atomicity
    async with database.transaction():
        # Set videos in this category to uncategorized
        await database.execute(videos.update().where(videos.c.category_id == category_id).values(category_id=None))
        await database.execute(categories.delete().where(categories.c.id == category_id))

    # Audit log
    log_audit(
        AuditAction.CATEGORY_DELETE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="category",
        resource_id=category_id,
        resource_name=existing["slug"],
        details={"name": existing["name"]},
    )

    return {"status": "ok"}


# ============ Tags ============


@app.get("/api/tags")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_tags(request: Request) -> List[TagResponse]:
    """List all tags with video counts (including non-ready videos for admin)."""
    query = sa.text("""
        SELECT t.*, COUNT(vt.video_id) as video_count
        FROM tags t
        LEFT JOIN video_tags vt ON vt.tag_id = t.id
        LEFT JOIN videos v ON v.id = vt.video_id AND v.deleted_at IS NULL
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


@app.post("/api/tags")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def create_tag(request: Request, data: TagCreate) -> TagResponse:
    """Create a new tag."""
    slug = slugify(data.name)

    # Check for duplicate slug
    existing = await fetch_one_with_retry(tags.select().where(tags.c.slug == slug))
    if existing:
        raise HTTPException(status_code=400, detail="Tag with this name already exists")

    query = tags.insert().values(
        name=data.name,
        slug=slug,
        created_at=datetime.now(timezone.utc),
    )
    tag_id = await db_execute_with_retry(query)

    # Audit log
    log_audit(
        AuditAction.TAG_CREATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="tag",
        resource_id=tag_id,
        resource_name=slug,
        details={"name": data.name},
    )

    return TagResponse(
        id=tag_id,
        name=data.name,
        slug=slug,
        created_at=datetime.now(timezone.utc),
        video_count=0,
    )


@app.put("/api/tags/{tag_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def update_tag(request: Request, tag_id: int, data: TagUpdate) -> TagResponse:
    """Update a tag name."""
    # Verify tag exists
    existing = await fetch_one_with_retry(tags.select().where(tags.c.id == tag_id))
    if not existing:
        raise HTTPException(status_code=404, detail="Tag not found")

    new_slug = slugify(data.name)

    # Check for duplicate slug (exclude current tag)
    duplicate = await fetch_one_with_retry(
        tags.select().where(tags.c.slug == new_slug).where(tags.c.id != tag_id)
    )
    if duplicate:
        raise HTTPException(status_code=400, detail="Tag with this name already exists")

    await db_execute_with_retry(
        tags.update().where(tags.c.id == tag_id).values(name=data.name, slug=new_slug)
    )

    # Get video count
    count_query = (
        sa.select(sa.func.count(sa.distinct(videos.c.id)))
        .select_from(video_tags.join(videos, videos.c.id == video_tags.c.video_id))
        .where(video_tags.c.tag_id == tag_id)
        .where(videos.c.deleted_at.is_(None))
    )
    video_count = await fetch_val_with_retry(count_query)

    # Audit log
    log_audit(
        AuditAction.TAG_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="tag",
        resource_id=tag_id,
        resource_name=new_slug,
        details={"old_name": existing["name"], "new_name": data.name},
    )

    return TagResponse(
        id=tag_id,
        name=data.name,
        slug=new_slug,
        created_at=existing["created_at"],
        video_count=video_count or 0,
    )


@app.delete("/api/tags/{tag_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def delete_tag(request: Request, tag_id: int):
    """Delete a tag. Videos with this tag will have it removed."""
    # Verify tag exists
    existing = await fetch_one_with_retry(tags.select().where(tags.c.id == tag_id))
    if not existing:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Use transaction to ensure atomicity
    async with database.transaction():
        # Delete video_tags entries first (FK constraint)
        await database.execute(video_tags.delete().where(video_tags.c.tag_id == tag_id))
        # Delete the tag
        await database.execute(tags.delete().where(tags.c.id == tag_id))

    # Audit log
    log_audit(
        AuditAction.TAG_DELETE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="tag",
        resource_id=tag_id,
        resource_name=existing["slug"],
        details={"name": existing["name"]},
    )

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
            videos.c.thumbnail_source,
            videos.c.thumbnail_timestamp,
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

    rows = await fetch_all_with_retry(query)

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
            thumbnail_source=row["thumbnail_source"] or "auto",
            thumbnail_timestamp=row["thumbnail_timestamp"],
        )
        for row in rows
    ]


@app.get("/api/videos/archived")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_archived_videos(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
):
    """List all soft-deleted videos in archive.

    NOTE: This route must be defined before /api/videos/{video_id}
    to prevent "archived" from being matched as a video_id.
    """
    query = (
        videos.select()
        .where(videos.c.deleted_at.is_not(None))
        .order_by(videos.c.deleted_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = await fetch_all_with_retry(query)

    # Get total count of archived videos
    count_query = sa.select(sa.func.count()).select_from(videos).where(videos.c.deleted_at.is_not(None))
    total = await fetch_val_with_retry(count_query)

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
        ],
        "total": total,
    }


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

    row = await fetch_one_with_retry(query)
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    quality_rows = await fetch_all_with_retry(video_qualities.select().where(video_qualities.c.video_id == video_id))

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
        thumbnail_source=row["thumbnail_source"] or "auto",
        thumbnail_timestamp=row["thumbnail_timestamp"],
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
    # Early rejection based on Content-Length header (if provided)
    validate_content_length(request)

    # Check storage availability before accepting upload
    if not await check_storage_available():
        raise HTTPException(
            status_code=503,
            detail="Video storage temporarily unavailable. Please try again later.",
            headers={"Retry-After": "30"},
        )

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
                duration=0,
                source_width=0,
                source_height=0,
            )
            video_id = await database.execute(query)
        except HTTPException:
            raise
        except Exception as e:
            # Check for unique constraint violation on slug (works with SQLite and PostgreSQL)
            if is_unique_violation(e, column="slug"):
                counter += 1
                slug = f"{base_slug}-{counter}"
            else:
                raise

    if video_id is None:
        raise HTTPException(status_code=500, detail="Failed to generate unique slug")

    # Save uploaded file with size validation (file_ext already validated above)
    # If file save fails, clean up the database record to avoid orphans
    try:
        upload_path = UPLOADS_DIR / f"{video_id}{file_ext}"
        await save_upload_with_size_limit(file, upload_path)

        # Create output directory
        (VIDEOS_DIR / slug).mkdir(parents=True, exist_ok=True)
    except HTTPException:
        # Clean up orphan database record on upload failure
        await delete_video_and_job(video_id)
        raise
    except (OSError, IOError, PermissionError) as e:
        # Storage-related errors - clean up and return 503
        await delete_video_and_job(video_id)
        logger.warning(f"Storage error during video upload (video_id={video_id}): {e}")
        raise HTTPException(
            status_code=503,
            detail="Video storage temporarily unavailable. Please try again later.",
            headers={"Retry-After": "30"},
        )

    # Probe video file to get actual duration and dimensions
    try:
        video_info = await get_video_info(upload_path)
        await database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(
                duration=video_info["duration"],
                source_width=video_info["width"],
                source_height=video_info["height"],
            )
        )
    except Exception as e:
        # Log but don't fail the upload - transcoder will get this info later
        logger.warning(f"Failed to probe video {video_id}: {e}")

    # Create transcoding job for remote workers to claim
    # If this fails, clean up to avoid orphaned video record (issue #162)
    # Uses ON CONFLICT to handle duplicate jobs gracefully (issue #270)
    try:
        await create_or_reset_transcoding_job(video_id)
    except HTTPException:
        # Clean up on HTTP errors
        await delete_video_and_job(video_id)
        upload_path.unlink(missing_ok=True)
        shutil.rmtree(VIDEOS_DIR / slug, ignore_errors=True)
        raise
    except DatabaseLockedError as e:
        # Database locked after all retries - clean up and return 503 (retryable)
        logger.error(f"Database locked creating transcoding job for video {video_id}: {e}")
        await delete_video_and_job(video_id)
        upload_path.unlink(missing_ok=True)
        shutil.rmtree(VIDEOS_DIR / slug, ignore_errors=True)
        raise HTTPException(
            status_code=503,
            detail="Database temporarily busy. Please try again.",
            headers={"Retry-After": "5"},
        )
    except Exception as e:
        # Clean up video record and uploaded file on job creation failure
        logger.exception(f"Failed to create transcoding job for video {video_id}: {e}")
        await delete_video_and_job(video_id)
        upload_path.unlink(missing_ok=True)
        shutil.rmtree(VIDEOS_DIR / slug, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Failed to create transcoding job")

    # Audit log
    log_audit(
        AuditAction.VIDEO_UPLOAD,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=slug,
        details={
            "title": title,
            "category_id": category_id,
            "filename": file.filename,
        },
    )

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
        if len(title.strip()) == 0:
            raise HTTPException(status_code=400, detail="Title is required")
        if len(title) > MAX_TITLE_LENGTH:
            raise HTTPException(status_code=400, detail=f"Title must be {MAX_TITLE_LENGTH} characters or less")
        update_data["title"] = title
    if description is not None:
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise HTTPException(
                status_code=400, detail=f"Description must be {MAX_DESCRIPTION_LENGTH} characters or less"
            )
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

        # Audit log
        log_audit(
            AuditAction.VIDEO_UPDATE,
            client_ip=get_real_ip(request),
            user_agent=request.headers.get("user-agent"),
            resource_type="video",
            resource_id=video_id,
            details={"updated_fields": list(update_data.keys())},
        )

    return {"status": "ok"}


@app.post("/api/videos/{video_id}/publish")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def publish_video(request: Request, video_id: int):
    """Publish a video (make it visible on the public site)."""
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail="Cannot publish a deleted video")

    # Idempotent: skip if already published
    if video["published_at"] is not None:
        return {"status": "ok", "published": True}

    await db_execute_with_retry(
        videos.update().where(videos.c.id == video_id).values(published_at=datetime.now(timezone.utc))
    )

    log_audit(
        AuditAction.VIDEO_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        details={"action": "publish"},
    )

    return {"status": "ok", "published": True}


@app.post("/api/videos/{video_id}/unpublish")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def unpublish_video(request: Request, video_id: int):
    """Unpublish a video (hide it from the public site)."""
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video["deleted_at"] is not None:
        raise HTTPException(status_code=400, detail="Cannot unpublish a deleted video")

    # Idempotent: skip if already unpublished
    if video["published_at"] is None:
        return {"status": "ok", "published": False}

    await db_execute_with_retry(videos.update().where(videos.c.id == video_id).values(published_at=None))

    log_audit(
        AuditAction.VIDEO_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        details={"action": "unpublish"},
    )

    return {"status": "ok", "published": False}


@app.get("/api/videos/{video_id}/tags")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_video_tags(request: Request, video_id: int) -> List[VideoTagInfo]:
    """Get all tags for a video."""
    # Verify video exists
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    query = (
        sa.select(tags.c.id, tags.c.name, tags.c.slug)
        .select_from(video_tags.join(tags, video_tags.c.tag_id == tags.c.id))
        .where(video_tags.c.video_id == video_id)
        .order_by(tags.c.name)
    )
    rows = await fetch_all_with_retry(query)

    return [VideoTagInfo(id=row["id"], name=row["name"], slug=row["slug"]) for row in rows]


@app.put("/api/videos/{video_id}/tags")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def set_video_tags(request: Request, video_id: int, data: VideoTagsUpdate) -> List[VideoTagInfo]:
    """Set tags for a video (replaces all existing tags)."""
    # Verify video exists
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify all tag_ids exist
    if data.tag_ids:
        existing_tags = await fetch_all_with_retry(tags.select().where(tags.c.id.in_(data.tag_ids)))
        existing_ids = {t["id"] for t in existing_tags}
        missing_ids = set(data.tag_ids) - existing_ids
        if missing_ids:
            raise HTTPException(status_code=400, detail=f"Tag IDs not found: {sorted(missing_ids)}")

    # Replace all tags in a transaction
    async with database.transaction():
        # Remove existing tags
        await database.execute(video_tags.delete().where(video_tags.c.video_id == video_id))
        # Add new tags
        if data.tag_ids:
            # Deduplicate tag_ids
            unique_tag_ids = list(dict.fromkeys(data.tag_ids))
            for tag_id in unique_tag_ids:
                await database.execute(video_tags.insert().values(video_id=video_id, tag_id=tag_id))

    # Audit log
    log_audit(
        AuditAction.VIDEO_TAGS_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=video["slug"],
        details={"tag_ids": data.tag_ids},
    )

    # Return updated tags
    query = (
        sa.select(tags.c.id, tags.c.name, tags.c.slug)
        .select_from(video_tags.join(tags, video_tags.c.tag_id == tags.c.id))
        .where(video_tags.c.video_id == video_id)
        .order_by(tags.c.name)
    )
    rows = await fetch_all_with_retry(query)

    return [VideoTagInfo(id=row["id"], name=row["name"], slug=row["slug"]) for row in rows]


@app.delete("/api/videos/{video_id}/tags/{tag_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def remove_video_tag(request: Request, video_id: int, tag_id: int):
    """Remove a single tag from a video."""
    # Verify video exists
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Verify tag exists
    tag = await fetch_one_with_retry(tags.select().where(tags.c.id == tag_id))
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Remove the tag association
    await db_execute_with_retry(
        video_tags.delete().where(
            sa.and_(video_tags.c.video_id == video_id, video_tags.c.tag_id == tag_id)
        )
    )

    # Audit log
    log_audit(
        AuditAction.VIDEO_TAGS_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=video["slug"],
        details={"removed_tag_id": tag_id, "removed_tag_name": tag["name"]},
    )

    return {"status": "ok"}


# ============ Thumbnail Selection Endpoints ============


def _get_video_source_path(video_id: int, slug: str) -> Optional[Path]:
    """
    Find the source video file for thumbnail generation.

    Checks in order:
    1. Original upload in UPLOADS_DIR
    2. Highest quality HLS variant in VIDEOS_DIR

    Returns None if no source is available.
    """
    # First check uploads directory for original file
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        upload_path = UPLOADS_DIR / f"{video_id}{ext}"
        if upload_path.exists():
            return upload_path

    # Fall back to highest quality HLS variant
    video_dir = VIDEOS_DIR / slug
    if not video_dir.exists():
        return None

    # Check for original quality first, then descending quality order
    quality_order = ["original", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
    for quality in quality_order:
        playlist = video_dir / f"{quality}.m3u8"
        if playlist.exists():
            # Verify segments exist before returning playlist (ffmpeg can read HLS directly)
            if any(video_dir.glob(f"{quality}_*.ts")):
                return playlist

    return None


def _cleanup_frames_directory(slug: str) -> None:
    """Remove the temporary frames directory for a video."""
    frames_dir = VIDEOS_DIR / slug / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)


@app.get("/api/videos/{video_id}/thumbnail")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_thumbnail_info(request: Request, video_id: int) -> ThumbnailInfoResponse:
    """Get current thumbnail information for a video."""
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    thumbnail_url = None
    if video["status"] == VideoStatus.READY:
        thumbnail_path = VIDEOS_DIR / video["slug"] / "thumbnail.jpg"
        if thumbnail_path.exists():
            thumbnail_url = f"/videos/{video['slug']}/thumbnail.jpg"

    return ThumbnailInfoResponse(
        video_id=video_id,
        thumbnail_url=thumbnail_url,
        thumbnail_source=video["thumbnail_source"] or "auto",
        thumbnail_timestamp=video["thumbnail_timestamp"],
    )


@app.post("/api/videos/{video_id}/thumbnail/frames")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def generate_thumbnail_frames(request: Request, video_id: int) -> ThumbnailFramesResponse:
    """
    Generate multiple frame options at different timestamps for thumbnail selection.

    Returns URLs to temporary frame images at 10%, 25%, 50%, 75%, 90% of video duration.
    Frames are stored in VIDEOS_DIR/{slug}/frames/ directory.
    """
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    duration = video["duration"]
    if not duration or duration <= 0:
        raise HTTPException(status_code=400, detail="Video has no duration information")

    # Find source file
    source_path = _get_video_source_path(video_id, video["slug"])
    if not source_path:
        raise HTTPException(
            status_code=400,
            detail="No source video available for frame extraction. Original upload may have been deleted.",
        )

    # Create frames directory (clean up any existing frames first to avoid stale sets)
    frames_dir = VIDEOS_DIR / video["slug"] / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Calculate timestamps based on percentages
    timestamps = [duration * pct for pct in THUMBNAIL_FRAME_PERCENTAGES]

    # Generate frames in parallel
    frames = []
    tasks = []
    for i, timestamp in enumerate(timestamps):
        frame_path = frames_dir / f"frame_{i}.jpg"
        tasks.append(generate_thumbnail(source_path, frame_path, timestamp=timestamp, timeout=30.0))
        frames.append(
            ThumbnailFrame(
                index=i,
                timestamp=round(timestamp, 2),
                url=f"/videos/{video['slug']}/frames/frame_{i}.jpg",
            )
        )

    # Run all frame generations concurrently
    try:
        await asyncio.gather(*tasks)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate frames: {str(e)}")

    return ThumbnailFramesResponse(video_id=video_id, frames=frames)


@app.post("/api/videos/{video_id}/thumbnail/upload")
@limiter.limit(RATE_LIMIT_ADMIN_UPLOAD)
async def upload_custom_thumbnail(
    request: Request,
    video_id: int,
    file: UploadFile = File(...),
) -> ThumbnailResponse:
    """
    Upload a custom thumbnail image.

    Accepts: JPEG, PNG, WebP (max 10MB)
    Converts to JPEG at 640px width, preserving aspect ratio.
    """
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Validate file extension
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image format. Allowed: {', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))}",
        )

    # Check file size via Content-Length header
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_THUMBNAIL_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_THUMBNAIL_UPLOAD_SIZE // (1024 * 1024)}MB",
        )

    # Save to temp file
    temp_path = UPLOADS_DIR / f"thumb_temp_{video_id}{ext}"
    try:
        total_size = 0
        with open(temp_path, "wb") as f:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                total_size += len(chunk)
                if total_size > MAX_THUMBNAIL_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum size: {MAX_THUMBNAIL_UPLOAD_SIZE // (1024 * 1024)}MB",
                    )
                f.write(chunk)

        # Convert and resize using ffmpeg
        video_dir = VIDEOS_DIR / video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)
        thumbnail_path = video_dir / "thumbnail.jpg"

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(temp_path),
            "-vf",
            f"scale={THUMBNAIL_WIDTH}:-1",
            "-q:v",
            "2",
            str(thumbnail_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise HTTPException(status_code=500, detail="Image conversion timed out")

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="ignore")[:200]
            raise HTTPException(status_code=400, detail=f"Invalid image file: {error_msg}")

        # Update database
        await db_execute_with_retry(
            videos.update()
            .where(videos.c.id == video_id)
            .values(thumbnail_source="custom", thumbnail_timestamp=None)
        )

        # Clean up frames directory if it exists
        _cleanup_frames_directory(video["slug"])

        # Audit log
        log_audit(
            AuditAction.VIDEO_UPDATE,
            client_ip=get_real_ip(request),
            user_agent=request.headers.get("user-agent"),
            resource_type="video",
            resource_id=video_id,
            resource_name=video["slug"],
            details={"action": "thumbnail_upload", "original_filename": file.filename},
        )

        return ThumbnailResponse(
            status="ok",
            thumbnail_url=f"/videos/{video['slug']}/thumbnail.jpg",
            thumbnail_source="custom",
            thumbnail_timestamp=None,
        )

    finally:
        # Clean up temp file
        if temp_path.exists():
            temp_path.unlink()


@app.post("/api/videos/{video_id}/thumbnail/select")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def select_thumbnail_frame(
    request: Request,
    video_id: int,
    timestamp: float = Form(...),
) -> ThumbnailResponse:
    """
    Select a frame at the specified timestamp as the thumbnail.

    Can use a timestamp from the generated frames or any custom timestamp.
    """
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    duration = video["duration"]
    if not duration or duration <= 0:
        raise HTTPException(status_code=400, detail="Video has no duration information")

    # Validate timestamp
    if timestamp < 0 or timestamp > duration:
        raise HTTPException(
            status_code=400, detail=f"Timestamp must be between 0 and {duration:.2f} seconds"
        )

    # Find source file
    source_path = _get_video_source_path(video_id, video["slug"])
    if not source_path:
        raise HTTPException(
            status_code=400,
            detail="No source video available for thumbnail generation. Original upload may have been deleted.",
        )

    # Generate thumbnail at the specified timestamp
    video_dir = VIDEOS_DIR / video["slug"]
    video_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_path = video_dir / "thumbnail.jpg"

    try:
        await generate_thumbnail(source_path, thumbnail_path, timestamp=timestamp, timeout=30.0)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate thumbnail: {str(e)}")

    # Update database
    await db_execute_with_retry(
        videos.update()
        .where(videos.c.id == video_id)
        .values(thumbnail_source="selected", thumbnail_timestamp=timestamp)
    )

    # Clean up frames directory
    _cleanup_frames_directory(video["slug"])

    # Audit log
    log_audit(
        AuditAction.VIDEO_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=video["slug"],
        details={"action": "thumbnail_select", "timestamp": timestamp},
    )

    return ThumbnailResponse(
        status="ok",
        thumbnail_url=f"/videos/{video['slug']}/thumbnail.jpg",
        thumbnail_source="selected",
        thumbnail_timestamp=timestamp,
    )


@app.post("/api/videos/{video_id}/thumbnail/revert")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def revert_thumbnail(request: Request, video_id: int) -> ThumbnailResponse:
    """
    Revert to the auto-generated thumbnail (default timestamp).

    Regenerates the thumbnail at the default position (5 seconds or 25% of duration).
    """
    video = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    duration = video["duration"]
    if not duration or duration <= 0:
        raise HTTPException(status_code=400, detail="Video has no duration information")

    # Find source file
    source_path = _get_video_source_path(video_id, video["slug"])
    if not source_path:
        raise HTTPException(
            status_code=400,
            detail="No source video available for thumbnail generation. Original upload may have been deleted.",
        )

    # Calculate default timestamp (same as transcoder)
    default_timestamp = min(5.0, duration / 4)

    # Generate thumbnail at the default timestamp
    video_dir = VIDEOS_DIR / video["slug"]
    video_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_path = video_dir / "thumbnail.jpg"

    try:
        await generate_thumbnail(source_path, thumbnail_path, timestamp=default_timestamp, timeout=30.0)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate thumbnail: {str(e)}")

    # Update database
    await db_execute_with_retry(
        videos.update()
        .where(videos.c.id == video_id)
        .values(thumbnail_source="auto", thumbnail_timestamp=None)
    )

    # Clean up frames directory
    _cleanup_frames_directory(video["slug"])

    # Audit log
    log_audit(
        AuditAction.VIDEO_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=video["slug"],
        details={"action": "thumbnail_revert"},
    )

    return ThumbnailResponse(
        status="ok",
        thumbnail_url=f"/videos/{video['slug']}/thumbnail.jpg",
        thumbnail_source="auto",
        thumbnail_timestamp=None,
    )


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
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            upload_file = UPLOADS_DIR / f"{video_id}{ext}"
            if upload_file.exists():
                upload_file.unlink()

        # Audit log
        log_audit(
            AuditAction.VIDEO_DELETE,
            client_ip=get_real_ip(request),
            user_agent=request.headers.get("user-agent"),
            resource_type="video",
            resource_id=video_id,
            resource_name=row["slug"],
            details={"permanent": True, "title": row["title"]},
        )

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
            for ext in SUPPORTED_VIDEO_EXTENSIONS:
                upload_file = UPLOADS_DIR / f"{video_id}{ext}"
                if upload_file.exists():
                    archive_upload = ARCHIVE_DIR / f"uploads/{video_id}{ext}"
                    archive_upload.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(upload_file), str(archive_upload))
                    moved_files.append(("file", archive_upload, upload_file))
        except HTTPException:
            # Rollback: restore files that were moved
            for item_type, src, dst in reversed(moved_files):
                try:
                    shutil.move(str(src), str(dst))
                except Exception:
                    pass  # Best effort rollback
            # Rollback database change
            await database.execute(videos.update().where(videos.c.id == video_id).values(deleted_at=None))
            raise
        except Exception as e:
            # Rollback: restore files that were moved
            for item_type, src, dst in reversed(moved_files):
                try:
                    shutil.move(str(src), str(dst))
                except Exception:
                    pass  # Best effort rollback
            # Rollback database change
            await database.execute(videos.update().where(videos.c.id == video_id).values(deleted_at=None))
            logger.exception(f"Failed to archive files for video {video_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to archive files")

        # Audit log
        log_audit(
            AuditAction.VIDEO_DELETE,
            client_ip=get_real_ip(request),
            user_agent=request.headers.get("user-agent"),
            resource_type="video",
            resource_id=video_id,
            resource_name=row["slug"],
            details={"permanent": False, "title": row["title"]},
        )

        return {"status": "ok", "message": "Video moved to archive"}


# ============ Bulk Operations ============


@app.post("/api/videos/bulk/delete")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def bulk_delete_videos(request: Request, data: BulkDeleteRequest) -> BulkDeleteResponse:
    """
    Delete multiple videos at once.

    Supports both soft-delete (moves to archive) and permanent delete.
    Operations are performed individually to track per-video success/failure.
    """
    bulk_operation_id = str(uuid.uuid4())
    results = []
    deleted_count = 0
    failed_count = 0
    client_ip = get_real_ip(request)
    user_agent = request.headers.get("user-agent")

    for video_id in data.video_ids:
        try:
            # Get video info
            row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
            if not row:
                results.append(BulkOperationResult(video_id=video_id, success=False, error="Video not found"))
                failed_count += 1
                continue

            if data.permanent:
                # PERMANENT DELETE
                async with database.transaction():
                    job = await database.fetch_one(
                        transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id)
                    )
                    if job:
                        await database.execute(quality_progress.delete().where(quality_progress.c.job_id == job["id"]))
                    await database.execute(transcoding_jobs.delete().where(transcoding_jobs.c.video_id == video_id))
                    await database.execute(playback_sessions.delete().where(playback_sessions.c.video_id == video_id))
                    await database.execute(transcriptions.delete().where(transcriptions.c.video_id == video_id))
                    await database.execute(video_qualities.delete().where(video_qualities.c.video_id == video_id))
                    await database.execute(videos.delete().where(videos.c.id == video_id))

                # Delete files AFTER successful transaction
                video_dir = VIDEOS_DIR / row["slug"]
                if video_dir.exists():
                    shutil.rmtree(video_dir)
                archive_dir = ARCHIVE_DIR / row["slug"]
                if archive_dir.exists():
                    shutil.rmtree(archive_dir)
                for ext in SUPPORTED_VIDEO_EXTENSIONS:
                    upload_file = UPLOADS_DIR / f"{video_id}{ext}"
                    if upload_file.exists():
                        upload_file.unlink()
            else:
                # SOFT DELETE
                await database.execute(
                    videos.update().where(videos.c.id == video_id).values(deleted_at=datetime.now(timezone.utc))
                )

                video_dir = VIDEOS_DIR / row["slug"]
                archive_video_dir = ARCHIVE_DIR / row["slug"]
                moved_files = []

                try:
                    if video_dir.exists():
                        archive_video_dir.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(video_dir), str(archive_video_dir))
                        moved_files.append(("dir", archive_video_dir, video_dir))

                    for ext in SUPPORTED_VIDEO_EXTENSIONS:
                        upload_file = UPLOADS_DIR / f"{video_id}{ext}"
                        if upload_file.exists():
                            archive_upload = ARCHIVE_DIR / f"uploads/{video_id}{ext}"
                            archive_upload.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(upload_file), str(archive_upload))
                            moved_files.append(("file", archive_upload, upload_file))
                except Exception as e:
                    # Rollback file moves
                    for item_type, src, dst in reversed(moved_files):
                        try:
                            shutil.move(str(src), str(dst))
                        except Exception:
                            # Ignore errors during rollback to avoid masking the original error
                            pass
                    # Rollback database change
                    await database.execute(videos.update().where(videos.c.id == video_id).values(deleted_at=None))
                    results.append(
                        BulkOperationResult(video_id=video_id, success=False, error=f"Failed to archive: {e}")
                    )
                    failed_count += 1
                    continue

            # Emit individual audit event for successful delete
            log_audit(
                AuditAction.VIDEO_DELETE,
                client_ip=client_ip,
                user_agent=user_agent,
                resource_type="video",
                resource_id=video_id,
                resource_name=row["slug"],
                details={
                    "permanent": data.permanent,
                    "bulk_operation_id": bulk_operation_id,
                },
            )

            results.append(BulkOperationResult(video_id=video_id, success=True))
            deleted_count += 1

        except Exception as e:
            logger.exception(f"Failed to delete video {video_id}: {e}")
            results.append(BulkOperationResult(video_id=video_id, success=False, error=str(e)))
            failed_count += 1

    # Summary audit log for bulk operation
    log_audit(
        AuditAction.VIDEO_BULK_DELETE,
        client_ip=client_ip,
        user_agent=user_agent,
        resource_type="video",
        details={
            "bulk_operation_id": bulk_operation_id,
            "video_ids": data.video_ids,
            "permanent": data.permanent,
            "deleted": deleted_count,
            "failed": failed_count,
        },
    )

    return BulkDeleteResponse(
        status="ok" if failed_count == 0 else "partial",
        deleted=deleted_count,
        failed=failed_count,
        results=results,
    )


@app.post("/api/videos/bulk/update")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def bulk_update_videos(request: Request, data: BulkUpdateRequest) -> BulkUpdateResponse:
    """
    Update multiple videos with the same values.

    Supports updating category, published_at, and unpublishing.
    """
    bulk_operation_id = str(uuid.uuid4())
    results = []
    updated_count = 0
    failed_count = 0
    client_ip = get_real_ip(request)
    user_agent = request.headers.get("user-agent")

    # Validate category exists if provided
    if data.category_id is not None and data.category_id > 0:
        existing_category = await database.fetch_one(categories.select().where(categories.c.id == data.category_id))
        if not existing_category:
            raise HTTPException(status_code=400, detail=f"Category with ID {data.category_id} does not exist")

    # Build update values
    update_values = {}
    if data.category_id is not None:
        update_values["category_id"] = data.category_id if data.category_id > 0 else None
    if data.unpublish:
        update_values["published_at"] = None
    elif data.published_at is not None:
        update_values["published_at"] = data.published_at

    if not update_values:
        raise HTTPException(status_code=400, detail="No update values provided")

    for video_id in data.video_ids:
        try:
            # Verify video exists
            row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
            if not row:
                results.append(BulkOperationResult(video_id=video_id, success=False, error="Video not found"))
                failed_count += 1
                continue

            await database.execute(videos.update().where(videos.c.id == video_id).values(**update_values))

            # Emit individual audit event for successful update
            log_audit(
                AuditAction.VIDEO_UPDATE,
                client_ip=client_ip,
                user_agent=user_agent,
                resource_type="video",
                resource_id=video_id,
                resource_name=row["slug"],
                details={
                    "updates": update_values,
                    "bulk_operation_id": bulk_operation_id,
                },
            )

            results.append(BulkOperationResult(video_id=video_id, success=True))
            updated_count += 1

        except Exception as e:
            logger.exception(f"Failed to update video {video_id}: {e}")
            results.append(BulkOperationResult(video_id=video_id, success=False, error=str(e)))
            failed_count += 1

    # Summary audit log for bulk operation
    log_audit(
        AuditAction.VIDEO_BULK_UPDATE,
        client_ip=client_ip,
        user_agent=user_agent,
        resource_type="video",
        details={
            "bulk_operation_id": bulk_operation_id,
            "video_ids": data.video_ids,
            "updates": update_values,
            "updated": updated_count,
            "failed": failed_count,
        },
    )

    return BulkUpdateResponse(
        status="ok" if failed_count == 0 else "partial",
        updated=updated_count,
        failed=failed_count,
        results=results,
    )


@app.post("/api/videos/bulk/retranscode")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def bulk_retranscode_videos(request: Request, data: BulkRetranscodeRequest) -> BulkRetranscodeResponse:
    """
    Queue multiple videos for re-transcoding.

    Each video will be reset to pending status and queued for transcoding.
    """
    bulk_operation_id = str(uuid.uuid4())
    results = []
    queued_count = 0
    failed_count = 0
    client_ip = get_real_ip(request)
    user_agent = request.headers.get("user-agent")

    for video_id in data.video_ids:
        try:
            # Get video info
            row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
            if not row:
                results.append(BulkOperationResult(video_id=video_id, success=False, error="Video not found"))
                failed_count += 1
                continue

            slug = row["slug"]
            source_height = row["source_height"]

            # Check source file exists
            source_file = None
            for ext in SUPPORTED_VIDEO_EXTENSIONS:
                potential_source = UPLOADS_DIR / f"{video_id}{ext}"
                if potential_source.exists():
                    source_file = potential_source
                    break

            if not source_file:
                results.append(
                    BulkOperationResult(video_id=video_id, success=False, error="Source file not found in uploads")
                )
                failed_count += 1
                continue

            # Determine qualities to retranscode
            retranscode_all = "all" in data.qualities
            if retranscode_all:
                qualities_to_delete = [q["name"] for q in QUALITY_PRESETS if q["height"] <= source_height]
                qualities_to_delete.append("original")
            else:
                qualities_to_delete = [q for q in data.qualities if q != "all"]

            async with database.transaction():
                # Cancel existing transcoding job
                existing_job = await database.fetch_one(
                    transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id)
                )
                if existing_job:
                    await database.execute(
                        quality_progress.delete().where(quality_progress.c.job_id == existing_job["id"])
                    )
                    await database.execute(transcoding_jobs.delete().where(transcoding_jobs.c.video_id == video_id))

                # Delete specified quality records
                for quality in qualities_to_delete:
                    await database.execute(
                        video_qualities.delete().where(
                            (video_qualities.c.video_id == video_id) & (video_qualities.c.quality == quality)
                        )
                    )

                # Reset video status
                await database.execute(
                    videos.update()
                    .where(videos.c.id == video_id)
                    .values(status=VideoStatus.PENDING, error_message=None)
                )

                # Create new transcoding job
                # Uses ON CONFLICT to handle duplicate jobs gracefully (issue #270)
                await create_or_reset_transcoding_job(video_id, priority=data.priority)

                # If retranscoding all, delete transcription record (inside transaction)
                if retranscode_all:
                    await database.execute(transcriptions.delete().where(transcriptions.c.video_id == video_id))

            # Delete quality files from disk (after transaction succeeds)
            video_dir = VIDEOS_DIR / slug
            if video_dir.exists():
                for quality in qualities_to_delete:
                    for pattern in [f"{quality}.m3u8", f"{quality}_*.ts"]:
                        for f in video_dir.glob(pattern):
                            f.unlink()

                # Delete VTT file if retranscoding all
                if retranscode_all:
                    vtt_path = video_dir / "captions.vtt"
                    if vtt_path.exists():
                        vtt_path.unlink()

            # Emit individual audit event for successful retranscode
            log_audit(
                AuditAction.VIDEO_RETRANSCODE,
                client_ip=client_ip,
                user_agent=user_agent,
                resource_type="video",
                resource_id=video_id,
                resource_name=slug,
                details={
                    "qualities": data.qualities,
                    "bulk_operation_id": bulk_operation_id,
                },
            )

            results.append(BulkOperationResult(video_id=video_id, success=True))
            queued_count += 1

        except Exception as e:
            logger.exception(f"Failed to queue retranscode for video {video_id}: {e}")
            results.append(BulkOperationResult(video_id=video_id, success=False, error=str(e)))
            failed_count += 1

    # Summary audit log for bulk operation
    log_audit(
        AuditAction.VIDEO_BULK_RETRANSCODE,
        client_ip=client_ip,
        user_agent=user_agent,
        resource_type="video",
        details={
            "bulk_operation_id": bulk_operation_id,
            "video_ids": data.video_ids,
            "qualities": data.qualities,
            "queued": queued_count,
            "failed": failed_count,
        },
    )

    return BulkRetranscodeResponse(
        status="ok" if failed_count == 0 else "partial",
        queued=queued_count,
        failed=failed_count,
        results=results,
    )


@app.post("/api/videos/bulk/restore")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def bulk_restore_videos(request: Request, data: BulkRestoreRequest) -> BulkRestoreResponse:
    """
    Restore multiple soft-deleted videos from archive.
    """
    bulk_operation_id = str(uuid.uuid4())
    results = []
    restored_count = 0
    failed_count = 0
    client_ip = get_real_ip(request)
    user_agent = request.headers.get("user-agent")

    for video_id in data.video_ids:
        try:
            row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
            if not row:
                results.append(BulkOperationResult(video_id=video_id, success=False, error="Video not found"))
                failed_count += 1
                continue

            if not row["deleted_at"]:
                results.append(BulkOperationResult(video_id=video_id, success=False, error="Video is not deleted"))
                failed_count += 1
                continue

            original_deleted_at = row["deleted_at"]

            # Update database first
            await database.execute(videos.update().where(videos.c.id == video_id).values(deleted_at=None))

            archive_video_dir = ARCHIVE_DIR / row["slug"]
            video_dir = VIDEOS_DIR / row["slug"]
            moved_files = []

            try:
                if archive_video_dir.exists():
                    shutil.move(str(archive_video_dir), str(video_dir))
                    moved_files.append(("dir", video_dir, archive_video_dir))

                for ext in SUPPORTED_VIDEO_EXTENSIONS:
                    archive_upload = ARCHIVE_DIR / f"uploads/{video_id}{ext}"
                    if archive_upload.exists():
                        upload_file = UPLOADS_DIR / f"{video_id}{ext}"
                        shutil.move(str(archive_upload), str(upload_file))
                        moved_files.append(("file", upload_file, archive_upload))
            except Exception as e:
                # Rollback file moves
                for item_type, src, dst in reversed(moved_files):
                    try:
                        shutil.move(str(src), str(dst))
                    except Exception:
                        # Ignore errors during rollback to avoid masking the original error
                        pass
                # Rollback database change
                await database.execute(
                    videos.update().where(videos.c.id == video_id).values(deleted_at=original_deleted_at)
                )
                results.append(BulkOperationResult(video_id=video_id, success=False, error=f"Failed to restore: {e}"))
                failed_count += 1
                continue

            # Emit individual audit event for successful restore
            log_audit(
                AuditAction.VIDEO_RESTORE,
                client_ip=client_ip,
                user_agent=user_agent,
                resource_type="video",
                resource_id=video_id,
                resource_name=row["slug"],
                details={
                    "bulk_operation_id": bulk_operation_id,
                },
            )

            results.append(BulkOperationResult(video_id=video_id, success=True))
            restored_count += 1

        except Exception as e:
            logger.exception(f"Failed to restore video {video_id}: {e}")
            results.append(BulkOperationResult(video_id=video_id, success=False, error=str(e)))
            failed_count += 1

    # Summary audit log for bulk operation
    log_audit(
        AuditAction.VIDEO_BULK_RESTORE,
        client_ip=client_ip,
        user_agent=user_agent,
        resource_type="video",
        details={
            "bulk_operation_id": bulk_operation_id,
            "video_ids": data.video_ids,
            "restored": restored_count,
            "failed": failed_count,
        },
    )

    return BulkRestoreResponse(
        status="ok" if failed_count == 0 else "partial",
        restored=restored_count,
        failed=failed_count,
        results=results,
    )


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
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            archive_upload = ARCHIVE_DIR / f"uploads/{video_id}{ext}"
            if archive_upload.exists():
                upload_file = UPLOADS_DIR / f"{video_id}{ext}"
                shutil.move(str(archive_upload), str(upload_file))
                moved_files.append(("file", upload_file, archive_upload))
    except HTTPException:
        # Rollback: restore files that were moved
        for item_type, src, dst in reversed(moved_files):
            try:
                shutil.move(str(src), str(dst))
            except Exception:
                pass  # Best effort rollback
        # Rollback database change
        await database.execute(videos.update().where(videos.c.id == video_id).values(deleted_at=original_deleted_at))
        raise
    except Exception as e:
        # Rollback: restore files that were moved
        for item_type, src, dst in reversed(moved_files):
            try:
                shutil.move(str(src), str(dst))
            except Exception:
                pass  # Best effort rollback
        # Rollback database change
        await database.execute(videos.update().where(videos.c.id == video_id).values(deleted_at=original_deleted_at))
        logger.exception(f"Failed to restore files for video {video_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to restore files")

    # Audit log
    log_audit(
        AuditAction.VIDEO_RESTORE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=row["slug"],
        details={"title": row["title"]},
    )

    return {"status": "ok", "message": "Video restored from archive"}


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
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
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

    # Audit log
    log_audit(
        AuditAction.VIDEO_RETRY,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=row["slug"],
        details={"title": row["title"]},
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
    # Early rejection based on Content-Length header (if provided)
    validate_content_length(request)

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
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
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

        # Create new transcoding job for remote workers to claim
        # Uses ON CONFLICT to handle duplicate jobs gracefully (issue #270)
        await create_or_reset_transcoding_job(video_id)

    # === UPLOAD NEW FILE === (file_ext already validated above)
    # Done after transaction so DB state is consistent even if upload fails
    upload_path = UPLOADS_DIR / f"{video_id}{file_ext}"
    await save_upload_with_size_limit(file, upload_path)

    # Probe video file to get actual duration and dimensions
    try:
        video_info = await get_video_info(upload_path)
        await database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(
                duration=video_info["duration"],
                source_width=video_info["width"],
                source_height=video_info["height"],
            )
        )
    except Exception as e:
        # Log but don't fail the upload - transcoder will get this info later
        logger.warning(f"Failed to probe re-uploaded video {video_id}: {e}")

    # Audit log
    log_audit(
        AuditAction.VIDEO_REUPLOAD,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=slug,
        details={"filename": file.filename},
    )

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


@app.get("/api/videos/{video_id}/qualities")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_video_qualities(request: Request, video_id: int) -> VideoQualitiesResponse:
    """Get available and existing qualities for a video."""
    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Get existing transcoded qualities
    quality_rows = await database.fetch_all(video_qualities.select().where(video_qualities.c.video_id == video_id))

    existing = [
        VideoQualityInfo(
            name=q["quality"],
            width=q["width"],
            height=q["height"],
            bitrate=q["bitrate"],
            status="completed",
        )
        for q in quality_rows
    ]

    # Determine available qualities based on source resolution
    source_height = video["source_height"] or 0
    available = ["original"]  # Always available
    for preset in QUALITY_PRESETS:
        if preset["height"] <= source_height:
            available.append(preset["name"])

    return VideoQualitiesResponse(
        video_id=video_id,
        source_width=video["source_width"] or 0,
        source_height=source_height,
        available_qualities=available,
        existing_qualities=existing,
    )


@app.post("/api/videos/{video_id}/retranscode")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def retranscode_video(
    request: Request,
    video_id: int,
    data: RetranscodeRequest,
) -> RetranscodeResponse:
    """
    Re-transcode a video, either all qualities or specific ones.

    This will:
    - Cancel any in-progress transcoding job
    - Delete specified quality files (HLS segments and playlists)
    - Delete corresponding video_qualities records
    - Reset video status to pending for reprocessing
    - Preserve: source file, metadata, thumbnail (unless re-transcoding all)
    """
    # Get video info
    row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    if row["deleted_at"]:
        raise HTTPException(status_code=400, detail="Cannot re-transcode a deleted video")

    slug = row["slug"]
    video_dir = VIDEOS_DIR / slug

    # Check if source file exists
    source_file = None
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        candidate = UPLOADS_DIR / f"{video_id}{ext}"
        if candidate.exists():
            source_file = candidate
            break

    if not source_file:
        raise HTTPException(
            status_code=400,
            detail="Source file not found. Cannot re-transcode without original video file.",
        )

    # Determine which qualities to re-transcode
    requested_qualities = data.qualities
    retranscode_all = "all" in requested_qualities

    if retranscode_all:
        # Get all quality names that could be transcoded based on source
        source_height = row["source_height"] or 0
        qualities_to_delete = ["original"]
        for preset in QUALITY_PRESETS:
            if preset["height"] <= source_height:
                qualities_to_delete.append(preset["name"])
    else:
        qualities_to_delete = requested_qualities

    # === FILE CLEANUP ===
    if video_dir.exists():
        for quality in qualities_to_delete:
            # Delete quality playlist
            playlist = video_dir / f"{quality}.m3u8"
            if playlist.exists():
                playlist.unlink()

            # Delete quality segments (pattern: {quality}_XXXX.ts)
            for segment in video_dir.glob(f"{quality}_*.ts"):
                segment.unlink()

        # If re-transcoding all, also delete master playlist and thumbnail
        if retranscode_all:
            master = video_dir / "master.m3u8"
            if master.exists():
                master.unlink()
            thumb = video_dir / "thumbnail.jpg"
            if thumb.exists():
                thumb.unlink()

    # === DATABASE CLEANUP ===
    async with database.transaction():
        # Cancel any existing transcoding job
        job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
        if job:
            # Delete quality_progress records
            if retranscode_all:
                await database.execute(quality_progress.delete().where(quality_progress.c.job_id == job["id"]))
            else:
                # Only delete progress for specified qualities
                await database.execute(
                    quality_progress.delete().where(
                        (quality_progress.c.job_id == job["id"]) & (quality_progress.c.quality.in_(qualities_to_delete))
                    )
                )
            # Delete the job itself
            await database.execute(transcoding_jobs.delete().where(transcoding_jobs.c.video_id == video_id))

        # Delete video_qualities records for specified qualities
        if retranscode_all:
            await database.execute(video_qualities.delete().where(video_qualities.c.video_id == video_id))
        else:
            await database.execute(
                video_qualities.delete().where(
                    (video_qualities.c.video_id == video_id) & (video_qualities.c.quality.in_(qualities_to_delete))
                )
            )

        # If re-transcoding all, also delete transcription
        if retranscode_all:
            await database.execute(transcriptions.delete().where(transcriptions.c.video_id == video_id))
            # Delete VTT file if exists
            vtt_path = video_dir / "captions.vtt"
            if vtt_path.exists():
                vtt_path.unlink()

        # Reset video status to pending
        await database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(
                status=VideoStatus.PENDING,
                error_message=None,
            )
        )

        # Create new transcoding job for remote workers to claim
        # Uses ON CONFLICT to handle duplicate jobs gracefully (issue #270)
        await create_or_reset_transcoding_job(video_id, priority=data.priority)

    # Audit log
    log_audit(
        AuditAction.VIDEO_RETRANSCODE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=slug,
        details={"qualities": qualities_to_delete, "retranscode_all": retranscode_all, "priority": data.priority},
    )

    return RetranscodeResponse(
        status="ok",
        video_id=video_id,
        message="Video queued for re-transcoding",
        qualities_queued=qualities_to_delete,
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

        # Audit log
        log_audit(
            AuditAction.TRANSCRIPTION_TRIGGER,
            client_ip=get_real_ip(request),
            user_agent=request.headers.get("user-agent"),
            resource_type="video",
            resource_id=video_id,
            resource_name=video["slug"],
            details={"retry": True, "language": data.language if data else None},
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

    # Audit log
    log_audit(
        AuditAction.TRANSCRIPTION_TRIGGER,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=video["slug"],
        details={"retry": False, "language": data.language if data else None},
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

    # Audit log
    log_audit(
        AuditAction.TRANSCRIPTION_UPDATE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=video["slug"],
        details={"word_count": word_count},
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

    # Audit log
    log_audit(
        AuditAction.TRANSCRIPTION_DELETE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        resource_id=video_id,
        resource_name=video["slug"],
    )

    return {"status": "ok", "message": "Transcription deleted"}


# ============ Analytics ============


@app.get("/api/analytics/overview")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def analytics_overview(request: Request, response: Response) -> AnalyticsOverview:
    """Get global analytics overview."""
    # Try to get from cache first
    cache_key = "analytics_overview"
    cached_data = analytics_cache.get(cache_key)

    if cached_data is not None:
        # Set Cache-Control header for client-side caching
        response.headers["Cache-Control"] = f"private, max-age={ANALYTICS_CLIENT_CACHE_MAX_AGE}"
        return AnalyticsOverview(**cached_data)

    # Cache miss - compute fresh data
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)

    # Total views
    total_views = await fetch_val_with_retry(sa.select(sa.func.count()).select_from(playback_sessions)) or 0

    # Unique viewers
    unique_viewers = (
        await fetch_val_with_retry(
            sa.select(sa.func.count(sa.distinct(playback_sessions.c.viewer_id)))
            .select_from(playback_sessions)
            .where(playback_sessions.c.viewer_id.isnot(None))
        )
        or 0
    )

    # Total watch time
    total_watch_seconds = (
        await fetch_val_with_retry(
            sa.select(sa.func.sum(playback_sessions.c.duration_watched)).select_from(playback_sessions)
        )
        or 0
    )
    total_watch_time_hours = total_watch_seconds / 3600

    # Completion rate
    completed_count = (
        await fetch_val_with_retry(
            sa.select(sa.func.count()).select_from(playback_sessions).where(playback_sessions.c.completed.is_(True))
        )
        or 0
    )
    completion_rate = completed_count / total_views if total_views > 0 else 0

    # Average watch duration
    avg_watch = (
        await fetch_val_with_retry(
            sa.select(sa.func.avg(playback_sessions.c.duration_watched)).select_from(playback_sessions)
        )
        or 0
    )

    # Views today
    views_today = (
        await fetch_val_with_retry(
            sa.select(sa.func.count())
            .select_from(playback_sessions)
            .where(playback_sessions.c.started_at >= today_start)
        )
        or 0
    )

    # Views this week
    views_week = (
        await fetch_val_with_retry(
            sa.select(sa.func.count())
            .select_from(playback_sessions)
            .where(playback_sessions.c.started_at >= week_start)
        )
        or 0
    )

    # Views this month
    views_month = (
        await fetch_val_with_retry(
            sa.select(sa.func.count())
            .select_from(playback_sessions)
            .where(playback_sessions.c.started_at >= month_start)
        )
        or 0
    )

    result_data = {
        "total_views": total_views,
        "unique_viewers": unique_viewers,
        "total_watch_time_hours": round(total_watch_time_hours, 1),
        "completion_rate": round(completion_rate, 2),
        "avg_watch_duration_seconds": round(avg_watch, 1),
        "views_today": views_today,
        "views_this_week": views_week,
        "views_this_month": views_month,
    }

    # Store in cache
    analytics_cache.set(cache_key, result_data)

    # Set Cache-Control header for client-side caching
    response.headers["Cache-Control"] = f"private, max-age={ANALYTICS_CLIENT_CACHE_MAX_AGE}"

    return AnalyticsOverview(**result_data)


@app.get("/api/analytics/videos")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def analytics_videos(
    request: Request,
    response: Response,
    limit: int = Query(default=50, ge=1, le=100, description="Max items per page"),
    offset: int = Query(default=0, ge=0, description="Number of items to skip"),
    sort_by: str = "views",
    period: str = "all",
) -> VideoAnalyticsListResponse:
    """Get per-video analytics."""
    # Try to get from cache first
    cache_key = f"analytics_videos:{limit}:{offset}:{sort_by}:{period}"
    cached_data = analytics_cache.get(cache_key)

    if cached_data is not None:
        # Set Cache-Control header for client-side caching
        response.headers["Cache-Control"] = f"private, max-age={ANALYTICS_CLIENT_CACHE_MAX_AGE}"
        # Reconstruct response models from cached data
        cached_videos = [VideoAnalyticsSummary(**v) for v in cached_data["videos"]]
        return VideoAnalyticsListResponse(videos=cached_videos, total_count=cached_data["total_count"])

    # Cache miss - compute fresh data
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

    rows = await database.fetch_all(sa.text(base_query).bindparams(**params))

    # Get total count
    count_result = await fetch_val_with_retry(
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

    result_data = {
        "videos": [v.model_dump() for v in video_stats],
        "total_count": count_result or 0,
    }

    # Store in cache
    analytics_cache.set(cache_key, result_data)

    # Set Cache-Control header for client-side caching
    response.headers["Cache-Control"] = f"private, max-age={ANALYTICS_CLIENT_CACHE_MAX_AGE}"

    return VideoAnalyticsListResponse(**result_data)


@app.get("/api/analytics/videos/{video_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def analytics_video_detail(request: Request, response: Response, video_id: int) -> VideoAnalyticsDetail:
    """Get detailed analytics for a specific video."""
    # Try to get from cache first
    cache_key = f"analytics_video_detail:{video_id}"
    cached_data = analytics_cache.get(cache_key)

    if cached_data is not None:
        # Set Cache-Control header for client-side caching
        response.headers["Cache-Control"] = f"private, max-age={ANALYTICS_CLIENT_CACHE_MAX_AGE}"
        # Reconstruct response models from cached data
        quality_breakdown = [QualityBreakdown(**q) for q in cached_data["quality_breakdown"]]
        views_over_time = [DailyViews(**v) for v in cached_data["views_over_time"]]
        return VideoAnalyticsDetail(
            video_id=cached_data["video_id"],
            title=cached_data["title"],
            duration=cached_data["duration"],
            total_views=cached_data["total_views"],
            unique_viewers=cached_data["unique_viewers"],
            total_watch_time_seconds=cached_data["total_watch_time_seconds"],
            avg_watch_duration_seconds=cached_data["avg_watch_duration_seconds"],
            completion_rate=cached_data["completion_rate"],
            avg_percent_watched=cached_data["avg_percent_watched"],
            quality_breakdown=quality_breakdown,
            views_over_time=views_over_time,
        )

    # Cache miss - compute fresh data
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
    stats = await database.fetch_one(
        sa.text(stats_query).bindparams(video_id=video_id, duration=video["duration"] or 1)
    )

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
    quality_rows = await database.fetch_all(sa.text(quality_query).bindparams(video_id=video_id))

    quality_breakdown = (
        [QualityBreakdown(quality=q["quality"], percentage=round(q["percentage"], 2)) for q in quality_rows]
        if quality_rows
        else []
    )

    # Views over time (last 30 days)
    views_query = """
        SELECT
            CAST(started_at AS DATE) as date,
            COUNT(*) as views
        FROM playback_sessions
        WHERE video_id = :video_id
            AND started_at >= CURRENT_DATE - INTERVAL '30 days'
        GROUP BY CAST(started_at AS DATE)
        ORDER BY date
    """
    views_rows = await database.fetch_all(sa.text(views_query).bindparams(video_id=video_id))

    views_over_time = [DailyViews(date=str(v["date"]), views=v["views"]) for v in views_rows] if views_rows else []

    result_data = {
        "video_id": video_id,
        "title": video["title"],
        "duration": video["duration"] or 0,
        "total_views": stats["total_views"] or 0,
        "unique_viewers": stats["unique_viewers"] or 0,
        "total_watch_time_seconds": stats["total_watch_time_seconds"] or 0,
        "avg_watch_duration_seconds": round(stats["avg_watch_duration_seconds"] or 0, 1),
        "completion_rate": round(stats["completion_rate"] or 0, 2),
        "avg_percent_watched": round(stats["avg_percent_watched"] or 0, 2),
        "quality_breakdown": [q.model_dump() for q in quality_breakdown],
        "views_over_time": [v.model_dump() for v in views_over_time],
    }

    # Store in cache
    analytics_cache.set(cache_key, result_data)

    # Set Cache-Control header for client-side caching
    response.headers["Cache-Control"] = f"private, max-age={ANALYTICS_CLIENT_CACHE_MAX_AGE}"

    return VideoAnalyticsDetail(**result_data)


@app.get("/api/analytics/trends")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def analytics_trends(
    request: Request,
    response: Response,
    period: str = "30d",
    video_id: Optional[int] = None,
) -> TrendsResponse:
    """Get time-series analytics data."""
    # Try to get from cache first
    cache_key = f"analytics_trends:{period}:{video_id or 'all'}"
    cached_data = analytics_cache.get(cache_key)

    if cached_data is not None:
        # Set Cache-Control header for client-side caching
        response.headers["Cache-Control"] = f"private, max-age={ANALYTICS_CLIENT_CACHE_MAX_AGE}"
        # Reconstruct response models from cached data
        data = [TrendDataPoint(**d) for d in cached_data["data"]]
        return TrendsResponse(period=cached_data["period"], data=data)

    # Cache miss - compute fresh data
    # Validate period to prevent SQL injection (whitelist approach)
    valid_periods = {"7d": 7, "30d": 30, "90d": 90}
    days = valid_periods.get(period, 30)

    # Build query with parameterized values
    video_clause = "AND video_id = :video_id" if video_id else ""

    base_query = f"""
        SELECT
            CAST(started_at AS DATE) as date,
            COUNT(*) as views,
            COUNT(DISTINCT viewer_id) as unique_viewers,
            COALESCE(SUM(duration_watched), 0) / 3600.0 as watch_time_hours
        FROM playback_sessions
        WHERE started_at >= CURRENT_DATE - :days_offset * INTERVAL '1 day'
        {video_clause}
        GROUP BY CAST(started_at AS DATE)
        ORDER BY date
    """

    params = {"days_offset": days}
    if video_id:
        params["video_id"] = video_id

    rows = await database.fetch_all(sa.text(base_query).bindparams(**params))

    data = [
        TrendDataPoint(
            date=str(r["date"]),
            views=r["views"],
            unique_viewers=r["unique_viewers"] or 0,
            watch_time_hours=round(r["watch_time_hours"], 2),
        )
        for r in rows
    ]

    result_data = {
        "period": period,
        "data": [d.model_dump() for d in data],
    }

    # Store in cache
    analytics_cache.set(cache_key, result_data)

    # Set Cache-Control header for client-side caching
    response.headers["Cache-Control"] = f"private, max-age={ANALYTICS_CLIENT_CACHE_MAX_AGE}"

    return TrendsResponse(**result_data)


@app.get("/api/videos/export")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def export_videos(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status (pending, processing, ready, failed)"),
    category_id: Optional[int] = Query(None, description="Filter by category ID"),
    include_deleted: bool = Query(False, description="Include soft-deleted videos"),
    limit: int = Query(10000, ge=1, le=10000, description="Maximum number of videos to export"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> VideoExportResponse:
    """
    Export video metadata as JSON.

    Supports filtering by status, category, and deleted state.
    For CSV export, use the JSON response and convert client-side.
    """
    # Validate status if provided
    valid_statuses = [s.value for s in VideoStatus]
    if status and status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Valid options: {', '.join(valid_statuses)}",
        )

    # Build query
    query = (
        sa.select(
            videos.c.id,
            videos.c.title,
            videos.c.slug,
            videos.c.description,
            videos.c.category_id,
            categories.c.name.label("category_name"),
            videos.c.duration,
            videos.c.source_width,
            videos.c.source_height,
            videos.c.status,
            videos.c.created_at,
            videos.c.published_at,
        )
        .select_from(videos.outerjoin(categories, videos.c.category_id == categories.c.id))
        .order_by(videos.c.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    # Apply filters
    conditions = []
    if not include_deleted:
        conditions.append(videos.c.deleted_at.is_(None))
    if status:
        conditions.append(videos.c.status == status)
    if category_id is not None:
        conditions.append(videos.c.category_id == category_id)

    if conditions:
        query = query.where(sa.and_(*conditions))

    rows = await database.fetch_all(query)

    # Get total count for the filter
    count_query = sa.select(sa.func.count()).select_from(videos)
    if conditions:
        count_query = count_query.where(sa.and_(*conditions))
    total_count = await fetch_val_with_retry(count_query)

    export_items = [
        VideoExportItem(
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
            created_at=row["created_at"],
            published_at=row["published_at"],
        )
        for row in rows
    ]

    # Audit log
    log_audit(
        AuditAction.VIDEO_EXPORT,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="video",
        details={
            "filters": {
                "status": status,
                "category_id": category_id,
                "include_deleted": include_deleted,
            },
            "exported_count": len(export_items),
            "total_count": total_count,
        },
    )

    return VideoExportResponse(
        videos=export_items,
        total_count=total_count,
        exported_at=datetime.now(timezone.utc),
    )


# ============ Worker Management ============


def parse_worker_capabilities(capabilities_json: Optional[str]) -> dict:
    """Parse worker capabilities JSON and return a dict with hardware info."""
    if not capabilities_json:
        return {"hwaccel_enabled": False, "hwaccel_type": None, "gpu_name": None}

    try:
        caps = json.loads(capabilities_json)
        # Handle nested structure {"capabilities": {...}} or flat structure
        if "capabilities" in caps and isinstance(caps["capabilities"], dict):
            caps = caps["capabilities"]
        return {
            "hwaccel_enabled": caps.get("hwaccel_enabled", False),
            "hwaccel_type": caps.get("hwaccel_type"),
            "gpu_name": caps.get("gpu_name"),
        }
    except (json.JSONDecodeError, TypeError):
        return {"hwaccel_enabled": False, "hwaccel_type": None, "gpu_name": None}


def determine_worker_status(
    db_status: str, last_heartbeat: Optional[datetime], current_job_id: Optional[int], offline_threshold: datetime
) -> str:
    """Determine the effective worker status based on heartbeat and current job."""
    if db_status == "disabled":
        return "disabled"

    if not last_heartbeat:
        return "offline"

    # Ensure timezone-aware comparison
    hb = last_heartbeat
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)

    if hb < offline_threshold:
        return "offline"

    # Active = has a job, Idle = no job but online
    if current_job_id:
        return "active"
    return "idle"


@app.get("/api/workers")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_workers_dashboard(request: Request) -> WorkerDashboardResponse:
    """
    List all workers with their status and current activity.

    Shows real-time worker status with heartbeat information,
    current job assignments, and hardware capabilities.
    """
    now = datetime.now(timezone.utc)
    offline_threshold = now - timedelta(minutes=WORKER_OFFLINE_THRESHOLD_MINUTES)

    # Get all workers
    worker_rows = await fetch_all_with_retry(workers.select().order_by(workers.c.last_heartbeat.desc()))

    # Batch fetch current job info for all workers with active jobs
    current_job_ids = [row["current_job_id"] for row in worker_rows if row["current_job_id"]]
    current_jobs_info = {}
    if current_job_ids:
        job_rows = await database.fetch_all(
            sa.select(
                transcoding_jobs.c.id,
                transcoding_jobs.c.current_step,
                transcoding_jobs.c.progress_percent,
                videos.c.slug,
                videos.c.title,
            )
            .select_from(transcoding_jobs.join(videos))
            .where(transcoding_jobs.c.id.in_(current_job_ids))
        )
        for job in job_rows:
            current_jobs_info[job["id"]] = job

    # Batch fetch job stats (completed, failed, last_completed) for all workers
    job_stats_query = sa.text("""
        SELECT
            tj.worker_id,
            COUNT(CASE WHEN tj.completed_at IS NOT NULL AND v.status = 'ready' THEN 1 END) as jobs_completed,
            COUNT(CASE WHEN tj.completed_at IS NOT NULL AND v.status = 'failed' THEN 1 END) as jobs_failed,
            MAX(CASE WHEN tj.completed_at IS NOT NULL THEN tj.completed_at END) as last_completed
        FROM transcoding_jobs tj
        JOIN videos v ON tj.video_id = v.id
        GROUP BY tj.worker_id
    """)
    job_stats_rows = await fetch_all_with_retry(job_stats_query)
    job_stats_map = {row["worker_id"]: dict(row) for row in job_stats_rows}

    worker_list = []
    active_count = 0
    idle_count = 0
    offline_count = 0
    disabled_count = 0

    for row in worker_rows:
        caps = parse_worker_capabilities(row["metadata"])
        status = determine_worker_status(row["status"], row["last_heartbeat"], row["current_job_id"], offline_threshold)

        # Count by status
        if status == "active":
            active_count += 1
        elif status == "idle":
            idle_count += 1
        elif status == "disabled":
            disabled_count += 1
        else:
            offline_count += 1

        # Calculate seconds since heartbeat
        seconds_since_hb = None
        if row["last_heartbeat"]:
            hb = row["last_heartbeat"]
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            seconds_since_hb = int((now - hb).total_seconds())

        # Get current job info from batch query
        current_video_slug = None
        current_video_title = None
        current_step = None
        current_progress = None
        if row["current_job_id"]:
            job_info = current_jobs_info.get(row["current_job_id"])
            if job_info:
                current_video_slug = job_info["slug"]
                current_video_title = job_info["title"]
                current_step = job_info["current_step"]
                current_progress = job_info["progress_percent"]

        # Get job stats from batch query
        stats = job_stats_map.get(row["worker_id"], {})
        jobs_completed = stats.get("jobs_completed", 0) or 0
        jobs_failed = stats.get("jobs_failed", 0) or 0
        last_completed = stats.get("last_completed")

        worker_list.append(
            WorkerDashboardStatus(
                id=row["id"],
                worker_id=row["worker_id"],
                worker_name=row["worker_name"],
                worker_type=row["worker_type"],
                status=status,
                registered_at=row["registered_at"],
                last_heartbeat=row["last_heartbeat"],
                seconds_since_heartbeat=seconds_since_hb,
                current_job_id=row["current_job_id"],
                current_video_slug=current_video_slug,
                current_video_title=current_video_title,
                current_step=current_step,
                current_progress=current_progress,
                hwaccel_enabled=caps["hwaccel_enabled"],
                hwaccel_type=caps["hwaccel_type"],
                gpu_name=caps["gpu_name"],
                jobs_completed=jobs_completed,
                jobs_failed=jobs_failed,
                last_job_completed_at=last_completed,
            )
        )

    return WorkerDashboardResponse(
        workers=worker_list,
        total_count=len(worker_list),
        active_count=active_count,
        idle_count=idle_count,
        offline_count=offline_count,
        disabled_count=disabled_count,
    )


@app.get("/api/workers/active-jobs")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_active_jobs(request: Request) -> ActiveJobsResponse:
    """
    List all active transcoding jobs with worker information.

    Shows jobs that are pending or being processed, including
    which worker is handling each job and progress details.
    """
    # Get all jobs for videos that are pending or processing
    query = sa.text("""
        SELECT
            tj.id as job_id,
            tj.video_id,
            v.slug as video_slug,
            v.title as video_title,
            v.status as video_status,
            tj.worker_id,
            tj.current_step,
            tj.progress_percent,
            tj.started_at,
            tj.claimed_at,
            tj.attempt_number,
            tj.max_attempts,
            w.worker_name,
            w.metadata as capabilities
        FROM transcoding_jobs tj
        JOIN videos v ON tj.video_id = v.id
        LEFT JOIN workers w ON tj.worker_id = w.worker_id
        WHERE v.status IN ('pending', 'processing')
          AND v.deleted_at IS NULL
        ORDER BY tj.claimed_at DESC NULLS LAST, v.created_at ASC
    """)

    rows = await fetch_all_with_retry(query)

    # Batch fetch quality progress for all jobs
    job_ids = [row["job_id"] for row in rows]
    quality_by_job = {}
    if job_ids:
        all_quality_rows = await database.fetch_all(
            quality_progress.select().where(quality_progress.c.job_id.in_(job_ids))
        )
        for q in all_quality_rows:
            quality_by_job.setdefault(q["job_id"], []).append(q)

    jobs = []
    processing_count = 0
    pending_count = 0

    for row in rows:
        caps = parse_worker_capabilities(row["capabilities"])

        # Count by status
        if row["video_status"] == "processing":
            processing_count += 1
        else:
            pending_count += 1

        # Get quality progress from batch query
        qualities = [
            QualityProgressResponse(
                name=q["quality"],
                status=q["status"],
                progress=q["progress_percent"] or 0,
            )
            for q in quality_by_job.get(row["job_id"], [])
        ]

        jobs.append(
            ActiveJobWithWorker(
                job_id=row["job_id"],
                video_id=row["video_id"],
                video_slug=row["video_slug"],
                video_title=row["video_title"],
                thumbnail_url=f"/videos/{row['video_slug']}/thumbnail.jpg" if row["video_status"] == "ready" else None,
                worker_id=row["worker_id"],
                worker_name=row["worker_name"],
                worker_hwaccel_type=caps["hwaccel_type"],
                status=row["video_status"],
                current_step=row["current_step"],
                progress_percent=row["progress_percent"] or 0,
                qualities=qualities,
                started_at=row["started_at"],
                claimed_at=row["claimed_at"],
                attempt=row["attempt_number"] or 1,
                max_attempts=row["max_attempts"] or 3,
            )
        )

    return ActiveJobsResponse(
        jobs=jobs,
        total_count=len(jobs),
        processing_count=processing_count,
        pending_count=pending_count,
    )


@app.get("/api/workers/{worker_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_worker_detail(request: Request, worker_id: str) -> WorkerDetailResponse:
    """
    Get detailed information about a specific worker.

    Includes capabilities, metadata, stats, and recent job history.
    """
    # Find worker by UUID
    worker = await fetch_one_with_retry(workers.select().where(workers.c.worker_id == worker_id))
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    now = datetime.now(timezone.utc)
    offline_threshold = now - timedelta(minutes=WORKER_OFFLINE_THRESHOLD_MINUTES)
    status = determine_worker_status(
        worker["status"], worker["last_heartbeat"], worker["current_job_id"], offline_threshold
    )

    # Parse capabilities and metadata
    capabilities = None
    if worker["capabilities"]:
        try:
            capabilities = json.loads(worker["capabilities"])
        except (json.JSONDecodeError, TypeError):
            # If capabilities is not valid JSON or malformed, leave as None
            pass

    metadata = None
    if worker["metadata"]:
        try:
            metadata = json.loads(worker["metadata"])
        except (json.JSONDecodeError, TypeError):
            # If metadata is not valid JSON or malformed, leave as None
            pass

    # Get job stats
    jobs_completed = (
        await fetch_val_with_retry(
            sa.select(sa.func.count())
            .select_from(transcoding_jobs.join(videos))
            .where(transcoding_jobs.c.worker_id == worker_id)
            .where(transcoding_jobs.c.completed_at.isnot(None))
            .where(videos.c.status == "ready")
        )
        or 0
    )

    jobs_failed = (
        await fetch_val_with_retry(
            sa.select(sa.func.count())
            .select_from(transcoding_jobs.join(videos))
            .where(transcoding_jobs.c.worker_id == worker_id)
            .where(transcoding_jobs.c.completed_at.isnot(None))
            .where(videos.c.status == "failed")
        )
        or 0
    )

    # Get average job duration for completed jobs (in seconds)
    avg_duration = await fetch_val_with_retry(
        sa.text("""
            SELECT AVG(EXTRACT(EPOCH FROM (tj.completed_at - tj.started_at)))
            FROM transcoding_jobs tj
            JOIN videos v ON tj.video_id = v.id
            WHERE tj.worker_id = :worker_id
              AND tj.completed_at IS NOT NULL
              AND tj.started_at IS NOT NULL
              AND v.status = 'ready'
        """).bindparams(worker_id=worker_id)
    )

    # Get recent jobs (last 20)
    recent_jobs_query = sa.text("""
        SELECT
            tj.id as job_id,
            tj.video_id,
            v.slug as video_slug,
            v.title as video_title,
            v.status as video_status,
            tj.started_at,
            tj.completed_at,
            tj.last_error
        FROM transcoding_jobs tj
        JOIN videos v ON tj.video_id = v.id
        WHERE tj.worker_id = :worker_id
          AND tj.completed_at IS NOT NULL
        ORDER BY tj.completed_at DESC
        LIMIT 20
    """)

    recent_rows = await fetch_all_with_retry(recent_jobs_query.bindparams(worker_id=worker_id))

    recent_jobs = []
    for row in recent_rows:
        duration_seconds = None
        if row["started_at"] and row["completed_at"]:
            start = row["started_at"]
            end = row["completed_at"]
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            duration_seconds = (end - start).total_seconds()

        recent_jobs.append(
            WorkerJobHistory(
                job_id=row["job_id"],
                video_id=row["video_id"],
                video_slug=row["video_slug"],
                video_title=row["video_title"],
                status="completed" if row["video_status"] == "ready" else "failed",
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                duration_seconds=duration_seconds,
                error_message=row["last_error"] if row["video_status"] == "failed" else None,
            )
        )

    return WorkerDetailResponse(
        id=worker["id"],
        worker_id=worker["worker_id"],
        worker_name=worker["worker_name"],
        worker_type=worker["worker_type"],
        status=status,
        registered_at=worker["registered_at"],
        last_heartbeat=worker["last_heartbeat"],
        capabilities=capabilities,
        metadata=metadata,
        jobs_completed=jobs_completed,
        jobs_failed=jobs_failed,
        avg_job_duration_seconds=avg_duration,
        recent_jobs=recent_jobs,
    )


@app.put("/api/workers/{worker_id}/disable")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def disable_worker(request: Request, worker_id: str):
    """
    Disable a worker, preventing it from claiming new jobs.

    The worker's existing claimed job (if any) is released back
    to the pending queue.
    """
    # Find worker by UUID
    worker = await fetch_one_with_retry(workers.select().where(workers.c.worker_id == worker_id))
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    if worker["status"] == "disabled":
        raise HTTPException(status_code=400, detail="Worker is already disabled")

    async with database.transaction():
        # Mark worker as disabled
        await database.execute(
            workers.update().where(workers.c.id == worker["id"]).values(status="disabled", current_job_id=None)
        )

        # Release any claimed job
        if worker["current_job_id"]:
            job = await database.fetch_one(
                transcoding_jobs.select().where(transcoding_jobs.c.id == worker["current_job_id"])
            )
            if job and not job["completed_at"]:
                # Release the job claim
                await database.execute(
                    transcoding_jobs.update()
                    .where(transcoding_jobs.c.id == job["id"])
                    .values(
                        claimed_at=None,
                        claim_expires_at=None,
                        worker_id=None,
                        current_step=None,
                    )
                )
                # Reset video status
                await database.execute(videos.update().where(videos.c.id == job["video_id"]).values(status="pending"))

    # Audit log
    log_audit(
        AuditAction.WORKER_DISABLE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="worker",
        resource_id=worker["id"],
        resource_name=worker["worker_name"] or worker["worker_id"][:8],
    )

    return {"status": "ok", "message": "Worker disabled"}


@app.put("/api/workers/{worker_id}/enable")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def enable_worker(request: Request, worker_id: str):
    """
    Re-enable a disabled worker, allowing it to claim jobs again.
    """
    # Find worker by UUID
    worker = await fetch_one_with_retry(workers.select().where(workers.c.worker_id == worker_id))
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    if worker["status"] != "disabled":
        raise HTTPException(status_code=400, detail="Worker is not disabled")

    # Re-enable worker
    await db_execute_with_retry(workers.update().where(workers.c.id == worker["id"]).values(status="active"))

    # Audit log
    log_audit(
        AuditAction.WORKER_ENABLE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="worker",
        resource_id=worker["id"],
        resource_name=worker["worker_name"] or worker["worker_id"][:8],
    )

    return {"status": "ok", "message": "Worker enabled"}


@app.delete("/api/workers/{worker_id}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def delete_worker(request: Request, worker_id: str, revoke_keys: bool = True):
    """
    Delete a worker and optionally revoke its API keys.

    This will:
    - Release any claimed job back to pending
    - Revoke all API keys (if revoke_keys=True)
    - Delete the worker record
    """
    # Find worker by UUID
    worker = await fetch_one_with_retry(workers.select().where(workers.c.worker_id == worker_id))
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    now = datetime.now(timezone.utc)

    async with database.transaction():
        # Release any claimed job
        if worker["current_job_id"]:
            job = await database.fetch_one(
                transcoding_jobs.select().where(transcoding_jobs.c.id == worker["current_job_id"])
            )
            if job and not job["completed_at"]:
                await database.execute(
                    transcoding_jobs.update()
                    .where(transcoding_jobs.c.id == job["id"])
                    .values(
                        claimed_at=None,
                        claim_expires_at=None,
                        worker_id=None,
                        current_step=None,
                    )
                )
                await database.execute(videos.update().where(videos.c.id == job["video_id"]).values(status="pending"))

        # Revoke API keys if requested
        if revoke_keys:
            await database.execute(
                worker_api_keys.update()
                .where(worker_api_keys.c.worker_id == worker["id"])
                .where(worker_api_keys.c.revoked_at.is_(None))
                .values(revoked_at=now)
            )

        # Nullify worker_id in all historical transcoding_jobs to prevent orphaned references
        await database.execute(
            transcoding_jobs.update().where(transcoding_jobs.c.worker_id == worker_id).values(worker_id=None)
        )

        # Delete the worker record
        await database.execute(workers.delete().where(workers.c.id == worker["id"]))

    # Audit log
    log_audit(
        AuditAction.WORKER_DELETE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="worker",
        resource_id=worker["id"],
        resource_name=worker["worker_name"] or worker["worker_id"][:8],
        details={"revoke_keys": revoke_keys},
    )

    return {"status": "ok", "message": "Worker deleted"}


# ============ Server-Sent Events (SSE) Endpoints ============


@app.get("/api/events/progress")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def sse_progress(
    request: Request,
    video_ids: Optional[str] = Query(None, description="Comma-separated video IDs to monitor"),
):
    """
    Server-Sent Events endpoint for real-time transcoding progress.

    Subscribes to progress updates for specified videos (or all if none specified).
    Falls back to database polling if Redis is unavailable.

    SSE Message Format:
        event: progress
        data: {"video_id": 123, "progress_percent": 45, ...}

        event: heartbeat
        data: {"timestamp": "..."}
    """

    async def event_generator():
        # Send retry interval for client reconnection
        yield {"event": "retry", "data": str(SSE_RECONNECT_TIMEOUT_MS)}

        # Parse and validate video IDs
        vid_list = []
        if video_ids:
            try:
                vid_list = [int(v.strip()) for v in video_ids.split(",") if v.strip()]
            except ValueError:
                logger.warning(f"Invalid video IDs in SSE request: {video_ids}")
                vid_list = []

        # Send initial progress state
        try:
            initial_progress = await _get_progress_from_database(video_ids)
            if initial_progress:
                yield {"event": "initial", "data": json.dumps({"progress": initial_progress})}
        except Exception as e:
            logger.warning(f"Failed to get initial progress state: {e}")

        # Check if Redis is available for pub/sub
        redis_available = await is_redis_available()

        if redis_available:
            # Redis-based real-time updates
            subscriber = None
            try:
                if vid_list:
                    subscriber = await subscribe_to_progress(vid_list)
                else:
                    subscriber = await subscribe_to_progress()

                if subscriber and subscriber.is_active:
                    last_heartbeat = asyncio.get_running_loop().time()

                    async for message in subscriber.listen():
                        if await request.is_disconnected():
                            break

                        event_type = message.get("type", "progress")
                        yield {"event": event_type, "data": json.dumps(message)}

                        # Send periodic heartbeats
                        now = asyncio.get_running_loop().time()
                        if now - last_heartbeat > SSE_HEARTBEAT_INTERVAL:
                            yield {
                                "event": "heartbeat",
                                "data": json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}),
                            }
                            last_heartbeat = now
                else:
                    # Subscription failed, fall through to polling
                    redis_available = False
            except Exception as e:
                logger.warning(f"SSE Redis subscription error: {e}")
                redis_available = False
            finally:
                if subscriber:
                    await subscriber.close()

        if not redis_available:
            # Fallback: Database polling every 3 seconds
            last_data = {}
            while not await request.is_disconnected():
                try:
                    # Query database for progress
                    progress_data = await _get_progress_from_database(video_ids)

                    # Only send if changed
                    for vid, data in progress_data.items():
                        if data != last_data.get(vid):
                            yield {"event": "progress", "data": json.dumps(data)}
                    last_data = progress_data

                    # Send heartbeat
                    yield {
                        "event": "heartbeat",
                        "data": json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}),
                    }
                except Exception as e:
                    logger.debug(f"SSE polling error: {e}")

                await asyncio.sleep(3)

    return EventSourceResponse(event_generator())


@app.get("/api/events/workers")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def sse_workers(request: Request):
    """
    Server-Sent Events endpoint for real-time worker status updates.

    Provides:
    - Worker status changes (active/idle/offline)
    - Current job assignments
    - Job completion/failure notifications
    """

    async def event_generator():
        # Send retry interval for client reconnection
        yield {"event": "retry", "data": str(SSE_RECONNECT_TIMEOUT_MS)}

        # Send initial state
        try:
            initial_state = await _get_workers_state()
            yield {"event": "initial", "data": json.dumps(initial_state)}
        except Exception as e:
            logger.warning(f"Failed to get initial workers state: {e}")

        # Check if Redis is available for pub/sub
        redis_available = await is_redis_available()

        if redis_available:
            # Redis-based real-time updates
            subscriber = None
            try:
                subscriber = await subscribe_to_workers()

                if subscriber and subscriber.is_active:
                    last_heartbeat = asyncio.get_running_loop().time()

                    async for message in subscriber.listen():
                        if await request.is_disconnected():
                            break

                        event_type = message.get("type", "update")
                        yield {"event": event_type, "data": json.dumps(message)}

                        # Send periodic heartbeats
                        now = asyncio.get_running_loop().time()
                        if now - last_heartbeat > SSE_HEARTBEAT_INTERVAL:
                            yield {
                                "event": "heartbeat",
                                "data": json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}),
                            }
                            last_heartbeat = now
                else:
                    redis_available = False
            except Exception as e:
                logger.warning(f"SSE Redis subscription error: {e}")
                redis_available = False
            finally:
                if subscriber:
                    await subscriber.close()

        if not redis_available:
            # Fallback: Database polling every 5 seconds
            last_data = {}
            while not await request.is_disconnected():
                try:
                    current_data = await _get_workers_state()

                    # Only send if changed
                    if current_data != last_data:
                        yield {"event": "update", "data": json.dumps(current_data)}
                        last_data = current_data

                    # Send heartbeat
                    yield {
                        "event": "heartbeat",
                        "data": json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}),
                    }
                except Exception as e:
                    logger.debug(f"SSE polling error: {e}")

                await asyncio.sleep(5)

    return EventSourceResponse(event_generator())


async def _get_progress_from_database(video_ids: Optional[str]) -> dict:
    """Get progress data from database for SSE fallback polling."""
    # Build query for active videos
    query = (
        sa.select(
            videos.c.id,
            videos.c.slug,
            videos.c.status,
            transcoding_jobs.c.id.label("job_id"),
            transcoding_jobs.c.current_step,
            transcoding_jobs.c.progress_percent,
            transcoding_jobs.c.last_error,
        )
        .select_from(videos.outerjoin(transcoding_jobs, videos.c.id == transcoding_jobs.c.video_id))
        .where(videos.c.status.in_(["pending", "processing"]))
        .where(videos.c.deleted_at.is_(None))
    )

    if video_ids:
        try:
            ids = [int(v.strip()) for v in video_ids.split(",") if v.strip()]
            query = query.where(videos.c.id.in_(ids))
        except ValueError:
            # If video_ids contains invalid values, ignore the filter and return all active videos
            pass

    rows = await database.fetch_all(query)
    result = {}

    for row in rows:
        video_id = row["id"]

        # Get quality progress for this job
        qualities = []
        if row["job_id"]:
            qp_rows = await database.fetch_all(
                quality_progress.select().where(quality_progress.c.job_id == row["job_id"])
            )
            qualities = [
                {
                    "name": qp["quality"],
                    "status": qp["status"],
                    "progress": qp["progress_percent"] or 0,
                }
                for qp in qp_rows
            ]

        result[video_id] = {
            "type": "progress",
            "video_id": video_id,
            "video_slug": row["slug"],
            "job_id": row["job_id"],
            "status": row["status"],
            "current_step": row["current_step"],
            "progress_percent": row["progress_percent"] or 0,
            "qualities": qualities,
            "last_error": row["last_error"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return result


async def _get_workers_state() -> dict:
    """Get current workers state from database for SSE."""
    now = datetime.now(timezone.utc)
    offline_threshold = now - timedelta(minutes=WORKER_OFFLINE_THRESHOLD_MINUTES)

    # Get all workers
    rows = await database.fetch_all(workers.select().order_by(workers.c.registered_at.desc()))

    workers_list = []
    stats = {"total": 0, "active": 0, "idle": 0, "offline": 0, "disabled": 0}

    for row in rows:
        stats["total"] += 1

        # Determine effective status
        status = row["status"]
        if status == "disabled":
            stats["disabled"] += 1
        elif status in ("active", "idle", "busy"):
            last_hb = row["last_heartbeat"]
            if last_hb and last_hb.replace(tzinfo=timezone.utc) < offline_threshold:
                status = "offline"
                stats["offline"] += 1
            elif status == "idle":
                stats["idle"] += 1
            else:
                stats["active"] += 1
        else:
            stats["offline"] += 1

        # Get current job info if any
        current_job = None
        if row["current_job_id"]:
            job = await database.fetch_one(
                transcoding_jobs.select().where(transcoding_jobs.c.id == row["current_job_id"])
            )
            if job:
                video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))
                current_job = {
                    "job_id": job["id"],
                    "video_id": job["video_id"],
                    "video_slug": video["slug"] if video else None,
                    "current_step": job["current_step"],
                    "progress_percent": job["progress_percent"] or 0,
                }

        # Parse capabilities
        hwaccel_type = None
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                caps = metadata.get("capabilities", {})
                if caps.get("hwaccel_available"):
                    hwaccel_type = caps.get("hwaccel_type")
            except (json.JSONDecodeError, TypeError):
                # Ignore malformed or missing metadata; capabilities will be left unset
                pass

        workers_list.append(
            {
                "worker_id": row["worker_id"],
                "worker_name": row["worker_name"],
                "status": status,
                "last_heartbeat": row["last_heartbeat"].isoformat() if row["last_heartbeat"] else None,
                "hwaccel_type": hwaccel_type,
                "current_job": current_job,
            }
        )

    # Get active jobs
    active_jobs = []
    job_rows = await database.fetch_all(
        sa.select(
            transcoding_jobs,
            videos.c.slug.label("video_slug"),
            videos.c.title.label("video_title"),
        )
        .select_from(transcoding_jobs.join(videos, transcoding_jobs.c.video_id == videos.c.id))
        .where(transcoding_jobs.c.completed_at.is_(None))
        .order_by(transcoding_jobs.c.id.desc())
        .limit(50)
    )

    for job in job_rows:
        active_jobs.append(
            {
                "job_id": job["id"],
                "video_id": job["video_id"],
                "video_slug": job["video_slug"],
                "video_title": job["video_title"],
                "worker_id": job["worker_id"],
                "current_step": job["current_step"],
                "progress_percent": job["progress_percent"] or 0,
                "status": "processing" if job["worker_id"] else "pending",
            }
        )

    return {
        "workers": workers_list,
        "stats": stats,
        "active_jobs": active_jobs,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# Watermark Settings Endpoints
# ============================================================================


@app.get("/api/settings/watermark")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_watermark_settings(request: Request):
    """
    Get current watermark configuration.

    Returns the current watermark settings from environment configuration.
    Supports both image and text watermark types.
    Note: Watermark settings are configured via environment variables.
    """
    watermark_exists = False
    if WATERMARK_IMAGE:
        watermark_path = NAS_STORAGE / WATERMARK_IMAGE
        watermark_exists = watermark_path.exists()

    return {
        "enabled": WATERMARK_ENABLED,
        "type": WATERMARK_TYPE,
        # Image settings
        "image": WATERMARK_IMAGE,
        "image_exists": watermark_exists,
        "image_url": "/api/settings/watermark/image" if watermark_exists else None,
        "max_width_percent": WATERMARK_MAX_WIDTH_PERCENT,
        # Text settings
        "text": WATERMARK_TEXT,
        "text_size": WATERMARK_TEXT_SIZE,
        "text_color": WATERMARK_TEXT_COLOR,
        # Common settings
        "position": WATERMARK_POSITION,
        "opacity": WATERMARK_OPACITY,
        "padding": WATERMARK_PADDING,
    }


@app.get("/api/settings/watermark/image")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_admin_watermark_image(request: Request):
    """Serve the watermark image for admin preview."""
    if not WATERMARK_IMAGE:
        raise HTTPException(status_code=404, detail="No watermark configured")

    watermark_path = NAS_STORAGE / WATERMARK_IMAGE
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

    return FileResponse(watermark_path, media_type=content_type)


@app.post("/api/settings/watermark/upload")
@limiter.limit(RATE_LIMIT_ADMIN_UPLOAD)
async def upload_watermark_image(
    request: Request,
    file: UploadFile = File(...),
):
    """
    Upload a new watermark image.

    The image is saved to the configured watermark path (VLOG_WATERMARK_IMAGE).
    If no path is configured, saves to 'watermark.png' in NAS_STORAGE.

    Accepts: PNG, JPEG, WebP, SVG, GIF (max 10MB)
    For best results, use a PNG with transparency.

    Note: After uploading, you must set VLOG_WATERMARK_ENABLED=true and
    restart the services for the watermark to appear.
    """
    # Validate file extension
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    allowed_extensions = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image format. Allowed: {', '.join(sorted(allowed_extensions))}",
        )

    # Check file size via Content-Length header
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_THUMBNAIL_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_THUMBNAIL_UPLOAD_SIZE // (1024 * 1024)}MB",
        )

    # Determine target path
    if WATERMARK_IMAGE:
        target_path = NAS_STORAGE / WATERMARK_IMAGE
    else:
        # Default to watermark.png if not configured
        target_path = NAS_STORAGE / f"watermark{ext}"

    # Save file
    temp_path = NAS_STORAGE / f"watermark_temp_{uuid.uuid4()}{ext}"
    try:
        total_size = 0
        with open(temp_path, "wb") as f:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                total_size += len(chunk)
                if total_size > MAX_THUMBNAIL_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum size: {MAX_THUMBNAIL_UPLOAD_SIZE // (1024 * 1024)}MB",
                    )
                f.write(chunk)

        # Move temp file to target (atomic on same filesystem)
        shutil.move(str(temp_path), str(target_path))

        # Audit log
        log_audit(
            AuditAction.SETTINGS_CHANGE,
            client_ip=get_real_ip(request),
            user_agent=request.headers.get("user-agent"),
            resource_type="watermark",
            resource_id=None,
            resource_name=str(target_path.name),
            details={"action": "watermark_upload", "original_filename": file.filename, "size": total_size},
        )

        return {
            "status": "ok",
            "message": "Watermark uploaded successfully",
            "path": str(target_path.relative_to(NAS_STORAGE)),
            "size": total_size,
            "note": "Set VLOG_WATERMARK_ENABLED=true and VLOG_WATERMARK_IMAGE="
            + str(target_path.relative_to(NAS_STORAGE))
            + " in your environment, then restart services.",
        }

    except Exception as e:
        # Clean up temp file on error
        if temp_path.exists():
            temp_path.unlink()
        logger.error(f"Failed to upload watermark: {e}")
        raise HTTPException(status_code=500, detail="Failed to save watermark image")


@app.delete("/api/settings/watermark")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def delete_watermark_image(request: Request):
    """
    Delete the current watermark image.

    Removes the watermark file from storage. You should also set
    VLOG_WATERMARK_ENABLED=false to disable the watermark overlay.
    """
    if not WATERMARK_IMAGE:
        raise HTTPException(status_code=404, detail="No watermark configured")

    watermark_path = NAS_STORAGE / WATERMARK_IMAGE
    if not watermark_path.exists():
        raise HTTPException(status_code=404, detail="Watermark image not found")

    try:
        watermark_path.unlink()

        # Audit log
        log_audit(
            AuditAction.SETTINGS_CHANGE,
            client_ip=get_real_ip(request),
            user_agent=request.headers.get("user-agent"),
            resource_type="watermark",
            resource_id=None,
            resource_name=WATERMARK_IMAGE,
            details={"action": "watermark_delete"},
        )

        return {
            "status": "ok",
            "message": "Watermark deleted successfully",
            "note": "Set VLOG_WATERMARK_ENABLED=false in your environment to disable the watermark overlay.",
        }
    except Exception as e:
        logger.error(f"Failed to delete watermark: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete watermark image")


# =============================================================================
# Runtime Settings API (Database-backed configuration)
# See: https://github.com/filthyrake/vlog/issues/400
# =============================================================================


@app.get("/api/settings")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_settings(request: Request) -> SettingsByCategoryResponse:
    """
    List all settings grouped by category.

    Returns settings from the database, grouped by their category
    for easy display in the admin UI.
    """
    service = get_settings_service()
    all_settings = await service.get_all()

    # Convert to response format
    categories_dict = {}
    for category, settings_list in all_settings.items():
        categories_dict[category] = [
            SettingResponse(
                key=s["key"],
                value=s["value"],
                category=category,
                value_type=s["value_type"],
                description=s["description"],
                constraints=s.get("constraints"),
                updated_at=s["updated_at"],
                updated_by=s.get("updated_by"),
            )
            for s in settings_list
        ]

    return SettingsByCategoryResponse(categories=categories_dict)


@app.get("/api/settings/categories")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def list_settings_categories(request: Request) -> List[str]:
    """
    List all setting categories.

    Returns a list of unique category names for building
    navigation in the admin UI.
    """
    service = get_settings_service()
    return await service.get_categories()


@app.get("/api/settings/category/{category}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_settings_by_category(request: Request, category: str) -> SettingsCategoryResponse:
    """
    Get all settings in a specific category.

    Returns settings filtered by the specified category,
    useful for displaying a single tab in the settings UI.
    """
    service = get_settings_service()
    settings_list = await service.get_category(category)

    if not settings_list:
        # Return empty category rather than 404 for consistent UI behavior
        return SettingsCategoryResponse(category=category, settings=[])

    return SettingsCategoryResponse(
        category=category,
        settings=[
            SettingResponse(
                key=s["key"],
                value=s["value"],
                category=category,
                value_type=s["value_type"],
                description=s["description"],
                constraints=s.get("constraints"),
                updated_at=s["updated_at"],
                updated_by=s.get("updated_by"),
            )
            for s in settings_list
        ],
    )


@app.get("/api/settings/key/{key:path}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_setting(request: Request, key: str) -> SettingResponse:
    """
    Get a single setting by key.

    The key uses dot notation (e.g., "transcoding.hls_segment_duration").
    """
    service = get_settings_service()
    setting = await service.get_single(key)

    if setting is None:
        raise HTTPException(status_code=404, detail=f"Setting not found: {key}")

    return SettingResponse(
        key=setting["key"],
        value=setting["value"],
        category=setting["category"],
        value_type=setting["value_type"],
        description=setting["description"],
        constraints=setting.get("constraints"),
        updated_at=setting["updated_at"],
        updated_by=setting.get("updated_by"),
    )


@app.put("/api/settings/key/{key:path}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def update_setting(request: Request, key: str, data: SettingUpdate) -> SettingResponse:
    """
    Update a setting value.

    Validates the value against the setting's type and constraints
    before saving. The change is reflected immediately in the cache.
    """
    service = get_settings_service()

    # Verify setting exists
    existing = await service.get_single(key)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Setting not found: {key}")

    try:
        await service.set(key, data.value, updated_by="admin")
    except SettingsValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Setting not found: {key}")

    # Audit log
    log_audit(
        AuditAction.SETTINGS_CHANGE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="setting",
        resource_id=None,
        resource_name=key,
        details={"action": "update", "new_value": data.value, "old_value": existing["value"]},
    )

    # Return updated setting
    updated = await service.get_single(key)
    return SettingResponse(
        key=updated["key"],
        value=updated["value"],
        category=updated["category"],
        value_type=updated["value_type"],
        description=updated["description"],
        constraints=updated.get("constraints"),
        updated_at=updated["updated_at"],
        updated_by=updated.get("updated_by"),
    )


@app.post("/api/settings")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def create_setting(request: Request, data: SettingCreate) -> SettingResponse:
    """
    Create a new setting.

    Settings are created with a unique key in dot notation
    (e.g., "transcoding.hls_segment_duration").
    """
    service = get_settings_service()

    # Check if already exists
    existing = await service.get_single(data.key)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Setting already exists: {data.key}")

    try:
        await service.create(
            key=data.key,
            value=data.value,
            category=data.category,
            value_type=data.value_type,
            description=data.description,
            constraints=data.constraints.model_dump() if data.constraints else None,
            updated_by="admin",
        )
    except SettingsValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Audit log
    log_audit(
        AuditAction.SETTINGS_CHANGE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="setting",
        resource_id=None,
        resource_name=data.key,
        details={"action": "create", "value": data.value, "category": data.category},
    )

    # Return created setting
    created = await service.get_single(data.key)
    return SettingResponse(
        key=created["key"],
        value=created["value"],
        category=created["category"],
        value_type=created["value_type"],
        description=created["description"],
        constraints=created.get("constraints"),
        updated_at=created["updated_at"],
        updated_by=created.get("updated_by"),
    )


@app.delete("/api/settings/key/{key:path}")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def delete_setting(request: Request, key: str):
    """
    Delete a setting.

    Removes the setting from the database. This should be used
    with caution as it may affect application behavior.
    """
    service = get_settings_service()

    # Verify setting exists
    existing = await service.get_single(key)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Setting not found: {key}")

    await service.delete(key)

    # Audit log
    log_audit(
        AuditAction.SETTINGS_CHANGE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="setting",
        resource_id=None,
        resource_name=key,
        details={"action": "delete", "old_value": existing["value"]},
    )

    return {"status": "ok", "message": f"Setting deleted: {key}"}


@app.post("/api/settings/export")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def export_settings(request: Request) -> SettingsExport:
    """
    Export all settings as JSON.

    Returns all settings in a format suitable for backup
    or transferring between environments.
    """
    service = get_settings_service()
    all_settings = await service.get_all()

    settings_list = []
    for category, cat_settings in all_settings.items():
        for s in cat_settings:
            settings_list.append(
                SettingResponse(
                    key=s["key"],
                    value=s["value"],
                    category=category,
                    value_type=s["value_type"],
                    description=s["description"],
                    constraints=s.get("constraints"),
                    updated_at=s["updated_at"],
                    updated_by=s.get("updated_by"),
                )
            )

    return SettingsExport(
        version="1.0",
        exported_at=datetime.now(timezone.utc),
        settings=settings_list,
    )


@app.post("/api/settings/import")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def import_settings(request: Request, data: SettingsImport):
    """
    Import settings from JSON.

    Imports settings from an export file. By default, existing
    settings are skipped. Use overwrite=true to replace existing values.
    """
    service = get_settings_service()
    results = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

    for setting in data.settings:
        try:
            existing = await service.get_single(setting.key)

            if existing is not None:
                if data.overwrite:
                    await service.set(setting.key, setting.value, updated_by="import")
                    results["updated"] += 1
                else:
                    results["skipped"] += 1
            else:
                await service.create(
                    key=setting.key,
                    value=setting.value,
                    category=setting.category,
                    value_type=setting.value_type,
                    description=setting.description,
                    constraints=setting.constraints.model_dump() if setting.constraints else None,
                    updated_by="import",
                )
                results["created"] += 1
        except Exception as e:
            results["errors"].append({"key": setting.key, "error": str(e)})

    # Audit log
    log_audit(
        AuditAction.SETTINGS_CHANGE,
        client_ip=get_real_ip(request),
        user_agent=request.headers.get("user-agent"),
        resource_type="settings",
        resource_id=None,
        resource_name="bulk_import",
        details={
            "action": "import",
            "created": results["created"],
            "updated": results["updated"],
            "skipped": results["skipped"],
            "errors": len(results["errors"]),
        },
    )

    return {
        "status": "ok",
        "created": results["created"],
        "updated": results["updated"],
        "skipped": results["skipped"],
        "errors": results["errors"],
    }


@app.post("/api/settings/invalidate-cache")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def invalidate_settings_cache(request: Request):
    """
    Invalidate the settings cache.

    Forces the next settings read to fetch fresh data from the database.
    Useful when settings have been modified directly in the database.
    """
    service = get_settings_service()
    service.invalidate_cache()

    return {"status": "ok", "message": "Settings cache invalidated"}


@app.get("/api/settings/cache-stats")
@limiter.limit(RATE_LIMIT_ADMIN_DEFAULT)
async def get_settings_cache_stats(request: Request):
    """
    Get settings cache statistics.

    Returns information about the current state of the settings cache.
    """
    service = get_settings_service()
    return service.get_cache_stats()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=ADMIN_PORT)
