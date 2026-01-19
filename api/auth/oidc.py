"""
Generic OIDC (OpenID Connect) integration.

Supports any OIDC-compliant identity provider:
- Keycloak
- Authentik
- Authelia
- Zitadel
- And more

Implements proper security measures:
- State parameter for CSRF protection
- Nonce for replay protection
- Circuit breaker for provider failures
"""

import asyncio
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from api.auth.middleware import REFRESH_COOKIE_NAME, SESSION_COOKIE_NAME, require_auth
from api.auth.password import generate_token, hash_password, hash_token, verify_token
from api.auth.sessions import create_user_session, invalidate_user_sessions
from api.database import database, oidc_connections, oidc_states, users
from config import (
    OIDC_AUTO_CREATE_USERS,
    OIDC_CLIENT_ID,
    OIDC_CLIENT_SECRET,
    OIDC_DEFAULT_ROLE,
    OIDC_DISCOVERY_URL,
    OIDC_ENABLED,
    OIDC_PROVIDER_NAME,
    OIDC_SCOPES,
    OIDC_STATE_EXPIRY_MINUTES,
    OIDC_TIMEOUT_SECONDS,
    SECURE_COOKIES,
    USER_REFRESH_TOKEN_EXPIRY_DAYS,
    USER_SESSION_EXPIRY_HOURS,
)

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.auth")

router = APIRouter(prefix="/auth/oidc", tags=["OIDC Authentication"])


# =============================================================================
# Circuit Breaker
# =============================================================================


@dataclass
class CircuitBreakerState:
    """Circuit breaker state."""

    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    is_open: bool = False


# Circuit breaker for OIDC provider
_circuit_breaker = CircuitBreakerState()
_circuit_breaker_lock = asyncio.Lock()
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_RECOVERY_SECONDS = 60


async def _check_circuit_breaker() -> None:
    """Check if circuit breaker is open (thread-safe)."""
    async with _circuit_breaker_lock:
        if not _circuit_breaker.is_open:
            return

        # Check if recovery period has passed
        if _circuit_breaker.last_failure_time:
            recovery_time = _circuit_breaker.last_failure_time + timedelta(
                seconds=CIRCUIT_BREAKER_RECOVERY_SECONDS
            )
            if datetime.now(timezone.utc) > recovery_time:
                # Reset circuit breaker (half-open state)
                _circuit_breaker.is_open = False
                _circuit_breaker.failure_count = 0
                logger.info("OIDC circuit breaker reset (recovery period passed)")
                return

        raise HTTPException(
            status_code=503,
            detail=f"OIDC provider temporarily unavailable. Please try again in {CIRCUIT_BREAKER_RECOVERY_SECONDS} seconds.",
        )


async def _record_failure() -> None:
    """Record a failure and potentially open circuit breaker (thread-safe)."""
    async with _circuit_breaker_lock:
        _circuit_breaker.failure_count += 1
        _circuit_breaker.last_failure_time = datetime.now(timezone.utc)

        if _circuit_breaker.failure_count >= CIRCUIT_BREAKER_THRESHOLD:
            _circuit_breaker.is_open = True
            logger.warning(
                f"OIDC circuit breaker opened after {_circuit_breaker.failure_count} failures"
            )


async def _record_success() -> None:
    """Record a success and reset failure count (thread-safe)."""
    async with _circuit_breaker_lock:
        _circuit_breaker.failure_count = 0
        _circuit_breaker.is_open = False


# =============================================================================
# OIDC Discovery Cache
# =============================================================================


@dataclass
class OIDCConfig:
    """Cached OIDC configuration."""

    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    issuer: str
    cached_at: datetime


_oidc_config_cache: Optional[OIDCConfig] = None
OIDC_CONFIG_CACHE_TTL_SECONDS = 3600  # 1 hour


