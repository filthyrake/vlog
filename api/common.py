"""
Common utilities shared between public and admin APIs.

This module contains shared code to avoid duplication (DRY principle).
"""

import asyncio
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from api.database import database
from config import TRUSTED_PROXIES, UPLOADS_DIR, VIDEOS_DIR

# Timeout for storage health check (seconds)
STORAGE_CHECK_TIMEOUT = 5


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
    except Exception:
        pass

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
        checks["storage"] = False
    except Exception:
        pass

    healthy = all(checks.values())
    return {
        "checks": checks,
        "healthy": healthy,
        "status_code": 200 if healthy else 503,
    }
