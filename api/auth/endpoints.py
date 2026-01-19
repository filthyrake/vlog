"""
Authentication API endpoints.

Provides endpoints for:
- Login/logout
- Session management
- Password reset
- Profile management
"""

import hmac
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from api.auth.middleware import (
    REFRESH_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    _get_client_ip,
    require_auth,
)
from api.auth.password import (
    generate_token,
    hash_password,
    hash_token,
    validate_password_strength,
    verify_password,
)
from api.auth.sessions import (
    RefreshTokenReusedError,
    SessionError,
    SessionExpiredError,
    SessionRevokedError,
    create_user_session,
    get_user_sessions,
    invalidate_session,
    invalidate_user_sessions,
    refresh_user_session,
)
from api.database import database, password_reset_tokens, users
from config import (
    LOGIN_LOCKOUT_DURATION_MINUTES,
    LOGIN_LOCKOUT_THRESHOLD,
    PASSWORD_RESET_EXPIRY_HOURS,
    SECURE_COOKIES,
    SESSION_SECRET_KEY,
)

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.auth")

router = APIRouter(prefix="/auth", tags=["Authentication"])


# =============================================================================
# Request/Response Models
# =============================================================================


class LoginRequest(BaseModel):
    """Login request body."""

    username_or_email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    remember: bool = False  # Extended session


class LoginResponse(BaseModel):
    """Login response."""

    user_id: str
    username: str
    email: str
    display_name: Optional[str]
    role: str
    expires_at: datetime


class AuthCheckUser(BaseModel):
    """User info for auth check response."""

    id: str
    username: str
    email: str
    display_name: Optional[str] = None
    role: str
    avatar_url: Optional[str] = None
    permissions: list[str] = []


class AuthCheckResponse(BaseModel):
    """Auth check response."""

    authenticated: bool
    auth_required: bool = True
    auth_mode: str = "user"
    oidc_enabled: bool = False
    oidc_provider_name: str = "SSO"
    user: Optional[AuthCheckUser] = None


class PasswordChangeRequest(BaseModel):
    """Password change request."""

    current_password: str
    new_password: str = Field(..., min_length=12)


class ForgotPasswordRequest(BaseModel):
    """Forgot password request."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Reset password request."""

    token: str
    new_password: str = Field(..., min_length=12)


class ProfileUpdateRequest(BaseModel):
    """Profile update request."""

    display_name: Optional[str] = Field(None, max_length=100)
    avatar_url: Optional[str] = Field(None, max_length=500)


class SessionInfo(BaseModel):
    """Session information for listing."""

    id: str
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: datetime
    expires_at: datetime
    is_current: bool


class SetupStatusResponse(BaseModel):
    """Setup status response."""

    needs_setup: bool
    message: str


class SetupRequest(BaseModel):
    """Initial admin setup request."""

    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=12)
    display_name: Optional[str] = Field(None, max_length=100)


class SetupResponse(BaseModel):
    """Setup response."""

    user_id: str
    username: str
    email: str
    message: str


# =============================================================================
# Helper Functions
# =============================================================================


def _set_session_cookies(
    response: Response,
    session_token: str,
    refresh_token: str,
    expires_at: datetime,
    refresh_expires_at: datetime,
) -> None:
    """Set session and refresh token cookies."""
    # Calculate max_age in seconds
    now = datetime.now(timezone.utc)
    session_max_age = int((expires_at - now).total_seconds())
    refresh_max_age = int((refresh_expires_at - now).total_seconds())

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=session_max_age,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        path="/",
    )

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=refresh_max_age,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        path="/api/v1/auth/refresh",  # Only sent to refresh endpoint
    )


