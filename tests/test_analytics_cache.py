"""
Tests for analytics caching functionality.
"""

import time
from unittest.mock import MagicMock, patch

from api.analytics_cache import (
    AnalyticsCache,
    RedisAnalyticsCache,
    create_analytics_cache,
)


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

    def test_probabilistic_cleanup_on_set(self):
        """Test that probabilistic cleanup occurs during set operations."""
        cache = AnalyticsCache(ttl_seconds=1)

        # Add entries that will expire
        for i in range(10):
            cache.set(f"old_key_{i}", {"data": i})

        # Wait for expiration
        time.sleep(1.1)

        # Add many new entries to trigger probabilistic cleanup
        # With 1% probability, we expect cleanup after ~100 sets on average
        # Add 500 to ensure we hit cleanup at least once
        for i in range(500):
            cache.set(f"new_key_{i}", {"data": i})

        # Check that old entries were cleaned up
        # At least some of them should be gone due to probabilistic cleanup
        old_entries_count = sum(1 for i in range(10) if cache.get(f"old_key_{i}") is not None)
        assert old_entries_count == 0, "Old expired entries should have been cleaned up"

    def test_max_size_enforcement(self):
        """Test that cache enforces max_size limit."""
        cache = AnalyticsCache(ttl_seconds=60, max_size=10)

        # Add entries up to max_size
        for i in range(10):
            cache.set(f"key_{i}", {"data": i})

        stats = cache.get_stats()
        assert stats["entry_count"] == 10
        assert stats["max_size"] == 10

        # Add one more entry, triggering eviction
        cache.set("key_10", {"data": 10})

        # Cache should still be at or below max_size
        stats = cache.get_stats()
        assert stats["entry_count"] <= 10

    def test_max_size_lru_eviction(self):
        """Test that LRU eviction removes oldest entries when at capacity."""
        cache = AnalyticsCache(ttl_seconds=3600, max_size=10)  # Long TTL to prevent expiry-based cleanup

        # Add 10 entries
        for i in range(10):
            cache.set(f"key_{i}", {"data": i})
            time.sleep(0.01)  # Small delay to ensure different timestamps

        # Verify all 10 entries exist
        assert cache.get_stats()["entry_count"] == 10

        # Add 5 more entries, should trigger LRU eviction
        for i in range(10, 15):
            cache.set(f"key_{i}", {"data": i})
            time.sleep(0.01)

        # Cache should be at or below max_size
        stats = cache.get_stats()
        assert stats["entry_count"] <= 10

        # Oldest entries should be evicted
        # When we hit max_size, we evict 10% (1 entry) at a time
        # So after adding 5 more, we should have evicted at least the oldest entries
        # But at least some old entries should be gone
        old_entries_remaining = sum(1 for i in range(5) if cache.get(f"key_{i}") is not None)
        assert old_entries_remaining < 5, "Some oldest entries should have been evicted"

        # Newest entries should still be present
        assert cache.get("key_14") is not None

    def test_max_size_with_expired_entries(self):
        """Test that expired entries are cleaned before LRU eviction."""
        cache = AnalyticsCache(ttl_seconds=1, max_size=10)

        # Add 10 entries
        for i in range(10):
            cache.set(f"old_key_{i}", {"data": i})

        # Wait for expiration
        time.sleep(1.1)

        # Add new entry - should clean up expired entries instead of LRU eviction
        cache.set("new_key", {"data": "new"})

        # Old entries should be gone (expired)
        for i in range(10):
            assert cache.get(f"old_key_{i}") is None

        # New entry should exist
        assert cache.get("new_key") == {"data": "new"}

        # Cache should have only 1 entry
        assert cache.get_stats()["entry_count"] == 1

    def test_max_size_custom_value(self):
        """Test cache initialization with custom max_size."""
        cache = AnalyticsCache(ttl_seconds=60, max_size=500)
        stats = cache.get_stats()
        assert stats["max_size"] == 500

    def test_max_size_default_value(self):
        """Test cache uses default max_size of 1000."""
        cache = AnalyticsCache()
        stats = cache.get_stats()
        assert stats["max_size"] == 1000

    def test_eviction_with_disabled_cache(self):
        """Test that disabled cache doesn't perform eviction."""
        cache = AnalyticsCache(ttl_seconds=60, enabled=False, max_size=5)

        # Try to add more than max_size
        for i in range(10):
            cache.set(f"key_{i}", {"data": i})

        # Cache should remain empty (disabled)
        stats = cache.get_stats()
        assert stats["entry_count"] == 0

    def test_concurrent_set_operations_stay_under_limit(self):
        """Test that rapid set operations keep cache under max_size."""
        cache = AnalyticsCache(ttl_seconds=3600, max_size=50)

        # Rapidly add many entries
        for i in range(200):
            cache.set(f"key_{i}", {"data": i})

        # Cache should never exceed max_size
        stats = cache.get_stats()
        assert stats["entry_count"] <= 50
        assert stats["entry_count"] > 0  # Should have some entries

    def test_overwrite_at_capacity_no_eviction(self):
        """Test that overwriting an existing key at capacity doesn't trigger eviction."""
        cache = AnalyticsCache(ttl_seconds=3600, max_size=10)

        # Fill cache to capacity
        for i in range(10):
            cache.set(f"key_{i}", {"data": i})

        # Verify we're at capacity
        assert cache.get_stats()["entry_count"] == 10

        # Overwrite an existing key - should not trigger eviction
        cache.set("key_5", {"data": "updated"})

        # All 10 entries should still exist (no eviction)
        assert cache.get_stats()["entry_count"] == 10
        assert cache.get("key_5") == {"data": "updated"}

        # Verify all original keys are still present
        for i in range(10):
            assert cache.get(f"key_{i}") is not None

    def test_get_stats_includes_backend(self):
        """Test that get_stats includes backend type."""
        cache = AnalyticsCache()
        stats = cache.get_stats()
        assert stats["backend"] == "memory"


