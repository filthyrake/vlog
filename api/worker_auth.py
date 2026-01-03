"""Authentication middleware for Worker API."""

import asyncio
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from api.common import ensure_utc
from api.database import database, worker_api_keys, workers
from config import TRUSTED_PROXIES

# Security event logger - separate from regular application logging
# Configure with appropriate handlers for security monitoring/SIEM integration
security_logger = logging.getLogger("security.auth")

# Standard logger for general operations
logger = logging.getLogger(__name__)

# API key header
api_key_header = APIKeyHeader(name="X-Worker-API-Key", auto_error=False)

# Hash version constants (Issue #445)
# Used to support dual-format verification during migration from SHA-256 to argon2id
HASH_VERSION_SHA256 = 1  # Legacy - fast, GPU-vulnerable
HASH_VERSION_ARGON2 = 2  # Current - memory-hard, GPU-resistant

# Explicit argon2 parameters (OWASP recommended minimums)
# These are stored in the hash output, so verification works even if defaults change
_password_hasher = PasswordHasher(
    time_cost=3,  # iterations
    memory_cost=65536,  # 64MB memory
    parallelism=4,  # threads
)


def hash_api_key(key: str) -> Tuple[str, int]:
    """
    Hash an API key using argon2id.

    Returns:
        Tuple of (hash_string, hash_version)
        - hash_string: The argon2id hash with embedded salt and parameters
        - hash_version: HASH_VERSION_ARGON2 (2)
    """
    return _password_hasher.hash(key), HASH_VERSION_ARGON2


def hash_api_key_legacy(key: str) -> str:
    """
    Hash an API key using SHA-256 (legacy method).

    This is only used for backward compatibility with existing keys.
    New keys should use hash_api_key() which uses argon2id.
    """
    return hashlib.sha256(key.encode()).hexdigest()


def verify_api_key_hash(key: str, stored_hash: str, hash_version: int, key_prefix: Optional[str] = None) -> bool:
    """
    Verify an API key against a stored hash using the appropriate algorithm.

    Args:
        key: The plaintext API key to verify
        stored_hash: The hash stored in the database
        hash_version: The algorithm version (1=SHA-256, 2=argon2id)
        key_prefix: Optional key prefix for error logging context

    Returns:
        True if the key matches the hash, False otherwise
    """
    if hash_version == HASH_VERSION_ARGON2:
        try:
            _password_hasher.verify(stored_hash, key)
            return True
        except VerifyMismatchError:
            return False
        except InvalidHashError:
            # Malformed hash in database - log and fail
            security_logger.error(
                "Invalid argon2 hash format in database",
                extra={
                    "event": "auth_error",
                    "reason": "invalid_hash_format",
                    "key_prefix": key_prefix,
                },
            )
            return False
    elif hash_version == HASH_VERSION_SHA256:
        # Legacy SHA-256 verification with timing-safe comparison
        computed = hashlib.sha256(key.encode()).hexdigest()
        return hmac.compare_digest(computed, stored_hash)
    else:
        # Unknown version - fail closed, don't default to legacy
        security_logger.error(
            f"Unknown hash_version in database: {hash_version}",
            extra={
                "event": "auth_error",
                "reason": "unknown_hash_version",
                "hash_version": hash_version,
                "key_prefix": key_prefix,
            },
        )
        return False


def get_key_prefix(key: str) -> str:
    """Get the first 8 characters of an API key for efficient lookup."""
    return key[:8]


def _get_hash_version(record: dict) -> int:
    """Safely get hash_version from a database record with fallback to SHA-256."""
    try:
        return record["hash_version"]
    except (KeyError, TypeError):
        return HASH_VERSION_SHA256


