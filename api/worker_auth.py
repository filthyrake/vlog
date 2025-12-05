"""Authentication middleware for Worker API."""
import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from api.database import database, worker_api_keys, workers

# API key header
api_key_header = APIKeyHeader(name="X-Worker-API-Key", auto_error=False)


def hash_api_key(key: str) -> str:
    """Hash an API key using SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def get_key_prefix(key: str) -> str:
    """Get the first 8 characters of an API key for efficient lookup."""
    return key[:8]


async def verify_worker_key(api_key: Optional[str] = Security(api_key_header)) -> dict:
    """
    Verify worker API key and return worker info.

    Raises HTTPException if the key is invalid, expired, or revoked.
    Returns the worker record as a dict on success.
    """
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key. Include X-Worker-API-Key header.",
        )

    # Extract prefix for efficient lookup
    prefix = get_key_prefix(api_key)
    key_hash = hash_api_key(api_key)

    # Query database for matching key
    key_record = await database.fetch_one(
        worker_api_keys.select()
        .where(worker_api_keys.c.key_prefix == prefix)
        .where(worker_api_keys.c.key_hash == key_hash)
        .where(worker_api_keys.c.revoked_at.is_(None))
    )

    if not key_record:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Check expiration
    now = datetime.now(timezone.utc)
    if key_record["expires_at"] and key_record["expires_at"] < now:
        raise HTTPException(status_code=401, detail="API key expired")

    # Update last_used_at (fire-and-forget, don't block on this)
    await database.execute(
        worker_api_keys.update()
        .where(worker_api_keys.c.id == key_record["id"])
        .values(last_used_at=now)
    )

    # Get worker info
    worker = await database.fetch_one(
        workers.select().where(workers.c.id == key_record["worker_id"])
    )

    if not worker:
        raise HTTPException(status_code=401, detail="Worker not found")

    if worker["status"] == "disabled":
        raise HTTPException(status_code=403, detail="Worker is disabled")

    return dict(worker)


async def get_worker_by_id(worker_id: str) -> Optional[dict]:
    """Get a worker by its UUID."""
    worker = await database.fetch_one(
        workers.select().where(workers.c.worker_id == worker_id)
    )
    return dict(worker) if worker else None
