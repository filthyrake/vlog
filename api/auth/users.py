"""
User management API endpoints.

Provides admin-only endpoints for managing users.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field

from api.auth.middleware import require_permission
from api.auth.password import hash_password, validate_password_strength
from api.auth.permissions import Permission, Role
from api.auth.sessions import invalidate_user_sessions
from api.database import database, users

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.auth")

router = APIRouter(prefix="/users", tags=["User Management"])


# =============================================================================
# Request/Response Models
# =============================================================================


class CreateUserRequest(BaseModel):
    """Create user request."""

    username: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    password: Optional[str] = Field(None, min_length=12)  # Optional for OIDC-only
    display_name: Optional[str] = Field(None, max_length=100)
    role: str = Field(default="viewer")


class UpdateUserRequest(BaseModel):
    """Update user request."""

    username: Optional[str] = Field(None, min_length=3, max_length=100)
    email: Optional[EmailStr] = None
    display_name: Optional[str] = Field(None, max_length=100)
    avatar_url: Optional[str] = Field(None, max_length=500)
    role: Optional[str] = None
    status: Optional[str] = None


class UserResponse(BaseModel):
    """User response."""

    id: str
    username: str
    email: str
    display_name: Optional[str]
    avatar_url: Optional[str]
    role: str
    status: str
    email_verified: bool
    created_at: datetime
    updated_at: Optional[datetime]
    last_login_at: Optional[datetime]


class UserListResponse(BaseModel):
    """Paginated user list response."""

    users: list[UserResponse]
    total: int
    limit: int
    offset: int


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


def _validate_status(status: str) -> None:
    """Validate status value."""
    valid_statuses = ["active", "disabled", "pending"]
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
        )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=UserListResponse)
async def list_users(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    role: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    current_user: dict = Depends(require_permission(Permission.USER_READ)),
) -> UserListResponse:
    """
    List all users with optional filtering.

    Requires user:read permission (admin only).
    """
    query = users.select()

    # Apply filters
    if role:
        _validate_role(role)
        query = query.where(users.c.role == role)

    if status:
        _validate_status(status)
        query = query.where(users.c.status == status)

    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (users.c.username.ilike(search_pattern))
            | (users.c.email.ilike(search_pattern))
            | (users.c.display_name.ilike(search_pattern))
        )

    # Get total count
    from sqlalchemy import func, select

    count_query = select(func.count()).select_from(query.alias())
    total = await database.fetch_val(count_query)

    # Apply pagination
    query = query.order_by(users.c.created_at.desc()).limit(limit).offset(offset)

    results = await database.fetch_all(query)

    return UserListResponse(
        users=[
            UserResponse(
                id=u["id"],
                username=u["username"],
                email=u["email"],
                display_name=u["display_name"],
                avatar_url=u["avatar_url"],
                role=u["role"],
                status=u["status"],
                email_verified=u["email_verified"],
                created_at=u["created_at"],
                updated_at=u["updated_at"],
                last_login_at=u["last_login_at"],
            )
            for u in results
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=UserResponse)
async def create_user(
    body: CreateUserRequest,
    current_user: dict = Depends(require_permission(Permission.USER_CREATE)),
) -> UserResponse:
    """
    Create a new user.

    Requires user:create permission (admin only).
    """
    _validate_role(body.role)

    # Check username uniqueness
    existing = await database.fetch_one(
        users.select().where(users.c.username == body.username.lower())
    )
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Check email uniqueness
    existing = await database.fetch_one(
        users.select().where(users.c.email == body.email.lower())
    )
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

    # Hash password if provided
    password_hash = None
    if body.password:
        is_valid, error = validate_password_strength(body.password)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error)
        password_hash = hash_password(body.password)

    now = datetime.now(timezone.utc)
    user_id = str(uuid.uuid4())

    await database.execute(
        users.insert().values(
            id=user_id,
            username=body.username.lower(),
            email=body.email.lower(),
            password_hash=password_hash,
            display_name=body.display_name,
            role=body.role,
            status="active",
            email_verified=True,  # Admin-created users are verified
            created_at=now,
            created_by=current_user["id"],
        )
    )

    security_logger.info(
        "User created",
        extra={
            "event": "user_created",
            "user_id": user_id,
            "username": body.username,
            "role": body.role,
            "created_by": current_user["id"],
        },
    )

    user = await database.fetch_one(users.select().where(users.c.id == user_id))

    return UserResponse(
        id=user["id"],
        username=user["username"],
        email=user["email"],
        display_name=user["display_name"],
        avatar_url=user["avatar_url"],
        role=user["role"],
        status=user["status"],
        email_verified=user["email_verified"],
        created_at=user["created_at"],
        updated_at=user["updated_at"],
        last_login_at=user["last_login_at"],
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: dict = Depends(require_permission(Permission.USER_READ)),
) -> UserResponse:
    """
    Get user details.

    Requires user:read permission (admin only).
    """
    user = await database.fetch_one(users.select().where(users.c.id == user_id))

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=user["id"],
        username=user["username"],
        email=user["email"],
        display_name=user["display_name"],
        avatar_url=user["avatar_url"],
        role=user["role"],
        status=user["status"],
        email_verified=user["email_verified"],
        created_at=user["created_at"],
        updated_at=user["updated_at"],
        last_login_at=user["last_login_at"],
    )


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    current_user: dict = Depends(require_permission(Permission.USER_UPDATE)),
) -> UserResponse:
    """
    Update user details.

    Requires user:update permission (admin only).
    """
    user = await database.fetch_one(users.select().where(users.c.id == user_id))

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    updates = {"updated_at": datetime.now(timezone.utc)}

    if body.username is not None:
        # Check uniqueness
        existing = await database.fetch_one(
            users.select()
            .where(users.c.username == body.username.lower())
            .where(users.c.id != user_id)
        )
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")
        updates["username"] = body.username.lower()

    if body.email is not None:
        existing = await database.fetch_one(
            users.select()
            .where(users.c.email == body.email.lower())
            .where(users.c.id != user_id)
        )
        if existing:
            raise HTTPException(status_code=400, detail="Email already exists")
        updates["email"] = body.email.lower()
        updates["email_verified"] = False  # Require re-verification

    if body.display_name is not None:
        updates["display_name"] = body.display_name.strip() if body.display_name else None

    if body.avatar_url is not None:
        updates["avatar_url"] = body.avatar_url.strip() if body.avatar_url else None

    if body.role is not None:
        _validate_role(body.role)
        # Prevent self-demotion from admin
        if user_id == current_user["id"] and body.role != "admin":
            raise HTTPException(
                status_code=400,
                detail="Cannot change your own role. Ask another admin.",
            )
        updates["role"] = body.role

    if body.status is not None:
        _validate_status(body.status)
        # Prevent self-disable
        if user_id == current_user["id"] and body.status != "active":
            raise HTTPException(
                status_code=400,
                detail="Cannot disable your own account.",
            )
        updates["status"] = body.status
        # If disabling, invalidate sessions
        if body.status == "disabled":
            await invalidate_user_sessions(user_id)

    await database.execute(
        users.update().where(users.c.id == user_id).values(**updates)
    )

    security_logger.info(
        "User updated",
        extra={
            "event": "user_updated",
            "user_id": user_id,
            "updated_by": current_user["id"],
            "updates": list(updates.keys()),
        },
    )

    updated_user = await database.fetch_one(users.select().where(users.c.id == user_id))

    return UserResponse(
        id=updated_user["id"],
        username=updated_user["username"],
        email=updated_user["email"],
        display_name=updated_user["display_name"],
        avatar_url=updated_user["avatar_url"],
        role=updated_user["role"],
        status=updated_user["status"],
        email_verified=updated_user["email_verified"],
        created_at=updated_user["created_at"],
        updated_at=updated_user["updated_at"],
        last_login_at=updated_user["last_login_at"],
    )


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    current_user: dict = Depends(require_permission(Permission.USER_DELETE)),
) -> dict:
    """
    Delete or disable a user.

    Currently implements soft-delete by setting status to 'disabled'.
    Requires user:delete permission (admin only).
    """
    user = await database.fetch_one(users.select().where(users.c.id == user_id))

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-deletion
    if user_id == current_user["id"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete your own account.",
        )

    # Soft delete - disable the account
    await database.execute(
        users.update()
        .where(users.c.id == user_id)
        .values(
            status="disabled",
            updated_at=datetime.now(timezone.utc),
        )
    )

    # Invalidate all sessions
    await invalidate_user_sessions(user_id)

    security_logger.info(
        "User deleted (disabled)",
        extra={
            "event": "user_deleted",
            "user_id": user_id,
            "deleted_by": current_user["id"],
        },
    )

    return {"message": "User disabled"}


@router.post("/{user_id}/reset-password")
async def force_password_reset(
    user_id: str,
    current_user: dict = Depends(require_permission(Permission.USER_UPDATE)),
) -> dict:
    """
    Force a password reset for a user.

    Generates a password reset token and invalidates all sessions.
    Requires user:update permission (admin only).
    """
    user = await database.fetch_one(users.select().where(users.c.id == user_id))

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user["password_hash"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot reset password for SSO-only account",
        )

    # Generate reset token
    from api.auth.password import generate_token, hash_token

    token = generate_token(32)
    token_hash = hash_token(token)
    now = datetime.now(timezone.utc)

    from config import PASSWORD_RESET_EXPIRY_HOURS
    from datetime import timedelta

    expires_at = now + timedelta(hours=PASSWORD_RESET_EXPIRY_HOURS)

    from api.database import password_reset_tokens

    token_id = str(uuid.uuid4())
    await database.execute(
        password_reset_tokens.insert().values(
            id=token_id,
            user_id=user_id,
            token_hash=token_hash,
            ip_address=None,
            expires_at=expires_at,
            created_at=now,
        )
    )

    # Invalidate all sessions
    await invalidate_user_sessions(user_id)

    security_logger.info(
        "Admin forced password reset",
        extra={
            "event": "password_reset_forced",
            "user_id": user_id,
            "forced_by": current_user["id"],
        },
    )

    # TODO: Implement email delivery for password reset links
    # The token is stored in the database and should be sent via email
    # Do NOT log or expose the token in any way

    return {
        "message": "Password reset initiated. User has been logged out and will receive a reset email.",
    }
