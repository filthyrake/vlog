"""Always-on password recovery limits using the application's shared rate storage.

Redis errors fail closed. memory:// is suitable only for a single process;
production must configure Redis for limits shared across workers and replicas.
"""

import asyncio
import hashlib
from functools import lru_cache

from fastapi import HTTPException
from limits import parse
from limits.storage import storage_from_string
from limits.strategies import FixedWindowRateLimiter

from config import RATE_LIMIT_STORAGE_URL


@lru_cache(maxsize=1)
def _limiter():
    return FixedWindowRateLimiter(storage_from_string(RATE_LIMIT_STORAGE_URL))


async def enforce_reset_limit(scope: str, identity: str, limit: str) -> None:
    # Do not retain email addresses or raw recovery tokens in rate-limit keys.
    key = hashlib.sha256(identity.encode()).hexdigest()
    try:
        allowed = await asyncio.to_thread(_limiter().hit, parse(limit), "password-recovery", scope, key)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Password recovery temporarily unavailable") from exc
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many password recovery requests", headers={"Retry-After": "3600"})
