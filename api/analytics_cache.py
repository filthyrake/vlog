"""
Analytics cache with TTL support.

Provides two implementations:
- AnalyticsCache: In-memory cache for single-process deployments
- RedisAnalyticsCache: Redis-backed cache for multi-instance deployments

Use create_analytics_cache() factory function to get the appropriate implementation.
"""

import json
import logging
import random
import time
from typing import Any, Dict, Optional, Union, Callable, TypeVar
T = TypeVar("T")

logger = logging.getLogger(__name__)


class AnalyticsCache:
    """
    Simple in-memory cache with TTL (Time To Live) for analytics data.

    Note: Designed for single-process FastAPI deployment. For multi-worker
    deployments, each worker maintains its own cache. Consider using Redis
    or similar for shared caching across multiple processes/servers.
    """

    CLEANUP_PROBABILITY = 0.01  # 1% chance of cleanup on each set operation

    def __init__(self, ttl_seconds: int = 60, enabled: bool = True, max_size: int = 1000):
        """
        Initialize the cache.

        Args:
            ttl_seconds: Time to live in seconds for cache entries
            enabled: Whether caching is enabled
            max_size: Maximum number of entries before triggering eviction
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._ttl = ttl_seconds
        self._enabled = enabled
        self._max_size = max_size

    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from the cache.

        Args:
            key: Cache key

        Returns:
            Cached value if exists and not expired, None otherwise
        """
        if not self._enabled:
            return None

        cached = self._cache.get(key)
        if cached is None:
            return None

        # Check if expired
        if time.time() - cached["timestamp"] > self._ttl:
            # Remove expired entry
            del self._cache[key]
            return None

        return cached["data"]

    def set(self, key: str, value: Any) -> None:
        """
        Set a value in the cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        if not self._enabled:
            return

        # Probabilistic cleanup to amortize cleanup cost and prevent unbounded growth
        if random.random() < self.CLEANUP_PROBABILITY:
            self.cleanup_expired()

        # Evict oldest entries if at capacity (only when adding new keys)
        if key not in self._cache and len(self._cache) >= self._max_size:
            self.cleanup_expired()

            # If still at capacity after cleanup, remove oldest 10% via LRU
            # Note: This uses O(n log n) sorting but only occurs when cache has
            # max_size non-expired entries, which is rare due to probabilistic cleanup
            # and TTL expiration. For default max_size of 1000, performance is acceptable.
            if len(self._cache) >= self._max_size:
                items = sorted(self._cache.items(), key=lambda x: x[1]["timestamp"])
                evict_count = max(1, len(items) // 10)
                for k, _ in items[:evict_count]:
                    del self._cache[k]

        self._cache[key] = {
            "data": value,
            "timestamp": time.time(),
        }

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()

    def invalidate(self, key: str) -> None:
        """
        Invalidate a specific cache entry.

        Args:
            key: Cache key to invalidate
        """
        if key in self._cache:
            del self._cache[key]

    def cleanup_expired(self) -> int:
        """
        Remove all expired entries from cache.

        Returns:
            Number of entries removed
        """
        if not self._enabled:
            return 0

        now = time.time()
        expired_keys = [key for key, cached in self._cache.items() if now - cached["timestamp"] > self._ttl]

        for key in expired_keys:
            del self._cache[key]

        return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with cache stats (size, TTL, enabled status, max size)
        """
        return {
            "enabled": self._enabled,
            "ttl_seconds": self._ttl,
            "entry_count": len(self._cache),
            "max_size": self._max_size,
            "backend": "memory",
        }


