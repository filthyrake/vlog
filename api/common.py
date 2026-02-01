"""
Common utilities shared between public and admin APIs.

This module contains shared code to avoid duplication (DRY principle).
"""

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

from api.auth.permissions import Permission, Role, has_permission
from api.database import database, live_streams
from api.db_retry import fetch_one_with_retry
from api.logging_config import (
    clear_request_context,
    sanitize_user_agent,
    set_request_context,
)
from config import (
    STORAGE_CHECK_TIMEOUT,
    TRUSTED_PROXIES,
    UPLOADS_DIR,
    VIDEOS_DIR,
)

logger = logging.getLogger(__name__)

# Slug validation pattern: lowercase alphanumeric with hyphens only
# Pattern: ^[a-z0-9]+(?:-[a-z0-9]+)*$
# - Must start with alphanumeric
# - Can contain hyphens between alphanumeric segments
# - No consecutive hyphens, no leading/trailing hyphens
SLUG_PATTERN = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')

# Request ID validation pattern: alphanumeric with common separator characters
# Allows UUIDs, trace IDs from various systems, and custom IDs
# Max 128 chars to prevent log injection/memory abuse
REQUEST_ID_PATTERN = re.compile(r'^[\w\-.]+$')
REQUEST_ID_MAX_LENGTH = 128


def validate_slug(slug: str) -> bool:
    r"""
    Validate slug contains only safe characters and has no path traversal attempts.

    Args:
        slug: The slug string to validate

    Returns:
        True if slug is valid, False otherwise

    Security:
        - Prevents path traversal attacks (../, ..\, etc.)
        - Ensures slug matches safe character pattern (lowercase alphanumeric with hyphens)
        - Defense in depth: slugs are generated server-side but this validates user input
    """
    if not slug:
        return False
    # Check for path traversal sequences
    if '..' in slug:
        return False
    # Check against allowed pattern
    return bool(SLUG_PATTERN.match(slug))


def require_valid_slug(slug: str, resource_type: str = "resource") -> None:
    """
    Validate slug or raise HTTPException with 400 status.

    Security: Prevents path traversal attacks by ensuring slug contains
    only safe characters (lowercase alphanumeric with hyphens).

    Note: This function intentionally duplicates validate_slug() logic to provide
    specific error messages for each failure case (missing, path traversal, format).
    This improves API usability by helping clients understand exactly what's wrong.

    Args:
        slug: The slug string to validate
        resource_type: Type of resource for error message (e.g., "video", "category")

    Raises:
        HTTPException: 400 error if slug is invalid
    """
    if not slug:
        raise HTTPException(
            status_code=400,
            detail=f"Missing {resource_type} slug"
        )
    if '..' in slug:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {resource_type} slug: path traversal not allowed"
        )
    if not SLUG_PATTERN.match(slug):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {resource_type} slug: must be lowercase alphanumeric with hyphens"
        )


async def verify_stream_access(slug: str, user: dict) -> dict:
    """
    Verify user has access to a stream (ownership or admin permission).

    This is a shared utility to avoid duplicating stream access logic across
    studio modules. Uses a 404 response for unauthorized access to prevent
    enumeration attacks.

    Args:
        slug: Stream slug to verify access for
        user: User dict from authenticated session

    Returns:
        Stream record as dict if access is granted

    Raises:
        HTTPException: 400 if slug format is invalid
        HTTPException: 404 if stream not found or user doesn't have access
    """
    require_valid_slug(slug, "stream")

    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.slug == slug)
    )

    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Owner check OR admin permission
    role = Role(user["role"])
    is_owner = stream["owner_id"] == user["id"]
    has_manage_any = has_permission(role, Permission.LIVE_STREAM_MANAGE)

    if not is_owner and not has_manage_any:
        raise HTTPException(status_code=404, detail="Stream not found")

    return dict(stream)


# Cache for storage health status to avoid hammering storage on every request
_storage_health_cache = {
    "healthy": True,
    "last_check": None,
    "last_error": None,
}
_storage_health_lock: Optional[asyncio.Lock] = None
STORAGE_HEALTH_CACHE_TTL = 5  # seconds