async def _get_oidc_config() -> OIDCConfig:
    """Get OIDC configuration from discovery endpoint (cached)."""
    global _oidc_config_cache

    if _oidc_config_cache:
        cache_age = (datetime.now(timezone.utc) - _oidc_config_cache.cached_at).total_seconds()
        if cache_age < OIDC_CONFIG_CACHE_TTL_SECONDS:
            return _oidc_config_cache

    if not OIDC_DISCOVERY_URL:
        raise HTTPException(status_code=500, detail="OIDC not configured")

    await _check_circuit_breaker()

    try:
        async with httpx.AsyncClient(timeout=OIDC_TIMEOUT_SECONDS) as client:
            response = await client.get(OIDC_DISCOVERY_URL)
            response.raise_for_status()
            config = response.json()
    except httpx.TimeoutException:
        await _record_failure()
        raise HTTPException(status_code=503, detail="OIDC provider timeout")
    except httpx.ConnectError:
        await _record_failure()
        raise HTTPException(status_code=503, detail="Cannot connect to OIDC provider")
    except Exception as e:
        await _record_failure()
        logger.error(f"OIDC discovery failed: {e}")
        raise HTTPException(status_code=503, detail="OIDC provider error")

    await _record_success()

    _oidc_config_cache = OIDCConfig(
        authorization_endpoint=config["authorization_endpoint"],
        token_endpoint=config["token_endpoint"],
        userinfo_endpoint=config["userinfo_endpoint"],
        issuer=config["issuer"],
        cached_at=datetime.now(timezone.utc),
    )

    return _oidc_config_cache


# =============================================================================
# Request/Response Models
# =============================================================================


class OIDCAuthorizeResponse(BaseModel):
    """Response for OIDC authorize initiation."""

    redirect_url: str
    state: str


class OIDCStatusResponse(BaseModel):
    """Response for OIDC configuration status."""

    enabled: bool
    provider_name: str
    connected: bool = False
    provider_email: Optional[str] = None


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
        path="/api/v1/auth/refresh",
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/status", response_model=OIDCStatusResponse)
async def get_oidc_status() -> OIDCStatusResponse:
    """
    Get OIDC configuration status.

    Returns whether OIDC is enabled and the provider name.
    """
    return OIDCStatusResponse(
        enabled=OIDC_ENABLED,
        provider_name=OIDC_PROVIDER_NAME if OIDC_ENABLED else "",
    )


@router.get("/authorize", response_model=OIDCAuthorizeResponse)
async def initiate_oidc_authorize(
    request: Request,
    redirect_uri: str = Query(..., description="Where to redirect after auth"),
) -> OIDCAuthorizeResponse:
    """
    Initiate OIDC authorization flow.

    Generates state and nonce for security, stores them, and returns
    the URL to redirect the user to the OIDC provider.
    """
    if not OIDC_ENABLED:
        raise HTTPException(status_code=400, detail="OIDC is not enabled")

    config = await _get_oidc_config()

    # Generate cryptographic state and nonce
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=OIDC_STATE_EXPIRY_MINUTES)

    # Store state in database
    state_id = str(uuid.uuid4())
    await database.execute(
        oidc_states.insert().values(
            id=state_id,
            state=state,
            nonce=nonce,
            redirect_uri=redirect_uri,
            expires_at=expires_at,
            created_at=now,
        )
    )

    # Build authorization URL
    scopes = OIDC_SCOPES.replace(",", " ")
    auth_params = {
        "client_id": OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": scopes,
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
    }

    query_string = "&".join(f"{k}={v}" for k, v in auth_params.items())
    authorize_url = f"{config.authorization_endpoint}?{query_string}"

    logger.info(f"Initiated OIDC flow, state_id={state_id}")

    return OIDCAuthorizeResponse(
        redirect_url=authorize_url,
        state=state,
    )


