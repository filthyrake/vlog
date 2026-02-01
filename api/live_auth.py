"""Authentication for live stream ingest API.

Follows the worker_auth.py pattern for API key management:
- argon2id hashing for secure storage
- Prefix-based lookup for efficient authentication
- Security event logging for monitoring/SIEM integration
"""

import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.database import database, live_streams
from config import TRUSTED_PROXIES

# Security event logger - separate from regular application logging
security_logger = logging.getLogger("security.live")

# Standard logger for general operations
logger = logging.getLogger(__name__)

# Bearer token auth
bearer_scheme = HTTPBearer(auto_error=False)

# Stream key prefix for easy identification
STREAM_KEY_PREFIX = "sk_live_"

# Hash version constants (same as worker_auth.py)
HASH_VERSION_ARGON2 = 2

# Explicit argon2 parameters (OWASP recommended minimums)
_password_hasher = PasswordHasher(
    time_cost=3,  # iterations
    memory_cost=65536,  # 64MB memory
    parallelism=4,  # threads
)


def generate_stream_key() -> str:
    """
    Generate a secure stream key.

    Format: sk_live_{random_32_bytes_base64}
    The prefix makes it easy to identify stream keys in logs/configs.
    """
    return f"{STREAM_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_stream_key(key: str) -> str:
    """
    Hash a stream key using argon2id.

    Returns the argon2id hash with embedded salt and parameters.
    """
    return _password_hasher.hash(key)


def get_key_prefix(key: str) -> str:
    """Get the first 8 characters of a stream key for efficient lookup."""
    return key[:8]


def verify_stream_key_hash(key: str, stored_hash: str, key_prefix: Optional[str] = None) -> bool:
    """
    Verify a stream key against a stored hash using argon2id.

    Args:
        key: The plaintext stream key to verify
        stored_hash: The hash stored in the database
        key_prefix: Optional key prefix for error logging context

    Returns:
        True if the key matches the hash, False otherwise
    """
    try:
        _password_hasher.verify(stored_hash, key)
        return True
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        # Malformed hash in database - log and fail
        security_logger.error(
            "Invalid argon2 hash format in database for stream key",
            extra={
                "event": "live_auth_error",
                "reason": "invalid_hash_format",
                "key_prefix": key_prefix,
            },
        )
        return False


def _get_request_context(request: Optional[Request]) -> dict:
    """
    Extract security-relevant context from request for logging.

    Returns both the direct client IP and any X-Forwarded-For value separately.
    The X-Forwarded-For header is only trusted if the direct client IP is in
    TRUSTED_PROXIES to prevent header spoofing attacks.
    """
    if request is None:
        return {
            "ip_address": "unknown",
            "direct_ip": "unknown",
            "forwarded_for": None,
            "user_agent": "unknown",
        }

    # Always get the direct connection IP
    direct_ip = request.client.host if request.client else "unknown"

    # Get X-Forwarded-For header if present (may be spoofed if not behind trusted proxy)
    forwarded_for_header = request.headers.get("x-forwarded-for")
    forwarded_for_ip = None
    if forwarded_for_header:
        # Take the first IP in the chain (claimed original client)
        forwarded_for_ip = forwarded_for_header.split(",")[0].strip()

    # Determine the effective IP address for logging
    # Only trust X-Forwarded-For if request comes from a trusted proxy
    if forwarded_for_ip and direct_ip in TRUSTED_PROXIES:
        effective_ip = forwarded_for_ip
    else:
        effective_ip = direct_ip

    user_agent = request.headers.get("user-agent", "unknown")

    return {
        "ip_address": effective_ip,
        "direct_ip": direct_ip,
        "forwarded_for": forwarded_for_ip,
        "user_agent": user_agent,
    }


