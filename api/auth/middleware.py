"""
Authentication middleware and FastAPI dependencies.

Provides dependency injection for:
- Getting current authenticated user
- Requiring specific permissions
- API key authentication
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import Cookie, Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from api.auth.password import get_token_prefix, is_sha256_hash, verify_token, verify_token_fast
from api.auth.permissions import Permission, Role, check_ownership_permission, has_permission
from api.auth.sessions import validate_session_token
from api.database import database, user_api_keys, users
from config import TRUSTED_PROXIES

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.auth")

# Cookie name for session token
SESSION_COOKIE_NAME = "vlog_session"
REFRESH_COOKIE_NAME = "vlog_refresh"

# API key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting trusted proxies."""
    direct_ip = request.client.host if request.client else "unknown"

    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for and direct_ip in TRUSTED_PROXIES:
        return forwarded_for.split(",")[0].strip()

    return direct_ip


async def get_current_user(
    request: Request,
    session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    api_key: Optional[str] = Security(api_key_header),
) -> Optional[dict]:
    """
    Get the current authenticated user from session cookie or API key.

    This is a non-enforcing dependency - returns None if not authenticated.
    Use require_auth() for endpoints that require authentication.

    Args:
        request: The FastAPI request
        session_token: Session token from cookie
        api_key: API key from header

    Returns:
        User record as dict, or None if not authenticated
    """
    # Try session token first
    if session_token:
        user = await validate_session_token(session_token)
        if user:
            return user

    # Try API key
    if api_key:
        user = await _authenticate_api_key(api_key, request)
        if user:
            return user

    return None


async def require_auth(
    request: Request,
    session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    api_key: Optional[str] = Security(api_key_header),
) -> dict:
    """
    Require authentication - raises 401 if not authenticated.

    Args:
        request: The FastAPI request
        session_token: Session token from cookie
        api_key: API key from header

    Returns:
        User record as dict

    Raises:
        HTTPException: 401 if not authenticated
    """
    user = await get_current_user(request, session_token, api_key)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def require_permission(permission: Permission) -> Callable:
    """
    Create a dependency that requires a specific permission.

    Usage:
        @router.post("/videos")
        async def create_video(user: dict = Depends(require_permission(Permission.VIDEO_CREATE))):
            ...

    Args:
        permission: The required permission

    Returns:
        A FastAPI dependency function
    """

    async def permission_checker(
        request: Request,
        session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
        api_key: Optional[str] = Security(api_key_header),
    ) -> dict:
        user = await require_auth(request, session_token, api_key)

        role = Role(user["role"])
        if not has_permission(role, permission):
            security_logger.warning(
                "Permission denied",
                extra={
                    "event": "permission_denied",
                    "user_id": user["id"],
                    "username": user["username"],
                    "role": user["role"],
                    "permission": permission.value,
                    "ip_address": _get_client_ip(request),
                },
            )
            raise HTTPException(
                status_code=403,
                detail=f"Permission denied: {permission.value}",
            )

        return user

    return permission_checker


def require_role(role: Role) -> Callable:
    """
    Create a dependency that requires a specific role or higher.

    Role hierarchy: admin > editor > viewer

    Args:
        role: The minimum required role

    Returns:
        A FastAPI dependency function
    """
    role_hierarchy = {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2}

    async def role_checker(
        request: Request,
        session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
        api_key: Optional[str] = Security(api_key_header),
    ) -> dict:
        user = await require_auth(request, session_token, api_key)

        user_role = Role(user["role"])
        required_level = role_hierarchy.get(role, 999)
        user_level = role_hierarchy.get(user_role, -1)

        if user_level < required_level:
            security_logger.warning(
                "Insufficient role",
                extra={
                    "event": "role_denied",
                    "user_id": user["id"],
                    "username": user["username"],
                    "user_role": user["role"],
                    "required_role": role.value,
                    "ip_address": _get_client_ip(request),
                },
            )
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role.value}' or higher required",
            )

        return user

    return role_checker


async def require_ownership_or_permission(
    resource_owner_id: Optional[str],
    permission: Permission,
    any_permission: Permission,
    user: dict,
) -> bool:
    """
    Check if user owns resource or has permission to access any.

    Args:
        resource_owner_id: The owner ID of the resource
        permission: The base permission (e.g., VIDEO_UPDATE)
        any_permission: The "any" permission (e.g., VIDEO_UPDATE_ANY)
        user: The current user

    Returns:
        True if authorized

    Raises:
        HTTPException: 403 if not authorized
    """
    role = Role(user["role"])

    # Check for "any" permission (admin level)
    if has_permission(role, any_permission):
        return True

    # Check ownership + base permission
    if has_permission(role, permission):
        if resource_owner_id is None or resource_owner_id == user["id"]:
            return True

    raise HTTPException(
        status_code=403,
        detail="You don't have permission to access this resource",
    )


async def _authenticate_api_key(api_key: str, request: Request) -> Optional[dict]:
    """
    Authenticate a user API key.

    Args:
        api_key: The API key to authenticate
        request: The request for logging context

    Returns:
        User record if valid, None otherwise
    """
    if not api_key or len(api_key) < 8:
        return None

    ip_address = _get_client_ip(request)
    prefix = get_token_prefix(api_key)
    now = datetime.now(timezone.utc)

    # Find keys with matching prefix
    key_records = await database.fetch_all(
        user_api_keys.select()
        .where(user_api_keys.c.key_prefix == prefix)
        .where(user_api_keys.c.revoked_at.is_(None))
    )

    for key_record in key_records:
        key_hash = key_record["key_hash"]
        # Support both SHA-256 (new) and argon2id (legacy) hashes
        if is_sha256_hash(key_hash):
            if not verify_token_fast(api_key, key_hash):
                continue
        else:
            # Legacy argon2id hash
            if not verify_token(api_key, key_hash):
                continue

        # Found matching key - check expiry
        if key_record["expires_at"]:
            expires_at = key_record["expires_at"]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < now:
                security_logger.warning(
                    "API key expired",
                    extra={
                        "event": "api_key_expired",
                        "key_prefix": prefix,
                        "user_id": key_record["user_id"],
                        "ip_address": ip_address,
                    },
                )
                return None

        # Get user
        user = await database.fetch_one(
            users.select().where(users.c.id == key_record["user_id"])
        )

        if not user:
            return None

        if user["status"] != "active":
            security_logger.warning(
                "API key for inactive user",
                extra={
                    "event": "api_key_inactive_user",
                    "key_prefix": prefix,
                    "user_id": user["id"],
                    "status": user["status"],
                    "ip_address": ip_address,
                },
            )
            return None

        # Update last used (non-blocking)
        asyncio.create_task(_update_api_key_last_used(key_record["id"]))

        security_logger.info(
            "API key authenticated",
            extra={
                "event": "api_key_auth",
                "user_id": user["id"],
                "username": user["username"],
                "key_name": key_record["name"],
                "ip_address": ip_address,
            },
        )

        return dict(user) | {"api_key_id": key_record["id"]}

    security_logger.warning(
        "Invalid API key",
        extra={
            "event": "api_key_invalid",
            "key_prefix": prefix,
            "ip_address": ip_address,
        },
    )
    return None


async def _update_api_key_last_used(key_id: str) -> None:
    """Update API key last_used_at in background."""
    try:
        await database.execute(
            user_api_keys.update()
            .where(user_api_keys.c.id == key_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
    except Exception:
        pass  # Non-critical
