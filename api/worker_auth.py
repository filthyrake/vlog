"""Authentication middleware for Worker API."""

import asyncio
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Tuple

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, Response, Security
from fastapi.security import APIKeyHeader

from api.common import ensure_utc
from api.database import database, worker_api_keys, workers
from api.settings_service import get_setting
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


class KeyExpirationStatus(str, Enum):
    """Status of API key expiration check (Issue #226)."""

    VALID = "valid"  # Key is valid and not near expiration
    EXPIRING_SOON = "expiring_soon"  # Key will expire within warning period
    IN_GRACE_PERIOD = "in_grace_period"  # Key is expired but within grace period
    EXPIRED = "expired"  # Key is expired and past grace period


# Rotation cooldown in seconds (5 minutes per security review)
ROTATION_COOLDOWN_SECONDS = 300

# Default settings for key expiration (used if not set in database)
DEFAULT_EXPIRATION_DAYS = 90
DEFAULT_GRACE_PERIOD_HOURS = 4
DEFAULT_OVERLAP_HOURS = 2
DEFAULT_WARNING_DAYS = 14


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


async def _check_key_expiration_with_grace(
    expires_at: Optional[datetime],
    grace_period_hours: Optional[int] = None,
    warning_days: Optional[int] = None,
) -> Tuple[KeyExpirationStatus, Optional[datetime]]:
    """
    Check key expiration status with grace period logic (Issue #226).

    Args:
        expires_at: Key expiration timestamp (None = never expires)
        grace_period_hours: Hours after expiration key is still valid (defaults to setting)
        warning_days: Days before expiration to warn (defaults to setting)

    Returns:
        Tuple of (status, grace_period_ends_at)
        - status: KeyExpirationStatus enum value
        - grace_period_ends_at: When grace period ends (None if not in grace period)
    """
    if expires_at is None:
        return KeyExpirationStatus.VALID, None

    now = datetime.now(timezone.utc)
    expires_at = ensure_utc(expires_at)

    # Get settings from database (with defaults)
    if grace_period_hours is None:
        grace_period_hours = await get_setting(
            "workers.api_key_grace_period_hours", DEFAULT_GRACE_PERIOD_HOURS
        )
    if warning_days is None:
        warning_days = await get_setting(
            "workers.api_key_expiration_warning_days", DEFAULT_WARNING_DAYS
        )

    # Calculate grace period end
    grace_period_ends = expires_at + timedelta(hours=grace_period_hours)

    # Check status
    if now < expires_at:
        # Key hasn't expired yet
        days_until = (expires_at - now).days
        if days_until <= warning_days:
            return KeyExpirationStatus.EXPIRING_SOON, None
        return KeyExpirationStatus.VALID, None
    elif now < grace_period_ends:
        # Key is expired but within grace period
        return KeyExpirationStatus.IN_GRACE_PERIOD, grace_period_ends
    else:
        # Key is fully expired (past grace period)
        return KeyExpirationStatus.EXPIRED, None


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

    # Check expiration with grace period (Issue #226)
    now = datetime.now(timezone.utc)
    expiration_status, grace_ends = await _check_key_expiration_with_grace(
        key_record["expires_at"]
    )

    # Store whether key is expiring for header injection
    key_expiring = False

    if expiration_status == KeyExpirationStatus.EXPIRED:
        security_logger.warning(
            "Authentication failed: expired API key (past grace period)",
            extra={
                "event": "auth_failure",
                "reason": "expired_key",
                "key_prefix": prefix,
                "worker_id": key_record["worker_id"],
                "hash_version": _get_hash_version(key_record),
                "expired_at": key_record["expires_at"].isoformat() if key_record["expires_at"] else None,
                **ctx,
            },
        )
        raise HTTPException(
            status_code=401,
            detail="API key expired. Rotate using: vlog worker rotate <worker-id>",
        )
    elif expiration_status == KeyExpirationStatus.IN_GRACE_PERIOD:
        # Allow but warn - key is expired but within grace period
        security_logger.warning(
            "API key in grace period - will expire soon",
            extra={
                "event": "auth_grace_period",
                "key_prefix": prefix,
                "worker_id": key_record["worker_id"],
                "expired_at": key_record["expires_at"].isoformat() if key_record["expires_at"] else None,
                "grace_ends_at": grace_ends.isoformat() if grace_ends else None,
                **ctx,
            },
        )
        key_expiring = True
    elif expiration_status == KeyExpirationStatus.EXPIRING_SOON:
        # Log that key is expiring soon
        logger.info(
            f"API key expiring soon for worker {key_record['worker_id']}",
        )
        key_expiring = True

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

    # Return worker dict with expiration info for response header injection
    worker_dict = dict(worker)
    worker_dict["_key_expiring"] = key_expiring
    worker_dict["_key_id"] = key_record["id"]
    return worker_dict