def _clear_session_cookies(response: Response) -> None:
    """Clear session cookies."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/v1/auth/refresh")


def _generate_csrf_token(session_token: str) -> str:
    """Generate CSRF token from session token."""
    if not SESSION_SECRET_KEY:
        raise ValueError("SESSION_SECRET_KEY not configured")

    return hmac.new(
        SESSION_SECRET_KEY.encode(),
        session_token.encode(),
        "sha256",
    ).hexdigest()[:32]


async def _increment_failed_login(user_id: str) -> None:
    """Increment failed login count and potentially lock account."""
    now = datetime.now(timezone.utc)

    user = await database.fetch_one(
        users.select().where(users.c.id == user_id)
    )

    if not user:
        return

    new_count = (user["failed_login_attempts"] or 0) + 1

    if new_count >= LOGIN_LOCKOUT_THRESHOLD:
        # Lock account
        locked_until = now + timedelta(minutes=LOGIN_LOCKOUT_DURATION_MINUTES)
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(
                failed_login_attempts=new_count,
                locked_until=locked_until,
            )
        )
        security_logger.warning(
            "Account locked due to failed logins",
            extra={
                "event": "account_locked",
                "user_id": user_id,
                "failed_attempts": new_count,
                "locked_until": locked_until.isoformat(),
            },
        )
    else:
        await database.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(failed_login_attempts=new_count)
        )


async def _reset_failed_login(user_id: str) -> None:
    """Reset failed login count after successful login."""
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(
            failed_login_attempts=0,
            locked_until=None,
            last_login_at=datetime.now(timezone.utc),
        )
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/setup", response_model=SetupStatusResponse)
async def get_setup_status() -> SetupStatusResponse:
    """
    Check if initial setup is required.

    Returns needs_setup=True if no users exist in the system.
    This endpoint is always public.
    """
    user_count = await database.fetch_val(
        "SELECT COUNT(*) FROM users"
    )

    if user_count == 0:
        return SetupStatusResponse(
            needs_setup=True,
            message="No users exist. Please create an admin account to get started.",
        )

    return SetupStatusResponse(
        needs_setup=False,
        message="Setup complete. Please log in.",
    )


@router.post("/setup", response_model=SetupResponse)
async def create_initial_admin(
    request: Request,
    response: Response,
    body: SetupRequest,
) -> SetupResponse:
    """
    Create the initial admin account.

    This endpoint only works when no users exist in the system.
    Once an admin is created, this endpoint returns 403.
    """
    # Check if any users exist
    user_count = await database.fetch_val(
        "SELECT COUNT(*) FROM users"
    )

    if user_count > 0:
        raise HTTPException(
            status_code=403,
            detail="Setup already complete. Use the admin panel to create additional users.",
        )

    # Validate password strength
    is_valid, error = validate_password_strength(body.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # Check username uniqueness (probably not needed for first user, but good practice)
    existing = await database.fetch_one(
        users.select().where(users.c.username == body.username.lower())
    )
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Create admin user
    now = datetime.now(timezone.utc)
    user_id = str(uuid.uuid4())
    password_hash = hash_password(body.password)

    await database.execute(
        users.insert().values(
            id=user_id,
            username=body.username.lower(),
            email=body.email.lower(),
            password_hash=password_hash,
            display_name=body.display_name,
            role="admin",
            status="active",
            email_verified=True,
            failed_login_attempts=0,
            created_at=now,
        )
    )

    ip_address = _get_client_ip(request)
    security_logger.info(
        "Initial admin created via setup wizard",
        extra={
            "event": "setup_admin_created",
            "user_id": user_id,
            "username": body.username,
            "email": body.email,
            "ip_address": ip_address,
        },
    )

    # Automatically log in the new admin
    user_agent = request.headers.get("user-agent")
    session_token, refresh_token, expires_at, refresh_expires_at = (
        await create_user_session(user_id, ip_address, user_agent)
    )

    _set_session_cookies(
        response,
        session_token,
        refresh_token,
        expires_at,
        refresh_expires_at,
    )

    return SetupResponse(
        user_id=user_id,
        username=body.username.lower(),
        email=body.email.lower(),
        message="Admin account created successfully. You are now logged in.",
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
) -> LoginResponse:
    """
    Authenticate with username/email and password.

    Sets HTTP-only session cookies on success.
    """
    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    identifier = body.username_or_email.lower()

    # Find user by email or username
    from sqlalchemy import or_
    user = await database.fetch_one(
        users.select().where(
            or_(
                users.c.email == identifier,
                users.c.username == identifier,
            )
        )
    )

    if not user:
        # Constant-time comparison even when user doesn't exist
        verify_password(body.password, hash_password("dummy-password-for-timing"))
        security_logger.warning(
            "Login failed: user not found",
            extra={
                "event": "login_failed",
                "reason": "user_not_found",
                "identifier": identifier,
                "ip_address": ip_address,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Check account status
    if user["status"] == "disabled":
        security_logger.warning(
            "Login failed: account disabled",
            extra={
                "event": "login_failed",
                "reason": "account_disabled",
                "user_id": user["id"],
                "ip_address": ip_address,
            },
        )
        raise HTTPException(status_code=401, detail="Account is disabled")

    if user["status"] == "pending":
        raise HTTPException(status_code=401, detail="Account pending verification")

    # Check lockout
    now = datetime.now(timezone.utc)
    if user["locked_until"]:
        locked_until = user["locked_until"]
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until > now:
            remaining = int((locked_until - now).total_seconds() / 60)
            raise HTTPException(
                status_code=429,
                detail=f"Account locked. Try again in {remaining} minutes.",
            )

    # Verify password
    if not user["password_hash"]:
        # OIDC-only user
        raise HTTPException(
            status_code=401,
            detail="This account uses single sign-on. Please use SSO to log in.",
        )

    if not verify_password(body.password, user["password_hash"]):
        await _increment_failed_login(user["id"])
        security_logger.warning(
            "Login failed: invalid password",
            extra={
                "event": "login_failed",
                "reason": "invalid_password",
                "user_id": user["id"],
                "ip_address": ip_address,
            },
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Successful login
    await _reset_failed_login(user["id"])

    # Create session
    session_token, refresh_token, expires_at, refresh_expires_at = await create_user_session(
        user_id=user["id"],
        ip_address=ip_address,
        user_agent=user_agent,
    )

    # Set cookies
    _set_session_cookies(response, session_token, refresh_token, expires_at, refresh_expires_at)

    security_logger.info(
        "Login successful",
        extra={
            "event": "login_success",
            "user_id": user["id"],
            "username": user["username"],
            "ip_address": ip_address,
        },
    )

    return LoginResponse(
        user_id=user["id"],
        username=user["username"],
        email=user["email"],
        display_name=user["display_name"],
        role=user["role"],
        expires_at=expires_at,
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    user: dict = Depends(require_auth),
) -> dict:
    """
    Log out and invalidate current session.
    """
    session_id = user.get("session_id")
    if session_id:
        await invalidate_session(session_id)

    _clear_session_cookies(response)

    security_logger.info(
        "Logout",
        extra={
            "event": "logout",
            "user_id": user["id"],
            "session_id": session_id,
            "ip_address": _get_client_ip(request),
        },
    )

    return {"message": "Logged out successfully"}


@router.post("/refresh", response_model=LoginResponse)
async def refresh(
    request: Request,
    response: Response,
    refresh_token: Optional[str] = Cookie(None, alias=REFRESH_COOKIE_NAME),
) -> LoginResponse:
    """
    Refresh session using refresh token.

    Implements token rotation - old tokens become invalid after use.
    """
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token provided")

    ip_address = _get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    try:
        new_session, new_refresh, expires_at, refresh_expires_at = await refresh_user_session(
            refresh_token=refresh_token,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except RefreshTokenReusedError:
        _clear_session_cookies(response)
        raise HTTPException(
            status_code=401,
            detail="Session invalidated for security. Please log in again.",
        )
    except (SessionExpiredError, SessionRevokedError):
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Session expired")
    except SessionError as e:
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail=str(e))

    # Get user info
    # We need to validate the new session to get user info
    from api.auth.sessions import validate_session_token

    user = await validate_session_token(new_session)
    if not user:
        raise HTTPException(status_code=401, detail="Session creation failed")

    # Set new cookies
    _set_session_cookies(response, new_session, new_refresh, expires_at, refresh_expires_at)

    return LoginResponse(
        user_id=user["id"],
        username=user["username"],
        email=user["email"],
        display_name=user["display_name"],
        role=user["role"],
        expires_at=expires_at,
    )


@router.get("/check", response_model=AuthCheckResponse)
async def check_auth(
    request: Request,
    session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
) -> AuthCheckResponse:
    """
    Check if the current session is valid.

    Returns user info if authenticated, or authenticated=false if not.
    """
    # Get OIDC settings
    oidc_enabled = os.getenv("VLOG_OIDC_ENABLED", "false").lower() == "true"
    oidc_provider_name = os.getenv("VLOG_OIDC_PROVIDER_NAME", "SSO")

    if not session_token:
        return AuthCheckResponse(
            authenticated=False,
            oidc_enabled=oidc_enabled,
            oidc_provider_name=oidc_provider_name,
        )

    from api.auth.sessions import validate_session_token
    from api.auth.permissions import get_role_permissions

    user = await validate_session_token(session_token, allow_grace_period=True)

    if not user:
        return AuthCheckResponse(
            authenticated=False,
            oidc_enabled=oidc_enabled,
            oidc_provider_name=oidc_provider_name,
        )

    # Get permissions for user's role
    role_permissions = get_role_permissions(user["role"])
    permissions = [p.value for p in role_permissions]

    return AuthCheckResponse(
        authenticated=True,
        oidc_enabled=oidc_enabled,
        oidc_provider_name=oidc_provider_name,
        user=AuthCheckUser(
            id=user["id"],
            username=user["username"],
            email=user["email"],
            display_name=user["display_name"],
            role=user["role"],
            avatar_url=user["avatar_url"],
            permissions=permissions,
        ),
    )


@router.get("/me")
async def get_current_user_info(user: dict = Depends(require_auth)) -> dict:
    """Get current user profile."""
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "display_name": user["display_name"],
        "avatar_url": user["avatar_url"],
        "role": user["role"],
        "email_verified": user["email_verified"],
        "created_at": user["created_at"].isoformat() if user["created_at"] else None,
        "last_login_at": user["last_login_at"].isoformat() if user["last_login_at"] else None,
    }


@router.put("/me")
async def update_profile(
    body: ProfileUpdateRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Update current user profile."""
    updates = {}

    if body.display_name is not None:
        updates["display_name"] = body.display_name.strip() if body.display_name else None

    if body.avatar_url is not None:
        updates["avatar_url"] = body.avatar_url.strip() if body.avatar_url else None

    if updates:
        updates["updated_at"] = datetime.now(timezone.utc)
        await database.execute(
            users.update().where(users.c.id == user["id"]).values(**updates)
        )

    # Return updated user
    updated_user = await database.fetch_one(
        users.select().where(users.c.id == user["id"])
    )

    return {
        "id": updated_user["id"],
        "username": updated_user["username"],
        "email": updated_user["email"],
        "display_name": updated_user["display_name"],
        "avatar_url": updated_user["avatar_url"],
        "role": updated_user["role"],
    }