class RedisAnalyticsCache:
    """
    Redis-backed cache with TTL for analytics data.

    Provides shared caching across multiple API instances for consistent
    analytics in multi-process/multi-server deployments.

    Falls back gracefully to returning None (cache miss) if Redis is unavailable.
    """

    CACHE_KEY_PREFIX = "vlog:analytics:"

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int = 60,
        enabled: bool = True,
    ):
        """
        Initialize the Redis cache.

        Args:
            redis_url: Redis connection URL (e.g., "redis://localhost:6379")
            ttl_seconds: Time to live in seconds for cache entries
            enabled: Whether caching is enabled
        """
        self._redis_url = redis_url
        self._ttl = ttl_seconds
        self._enabled = enabled
        self._client: Optional[Any] = None
        self._connection_failed = False

        if enabled:
            self._initialize_client()

    def _initialize_client(self) -> None:
        """Initialize the Redis client."""
        try:
            import redis # Lazy import
            self._client = redis.Redis.from_url(
                self._redis_url,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
                decode_responses=True,
            )
            # Test connection
            self._client.ping()
            logger.info(f"Redis analytics cache connected: {self._redis_url.split('@')[-1]}")
        except Exception as e:
            # Catch broad exceptions to avoid hard dependency on redis
            logger.warning(f"Redis analytics cache connection failed: {e}")
            self._client = None
            self._connection_failed = True

    def _get_full_key(self, key: str) -> str:
        """Get the full Redis key with prefix."""
        return f"{self.CACHE_KEY_PREFIX}{key}"
    
    def _safe_redis_call(
        self,
        operation: Callable[[], T],
        operation_name: str,
        fallback: Optional[T] = None
    ) -> Optional[T]:

        if not self._enabled or self._client is None:
            return fallback
        try:
            return operation()
        except Exception as e:
            logger.warning(f"Redis analytics cache {operation_name} failed: {e}")
            return fallback

    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from the cache.

        Args:
            key: Cache key

        Returns:
            Cached value if exists and not expired, None otherwise
        """
        def get_operation():
            data = self._client.get(self._get_full_key(key))
            return None if data is None else json.loads(data)

        return self._safe_redis_call(
            get_operation,
            "get",
            fallback=None,
        )

    def set(self, key: str, value: Any) -> None:
        """
        Set a value in the cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        self._safe_redis_call(
            lambda: self._client.setex(
                self._get_full_key(key),
                self._ttl,
                json.dumps(value),
            ),
            "set",
        )

    def clear(self) -> None:
        """Clear all analytics cache entries."""
        def operation():
            cursor = 0
            pattern = f"{self.CACHE_KEY_PREFIX}*"
            while True:
                cursor, keys = self._client.scan(cursor, match=pattern, count=100)
                if keys:
                    self._client.delete(*keys)
                if cursor == 0:
                    break

        self._safe_redis_call(operation, "clear")

    def invalidate(self, key: str) -> None:
        """
        Invalidate a specific cache entry.

        Args:
            key: Cache key to invalidate
        """
        self._safe_redis_call(
            lambda: self._client.delete(self._get_full_key(key)),
            "invalidate",
        )

    def cleanup_expired(self) -> int:
        """
        Remove all expired entries from cache.

        Redis handles TTL-based expiration automatically, so this is a no-op.

        Returns:
            Always returns 0 (Redis handles expiration)
        """
        # Redis handles TTL expiration automatically
        return 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with cache stats (TTL, enabled status, backend type)
            # Redis has no fixed max size
        """
        def count_keys():
            entry_count = 0
            cursor = 0
            pattern = f"{self.CACHE_KEY_PREFIX}*"
            while True:
                cursor, keys = self._client.scan(cursor, match=pattern, count=100)
                entry_count += len(keys)
                if cursor == 0:
                    break
            return entry_count

        entry_count = self._safe_redis_call(count_keys, "count", fallback=0)

        return {
            "enabled": self._enabled,
            "ttl_seconds": self._ttl,
            "entry_count": entry_count,
            "max_size": -1,
            "backend": "redis",
            "connected": self._client is not None and not self._connection_failed,
        }


# Type alias for either cache implementation
AnalyticsCacheType = Union[AnalyticsCache, RedisAnalyticsCache]


def create_analytics_cache(
    storage_url: str = "memory://",
    ttl_seconds: int = 60,
    enabled: bool = True,
    max_size: int = 1000,
) -> AnalyticsCacheType:
    """
    Factory function to create the appropriate analytics cache implementation.

    Args:
        storage_url: Storage backend URL. Use "memory://" for in-memory cache,
                    or a Redis URL like "redis://localhost:6379" for shared cache.
        ttl_seconds: Time to live in seconds for cache entries
        enabled: Whether caching is enabled
        max_size: Maximum entries for in-memory cache (ignored for Redis)

    Returns:
        Either AnalyticsCache (memory) or RedisAnalyticsCache (Redis) instance
    """
    if not enabled:
        # Return disabled memory cache - simplest option
        return AnalyticsCache(ttl_seconds=ttl_seconds, enabled=False, max_size=max_size)

    if storage_url.startswith("redis://") or storage_url.startswith("rediss://"):
        return RedisAnalyticsCache(
            redis_url=storage_url,
            ttl_seconds=ttl_seconds,
            enabled=enabled,
        )

    # Default to in-memory cache
    return AnalyticsCache(
        ttl_seconds=ttl_seconds,
        enabled=enabled,
        max_size=max_size,
    )
