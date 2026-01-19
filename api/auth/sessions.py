"""
Session management for user authentication.

Implements session-based authentication with:
- Session tokens (short-lived, for API access)
- Refresh tokens (long-lived, for session rotation)
- Token family tracking for theft detection
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from api.auth.password import (
    generate_token,
    get_token_prefix,
    hash_token_fast,
    is_sha256_hash,
    verify_token,
    verify_token_fast,
)
from api.database import database, user_sessions, users
from config import (
    USER_MAX_SESSIONS,
    USER_REFRESH_TOKEN_EXPIRY_DAYS,
    USER_SESSION_EXPIRY_HOURS,
    USER_SESSION_GRACE_SECONDS,
)

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.auth")


class SessionError(Exception):
    """Base exception for session errors."""

    pass


class SessionExpiredError(SessionError):
    """Session has expired."""

    pass


class SessionRevokedError(SessionError):
    """Session has been revoked."""

    pass


class RefreshTokenReusedError(SessionError):
    """Refresh token was reused after rotation (potential theft)."""

    pass


async def create_user_session(
    user_id: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[str, str, datetime, datetime]:
    """
    Create a new session for a user.

    Uses a transaction to ensure session limit enforcement and creation
    are atomic, preventing race conditions from concurrent logins.

    Args:
        user_id: The user's ID
        ip_address: Client IP address for audit
        user_agent: Client user agent for audit

    Returns:
        Tuple of (session_token, refresh_token, expires_at, refresh_expires_at)
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=USER_SESSION_EXPIRY_HOURS)
    refresh_expires_at = now + timedelta(days=USER_REFRESH_TOKEN_EXPIRY_DAYS)

    # Generate tokens
    session_token = generate_token(48)  # 64-char token
    refresh_token = generate_token(48)
    refresh_family_id = str(uuid.uuid4())

    # Hash tokens for storage using SHA-256 (fast, tokens are high-entropy)
    session_token_hash = hash_token_fast(session_token)
    refresh_token_hash = hash_token_fast(refresh_token)

    # Store prefixes for indexed lookup
    session_token_prefix = get_token_prefix(session_token)
    refresh_token_prefix = get_token_prefix(refresh_token)

    session_id = str(uuid.uuid4())

    # Use transaction to ensure atomicity:
    # - Session limit check uses FOR UPDATE to lock rows
    # - Insert happens within same transaction
    # - Prevents concurrent logins from exceeding limit
    async with database.transaction():
        # Check session limit and cleanup old sessions (with row locking)
        await _enforce_session_limit(user_id)

        # Create session record
        await database.execute(
            user_sessions.insert().values(
                id=session_id,
                user_id=user_id,
                token_hash=session_token_hash,
                token_prefix=session_token_prefix,
                refresh_token_hash=refresh_token_hash,
                refresh_token_prefix=refresh_token_prefix,
                refresh_family_id=refresh_family_id,
                refresh_generation=0,
                expires_at=expires_at,
                refresh_expires_at=refresh_expires_at,
                ip_address=ip_address,
                user_agent=user_agent[:512] if user_agent else None,
                created_at=now,
            )
        )

    security_logger.info(
        "User session created",
        extra={
            "event": "session_created",
            "user_id": user_id,
            "session_id": session_id,
            "ip_address": ip_address,
            "expires_at": expires_at.isoformat(),
        },
    )

    return session_token, refresh_token, expires_at, refresh_expires_at