def _get_storage_health_lock() -> asyncio.Lock:
    """Get or create the storage health cache lock.

    The lock is created lazily to ensure it's bound to the correct event loop.
    This is necessary because the lock may be used across different event loops
    in testing or when the application restarts.
    """
    global _storage_health_lock

    if _storage_health_lock is None:
        _storage_health_lock = asyncio.Lock()
        return _storage_health_lock

    # Check if the lock is bound to a different event loop
    # by comparing the lock's internal loop (if accessible) with the current loop
    try:
        current_loop = asyncio.get_running_loop()
        # Access the internal _loop attribute which exists on asyncio.Lock
        # This is safer than calling _get_loop() which is more private
        lock_loop = getattr(_storage_health_lock, '_loop', None)
        if lock_loop is not None and lock_loop is not current_loop:
            # Lock is from a different event loop, create a new one
            _storage_health_lock = asyncio.Lock()
    except RuntimeError:
        # No event loop running, the existing lock should be fine
        pass

    return _storage_health_lock


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Ensure datetime is timezone-aware UTC.

    This function ensures consistent timezone handling for datetime comparisons,
    handling both timezone-aware and timezone-naive datetime objects.

    Args:
        dt: A datetime object (may be None, timezone-aware, or timezone-naive)

    Returns:
        - None if input is None
        - UTC datetime if input was timezone-aware (converted to UTC if needed)
        - UTC datetime if input was timezone-naive (assumed to be UTC)

    Examples:
        >>> ensure_utc(None)
        None
        >>> ensure_utc(datetime(2024, 1, 1, 12, 0, 0))  # naive
        datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        >>> ensure_utc(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
        datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume naive datetimes are UTC
        return dt.replace(tzinfo=timezone.utc)
    # Convert timezone-aware datetimes to UTC
    return dt.astimezone(timezone.utc)


def calculate_stream_offset_ms(
    stream_started_at: Optional[datetime],
    current_time: datetime
) -> Optional[int]:
    """
    Calculate milliseconds elapsed since stream start.

    Handles timezone normalization for naive datetimes (assumes UTC).
    Returns None if stream_started_at is None. Clamps result to non-negative
    to protect against clock skew.

    Args:
        stream_started_at: When the stream started (may be timezone-naive, assumed UTC)
        current_time: Current timestamp (may be timezone-naive, assumed UTC)

    Returns:
        Non-negative milliseconds since stream start, or None if stream_started_at is None
    """
    if stream_started_at is None:
        return None

    # Normalize both timestamps to UTC to prevent TypeError on subtraction
    started = ensure_utc(stream_started_at)
    current = ensure_utc(current_time)

    offset_ms = int((current - started).total_seconds() * 1000)
    # Clamp to non-negative to protect against clock skew
    return max(0, offset_ms)


def get_real_ip(request: Request) -> str:
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


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Add request ID tracking for request tracing across services.

    Generates a unique request ID for each incoming request, or uses an existing
    X-Request-ID header if provided. The request ID is stored in request.state
    and returned in the response headers.

    This enables:
    - Correlating logs across multiple services
    - Tracing requests through the entire request lifecycle
    - Debugging production issues by filtering logs by request ID
    - Automatic inclusion of request context in structured JSON logs (Issue #208)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Defensive: clear any leaked context from previous request
        clear_request_context()

        # Use existing request ID from header, or generate a new one
        request_id = request.headers.get("X-Request-ID")
        if request_id:
            # Sanitize: limit length and allow only safe characters
            # This prevents log injection attacks and excessive memory usage
            request_id = request_id[:REQUEST_ID_MAX_LENGTH].strip()
            if not REQUEST_ID_PATTERN.match(request_id):
                request_id = None
        if not request_id:
            request_id = str(uuid.uuid4())

        # Store in request state for access by handlers
        request.state.request_id = request_id

        # Set logging context for structured logs (Issue #208)
        # User-Agent is sanitized to prevent log injection attacks
        set_request_context(
            request_id=request_id,
            client_ip=get_real_ip(request),
            user_agent=sanitize_user_agent(request.headers.get("user-agent", "")),
        )

        try:
            response = await call_next(request)

            # Include request ID in response for client correlation
            response.headers["X-Request-ID"] = request_id

            return response
        finally:
            # Always cleanup context, even on exception (per Margo's review)
            clear_request_context()


def get_request_id(request: Request) -> Optional[str]:
    """
    Get the request ID from the request state.

    Returns None if RequestIDMiddleware hasn't processed the request yet.
    """
    return getattr(request.state, "request_id", None)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        # Prevent clickjacking (skip for embed pages - they use CSP frame-ancestors)
        if not request.url.path.startswith("/embed/"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        # Prevent MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # XSS protection for legacy browsers
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Permissions policy (disable unnecessary browser features)
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Content Security Policy - restrict resource loading
        # Skip CSP for HTML pages (they have their own CSP meta tag with Alpine.js support)
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            # API responses get restrictive CSP
            response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        return response


class HTTPMetricsMiddleware:
    """
    Pure ASGI middleware for HTTP metrics.

    This middleware is 6x faster than BaseHTTPMiddleware because it doesn't
    wrap requests in an additional task. It tracks:
    - HTTP requests in progress (gauge)
    - HTTP request duration (histogram)
    - HTTP request total (counter with status code)

    Uses low-cardinality labels to prevent metrics explosion:
    - api: "admin", "worker", or "public" (3 values max)
    - endpoint: Normalized paths like /api/videos/{id}

    Issue #207
    """

    def __init__(self, app: "ASGIApp", api_name: str):
        """
        Initialize the middleware.

        Args:
            app: The ASGI application to wrap
            api_name: Name of the API for labeling ("admin", "worker", "public")
        """
        self.app = app
        self.api_name = api_name

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        """Process an ASGI request."""
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Import here to avoid circular imports
        from api.metrics import (
            HTTP_REQUEST_DURATION_SECONDS,
            HTTP_REQUESTS_IN_PROGRESS,
            HTTP_REQUESTS_TOTAL,
            normalize_endpoint,
        )

        # Increment in-progress gauge
        HTTP_REQUESTS_IN_PROGRESS.labels(api=self.api_name).inc()
        start_time = time.perf_counter()
        status_code = 500  # Default if exception occurs before response

        async def send_wrapper(message: dict) -> None:
            """Capture status code from response."""
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Re-raise after metrics are recorded in finally block
            raise
        finally:
            # ALWAYS decrement and record metrics (even on exception)
            HTTP_REQUESTS_IN_PROGRESS.labels(api=self.api_name).dec()
            duration = time.perf_counter() - start_time

            # Extract method and normalize path for low cardinality
            method = scope.get("method", "UNKNOWN")
            path = normalize_endpoint(scope.get("path", "/"))

            # Record duration histogram
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, endpoint=path).observe(duration)
            # Increment request counter with status code and api label (for Grafana grouping)
            HTTP_REQUESTS_TOTAL.labels(
                method=method, endpoint=path, status_code=str(status_code), api=self.api_name
            ).inc()


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handle rate limit exceeded errors with a proper JSON response."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded",
            "error": str(exc.detail),
        },
    )


