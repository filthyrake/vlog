"""
Redis client with connection pooling and graceful fallback.

Provides:
- Shared async connection pool across all services
- Automatic reconnection with exponential backoff
- Circuit breaker pattern to prevent cascade failures
- Graceful degradation when Redis unavailable
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, TypeVar

from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from config import (
    REDIS_HEALTH_CHECK_INTERVAL,
    REDIS_POOL_SIZE,
    REDIS_SOCKET_CONNECT_TIMEOUT,
    REDIS_SOCKET_TIMEOUT,
    REDIS_URL,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RedisClient:
    """Singleton Redis client with connection pooling and health monitoring."""

    _instance: Optional["RedisClient"] = None
    _lock: Optional[asyncio.Lock] = None
    _initialized: bool = False

    def __init__(self) -> None:
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[Redis] = None
        self._healthy: bool = False
        self._last_health_check: Optional[datetime] = None
        self._consecutive_failures: int = 0
        self._circuit_open: bool = False
        self._circuit_open_until: Optional[datetime] = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Get or create the asyncio lock, ensuring it's bound to the current event loop."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
            return cls._lock

        try:
            current_loop = asyncio.get_running_loop()
            # Check if lock is bound to a different loop (Python 3.9 compatibility)
            lock_loop = getattr(cls._lock, "_loop", None)
            if lock_loop is not None and lock_loop is not current_loop:
                cls._lock = asyncio.Lock()
        except RuntimeError:
            # No running loop, create new lock
            cls._lock = asyncio.Lock()

        return cls._lock

    @classmethod
    async def get_instance(cls) -> "RedisClient":
        """Get or create the singleton instance."""
        async with cls._get_lock():
            if cls._instance is None:
                cls._instance = cls()
            if not cls._initialized:
                await cls._instance._initialize()
                cls._initialized = True
            return cls._instance

    @classmethod
    async def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        async with cls._get_lock():
            if cls._instance is not None:
                await cls._instance.close()
                cls._instance = None
                cls._initialized = False

    async def _initialize(self) -> None:
        """Initialize the connection pool."""
        if not REDIS_URL:
            logger.info("Redis URL not configured, Redis features disabled")
            return

        try:
            self._pool = ConnectionPool.from_url(
                REDIS_URL,
                max_connections=REDIS_POOL_SIZE,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
                retry_on_error=[RedisConnectionError],
                decode_responses=True,
            )
            self._client = Redis(connection_pool=self._pool)

            # Test connection
            await self._client.ping()
            self._healthy = True
            self._last_health_check = datetime.now(timezone.utc)
            logger.info(f"Redis connection established: {REDIS_URL.split('@')[-1]}")
        except Exception as e:
            logger.warning(f"Redis connection failed during initialization: {e}")
            self._healthy = False

    @property
    def is_configured(self) -> bool:
        """Check if Redis URL is configured."""
        return bool(REDIS_URL)

    @property
    def is_available(self) -> bool:
        """Check if Redis is currently available (respects circuit breaker)."""
        if not REDIS_URL or self._client is None:
            return False

        if self._circuit_open:
            now = datetime.now(timezone.utc)
            if self._circuit_open_until and now < self._circuit_open_until:
                return False
            # Try to close circuit
            self._circuit_open = False
            logger.info("Redis circuit breaker closing, attempting reconnection")

        return self._healthy

    async def get_client(self) -> Optional[Redis]:
        """Get the Redis client, returns None if unavailable."""
        if not self.is_available:
            return None
        return self._client

    async def execute_with_fallback(
        self,
        redis_fn: Callable[..., Any],
        fallback_fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        Execute Redis operation with fallback on failure.

        Args:
            redis_fn: Async function that takes Redis client as first arg
            fallback_fn: Async function to call if Redis fails
            *args: Additional args passed to both functions
            **kwargs: Additional kwargs passed to both functions

        Returns:
            Result from either redis_fn or fallback_fn
        """
        if not self.is_available:
            return await fallback_fn(*args, **kwargs)

        try:
            result = await redis_fn(self._client, *args, **kwargs)
            self._record_success()
            return result
        except RedisError as e:
            logger.warning(f"Redis operation failed, using fallback: {e}")
            self._record_failure()
            return await fallback_fn(*args, **kwargs)

    def _record_failure(self) -> None:
        """Record a failure and potentially open circuit breaker."""
        self._consecutive_failures += 1
        self._healthy = False

        if self._consecutive_failures >= 3:
            self._circuit_open = True
            # Exponential backoff: 30s, 60s, 120s, 240s, max 300s
            backoff = min(300, 30 * (2 ** (self._consecutive_failures - 3)))
            self._circuit_open_until = datetime.now(timezone.utc) + timedelta(seconds=backoff)
            logger.warning(
                f"Redis circuit breaker opened for {backoff}s (consecutive failures: {self._consecutive_failures})"
            )

    def _record_success(self) -> None:
        """Record a successful operation."""
        if self._consecutive_failures > 0:
            logger.info(f"Redis connection recovered after {self._consecutive_failures} failures")
        self._consecutive_failures = 0
        self._healthy = True
        self._circuit_open = False
        self._circuit_open_until = None

    async def health_check(self) -> bool:
        """
        Perform health check on Redis connection.

        Returns:
            True if healthy, False otherwise
        """
        if not self._client:
            return False

        # Skip if recently checked
        if self._last_health_check:
            elapsed = (datetime.now(timezone.utc) - self._last_health_check).total_seconds()
            if elapsed < REDIS_HEALTH_CHECK_INTERVAL:
                return self._healthy

        try:
            await self._client.ping()
            self._record_success()
            self._last_health_check = datetime.now(timezone.utc)
            return True
        except Exception as e:
            logger.warning(f"Redis health check failed: {e}")
            self._record_failure()
            return False

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._client:
            try:
                await self._client.close()
            except Exception as e:
                # Ignore close errors during shutdown
                logger.debug(f"Exception while closing Redis client: {e}")
        if self._pool:
            try:
                await self._pool.disconnect()
            except Exception as e:
                # Ignore disconnect errors during shutdown
                logger.debug(f"Exception while disconnecting Redis pool: {e}")
        self._client = None
        self._pool = None
        self._healthy = False


async def get_redis() -> Optional[Redis]:
    """
    Convenience function to get Redis client.

    Returns:
        Redis client if available, None otherwise
    """
    client = await RedisClient.get_instance()
    return await client.get_client()


async def is_redis_available() -> bool:
    """
    Check if Redis is available.

    Returns:
        True if Redis is configured and healthy
    """
    client = await RedisClient.get_instance()
    return client.is_available
