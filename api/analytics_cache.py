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
from typing import Any, Callable, Dict, Optional, TypeVar, Union

T = TypeVar("T")

logger = logging.getLogger(__name__)

# Cache configuration constants
DEFAULT_CACHE_TTL_SECONDS = 60  # Default time-to-live for cache entries
DEFAULT_CACHE_MAX_SIZE = 1000  # Maximum entries before triggering eviction
CACHE_EVICTION_RATIO = 0.10  # Evict 10% of entries when cache is full

# Redis connection constants
# 5 second timeout balances fast failure detection with tolerance for network latency
REDIS_SOCKET_TIMEOUT = 5.0
REDIS_CONNECT_TIMEOUT = 5.0
# Batch size of 100 balances memory usage vs network round-trips for SCAN operations
REDIS_SCAN_BATCH_SIZE = 100
# Maximum time (seconds) for bulk operations like clear() to prevent blocking
REDIS_BULK_OPERATION_TIMEOUT = 5.0
# Reconnection settings for transient failures
REDIS_RECONNECT_MIN_INTERVAL = 1.0  # Minimum seconds between reconnection attempts
REDIS_RECONNECT_MAX_INTERVAL = 60.0  # Maximum backoff interval


class AnalyticsCache:
    """
    Simple in-memory cache with TTL (Time To Live) for analytics data.

    Note: Designed for single-process FastAPI deployment. For multi-worker
    deployments, each worker maintains its own cache. Consider using Redis
    or similar for shared caching across multiple processes/servers.
    """

    CLEANUP_PROBABILITY = 0.01  # 1% chance of cleanup on each set operation

    def __init__(self, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS, enabled: bool = True, max_size: int = DEFAULT_CACHE_MAX_SIZE):
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
                evict_count = max(1, int(len(items) * CACHE_EVICTION_RATIO))
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
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
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
        self._last_reconnect_attempt: Optional[float] = None
        self._reconnect_backoff = REDIS_RECONNECT_MIN_INTERVAL

        if enabled:
            self._initialize_client()

    def _initialize_client(self) -> bool:
        """
        Initialize the Redis client.

        Returns:
            True if connection successful, False otherwise.
        """
        try:
            import redis  # Lazy import
            self._client = redis.Redis.from_url(
                self._redis_url,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT,
                decode_responses=True,
            )
            # Test connection (respects socket_timeout)
            self._client.ping()
            logger.info(f"Redis analytics cache connected: {self._redis_url.split('@')[-1]}")
            self._connection_failed = False
            self._reconnect_backoff = REDIS_RECONNECT_MIN_INTERVAL
            return True
        except Exception as e:
            # Catch broad exceptions to avoid hard dependency on redis
            logger.warning(f"Redis analytics cache connection failed: {e}")
            self._client = None
            self._connection_failed = True
            return False

    def _maybe_reconnect(self) -> bool:
        """
        Attempt reconnection if enough time has passed since last attempt.

        Uses exponential backoff to avoid hammering a failing Redis server.

        Returns:
            True if connected (either already or after reconnect), False otherwise.
        """
        if not self._connection_failed:
            return self._client is not None

        now = time.time()
        if self._last_reconnect_attempt is not None:
            elapsed = now - self._last_reconnect_attempt
            if elapsed < self._reconnect_backoff:
                return False

        self._last_reconnect_attempt = now
        logger.info(f"Attempting Redis reconnection (backoff: {self._reconnect_backoff}s)")

        if self._initialize_client():
            logger.info("Redis analytics cache reconnected successfully")
            return True

        # Increase backoff for next attempt (exponential with cap)
        self._reconnect_backoff = min(
            self._reconnect_backoff * 2,
            REDIS_RECONNECT_MAX_INTERVAL
        )
        return False

    def _get_full_key(self, key: str) -> str:
        """Get the full Redis key with prefix."""
        return f"{self.CACHE_KEY_PREFIX}{key}"

    def _safe_redis_call(
        self,
        operation: Callable[[], T],
        operation_name: str,
        fallback: Optional[T] = None
    ) -> Optional[T]:
        """
        Execute a Redis operation with error handling and automatic reconnection.

        Args:
            operation: The Redis operation to execute
            operation_name: Name for logging purposes
            fallback: Value to return on failure

        Returns:
            Operation result or fallback value on failure.
        """
        if not self._enabled:
            return fallback

        # Attempt reconnection if previously failed
        if self._client is None or self._connection_failed:
            if not self._maybe_reconnect():
                return fallback

        try:
            return operation()
        except Exception as e:
            logger.warning(f"Redis analytics cache {operation_name} failed: {e}")
            # Mark as failed to trigger reconnection on next call
            self._connection_failed = True
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
        """
        Clear all analytics cache entries.

        Uses a timeout to prevent blocking indefinitely on large keyspaces.
        If the operation times out, some keys may remain.
        """
        def operation():
            start_time = time.time()
            cursor = 0
            pattern = f"{self.CACHE_KEY_PREFIX}*"
            keys_deleted = 0
            while True:
                # Check timeout to prevent blocking on large keyspaces
                if time.time() - start_time > REDIS_BULK_OPERATION_TIMEOUT:
                    logger.warning(
                        f"Redis clear timed out after {REDIS_BULK_OPERATION_TIMEOUT}s, "
                        f"deleted {keys_deleted} keys, some may remain"
                    )
                    return
                cursor, keys = self._client.scan(cursor, match=pattern, count=REDIS_SCAN_BATCH_SIZE)
                if keys:
                    self._client.delete(*keys)
                    keys_deleted += len(keys)
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
            Dict with cache stats (TTL, enabled status, backend type, connection status)
        """
        def count_keys():
            start_time = time.time()
            entry_count = 0
            cursor = 0
            pattern = f"{self.CACHE_KEY_PREFIX}*"
            while True:
                # Check timeout to prevent blocking on large keyspaces
                if time.time() - start_time > REDIS_BULK_OPERATION_TIMEOUT:
                    logger.warning(
                        f"Redis key count timed out after {REDIS_BULK_OPERATION_TIMEOUT}s, "
                        f"returning partial count: {entry_count}"
                    )
                    return entry_count
                cursor, keys = self._client.scan(cursor, match=pattern, count=REDIS_SCAN_BATCH_SIZE)
                entry_count += len(keys)
                if cursor == 0:
                    break
            return entry_count

        entry_count = self._safe_redis_call(count_keys, "count", fallback=0)

        return {
            "enabled": self._enabled,
            "ttl_seconds": self._ttl,
            "entry_count": entry_count,
            "max_size": -1,  # Redis has no fixed max size
            "backend": "redis",
            "connected": self._client is not None and not self._connection_failed,
        }


# Type alias for either cache implementation
AnalyticsCacheType = Union[AnalyticsCache, RedisAnalyticsCache]


def create_analytics_cache(
    storage_url: str = "memory://",
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    enabled: bool = True,
    max_size: int = DEFAULT_CACHE_MAX_SIZE,
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
