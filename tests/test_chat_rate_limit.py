import os
from unittest.mock import AsyncMock

import pytest

from api import studio_chat_ws as chat


@pytest.mark.asyncio
async def test_reconnecting_does_not_reset_local_limit(monkeypatch):
    monkeypatch.setattr(chat, "REDIS_URL", "")
    monkeypatch.setattr(chat.MessageRateLimiter, "_local_windows", {})
    first = chat.MessageRateLimiter("stream:alice", max_messages=2)
    second = chat.MessageRateLimiter("stream:alice", max_messages=2)
    assert (await first.is_allowed())[0]
    assert (await second.is_allowed())[0]
    assert not (await first.is_allowed())[0]
    assert (await chat.MessageRateLimiter("stream:bob", max_messages=2).is_allowed())[0]


@pytest.mark.asyncio
async def test_configured_redis_failure_is_closed(monkeypatch):
    monkeypatch.setattr(chat, "REDIS_URL", "redis://configured")
    monkeypatch.setattr(chat, "get_redis", AsyncMock(return_value=None))
    assert await chat.MessageRateLimiter("stream:alice").is_allowed() == (False, 1)


@pytest.mark.asyncio
async def test_redis_window_is_atomic_across_connections(monkeypatch):
    import asyncio
    import uuid

    from redis.asyncio import Redis
    url = os.environ.get("VLOG_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set VLOG_TEST_REDIS_URL to an isolated Redis instance")
    redis = Redis.from_url(url)
    key = "test:" + uuid.uuid4().hex
    monkeypatch.setattr(chat, "REDIS_URL", url)
    monkeypatch.setattr(chat, "get_redis", AsyncMock(return_value=redis))
    try:
        results = await asyncio.gather(*[
            chat.MessageRateLimiter(key, max_messages=3, window_seconds=1).is_allowed()
            for _ in range(12)
        ])
        assert sum(allowed for allowed, _ in results) == 3
        await asyncio.sleep(1.1)
        assert (await chat.MessageRateLimiter(key, max_messages=3, window_seconds=1).is_allowed())[0]
    finally:
        await redis.delete("vlog:chat:rate:" + key)
        await redis.aclose()
