"""
Simple in-memory cache for analytics endpoints with TTL support.
"""

import time
from typing import Any, Dict, Optional


class AnalyticsCache:
    """
    Simple in-memory cache with TTL (Time To Live) for analytics data.
    
    Note: Designed for single-process FastAPI deployment. For multi-worker
    deployments, each worker maintains its own cache. Consider using Redis
    or similar for shared caching across multiple processes/servers.
    """

    def __init__(self, ttl_seconds: int = 60, enabled: bool = True):
        """
        Initialize the cache.
        
        Args:
            ttl_seconds: Time to live in seconds for cache entries
            enabled: Whether caching is enabled
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._ttl = ttl_seconds
        self._enabled = enabled

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
        expired_keys = [
            key
            for key, cached in self._cache.items()
            if now - cached["timestamp"] > self._ttl
        ]

        for key in expired_keys:
            del self._cache[key]

        return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dict with cache stats (size, TTL, enabled status)
        """
        return {
            "enabled": self._enabled,
            "ttl_seconds": self._ttl,
            "entry_count": len(self._cache),
        }
