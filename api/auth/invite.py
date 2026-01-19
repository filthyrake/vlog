"""
Invite management endpoints for invite-only registration.

Provides:
- Admin endpoints to create and manage invites
- Public endpoints to accept invites and create accounts
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field

from api.auth.middleware import require_permission
from api.auth.password import (
    generate_token,
    hash_password,
    hash_token,
    validate_password_strength,
    verify_token,
)
from api.auth.permissions import Permission, Role
from api.database import database, user_invites, users
from config import INVITE_EXPIRY_DAYS, REGISTRATION_MODE

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.auth")

router = APIRouter(prefix="/invites", tags=["Invites"])


# =============================================================================
# Request/Response Models
# =============================================================================


class CreateInviteRequest(BaseModel):
    """Create invite request."""

    email: EmailStr
    role: str = Field(default="viewer")
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=90)


class CreateInviteResponse(BaseModel):
    """Create invite response - includes the token (send via email)."""

    id: str
    email: str
    role: str
    token: str  # Send this to user!
    invite_url: str
    expires_at: datetime
    created_at: datetime


class InviteResponse(BaseModel):
    """Invite response (without the token)."""

    id: str
    email: str
    role: str
    expires_at: datetime
    created_at: datetime
    used_at: Optional[datetime]


class InviteListResponse(BaseModel):
    """Invite list response."""

    invites: list[InviteResponse]
    total: int


class ValidateInviteResponse(BaseModel):
    """Invite validation response."""

    valid: bool
    email: Optional[str] = None
    role: Optional[str] = None
    expires_at: Optional[datetime] = None


class AcceptInviteRequest(BaseModel):
    """Accept invite request."""

    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=12)
    display_name: Optional[str] = Field(None, max_length=100)


class AcceptInviteResponse(BaseModel):
    """Accept invite response."""

    user_id: str
    username: str
    email: str
    role: str
    message: str


# =============================================================================
# Helper Functions
# =============================================================================


def _validate_role(role: str) -> None:
    """Validate role value."""
    valid_roles = [r.value for r in Role]
    if role not in valid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}",
        )


async def _find_invite_by_token(token: str) -> Optional[dict]:
    """Find an invite by token."""
    now = datetime.now(timezone.utc)

    # Get all pending invites
    invites = await database.fetch_all(
        user_invites.select()
        .where(user_invites.c.used_at.is_(None))
        .where(user_invites.c.expires_at > now)
    )

    for invite in invites:
        if verify_token(token, invite["token_hash"]):
            return dict(invite)

    return None


# =============================================================================
# Admin Endpoints
# =============================================================================


@router.get("", response_model=InviteListResponse)
async def list_invites(
    pending_only: bool = Query(default=True),
    current_user: dict = Depends(require_permission(Permission.INVITE_READ)),
) -> InviteListResponse:
    """
    List all invites.

    Requires invite:read permission (admin only).
    """
    query = user_invites.select()

    if pending_only:
        now = datetime.now(timezone.utc)
        query = query.where(user_invites.c.used_at.is_(None))
        query = query.where(user_invites.c.expires_at > now)

    query = query.order_by(user_invites.c.created_at.desc())

    invites = await database.fetch_all(query)

    return InviteListResponse(
        invites=[
            InviteResponse(
                id=i["id"],
                email=i["email"],
                role=i["role"],
                expires_at=i["expires_at"],
                created_at=i["created_at"],
                used_at=i["used_at"],
            )
            for i in invites
        ],
        total=len(invites),
    )


@router.post("", response_model=CreateInviteResponse)
async def create_invite(
    body: CreateInviteRequest,
    current_user: dict = Depends(require_permission(Permission.INVITE_CREATE)),
) -> CreateInviteResponse:
    """
    Create an invite for a new user.

    Requires invite:create permission (admin only).
    The token should be sent to the user via email.
    """
    _validate_role(body.role)

    # Check if email already has a user
    existing_user = await database.fetch_one(
        users.select().where(users.c.email == body.email.lower())
    )
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="A user with this email already exists",
        )

    # Check for pending invite
    now = datetime.now(timezone.utc)
    existing_invite = await database.fetch_one(
        user_invites.select()
        .where(user_invites.c.email == body.email.lower())
        .where(user_invites.c.used_at.is_(None))
        .where(user_invites.c.expires_at > now)
    )
    if existing_invite:
        raise HTTPException(
            status_code=400,
            detail="An active invite already exists for this email",
        )

    # Generate invite token
    token = generate_token(32)
    token_hash = hash_token(token)

    # Calculate expiry
    expiry_days = body.expires_in_days or INVITE_EXPIRY_DAYS
    expires_at = now + timedelta(days=expiry_days)

    invite_id = str(uuid.uuid4())

    await database.execute(
        user_invites.insert().values(
            id=invite_id,
            email=body.email.lower(),
            role=body.role,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by=current_user["id"],
            created_at=now,
        )
    )

    security_logger.info(
        "Invite created",
        extra={
            "event": "invite_created",
            "invite_id": invite_id,
            "email": body.email,
            "role": body.role,
            "created_by": current_user["id"],
            "expires_at": expires_at.isoformat(),
        },
    )

    # Generate invite URL (would need base URL from config)
    invite_url = f"/accept-invite?token={token}"

    return CreateInviteResponse(
        id=invite_id,
        email=body.email.lower(),
        role=body.role,
        token=token,
        invite_url=invite_url,
        expires_at=expires_at,
        created_at=now,
    )


@router.delete("/{invite_id}")
async def revoke_invite(
    invite_id: str,
    current_user: dict = Depends(require_permission(Permission.INVITE_DELETE)),
) -> dict:
    """
    Revoke an invite.

    Requires invite:delete permission (admin only).
    """
    invite = await database.fetch_one(
        user_invites.select().where(user_invites.c.id == invite_id)
    )

    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    if invite["used_at"]:
        raise HTTPException(status_code=400, detail="Invite has already been used")

    # Delete the invite
    await database.execute(
        user_invites.delete().where(user_invites.c.id == invite_id)
    )

    security_logger.info(
        "Invite revoked",
        extra={
            "event": "invite_revoked",
            "invite_id": invite_id,
            "email": invite["email"],
            "revoked_by": current_user["id"],
        },
    )

    return {"message": "Invite revoked"}


# =============================================================================
# Public Endpoints
# =============================================================================


@router.get("/validate/{token}", response_model=ValidateInviteResponse)
async def validate_invite(token: str) -> ValidateInviteResponse:
    """
    Validate an invite token.

    Public endpoint - no authentication required.
    """
    invite = await _find_invite_by_token(token)

    if not invite:
        return ValidateInviteResponse(valid=False)

    return ValidateInviteResponse(
        valid=True,
        email=invite["email"],
        role=invite["role"],
        expires_at=invite["expires_at"],
    )


@router.post("/accept/{token}", response_model=AcceptInviteResponse)
async def accept_invite(
    token: str,
    body: AcceptInviteRequest,
) -> AcceptInviteResponse:
    """
    Accept an invite and create a user account.

    Public endpoint - no authentication required.
    """
    # Check registration mode
    if REGISTRATION_MODE == "disabled":
        raise HTTPException(
            status_code=403,
            detail="Registration is currently disabled",
        )

    # Find and validate invite
    invite = await _find_invite_by_token(token)

    if not invite:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired invite token",
        )

    # Validate username uniqueness
    existing = await database.fetch_one(
        users.select().where(users.c.username == body.username.lower())
    )
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Check if email already has a user (race condition check)
    existing = await database.fetch_one(
        users.select().where(users.c.email == invite["email"])
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="A user with this email already exists",
        )

    # Validate password
    is_valid, error = validate_password_strength(body.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # Create user
    now = datetime.now(timezone.utc)
    user_id = str(uuid.uuid4())
    password_hash = hash_password(body.password)

    # Use transaction to ensure atomicity:
    # If invite marking fails, user creation is rolled back
    async with database.transaction():
        await database.execute(
            users.insert().values(
                id=user_id,
                username=body.username.lower(),
                email=invite["email"],
                password_hash=password_hash,
                display_name=body.display_name,
                role=invite["role"],
                status="active",
                email_verified=True,  # Invite-based users are verified
                created_at=now,
            )
        )

        # Mark invite as used
        await database.execute(
            user_invites.update()
            .where(user_invites.c.id == invite["id"])
            .values(
                used_at=now,
                used_by=user_id,
            )
        )

    security_logger.info(
        "Invite accepted",
        extra={
            "event": "invite_accepted",
            "invite_id": invite["id"],
            "user_id": user_id,
            "username": body.username,
            "email": invite["email"],
            "role": invite["role"],
        },
    )

    return AcceptInviteResponse(
        user_id=user_id,
        username=body.username.lower(),
        email=invite["email"],
        role=invite["role"],
        message="Account created successfully. You can now log in.",
    )
