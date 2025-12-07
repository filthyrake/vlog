"""Authentication middleware for Worker API."""

import asyncio
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from api.common import ensure_utc
from api.database import database, worker_api_keys, workers
from api.db_retry import fetch_one_with_retry
from config import TRUSTED_PROXIES

# Security event logger - separate from regular application logging
# Configure with appropriate handlers for security monitoring/SIEM integration
security_logger = logging.getLogger("security.auth")

# Standard logger for general operations
logger = logging.getLogger(__name__)

# API key header
api_key_header = APIKeyHeader(name="X-Worker-API-Key", auto_error=False)


def hash_api_key(key: str) -> str:
    """Hash an API key using SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def get_key_prefix(key: str) -> str:
    """Get the first 8 characters of an API key for efficient lookup."""
    return key[:8]


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

    # Extract prefix for efficient lookup (safe to log - not the full key)
    prefix = get_key_prefix(api_key)
    key_hash = hash_api_key(api_key)

    # Query database for matching key by prefix (non-revoked keys only)
    key_record = await fetch_one_with_retry(
        worker_api_keys.select()
        .where(worker_api_keys.c.key_prefix == prefix)
        .where(worker_api_keys.c.revoked_at.is_(None))
    )

    if not key_record:
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

    # Use timing-safe comparison to prevent timing attacks on the hash
    if not hmac.compare_digest(key_hash, key_record["key_hash"]):
        security_logger.warning(
            "Authentication failed: key hash mismatch",
            extra={
                "event": "auth_failure",
                "reason": "hash_mismatch",
                "key_prefix": prefix,
                **ctx,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid API key")

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
    worker = await fetch_one_with_retry(workers.select().where(workers.c.id == key_record["worker_id"]))

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
            **ctx,
        },
    )

    return dict(worker)


async def get_worker_by_id(worker_id: str) -> Optional[dict]:
    """Get a worker by its UUID."""
    worker = await fetch_one_with_retry(workers.select().where(workers.c.worker_id == worker_id))
    return dict(worker) if worker else None
