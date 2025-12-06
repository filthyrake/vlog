"""
Simple in-memory cache for analytics endpoints with TTL support.
"""

import random
import time
from typing import Any, Dict, Optional


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
        }
