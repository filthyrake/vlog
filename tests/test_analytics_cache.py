"""
Tests for analytics caching functionality.
"""

import time

import pytest

from api.analytics_cache import AnalyticsCache


class TestAnalyticsCache:
    """Test suite for AnalyticsCache."""

    def test_cache_initialization(self):
        """Test cache initializes with correct default values."""
        cache = AnalyticsCache()
        stats = cache.get_stats()
        assert stats["enabled"] is True
        assert stats["ttl_seconds"] == 60
        assert stats["entry_count"] == 0

    def test_cache_initialization_custom_values(self):
        """Test cache initializes with custom values."""
        cache = AnalyticsCache(ttl_seconds=120, enabled=False)
        stats = cache.get_stats()
        assert stats["enabled"] is False
        assert stats["ttl_seconds"] == 120
        assert stats["entry_count"] == 0

    def test_cache_set_and_get(self):
        """Test basic cache set and get operations."""
        cache = AnalyticsCache(ttl_seconds=60)
        test_data = {"total_views": 100, "unique_viewers": 50}

        # Set data
        cache.set("test_key", test_data)

        # Get data
        result = cache.get("test_key")
        assert result == test_data

    def test_cache_get_nonexistent_key(self):
        """Test getting a non-existent key returns None."""
        cache = AnalyticsCache()
        result = cache.get("nonexistent_key")
        assert result is None

    def test_cache_expiration(self):
        """Test cache entries expire after TTL."""
        cache = AnalyticsCache(ttl_seconds=1)  # 1 second TTL
        test_data = {"total_views": 100}

        # Set data
        cache.set("test_key", test_data)

        # Should exist immediately
        result = cache.get("test_key")
        assert result == test_data

        # Wait for expiration
        time.sleep(1.1)

        # Should be expired
        result = cache.get("test_key")
        assert result is None

    def test_cache_disabled(self):
        """Test cache doesn't store data when disabled."""
        cache = AnalyticsCache(enabled=False)
        test_data = {"total_views": 100}

        # Try to set data
        cache.set("test_key", test_data)

        # Should not be cached
        result = cache.get("test_key")
        assert result is None

    def test_cache_clear(self):
        """Test clearing all cache entries."""
        cache = AnalyticsCache()
        cache.set("key1", {"data": 1})
        cache.set("key2", {"data": 2})
        cache.set("key3", {"data": 3})

        # Verify data exists
        assert cache.get("key1") is not None
        assert cache.get("key2") is not None
        assert cache.get("key3") is not None

        # Clear cache
        cache.clear()

        # Verify all data is gone
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.get("key3") is None

    def test_cache_invalidate(self):
        """Test invalidating a specific cache entry."""
        cache = AnalyticsCache()
        cache.set("key1", {"data": 1})
        cache.set("key2", {"data": 2})

        # Invalidate key1
        cache.invalidate("key1")

        # key1 should be gone, key2 should still exist
        assert cache.get("key1") is None
        assert cache.get("key2") == {"data": 2}

    def test_cache_invalidate_nonexistent_key(self):
        """Test invalidating a non-existent key doesn't raise error."""
        cache = AnalyticsCache()
        # Should not raise an error
        cache.invalidate("nonexistent_key")

    def test_cleanup_expired(self):
        """Test cleanup of expired entries."""
        cache = AnalyticsCache(ttl_seconds=1)
        
        # Add some entries
        cache.set("key1", {"data": 1})
        cache.set("key2", {"data": 2})
        cache.set("key3", {"data": 3})

        # Wait for expiration
        time.sleep(1.1)

        # Add a new entry that shouldn't expire
        cache.set("key4", {"data": 4})

        # Cleanup expired entries
        removed_count = cache.cleanup_expired()

        # Should have removed 3 expired entries
        assert removed_count == 3

        # Only key4 should remain
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.get("key3") is None
        assert cache.get("key4") == {"data": 4}

    def test_cleanup_expired_disabled_cache(self):
        """Test cleanup returns 0 when cache is disabled."""
        cache = AnalyticsCache(enabled=False)
        removed_count = cache.cleanup_expired()
        assert removed_count == 0

    def test_cache_stats(self):
        """Test cache statistics."""
        cache = AnalyticsCache(ttl_seconds=120, enabled=True)
        
        # Initially empty
        stats = cache.get_stats()
        assert stats["entry_count"] == 0
        
        # Add some entries
        cache.set("key1", {"data": 1})
        cache.set("key2", {"data": 2})
        
        stats = cache.get_stats()
        assert stats["entry_count"] == 2
        assert stats["ttl_seconds"] == 120
        assert stats["enabled"] is True

    def test_cache_overwrites_existing_key(self):
        """Test that setting the same key overwrites the old value."""
        cache = AnalyticsCache()
        
        cache.set("test_key", {"value": 1})
        assert cache.get("test_key") == {"value": 1}
        
        cache.set("test_key", {"value": 2})
        assert cache.get("test_key") == {"value": 2}

    def test_cache_multiple_data_types(self):
        """Test cache can store different data types."""
        cache = AnalyticsCache()
        
        # Dictionary
        cache.set("dict_key", {"a": 1, "b": 2})
        assert cache.get("dict_key") == {"a": 1, "b": 2}
        
        # List
        cache.set("list_key", [1, 2, 3])
        assert cache.get("list_key") == [1, 2, 3]
        
        # String
        cache.set("string_key", "test_value")
        assert cache.get("string_key") == "test_value"
        
        # Number
        cache.set("number_key", 42)
        assert cache.get("number_key") == 42

    def test_cache_key_generation_consistency(self):
        """Test that cache keys are consistent for the same parameters."""
        cache = AnalyticsCache()
        
        # Simulate cache key generation for analytics endpoints
        params1 = {"limit": 50, "offset": 0, "sort_by": "views", "period": "all"}
        key1 = f"analytics_videos:{params1['limit']}:{params1['offset']}:{params1['sort_by']}:{params1['period']}"
        
        params2 = {"limit": 50, "offset": 0, "sort_by": "views", "period": "all"}
        key2 = f"analytics_videos:{params2['limit']}:{params2['offset']}:{params2['sort_by']}:{params2['period']}"
        
        assert key1 == key2
        
        # Different parameters should generate different keys
        params3 = {"limit": 100, "offset": 0, "sort_by": "views", "period": "all"}
        key3 = f"analytics_videos:{params3['limit']}:{params3['offset']}:{params3['sort_by']}:{params3['period']}"
        
        assert key1 != key3