async def authenticate_stream_key(stream_key: str, request: Optional[Request] = None) -> dict:
    """
    Authenticate a stream key and return the stream record.

    Args:
        stream_key: The plaintext stream key from the Authorization header
        request: Optional request for logging context

    Returns:
        The stream record as a dict on success

    Raises:
        HTTPException(401) if key is invalid or stream is not in valid state
        HTTPException(403) if stream has ended
    """
    ctx = _get_request_context(request)

    # Validate stream key format
    if not stream_key or len(stream_key) < 8:
        security_logger.warning(
            "Live auth failed: invalid stream key format",
            extra={
                "event": "live_auth_failure",
                "reason": "invalid_key_format",
                "key_length": len(stream_key) if stream_key else 0,
                **ctx,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid stream key")

    prefix = get_key_prefix(stream_key)

    # Query database for matching streams by prefix (non-ended streams only)
    # We accept both 'idle' and 'live' status for segment uploads
    # 'ending' status also accepted (grace period for network hiccups)
    stream_records = await database.fetch_all(
        live_streams.select()
        .where(live_streams.c.stream_key_prefix == prefix)
        .where(live_streams.c.status.in_(["idle", "live", "ending"]))
    )

    if not stream_records:
        # Check if stream exists but has ended
        ended_stream = await database.fetch_one(
            live_streams.select()
            .where(live_streams.c.stream_key_prefix == prefix)
            .where(live_streams.c.status == "ended")
        )
        if ended_stream:
            security_logger.warning(
                "Live auth failed: stream has ended",
                extra={
                    "event": "live_auth_failure",
                    "reason": "stream_ended",
                    "key_prefix": prefix,
                    "stream_id": ended_stream["id"],
                    **ctx,
                },
            )
            raise HTTPException(status_code=403, detail="Stream has ended")

        security_logger.warning(
            "Live auth failed: invalid stream key",
            extra={
                "event": "live_auth_failure",
                "reason": "invalid_key",
                "key_prefix": prefix,
                **ctx,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid stream key")

    # Try each candidate stream with matching prefix
    for stream_record in stream_records:
        if verify_stream_key_hash(stream_key, stream_record["stream_key_hash"], key_prefix=prefix):
            # Found matching stream
            security_logger.info(
                "Live auth successful",
                extra={
                    "event": "live_auth_success",
                    "stream_id": stream_record["id"],
                    "stream_slug": stream_record["slug"],
                    **ctx,
                },
            )
            return dict(stream_record)

    # None of the candidates matched
    security_logger.warning(
        "Live auth failed: key hash mismatch",
        extra={
            "event": "live_auth_failure",
            "reason": "hash_mismatch",
            "key_prefix": prefix,
            "candidates_checked": len(stream_records),
            **ctx,
        },
    )
    raise HTTPException(status_code=401, detail="Invalid stream key")


async def verify_stream_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
) -> dict:
    """
    FastAPI dependency for stream key verification.

    Expects Authorization: Bearer <stream_key>

    Returns the stream record as a dict on success.
    Raises HTTPException if authentication fails.
    """
    ctx = _get_request_context(request)

    if not credentials:
        security_logger.warning(
            "Live auth failed: missing Authorization header",
            extra={
                "event": "live_auth_failure",
                "reason": "missing_header",
                **ctx,
            },
        )
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header. Use: Authorization: Bearer <stream_key>",
        )

    return await authenticate_stream_key(credentials.credentials, request)


async def revoke_stream_key(stream_id: int) -> bool:
    """
    Atomically revoke a stream key by setting status to 'ended'.

    This prevents race conditions where segments could be uploaded
    during VOD creation.

    Args:
        stream_id: The ID of the stream to revoke

    Returns:
        True if revoked successfully, False if stream was already ended
    """
    now = datetime.now(timezone.utc)

    # Atomically update status to 'ended' if not already ended
    result = await database.execute(
        live_streams.update()
        .where(live_streams.c.id == stream_id)
        .where(live_streams.c.status != "ended")
        .values(status="ended", ended_at=now)
    )

    if result > 0:
        logger.info(f"Revoked stream key for stream {stream_id}")
        return True
    else:
        logger.debug(f"Stream {stream_id} was already ended")
        return False