@router.post("/password")
async def change_password(
    request: Request,
    body: PasswordChangeRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Change current user's password."""
    # Verify current password
    current_user = await database.fetch_one(
        users.select().where(users.c.id == user["id"])
    )

    if not current_user["password_hash"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot change password for SSO-only account",
        )

    if not verify_password(body.current_password, current_user["password_hash"]):
        security_logger.warning(
            "Password change failed: invalid current password",
            extra={
                "event": "password_change_failed",
                "user_id": user["id"],
                "ip_address": _get_client_ip(request),
            },
        )
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    # Validate new password
    is_valid, error = validate_password_strength(body.new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # Update password
    new_hash = hash_password(body.new_password)
    await database.execute(
        users.update()
        .where(users.c.id == user["id"])
        .values(
            password_hash=new_hash,
            updated_at=datetime.now(timezone.utc),
        )
    )

    security_logger.info(
        "Password changed",
        extra={
            "event": "password_changed",
            "user_id": user["id"],
            "ip_address": _get_client_ip(request),
        },
    )

    return {"message": "Password updated successfully"}


@router.post("/forgot")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
) -> dict:
    """
    Request password reset.

    Always returns success to prevent email enumeration.
    """
    ip_address = _get_client_ip(request)
    now = datetime.now(timezone.utc)

    # Always return success (constant-time response)
    success_response = {"message": "If an account exists with this email, a reset link has been sent"}

    # Find user
    user = await database.fetch_one(
        users.select().where(users.c.email == body.email.lower())
    )

    if not user:
        # Simulate work to prevent timing attacks
        hash_password("dummy")
        return success_response

    if not user["password_hash"]:
        # OIDC-only user
        return success_response

    # Generate reset token
    token = generate_token(32)
    token_hash = hash_token(token)
    expires_at = now + timedelta(hours=PASSWORD_RESET_EXPIRY_HOURS)

    # Store token
    token_id = str(uuid.uuid4())
    await database.execute(
        password_reset_tokens.insert().values(
            id=token_id,
            user_id=user["id"],
            token_hash=token_hash,
            ip_address=ip_address,
            expires_at=expires_at,
            created_at=now,
        )
    )

    security_logger.info(
        "Password reset requested",
        extra={
            "event": "password_reset_requested",
            "user_id": user["id"],
            "ip_address": ip_address,
        },
    )

    # TODO: Implement email delivery for password reset links
    # The token is stored in the database and should be sent via email
    # Do NOT log or expose the token in any way

    return success_response


@router.post("/reset")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
) -> dict:
    """Reset password using token."""
    ip_address = _get_client_ip(request)
    now = datetime.now(timezone.utc)

    # Validate new password
    is_valid, error = validate_password_strength(body.new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # Find token
    all_tokens = await database.fetch_all(
        password_reset_tokens.select()
        .where(password_reset_tokens.c.used_at.is_(None))
        .where(password_reset_tokens.c.expires_at > now)
    )

    from api.auth.password import verify_token

    valid_token = None
    for token_record in all_tokens:
        if verify_token(body.token, token_record["token_hash"]):
            valid_token = token_record
            break

    if not valid_token:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # Get user
    user = await database.fetch_one(
        users.select().where(users.c.id == valid_token["user_id"])
    )

    if not user or user["status"] != "active":
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # Update password
    new_hash = hash_password(body.new_password)
    await database.execute(
        users.update()
        .where(users.c.id == user["id"])
        .values(
            password_hash=new_hash,
            failed_login_attempts=0,
            locked_until=None,
            updated_at=now,
        )
    )

    # Mark token as used
    await database.execute(
        password_reset_tokens.update()
        .where(password_reset_tokens.c.id == valid_token["id"])
        .values(used_at=now)
    )

    # Invalidate all sessions for security
    await invalidate_user_sessions(user["id"])

    security_logger.info(
        "Password reset completed",
        extra={
            "event": "password_reset_completed",
            "user_id": user["id"],
            "ip_address": ip_address,
        },
    )

    return {"message": "Password reset successfully. Please log in with your new password."}


@router.get("/sessions")
async def list_sessions(
    request: Request,
    user: dict = Depends(require_auth),
) -> list[dict]:
    """List active sessions for current user."""
    current_session_id = user.get("session_id")
    sessions = await get_user_sessions(user["id"])

    return [
        {
            "id": s["id"],
            "ip_address": s["ip_address"],
            "user_agent": s["user_agent"],
            "created_at": s["created_at"].isoformat() if s["created_at"] else None,
            "expires_at": s["expires_at"].isoformat() if s["expires_at"] else None,
            "is_current": s["id"] == current_session_id,
        }
        for s in sessions
    ]


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: str,
    request: Request,
    response: Response,
    user: dict = Depends(require_auth),
) -> dict:
    """Revoke a specific session."""
    # Verify session belongs to user
    from api.database import user_sessions

    session = await database.fetch_one(
        user_sessions.select()
        .where(user_sessions.c.id == session_id)
        .where(user_sessions.c.user_id == user["id"])
    )

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await invalidate_session(session_id)

    # If revoking current session, clear cookies
    if session_id == user.get("session_id"):
        _clear_session_cookies(response)

    security_logger.info(
        "Session revoked",
        extra={
            "event": "session_revoked",
            "user_id": user["id"],
            "revoked_session_id": session_id,
            "ip_address": _get_client_ip(request),
        },
    )

    return {"message": "Session revoked"}


@router.get("/csrf-token")
async def get_csrf_token(
    session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
) -> dict:
    """
    Get CSRF token for the current session.

    The CSRF token is derived from the session token using HMAC.
    """
    if not session_token:
        raise HTTPException(status_code=401, detail="No session")

    try:
        csrf_token = _generate_csrf_token(session_token)
    except ValueError as e:
        raise HTTPException(status_code=500, detail="Server configuration error")

    return {"csrf_token": csrf_token}
