"""
API Key management endpoints.

Provides endpoints for users to manage their API keys.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth.middleware import require_auth
from api.auth.password import generate_token, get_token_prefix, hash_token_fast
from api.database import database, user_api_keys

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.auth")

router = APIRouter(prefix="/api-keys", tags=["API Keys"])


# =============================================================================
# Request/Response Models
# =============================================================================


class CreateApiKeyRequest(BaseModel):
    """Create API key request."""

    name: str = Field(..., min_length=1, max_length=100)
    expires_in_days: Optional[int] = Field(None, ge=1, le=365)


class CreateApiKeyResponse(BaseModel):
    """Create API key response - includes the key (only shown once)."""

    id: str
    name: str
    key: str  # Only returned on creation!
    key_prefix: str
    expires_at: Optional[datetime]
    created_at: datetime


class ApiKeyResponse(BaseModel):
    """API key response (without the actual key)."""

    id: str
    name: str
    key_prefix: str
    expires_at: Optional[datetime]
    last_used_at: Optional[datetime]
    created_at: datetime


class ApiKeyListResponse(BaseModel):
    """API key list response."""

    keys: list[ApiKeyResponse]
    total: int


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(
    user: dict = Depends(require_auth),
) -> ApiKeyListResponse:
    """
    List all API keys for the current user.

    Note: The actual key values are never returned after creation.
    """
    keys = await database.fetch_all(
        user_api_keys.select()
        .where(user_api_keys.c.user_id == user["id"])
        .where(user_api_keys.c.revoked_at.is_(None))
        .order_by(user_api_keys.c.created_at.desc())
    )

    return ApiKeyListResponse(
        keys=[
            ApiKeyResponse(
                id=k["id"],
                name=k["name"],
                key_prefix=k["key_prefix"],
                expires_at=k["expires_at"],
                last_used_at=k["last_used_at"],
                created_at=k["created_at"],
            )
            for k in keys
        ],
        total=len(keys),
    )


@router.post("", response_model=CreateApiKeyResponse)
async def create_api_key(
    body: CreateApiKeyRequest,
    user: dict = Depends(require_auth),
) -> CreateApiKeyResponse:
    """
    Create a new API key.

    IMPORTANT: The key is only returned once. Store it securely.
    """
    now = datetime.now(timezone.utc)

    # Generate key
    api_key = generate_token(32)  # ~44 character URL-safe token
    key_hash = hash_token_fast(api_key)  # SHA-256 for fast verification
    key_prefix = get_token_prefix(api_key)

    # Calculate expiry
    expires_at = None
    if body.expires_in_days:
        expires_at = now + timedelta(days=body.expires_in_days)

    key_id = str(uuid.uuid4())

    await database.execute(
        user_api_keys.insert().values(
            id=key_id,
            user_id=user["id"],
            name=body.name.strip(),
            key_prefix=key_prefix,
            key_hash=key_hash,
            expires_at=expires_at,
            created_at=now,
        )
    )

    security_logger.info(
        "API key created",
        extra={
            "event": "api_key_created",
            "user_id": user["id"],
            "key_id": key_id,
            "key_name": body.name,
            "key_prefix": key_prefix,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )

    return CreateApiKeyResponse(
        id=key_id,
        name=body.name.strip(),
        key=api_key,  # Only returned once!
        key_prefix=key_prefix,
        expires_at=expires_at,
        created_at=now,
    )


@router.get("/{key_id}", response_model=ApiKeyResponse)
async def get_api_key(
    key_id: str,
    user: dict = Depends(require_auth),
) -> ApiKeyResponse:
    """Get details of a specific API key."""
    key = await database.fetch_one(
        user_api_keys.select()
        .where(user_api_keys.c.id == key_id)
        .where(user_api_keys.c.user_id == user["id"])
        .where(user_api_keys.c.revoked_at.is_(None))
    )

    if not key:
        raise HTTPException(status_code=404, detail="API key not found")

    return ApiKeyResponse(
        id=key["id"],
        name=key["name"],
        key_prefix=key["key_prefix"],
        expires_at=key["expires_at"],
        last_used_at=key["last_used_at"],
        created_at=key["created_at"],
    )


@router.delete("/{key_id}")
async def revoke_api_key(
    key_id: str,
    user: dict = Depends(require_auth),
) -> dict:
    """
    Revoke an API key.

    The key will immediately stop working.
    """
    key = await database.fetch_one(
        user_api_keys.select()
        .where(user_api_keys.c.id == key_id)
        .where(user_api_keys.c.user_id == user["id"])
        .where(user_api_keys.c.revoked_at.is_(None))
    )

    if not key:
        raise HTTPException(status_code=404, detail="API key not found")

    await database.execute(
        user_api_keys.update()
        .where(user_api_keys.c.id == key_id)
        .values(revoked_at=datetime.now(timezone.utc))
    )

    security_logger.info(
        "API key revoked",
        extra={
            "event": "api_key_revoked",
            "user_id": user["id"],
            "key_id": key_id,
            "key_name": key["name"],
            "key_prefix": key["key_prefix"],
        },
    )

    return {"message": "API key revoked"}