async def validate_session_token(
    session_token: str,
    allow_grace_period: bool = False,
) -> Optional[dict]:
    """
    Validate a session token and return the user.

    Args:
        session_token: The session token to validate
        allow_grace_period: Whether to allow expired sessions within grace period

    Returns:
        User record as dict if valid, None otherwise
    """
    if not session_token or len(session_token) < 8:
        return None

    now = datetime.now(timezone.utc)

    # Use prefix for efficient indexed lookup
    token_prefix = get_token_prefix(session_token)

    # Query sessions with matching prefix (typically 1 match)
    sessions = await database.fetch_all(
        user_sessions.select().where(
            user_sessions.c.token_prefix == token_prefix,
            user_sessions.c.revoked_at.is_(None),
        )
    )

    for session in sessions:
        token_hash = session["token_hash"]
        # Support both SHA-256 (new) and argon2id (legacy) hashes
        if is_sha256_hash(token_hash):
            if not verify_token_fast(session_token, token_hash):
                continue
        else:
            # Legacy argon2id hash - verify with slow method
            if not verify_token(session_token, token_hash):
                continue

        # Found matching session
        expires_at = session["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        # Check expiry
        if expires_at < now:
            if allow_grace_period:
                grace_expires = expires_at + timedelta(seconds=USER_SESSION_GRACE_SECONDS)
                if grace_expires < now:
                    return None  # Grace period also expired
            else:
                return None  # Expired

        # Get user
        user = await database.fetch_one(
            users.select().where(users.c.id == session["user_id"])
        )

        if not user:
            return None

        # Check user status
        if user["status"] != "active":
            return None

        # Check lockout
        if user["locked_until"]:
            locked_until = user["locked_until"]
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            if locked_until > now:
                return None

        # Update last used (non-blocking, but log failures for monitoring)
        try:
            await database.execute(
                user_sessions.update()
                .where(user_sessions.c.id == session["id"])
                .values(last_used_at=now)
            )
        except Exception as e:
            # Log but don't fail the request - this is non-critical
            # Repeated failures may indicate database issues
            logger.warning(
                "Failed to update session last_used_at: %s",
                str(e),
                extra={"session_id": session["id"]},
            )

        return dict(user) | {"session_id": session["id"]}

    return None


async def refresh_user_session(
    refresh_token: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[str, str, datetime, datetime]:
    """
    Refresh a session using a refresh token.

    Implements token rotation with family tracking for theft detection.

    Args:
        refresh_token: The refresh token
        ip_address: Client IP address for new session
        user_agent: Client user agent for new session

    Returns:
        Tuple of (new_session_token, new_refresh_token, expires_at, refresh_expires_at)

    Raises:
        SessionExpiredError: If refresh token is expired
        SessionRevokedError: If session was revoked
        RefreshTokenReusedError: If token was already rotated (potential theft)
    """
    if not refresh_token or len(refresh_token) < 8:
        raise SessionError("Invalid refresh token")

    now = datetime.now(timezone.utc)

    # Use prefix for efficient indexed lookup
    refresh_prefix = get_token_prefix(refresh_token)

    # Find session by refresh token prefix
    sessions = await database.fetch_all(
        user_sessions.select().where(
            user_sessions.c.refresh_token_prefix == refresh_prefix,
            user_sessions.c.refresh_token_hash.isnot(None),
        )
    )

    session = None
    for s in sessions:
        token_hash = s["refresh_token_hash"]
        # Support both SHA-256 (new) and argon2id (legacy) hashes
        if is_sha256_hash(token_hash):
            if verify_token_fast(refresh_token, token_hash):
                session = s
                break
        else:
            # Legacy argon2id hash
            if verify_token(refresh_token, token_hash):
                session = s
                break

    if not session:
        raise SessionError("Invalid refresh token")

    # Check if revoked
    if session["revoked_at"]:
        raise SessionRevokedError("Session was revoked")

    # Check expiry
    refresh_expires_at = session["refresh_expires_at"]
    if refresh_expires_at.tzinfo is None:
        refresh_expires_at = refresh_expires_at.replace(tzinfo=timezone.utc)

    if refresh_expires_at < now:
        raise SessionExpiredError("Refresh token expired")

    # Check for token reuse (theft detection)
    # If the refresh token hash matches but generation is higher than expected,
    # someone is using an old token - potential theft!
    # This would require storing the expected generation somewhere or checking
    # if a newer session exists in the same family

    family_sessions = await database.fetch_all(
        user_sessions.select()
        .where(user_sessions.c.refresh_family_id == session["refresh_family_id"])
        .where(user_sessions.c.refresh_generation > session["refresh_generation"])
        .where(user_sessions.c.revoked_at.is_(None))
    )

    if family_sessions:
        # Token was already rotated! Revoke entire family
        await _revoke_session_family(session["refresh_family_id"])
        security_logger.warning(
            "Refresh token reuse detected - session family revoked",
            extra={
                "event": "refresh_token_reuse",
                "family_id": session["refresh_family_id"],
                "user_id": session["user_id"],
                "ip_address": ip_address,
            },
        )
        raise RefreshTokenReusedError("Refresh token was already used")

    # Get user to verify still active
    user = await database.fetch_one(
        users.select().where(users.c.id == session["user_id"])
    )

    if not user or user["status"] != "active":
        raise SessionRevokedError("User account is not active")

    # Generate new tokens
    new_session_token = generate_token(48)
    new_refresh_token = generate_token(48)
    new_expires_at = now + timedelta(hours=USER_SESSION_EXPIRY_HOURS)
    new_refresh_expires_at = now + timedelta(days=USER_REFRESH_TOKEN_EXPIRY_DAYS)

    # Hash new tokens using SHA-256 (fast, tokens are high-entropy)
    new_session_token_hash = hash_token_fast(new_session_token)
    new_refresh_token_hash = hash_token_fast(new_refresh_token)

    # Store prefixes for indexed lookup
    new_session_token_prefix = get_token_prefix(new_session_token)
    new_refresh_token_prefix = get_token_prefix(new_refresh_token)

    new_session_id = str(uuid.uuid4())

    # Use transaction to ensure atomicity:
    # If new session creation fails, old session remains valid
    async with database.transaction():
        # Revoke old session
        await database.execute(
            user_sessions.update()
            .where(user_sessions.c.id == session["id"])
            .values(revoked_at=now)
        )

        # Create new session with incremented generation
        await database.execute(
            user_sessions.insert().values(
                id=new_session_id,
                user_id=session["user_id"],
                token_hash=new_session_token_hash,
                token_prefix=new_session_token_prefix,
                refresh_token_hash=new_refresh_token_hash,
                refresh_token_prefix=new_refresh_token_prefix,
                refresh_family_id=session["refresh_family_id"],
                refresh_generation=session["refresh_generation"] + 1,
                expires_at=new_expires_at,
                refresh_expires_at=new_refresh_expires_at,
                ip_address=ip_address,
                user_agent=user_agent[:512] if user_agent else None,
                created_at=now,
            )
        )

    security_logger.info(
        "Session refreshed",
        extra={
            "event": "session_refreshed",
            "user_id": session["user_id"],
            "old_session_id": session["id"],
            "new_session_id": new_session_id,
            "generation": session["refresh_generation"] + 1,
            "ip_address": ip_address,
        },
    )

    return new_session_token, new_refresh_token, new_expires_at, new_refresh_expires_at


async def invalidate_session(session_id: str) -> bool:
    """
    Invalidate a specific session.

    Args:
        session_id: The session ID to invalidate

    Returns:
        True if session was found and invalidated
    """
    now = datetime.now(timezone.utc)
    result = await database.execute(
        user_sessions.update()
        .where(user_sessions.c.id == session_id)
        .where(user_sessions.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return result > 0


async def invalidate_user_sessions(user_id: str, except_session_id: Optional[str] = None) -> int:
    """
    Invalidate all sessions for a user.

    Args:
        user_id: The user's ID
        except_session_id: Optional session ID to keep (for logout elsewhere)

    Returns:
        Number of sessions invalidated
    """
    now = datetime.now(timezone.utc)
    query = (
        user_sessions.update()
        .where(user_sessions.c.user_id == user_id)
        .where(user_sessions.c.revoked_at.is_(None))
    )

    if except_session_id:
        query = query.where(user_sessions.c.id != except_session_id)

    result = await database.execute(query.values(revoked_at=now))
    return result


async def get_user_sessions(user_id: str) -> list[dict]:
    """
    Get all active sessions for a user.

    Args:
        user_id: The user's ID

    Returns:
        List of session records
    """
    sessions = await database.fetch_all(
        user_sessions.select()
        .where(user_sessions.c.user_id == user_id)
        .where(user_sessions.c.revoked_at.is_(None))
        .where(user_sessions.c.expires_at > datetime.now(timezone.utc))
        .order_by(user_sessions.c.created_at.desc())
    )
    return [dict(s) for s in sessions]


async def cleanup_expired_sessions() -> int:
    """
    Clean up expired sessions.

    Returns:
        Number of sessions deleted
    """
    now = datetime.now(timezone.utc)

    # Delete sessions where both session and refresh have expired
    # Keep revoked sessions for a short time for audit trail
    cutoff = now - timedelta(days=7)  # Keep audit trail for 7 days

    result = await database.execute(
        user_sessions.delete().where(
            (user_sessions.c.refresh_expires_at < now)
            | (
                (user_sessions.c.revoked_at.isnot(None))
                & (user_sessions.c.revoked_at < cutoff)
            )
        )
    )

    if result > 0:
        logger.info(f"Cleaned up {result} expired sessions")

    return result


async def _enforce_session_limit(user_id: str) -> None:
    """
    Enforce maximum sessions per user by revoking oldest sessions.

    Uses row-level locking (FOR UPDATE) to prevent race conditions
    when multiple concurrent logins occur for the same user.
    """
    now = datetime.now(timezone.utc)

    # Use FOR UPDATE to lock rows and prevent concurrent session creation
    # from exceeding the limit. The lock is held until transaction commits.
    # Note: This query uses raw SQL for FOR UPDATE support
    active_sessions = await database.fetch_all(
        """
        SELECT id, created_at FROM user_sessions
        WHERE user_id = :user_id
          AND revoked_at IS NULL
          AND expires_at > :now
        ORDER BY created_at DESC
        FOR UPDATE
        """,
        {"user_id": user_id, "now": now},
    )

    if len(active_sessions) >= USER_MAX_SESSIONS:
        # Revoke oldest sessions to make room (keep newest USER_MAX_SESSIONS - 1)
        sessions_to_revoke = active_sessions[USER_MAX_SESSIONS - 1 :]
        for session in sessions_to_revoke:
            await database.execute(
                user_sessions.update()
                .where(user_sessions.c.id == session["id"])
                .values(revoked_at=now)
            )


async def _revoke_session_family(family_id: str) -> int:
    """
    Revoke all sessions in a token family.

    Used when token theft is detected.
    """
    now = datetime.now(timezone.utc)
    result = await database.execute(
        user_sessions.update()
        .where(user_sessions.c.refresh_family_id == family_id)
        .where(user_sessions.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return result