async def get_worker_by_id(worker_id: str) -> Optional[dict]:
    """Get a worker by its UUID."""
    worker = await database.fetch_one(workers.select().where(workers.c.worker_id == worker_id))
    return dict(worker) if worker else None


async def rotate_worker_key(
    worker_db_id: int,
    worker_uuid: str,
    revoke_old: bool = False,
) -> Tuple[str, datetime, Optional[datetime], int]:
    """
    Rotate a worker's API key with overlap period (Issue #226).

    Creates a new API key for the worker and optionally schedules the old key
    for expiration after the overlap period.

    Args:
        worker_db_id: Database ID of the worker (integer PK)
        worker_uuid: UUID of the worker (for logging)
        revoke_old: If True, revoke old key immediately instead of scheduling expiration

    Returns:
        Tuple of (new_api_key, new_expires_at, old_key_expires_at, overlap_hours)

    Raises:
        HTTPException(429): If rotation was attempted too recently (cooldown)
        HTTPException(404): If no active key found for worker
    """
    now = datetime.now(timezone.utc)

    # Get settings
    expiration_days = await get_setting("workers.api_key_expiration_days", DEFAULT_EXPIRATION_DAYS)
    overlap_hours = await get_setting("workers.api_key_rotation_overlap_hours", DEFAULT_OVERLAP_HOURS)

    # Check cooldown - find the most recent key for this worker
    latest_key = await database.fetch_one(
        worker_api_keys.select()
        .where(worker_api_keys.c.worker_id == worker_db_id)
        .order_by(worker_api_keys.c.created_at.desc())
        .limit(1)
    )

    if not latest_key:
        raise HTTPException(status_code=404, detail="No API key found for worker")

    # Check cooldown (5 minutes between rotations)
    key_created_at = ensure_utc(latest_key["created_at"])
    seconds_since_last = (now - key_created_at).total_seconds()
    if seconds_since_last < ROTATION_COOLDOWN_SECONDS:
        remaining = int(ROTATION_COOLDOWN_SECONDS - seconds_since_last)
        raise HTTPException(
            status_code=429,
            detail=f"Rotation cooldown active. Please wait {remaining} seconds.",
            headers={"Retry-After": str(remaining)},
        )

    # Generate new key
    new_api_key = secrets.token_urlsafe(32)  # 256-bit key
    key_hash, hash_version = hash_api_key(new_api_key)
    key_prefix = get_key_prefix(new_api_key)

    # Calculate expiration for new key
    new_expires_at = None
    if expiration_days > 0:
        new_expires_at = now + timedelta(days=expiration_days)

    # Calculate old key expiration (either immediate revoke or scheduled)
    old_key_expires_at = None
    if revoke_old:
        # Revoke immediately
        old_key_expires_at = now
    else:
        # Schedule expiration after overlap period
        old_key_expires_at = now + timedelta(hours=overlap_hours)

    # Find the current active key (non-revoked, non-expired)
    active_key = await database.fetch_one(
        worker_api_keys.select()
        .where(worker_api_keys.c.worker_id == worker_db_id)
        .where(worker_api_keys.c.revoked_at.is_(None))
        .order_by(worker_api_keys.c.created_at.desc())
        .limit(1)
    )

    if not active_key:
        raise HTTPException(status_code=404, detail="No active API key found for worker")

    old_key_id = active_key["id"]

    # Use transaction for atomicity
    async with database.transaction():
        # Create new key with reference to old key (rotated_from)
        await database.execute(
            worker_api_keys.insert().values(
                worker_id=worker_db_id,
                key_hash=key_hash,
                hash_version=hash_version,
                key_prefix=key_prefix,
                created_at=now,
                expires_at=new_expires_at,
                rotated_from=old_key_id,
            )
        )

        # Update old key - either revoke or set expiration
        if revoke_old:
            await database.execute(
                worker_api_keys.update()
                .where(worker_api_keys.c.id == old_key_id)
                .values(revoked_at=now)
            )
        else:
            # Set expires_at if not already set, or update if new expiration is sooner
            await database.execute(
                worker_api_keys.update()
                .where(worker_api_keys.c.id == old_key_id)
                .values(expires_at=old_key_expires_at)
            )

    security_logger.info(
        "API key rotated",
        extra={
            "event": "key_rotate",
            "worker_id": worker_uuid,
            "old_key_id": old_key_id,
            "revoked_immediately": revoke_old,
            "overlap_hours": overlap_hours,
        },
    )

    return new_api_key, new_expires_at, old_key_expires_at, overlap_hours