class TestRedisAnalyticsCache:
    """Test suite for RedisAnalyticsCache with mocked Redis."""

    def test_initialization_with_mock_redis(self):
        """Test cache initializes with mocked Redis."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=120,
                enabled=True,
            )

            assert cache._enabled is True
            assert cache._ttl == 120
            mock_client.ping.assert_called_once()

    def test_initialization_disabled(self):
        """Test cache doesn't connect when disabled."""
        cache = RedisAnalyticsCache(
            redis_url="redis://localhost:6379",
            ttl_seconds=60,
            enabled=False,
        )

        assert cache._enabled is False
        assert cache._client is None

    def test_get_returns_none_when_disabled(self):
        """Test get returns None when cache is disabled."""
        cache = RedisAnalyticsCache(
            redis_url="redis://localhost:6379",
            ttl_seconds=60,
            enabled=False,
        )

        result = cache.get("test_key")
        assert result is None

    def test_set_does_nothing_when_disabled(self):
        """Test set does nothing when cache is disabled."""
        cache = RedisAnalyticsCache(
            redis_url="redis://localhost:6379",
            ttl_seconds=60,
            enabled=False,
        )

        # Should not raise any errors
        cache.set("test_key", {"data": 1})
        assert cache.get("test_key") is None

    def test_get_and_set_with_mock_redis(self):
        """Test get and set operations with mocked Redis."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=60,
                enabled=True,
            )

            # Test set
            test_data = {"total_views": 100, "unique_viewers": 50}
            cache.set("test_key", test_data)
            mock_client.setex.assert_called_once()

            # Verify the call arguments
            call_args = mock_client.setex.call_args
            assert call_args[0][0] == "vlog:analytics:test_key"
            assert call_args[0][1] == 60

    def test_get_with_mock_redis(self):
        """Test get operation with mocked Redis."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client

            # Configure mock to return JSON data
            mock_client.get.return_value = '{"total_views": 100}'

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=60,
                enabled=True,
            )

            result = cache.get("test_key")
            assert result == {"total_views": 100}
            mock_client.get.assert_called_with("vlog:analytics:test_key")

    def test_get_returns_none_for_missing_key(self):
        """Test get returns None for missing key."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client
            mock_client.get.return_value = None

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=60,
                enabled=True,
            )

            result = cache.get("nonexistent_key")
            assert result is None

    def test_invalidate_with_mock_redis(self):
        """Test invalidate operation with mocked Redis."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=60,
                enabled=True,
            )

            cache.invalidate("test_key")
            mock_client.delete.assert_called_with("vlog:analytics:test_key")

    def test_clear_with_mock_redis(self):
        """Test clear operation with mocked Redis."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client
            # Simulate SCAN returning keys then finishing
            mock_client.scan.side_effect = [
                (1, ["vlog:analytics:key1", "vlog:analytics:key2"]),
                (0, []),  # End of scan
            ]

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=60,
                enabled=True,
            )

            cache.clear()
            mock_client.delete.assert_called_once_with(
                "vlog:analytics:key1", "vlog:analytics:key2"
            )

    def test_cleanup_expired_returns_zero(self):
        """Test cleanup_expired returns 0 (Redis handles TTL)."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=60,
                enabled=True,
            )

            result = cache.cleanup_expired()
            assert result == 0

    def test_get_stats_with_mock_redis(self):
        """Test get_stats with mocked Redis."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client
            mock_client.scan.side_effect = [
                (1, ["vlog:analytics:key1", "vlog:analytics:key2"]),
                (0, ["vlog:analytics:key3"]),
            ]

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=120,
                enabled=True,
            )

            stats = cache.get_stats()
            assert stats["enabled"] is True
            assert stats["ttl_seconds"] == 120
            assert stats["entry_count"] == 3
            assert stats["max_size"] == -1
            assert stats["backend"] == "redis"
            assert stats["connected"] is True

    def test_connection_failure_graceful_degradation(self):
        """Test cache gracefully handles connection failures."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client
            mock_client.ping.side_effect = Exception("Connection refused")

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=60,
                enabled=True,
            )

            # Should not raise, just log warning and set client to None
            assert cache._client is None
            assert cache._connection_failed is True

            # Operations should gracefully return None/do nothing
            assert cache.get("test_key") is None
            cache.set("test_key", {"data": 1})  # Should not raise

    def test_operation_failure_graceful_degradation(self):
        """Test cache gracefully handles operation failures."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client
            mock_client.get.side_effect = Exception("Network error")

            cache = RedisAnalyticsCache(
                redis_url="redis://localhost:6379",
                ttl_seconds=60,
                enabled=True,
            )

            # Should return None instead of raising
            result = cache.get("test_key")
            assert result is None


class TestCreateAnalyticsCache:
    """Test suite for create_analytics_cache factory function."""

    def test_creates_memory_cache_by_default(self):
        """Test factory creates memory cache by default."""
        cache = create_analytics_cache()
        assert isinstance(cache, AnalyticsCache)
        assert cache.get_stats()["backend"] == "memory"

    def test_creates_memory_cache_for_memory_url(self):
        """Test factory creates memory cache for memory:// URL."""
        cache = create_analytics_cache(storage_url="memory://")
        assert isinstance(cache, AnalyticsCache)

    def test_creates_memory_cache_when_disabled(self):
        """Test factory creates disabled memory cache when disabled."""
        cache = create_analytics_cache(
            storage_url="redis://localhost:6379",
            enabled=False,
        )
        assert isinstance(cache, AnalyticsCache)
        assert cache.get_stats()["enabled"] is False

    def test_creates_redis_cache_for_redis_url(self):
        """Test factory creates Redis cache for redis:// URL."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client

            cache = create_analytics_cache(storage_url="redis://localhost:6379")
            assert isinstance(cache, RedisAnalyticsCache)
            assert cache.get_stats()["backend"] == "redis"

    def test_creates_redis_cache_for_rediss_url(self):
        """Test factory creates Redis cache for rediss:// (TLS) URL."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client

            cache = create_analytics_cache(storage_url="rediss://localhost:6379")
            assert isinstance(cache, RedisAnalyticsCache)

    def test_passes_ttl_to_memory_cache(self):
        """Test factory passes TTL to memory cache."""
        cache = create_analytics_cache(storage_url="memory://", ttl_seconds=120)
        assert cache.get_stats()["ttl_seconds"] == 120

    def test_passes_ttl_to_redis_cache(self):
        """Test factory passes TTL to Redis cache."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client

            cache = create_analytics_cache(
                storage_url="redis://localhost:6379",
                ttl_seconds=120,
            )
            assert cache.get_stats()["ttl_seconds"] == 120

    def test_passes_max_size_to_memory_cache(self):
        """Test factory passes max_size to memory cache."""
        cache = create_analytics_cache(storage_url="memory://", max_size=500)
        assert cache.get_stats()["max_size"] == 500

    def test_ignores_max_size_for_redis_cache(self):
        """Test factory ignores max_size for Redis cache."""
        with patch.dict("sys.modules", {"redis": MagicMock()}):
            import sys
            mock_redis_module = sys.modules["redis"]
            mock_client = MagicMock()
            mock_redis_module.Redis.from_url.return_value = mock_client

            cache = create_analytics_cache(
                storage_url="redis://localhost:6379",
                max_size=500,
            )
            # Redis cache should have -1 for max_size (unlimited)
            assert cache.get_stats()["max_size"] == -1