def _check_storage_sync() -> bool:
    """
    Synchronous storage check that verifies both existence and writability.

    This runs in a thread pool to avoid blocking the event loop, and includes
    a write test to detect read-only mounts, permission issues, or full disks.
    """
    import os

    # Skip storage check in test mode (CI doesn't have real storage)
    if os.environ.get("VLOG_TEST_MODE"):
        return True

    try:
        # Check directories exist
        if not VIDEOS_DIR.exists() or not UPLOADS_DIR.exists():
            return False

        # Test write capability by creating and removing a temp file
        # Use uploads dir since that's where new files arrive
        test_file = UPLOADS_DIR / f".health_check_{uuid.uuid4().hex}"
        test_file.write_text("health check")
        test_file.unlink()

        return True
    except (IOError, OSError, PermissionError):
        return False


async def check_health() -> dict:
    """
    Perform health checks for database and storage.

    Returns a dict with:
        - checks: dict of individual check results
        - healthy: bool indicating overall health
        - status_code: HTTP status code (200 if healthy, 503 if not)
    """
    checks = {
        "database": False,
        "storage": False,
    }

    # Check database connectivity
    try:
        await database.fetch_one("SELECT 1")
        checks["database"] = True
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")

    # Check storage accessibility (NAS mount) with timeout
    # Uses a timeout to detect stale NFS mounts that would otherwise hang
    try:
        loop = asyncio.get_running_loop()
        checks["storage"] = await asyncio.wait_for(
            loop.run_in_executor(None, _check_storage_sync),
            timeout=STORAGE_CHECK_TIMEOUT,
        )
    except asyncio.TimeoutError:
        # Storage check timed out - likely a stale mount
        logger.warning("Storage health check timed out - possible stale NFS mount")
        checks["storage"] = False
    except Exception as e:
        logger.warning(f"Storage health check failed: {e}")

    healthy = all(checks.values())

    # Update storage health cache atomically
    async with _get_storage_health_lock():
        _storage_health_cache["healthy"] = checks["storage"]
        _storage_health_cache["last_check"] = datetime.now(timezone.utc)
        if not checks["storage"]:
            _storage_health_cache["last_error"] = "Storage check failed or timed out"
        else:
            _storage_health_cache["last_error"] = None

    return {
        "checks": checks,
        "healthy": healthy,
        "status_code": 200 if healthy else 503,
    }