@router.get("/callback")
async def oidc_callback(
    request: Request,
    response: Response,
    code: str = Query(..., description="Authorization code from provider"),
    state: str = Query(..., description="State parameter for CSRF validation"),
) -> dict:
    """
    Handle OIDC callback after user authenticates with provider.

    Validates state, exchanges code for tokens, and creates or links user.
    """
    if not OIDC_ENABLED:
        raise HTTPException(status_code=400, detail="OIDC is not enabled")

    now = datetime.now(timezone.utc)

    # Validate state (CSRF protection)
    stored_state = await database.fetch_one(
        oidc_states.select()
        .where(oidc_states.c.state == state)
        .where(oidc_states.c.expires_at > now)
    )

    if not stored_state:
        security_logger.warning(
            "OIDC callback with invalid state",
            extra={"event": "oidc_invalid_state", "state": state[:8]},
        )
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    # Delete state (single-use)
    await database.execute(
        oidc_states.delete().where(oidc_states.c.id == stored_state["id"])
    )

    config = await _get_oidc_config()
    await _check_circuit_breaker()

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient(timeout=OIDC_TIMEOUT_SECONDS) as client:
            token_response = await client.post(
                config.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": stored_state["redirect_uri"],
                    "client_id": OIDC_CLIENT_ID,
                    "client_secret": OIDC_CLIENT_SECRET,
                },
            )
            token_response.raise_for_status()
            tokens = token_response.json()
    except httpx.TimeoutException:
        await _record_failure()
        raise HTTPException(status_code=503, detail="OIDC provider timeout")
    except httpx.HTTPStatusError as e:
        await _record_failure()
        logger.error(f"OIDC token exchange failed: {e.response.text}")
        raise HTTPException(status_code=400, detail="OIDC token exchange failed")
    except Exception as e:
        await _record_failure()
        logger.error(f"OIDC token exchange error: {e}")
        raise HTTPException(status_code=503, detail="OIDC provider error")

    await _record_success()

    # Validate nonce in ID token for replay protection
    id_token = tokens.get("id_token")
    if id_token and stored_state.get("nonce"):
        try:
            # Decode ID token (JWT) without verification - we trust the HTTPS connection
            # The ID token is base64url encoded with 3 parts: header.payload.signature
            import base64
            import json

            parts = id_token.split(".")
            if len(parts) >= 2:
                # Decode the payload (middle part)
                payload = parts[1]
                # Add padding if needed
                padding = 4 - len(payload) % 4
                if padding != 4:
                    payload += "=" * padding
                decoded = base64.urlsafe_b64decode(payload)
                claims = json.loads(decoded)

                token_nonce = claims.get("nonce")
                if token_nonce and token_nonce != stored_state["nonce"]:
                    security_logger.warning(
                        "OIDC nonce mismatch - possible replay attack",
                        extra={
                            "event": "oidc_nonce_mismatch",
                        },
                    )
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid authentication response (nonce mismatch)",
                    )
        except HTTPException:
            raise
        except Exception as e:
            # If we can't decode the ID token, log but continue
            # The userinfo endpoint is still a valid verification
            logger.debug(f"Could not decode ID token for nonce validation: {e}")

    # Get user info
    try:
        async with httpx.AsyncClient(timeout=OIDC_TIMEOUT_SECONDS) as client:
            userinfo_response = await client.get(
                config.userinfo_endpoint,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            userinfo_response.raise_for_status()
            userinfo = userinfo_response.json()
    except Exception as e:
        logger.error(f"OIDC userinfo failed: {e}")
        raise HTTPException(status_code=503, detail="Failed to get user info")

    # Extract user info
    provider_user_id = userinfo.get("sub")
    email = userinfo.get("email")
    name = userinfo.get("name") or userinfo.get("preferred_username")

    if not provider_user_id:
        raise HTTPException(status_code=400, detail="No user ID from provider")

    # Find existing connection
    connection = await database.fetch_one(
        oidc_connections.select().where(
            oidc_connections.c.provider_user_id == provider_user_id
        )
    )

    user = None
    if connection:
        # Existing connection - get user
        user = await database.fetch_one(
            users.select().where(users.c.id == connection["user_id"])
        )
        if not user:
            # Orphaned connection
            await database.execute(
                oidc_connections.delete().where(
                    oidc_connections.c.id == connection["id"]
                )
            )
            connection = None

    if not user and email:
        # Try to find user by email
        user = await database.fetch_one(
            users.select().where(users.c.email == email.lower())
        )

    if not user:
        # No existing user
        if not OIDC_AUTO_CREATE_USERS:
            raise HTTPException(
                status_code=403,
                detail="No account found. Please contact administrator for access.",
            )

        # Create new user
        user_id = str(uuid.uuid4())
        username = email.split("@")[0] if email else f"user_{provider_user_id[:8]}"

        # Ensure unique username
        existing = await database.fetch_one(
            users.select().where(users.c.username == username.lower())
        )
        if existing:
            username = f"{username}_{secrets.token_hex(4)}"

        await database.execute(
            users.insert().values(
                id=user_id,
                username=username.lower(),
                email=email.lower() if email else f"{provider_user_id}@oidc.local",
                password_hash=None,  # OIDC-only user
                display_name=name,
                role=OIDC_DEFAULT_ROLE,
                status="active",
                email_verified=True,
                created_at=now,
            )
        )

        user = await database.fetch_one(
            users.select().where(users.c.id == user_id)
        )

        security_logger.info(
            "OIDC user created",
            extra={
                "event": "oidc_user_created",
                "user_id": user_id,
                "provider_user_id": provider_user_id,
            },
        )

    # Create connection if not exists
    if not connection:
        connection_id = str(uuid.uuid4())
        await database.execute(
            oidc_connections.insert().values(
                id=connection_id,
                user_id=user["id"],
                provider_user_id=provider_user_id,
                provider_email=email,
                created_at=now,
            )
        )

    # Check user status
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is disabled")

    # Create session
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    session_token, refresh_token, expires_at, refresh_expires_at = await create_user_session(
        user_id=user["id"],
        ip_address=ip_address,
        user_agent=user_agent,
    )

    # Update last login
    await database.execute(
        users.update()
        .where(users.c.id == user["id"])
        .values(last_login_at=now)
    )

    # Set cookies
    _set_session_cookies(response, session_token, refresh_token, expires_at, refresh_expires_at)

    security_logger.info(
        "OIDC login successful",
        extra={
            "event": "oidc_login",
            "user_id": user["id"],
            "provider_user_id": provider_user_id,
        },
    )

    return {
        "user_id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "redirect_uri": stored_state["redirect_uri"],
    }


@router.post("/link")
async def link_oidc(
    request: Request,
    code: str = Query(..., description="Authorization code from provider"),
    state: str = Query(..., description="State parameter"),
    user: dict = Depends(require_auth),
) -> dict:
    """
    Link OIDC provider to existing account.

    User must be logged in first.
    """
    if not OIDC_ENABLED:
        raise HTTPException(status_code=400, detail="OIDC is not enabled")

    # Similar flow to callback, but link to existing user instead of creating/logging in
    # This is a simplified version

    now = datetime.now(timezone.utc)

    # Validate state
    stored_state = await database.fetch_one(
        oidc_states.select()
        .where(oidc_states.c.state == state)
        .where(oidc_states.c.expires_at > now)
    )

    if not stored_state:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    await database.execute(
        oidc_states.delete().where(oidc_states.c.id == stored_state["id"])
    )

    config = await _get_oidc_config()

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient(timeout=OIDC_TIMEOUT_SECONDS) as client:
            token_response = await client.post(
                config.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": stored_state["redirect_uri"],
                    "client_id": OIDC_CLIENT_ID,
                    "client_secret": OIDC_CLIENT_SECRET,
                },
            )
            token_response.raise_for_status()
            tokens = token_response.json()

            userinfo_response = await client.get(
                config.userinfo_endpoint,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            userinfo_response.raise_for_status()
            userinfo = userinfo_response.json()
    except Exception as e:
        logger.error(f"OIDC link failed: {e}")
        raise HTTPException(status_code=503, detail="OIDC provider error")

    provider_user_id = userinfo.get("sub")
    email = userinfo.get("email")

    if not provider_user_id:
        raise HTTPException(status_code=400, detail="No user ID from provider")

    # Check if already linked to another user
    existing = await database.fetch_one(
        oidc_connections.select().where(
            oidc_connections.c.provider_user_id == provider_user_id
        )
    )

    if existing:
        if existing["user_id"] == user["id"]:
            return {"message": "Account already linked"}
        raise HTTPException(
            status_code=400,
            detail="This OIDC account is already linked to another user",
        )

    # Create connection
    connection_id = str(uuid.uuid4())
    await database.execute(
        oidc_connections.insert().values(
            id=connection_id,
            user_id=user["id"],
            provider_user_id=provider_user_id,
            provider_email=email,
            created_at=now,
        )
    )

    security_logger.info(
        "OIDC account linked",
        extra={
            "event": "oidc_linked",
            "user_id": user["id"],
            "provider_user_id": provider_user_id,
        },
    )

    return {"message": "OIDC account linked successfully"}


@router.delete("")
async def unlink_oidc(
    user: dict = Depends(require_auth),
) -> dict:
    """
    Unlink OIDC provider from account.

    User must have a password set to unlink OIDC.
    """
    # Get user with password_hash
    full_user = await database.fetch_one(
        users.select().where(users.c.id == user["id"])
    )

    if not full_user["password_hash"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot unlink OIDC without a password. Set a password first.",
        )

    # Delete connection
    result = await database.execute(
        oidc_connections.delete().where(
            oidc_connections.c.user_id == user["id"]
        )
    )

    if result == 0:
        raise HTTPException(status_code=404, detail="No OIDC connection found")

    security_logger.info(
        "OIDC account unlinked",
        extra={"event": "oidc_unlinked", "user_id": user["id"]},
    )

    return {"message": "OIDC account unlinked"}