async def authenticate_api_key(api_key: str, request: Optional[Request] = None) -> dict:
    """
    Authenticate an API key and return the key record.

    This is a shared helper used by both verify_worker_key() and admin endpoints.
    It handles the prefix-based lookup and hash verification for both argon2id
    and legacy SHA-256 keys.

    Args:
        api_key: The plaintext API key from the request header
        request: Optional request for logging context

    Returns:
        The key record as a dict on success

    Raises:
        HTTPException(401) if key is invalid, too short, or revoked
    """
    ctx = _get_request_context(request)

    # Validate API key format - must be at least 8 chars for prefix extraction
    if not api_key or len(api_key) < 8:
        security_logger.warning(
            "Authentication failed: invalid API key format",
            extra={
                "event": "auth_failure",
                "reason": "invalid_key_format",
                "key_length": len(api_key) if api_key else 0,
                **ctx,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid API key")

    prefix = get_key_prefix(api_key)

    # Query database for ALL matching keys by prefix (non-revoked only)
    # Multiple keys may share a prefix (1 in 2^32 collision chance per key)
    # We must check each candidate to find the matching one
    key_records = await database.fetch_all(
        worker_api_keys.select()
        .where(worker_api_keys.c.key_prefix == prefix)
        .where(worker_api_keys.c.revoked_at.is_(None))
    )

    if not key_records:
        security_logger.warning(
            "Authentication failed: invalid API key",
            extra={
                "event": "auth_failure",
                "reason": "invalid_key",
                "key_prefix": prefix,
                **ctx,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Try each candidate key with matching prefix
    for key_record in key_records:
        hash_version = _get_hash_version(key_record)
        if verify_api_key_hash(api_key, key_record["key_hash"], hash_version, key_prefix=prefix):
            # Found matching key
            return dict(key_record)

    # None of the candidates matched - log with first candidate's version for debugging
    security_logger.warning(
        "Authentication failed: key hash mismatch",
        extra={
            "event": "auth_failure",
            "reason": "hash_mismatch",
            "key_prefix": prefix,
            "candidates_checked": len(key_records),
            **ctx,
        },
    )
    raise HTTPException(status_code=401, detail="Invalid API key")


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
        "ip_address": effective_ip,  # The IP to use for security decisions
        "direct_ip": direct_ip,  # Always the direct connection IP
        "forwarded_for": forwarded_for_ip,  # X-Forwarded-For value (may be spoofed)
        "user_agent": user_agent,
    }


async def verify_worker_key(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
) -> dict:
    """
    Verify worker API key and return worker info.

    Raises HTTPException if the key is invalid, expired, or revoked.
    Returns the worker record as a dict on success.
    """
    ctx = _get_request_context(request)

    if not api_key:
        security_logger.warning(
            "Authentication failed: missing API key",
            extra={
                "event": "auth_failure",
                "reason": "missing_key",
                **ctx,  # includes ip_address, direct_ip, forwarded_for, user_agent
            },
        )
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Include X-Worker-API-Key header.",
        )

    # Use shared helper for key authentication (handles both argon2 and SHA-256)
    key_record = await authenticate_api_key(api_key, request)
    prefix = get_key_prefix(api_key)

    # Check expiration (handle both timezone-aware and naive datetimes from SQLite)
    now = datetime.now(timezone.utc)
    if key_record["expires_at"]:
        expires_at = ensure_utc(key_record["expires_at"])
        if expires_at < now:
            security_logger.warning(
                "Authentication failed: expired API key",
                extra={
                    "event": "auth_failure",
                    "reason": "expired_key",
                    "key_prefix": prefix,
                    "worker_id": key_record["worker_id"],
                    "hash_version": _get_hash_version(key_record),
                    "expired_at": expires_at.isoformat(),
                    **ctx,
                },
            )
            raise HTTPException(status_code=401, detail="API key expired")

    # Update last_used_at in background (non-blocking)
    async def update_last_used():
        try:
            await database.execute(
                worker_api_keys.update().where(worker_api_keys.c.id == key_record["id"]).values(last_used_at=now)
            )
        except Exception as e:
            # Log failure but don't raise - last_used tracking is non-critical
            logger.debug(f"Failed to update last_used_at for worker API key: {e}")

    asyncio.create_task(update_last_used())

    # Get worker info
    worker = await database.fetch_one(workers.select().where(workers.c.id == key_record["worker_id"]))

    if not worker:
        security_logger.warning(
            "Authentication failed: worker not found",
            extra={
                "event": "auth_failure",
                "reason": "worker_not_found",
                "key_prefix": prefix,
                "worker_id": key_record["worker_id"],
                **ctx,
            },
        )
        raise HTTPException(status_code=401, detail="Worker not found")

    if worker["status"] == "disabled":
        security_logger.warning(
            "Authentication failed: worker disabled",
            extra={
                "event": "auth_failure",
                "reason": "worker_disabled",
                "worker_id": worker["worker_id"],
                "worker_name": worker["worker_name"],
                **ctx,
            },
        )
        raise HTTPException(status_code=403, detail="Worker is disabled")

    # Log successful authentication
    security_logger.info(
        "Authentication successful",
        extra={
            "event": "auth_success",
            "worker_id": worker["worker_id"],
            "worker_name": worker["worker_name"],
            "hash_version": _get_hash_version(key_record),
            **ctx,
        },
    )

    return dict(worker)


async def get_worker_by_id(worker_id: str) -> Optional[dict]:
    """Get a worker by its UUID."""
    worker = await database.fetch_one(workers.select().where(workers.c.worker_id == worker_id))
    return dict(worker) if worker else None