async def check_storage_available() -> bool:
    """
    Check if storage is currently available, using cached status when recent.

    This is a fast check suitable for use in request handling. It uses a cached
    status within the TTL to avoid hammering the storage on every request.

    Returns:
        True if storage is available, False otherwise.
    """
    import os

    # Skip storage check in test mode (CI doesn't have real storage)
    if os.environ.get("VLOG_TEST_MODE"):
        return True

    now = datetime.now(timezone.utc)

    # Use lock to prevent race conditions with concurrent access
    async with _get_storage_health_lock():
        # Return cached status if recent
        if _storage_health_cache["last_check"] is not None:
            age = (now - _storage_health_cache["last_check"]).total_seconds()
            if age < STORAGE_HEALTH_CACHE_TTL:
                return _storage_health_cache["healthy"]

        # Perform a quick storage check (outside lock would allow thundering herd)
        try:
            loop = asyncio.get_running_loop()
            is_healthy = await asyncio.wait_for(
                loop.run_in_executor(None, _check_storage_sync),
                timeout=STORAGE_CHECK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("Storage availability check timed out - possible stale NFS mount")
            is_healthy = False
        except Exception as e:
            logger.warning(f"Storage availability check failed: {e}")
            is_healthy = False

        # Update cache atomically
        _storage_health_cache["healthy"] = is_healthy
        _storage_health_cache["last_check"] = now
        if not is_healthy:
            _storage_health_cache["last_error"] = "Storage unavailable"
        else:
            _storage_health_cache["last_error"] = None

        return is_healthy


async def require_storage_available():
    """
    FastAPI dependency that ensures storage is available.

    Use this as a dependency for endpoints that require storage access.
    Raises HTTPException 503 if storage is unavailable.

    Example:
        @app.get("/videos/{slug}/stream")
        async def stream_video(slug: str, _=Depends(require_storage_available)):
            ...
    """
    if not await check_storage_available():
        raise HTTPException(
            status_code=503,
            detail="Video storage temporarily unavailable. Please try again later.",
            headers={"Retry-After": "30"},
        )


def get_storage_status() -> dict:
    """
    Get the current storage health status from cache.

    Returns a dict with:
        - healthy: bool indicating storage health
        - last_check: ISO timestamp of last check (or None)
        - last_error: Error message if unhealthy (or None)
    """
    return {
        "healthy": _storage_health_cache["healthy"],
        "last_check": (
            _storage_health_cache["last_check"].isoformat()
            if _storage_health_cache["last_check"]
            else None
        ),
        "last_error": _storage_health_cache["last_error"],
    }


class StorageUnavailableError(Exception):
    """Raised when storage operations fail due to unavailable storage."""

    def __init__(self, message: str = "Video storage temporarily unavailable"):
        self.message = message
        super().__init__(self.message)