async def get_expiring_keys(
    days: int = DEFAULT_WARNING_DAYS,
    include_grace: bool = False,
    page: int = 1,
    per_page: int = 50,
) -> Tuple[list, int]:
    """
    Get list of API keys expiring within the specified number of days.

    Args:
        days: Number of days to look ahead for expiring keys
        include_grace: If True, also include keys in grace period (already expired)
        page: Page number for pagination
        per_page: Number of results per page

    Returns:
        Tuple of (list of expiring key info dicts, total count)
    """
    import sqlalchemy as sa

    now = datetime.now(timezone.utc)
    threshold = now + timedelta(days=days)

    # Get grace period for calculating if key is in grace period
    grace_period_hours = await get_setting(
        "workers.api_key_grace_period_hours", DEFAULT_GRACE_PERIOD_HOURS
    )

    # Base query - keys that are not revoked and have an expiration date
    base_conditions = [
        worker_api_keys.c.revoked_at.is_(None),
        worker_api_keys.c.expires_at.isnot(None),
    ]

    if include_grace:
        # Include keys expired up to grace_period_hours ago
        grace_start = now - timedelta(hours=grace_period_hours)
        base_conditions.append(worker_api_keys.c.expires_at > grace_start)
        base_conditions.append(worker_api_keys.c.expires_at <= threshold)
    else:
        # Only future expirations
        base_conditions.append(worker_api_keys.c.expires_at > now)
        base_conditions.append(worker_api_keys.c.expires_at <= threshold)

    # Count total
    count_query = sa.select(sa.func.count()).select_from(worker_api_keys).where(sa.and_(*base_conditions))
    total_count = await database.fetch_val(count_query)

    # Fetch paginated results with worker info
    offset = (page - 1) * per_page
    query = (
        sa.select(
            worker_api_keys.c.id.label("key_id"),
            worker_api_keys.c.expires_at,
            workers.c.worker_id,
            workers.c.worker_name,
        )
        .select_from(worker_api_keys.join(workers, worker_api_keys.c.worker_id == workers.c.id))
        .where(sa.and_(*base_conditions))
        .order_by(worker_api_keys.c.expires_at.asc())
        .offset(offset)
        .limit(per_page)
    )

    rows = await database.fetch_all(query)

    results = []
    for row in rows:
        expires_at = ensure_utc(row["expires_at"])
        days_until = (expires_at - now).days
        in_grace = expires_at < now  # If already expired, it's in grace period

        results.append({
            "worker_id": row["worker_id"],
            "worker_name": row["worker_name"],
            "key_id": row["key_id"],
            "expires_at": expires_at,
            "days_until_expiration": max(0, days_until),
            "in_grace_period": in_grace,
        })

    return results, total_count or 0


async def bulk_revoke_expired_keys(
    dry_run: bool = True,
    include_grace_period: bool = False,
) -> Tuple[int, list]:
    """
    Revoke expired API keys in bulk (Issue #226).

    Args:
        dry_run: If True, only return count without actually revoking
        include_grace_period: If False, only revoke keys past grace period

    Returns:
        Tuple of (count of revoked/would-be-revoked keys, list of affected worker UUIDs)
    """
    import sqlalchemy as sa

    now = datetime.now(timezone.utc)

    # Get grace period setting
    grace_period_hours = await get_setting(
        "workers.api_key_grace_period_hours", DEFAULT_GRACE_PERIOD_HOURS
    )

    # Determine cutoff time
    if include_grace_period:
        # Revoke anything that's expired (regardless of grace period)
        cutoff = now
    else:
        # Only revoke keys past grace period
        cutoff = now - timedelta(hours=grace_period_hours)

    # Find expired keys
    conditions = [
        worker_api_keys.c.revoked_at.is_(None),
        worker_api_keys.c.expires_at.isnot(None),
        worker_api_keys.c.expires_at < cutoff,
    ]

    # Get list of affected worker UUIDs before revoking
    affected_query = (
        sa.select(workers.c.worker_id)
        .select_from(worker_api_keys.join(workers, worker_api_keys.c.worker_id == workers.c.id))
        .where(sa.and_(*conditions))
        .distinct()
    )
    affected_rows = await database.fetch_all(affected_query)
    affected_worker_ids = [row["worker_id"] for row in affected_rows]

    # Count keys to revoke
    count_query = sa.select(sa.func.count()).select_from(worker_api_keys).where(sa.and_(*conditions))
    count = await database.fetch_val(count_query) or 0

    if not dry_run and count > 0:
        # Actually revoke the keys
        await database.execute(
            worker_api_keys.update()
            .where(sa.and_(*conditions))
            .values(revoked_at=now)
        )

        security_logger.info(
            "Bulk key revocation completed",
            extra={
                "event": "bulk_revoke",
                "count": count,
                "include_grace_period": include_grace_period,
                "affected_workers": len(affected_worker_ids),
            },
        )

    return count, affected_worker_ids
