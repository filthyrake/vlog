"""
Tests for storage health cache race condition fixes.

These tests verify that the storage health cache is properly synchronized
when accessed by multiple async coroutines concurrently.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

# Import the module to test
from api import common


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the storage health cache before each test."""
    common._storage_health_cache["healthy"] = True
    common._storage_health_cache["last_check"] = None
    common._storage_health_cache["last_error"] = None
    yield
    # Reset after test as well
    common._storage_health_cache["healthy"] = True
    common._storage_health_cache["last_check"] = None
    common._storage_health_cache["last_error"] = None


@pytest.fixture
def mock_storage_check():
    """Mock the synchronous storage check function."""
    with patch("api.common._check_storage_sync") as mock:
        mock.return_value = True
        yield mock


@pytest.fixture
def disable_test_mode():
    """Temporarily disable VLOG_TEST_MODE for testing."""
    original_value = os.environ.get("VLOG_TEST_MODE")
    if "VLOG_TEST_MODE" in os.environ:
        del os.environ["VLOG_TEST_MODE"]
    yield
    if original_value is not None:
        os.environ["VLOG_TEST_MODE"] = original_value


class TestStorageHealthCacheConcurrency:
    """Test concurrent access to storage health cache."""

    @pytest.mark.asyncio
    async def test_concurrent_cache_miss_single_check(self, mock_storage_check, disable_test_mode):
        """
        Test that multiple concurrent requests trigger only one storage check
        when cache is stale (prevents thundering herd).
        """
        mock_storage_check.return_value = True

        # Launch 10 concurrent requests when cache is empty
        tasks = [common.check_storage_available() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should return True
        assert all(results), "All requests should return True"

        # Storage check should be called exactly once (not 10 times)
        # because the lock prevents thundering herd
        assert mock_storage_check.call_count == 1, (
            f"Storage check should be called once, but was called "
            f"{mock_storage_check.call_count} times"
        )

    @pytest.mark.asyncio
    async def test_concurrent_cache_hit_no_checks(self, mock_storage_check, disable_test_mode):
        """
        Test that concurrent requests use cached value when cache is fresh.
        """
        # Prime the cache with a recent check
        common._storage_health_cache["healthy"] = True
        common._storage_health_cache["last_check"] = datetime.now(timezone.utc)
        common._storage_health_cache["last_error"] = None

        # Launch 10 concurrent requests
        tasks = [common.check_storage_available() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should return True
        assert all(results), "All requests should return True"

        # Storage check should NOT be called at all (cache is fresh)
        assert mock_storage_check.call_count == 0, (
            f"Storage check should not be called, but was called "
            f"{mock_storage_check.call_count} times"
        )

    @pytest.mark.asyncio
    async def test_cache_consistency_concurrent_updates(self, mock_storage_check, disable_test_mode):
        """
        Test that cache state remains consistent when multiple tasks update it.
        This prevents the scenario where:
        - Task A sets healthy=True, last_check=T1
        - Task B sets healthy=False, last_check=T2
        - Result is healthy=True, last_check=T2 (inconsistent)
        """
        # Make storage check alternate between healthy/unhealthy
        health_states = [True, False, True, False, True]
        mock_storage_check.side_effect = health_states

        # Launch 5 concurrent requests when cache is stale
        tasks = [common.check_storage_available() for _ in range(5)]
        results = await asyncio.gather(*tasks)

        # Due to the lock, only the first task should perform the check
        # and all others should get the cached result
        assert mock_storage_check.call_count == 1, (
            "Only one storage check should occur due to lock"
        )

        # All results should be the same (the first check result)
        assert len(set(results)) == 1, "All results should be identical"

        # Verify cache consistency: healthy and last_check should match
        cache = common._storage_health_cache
        if cache["healthy"]:
            assert cache["last_error"] is None, (
                "Healthy cache should not have an error"
            )
        else:
            assert cache["last_error"] is not None, (
                "Unhealthy cache should have an error message"
            )

    @pytest.mark.asyncio
    async def test_cache_expiry_triggers_new_check(self, mock_storage_check, disable_test_mode):
        """
        Test that expired cache triggers a new storage check.
        """
        mock_storage_check.return_value = True

        # Prime cache with old timestamp (expired)
        old_time = datetime.now(timezone.utc) - timedelta(
            seconds=common.STORAGE_HEALTH_CACHE_TTL + 1
        )
        common._storage_health_cache["healthy"] = False
        common._storage_health_cache["last_check"] = old_time
        common._storage_health_cache["last_error"] = "Old error"

        # Call check_storage_available
        result = await common.check_storage_available()

        # Should return True (new check result)
        assert result is True, "Should return new check result"

        # Storage check should be called once
        assert mock_storage_check.call_count == 1

        # Cache should be updated
        assert common._storage_health_cache["healthy"] is True
        assert common._storage_health_cache["last_error"] is None
        assert common._storage_health_cache["last_check"] > old_time

    @pytest.mark.asyncio
    async def test_check_health_updates_cache_atomically(self, mock_storage_check, disable_test_mode):
        """
        Test that check_health() updates the cache atomically.
        """
        mock_storage_check.return_value = True

        # Mock database query
        with patch("api.common.database") as mock_db:
            mock_db.fetch_one = AsyncMock(return_value={"result": 1})

            # Call check_health
            result = await common.check_health()

            # Verify result
            assert result["healthy"] is True
            assert result["checks"]["storage"] is True
            assert result["checks"]["database"] is True

            # Verify cache was updated
            assert common._storage_health_cache["healthy"] is True
            assert common._storage_health_cache["last_check"] is not None
            assert common._storage_health_cache["last_error"] is None

    @pytest.mark.asyncio
    async def test_concurrent_check_health_calls(self, mock_storage_check, disable_test_mode):
        """
        Test that concurrent check_health() calls don't corrupt the cache.
        """
        mock_storage_check.return_value = True

        # Mock database query
        with patch("api.common.database") as mock_db:
            mock_db.fetch_one = AsyncMock(return_value={"result": 1})

            # Launch multiple concurrent health checks
            tasks = [common.check_health() for _ in range(5)]
            results = await asyncio.gather(*tasks)

            # All should succeed
            assert all(r["healthy"] for r in results)

            # Verify cache consistency
            cache = common._storage_health_cache
            assert cache["last_check"] is not None
            if cache["healthy"]:
                assert cache["last_error"] is None

    @pytest.mark.asyncio
    async def test_storage_check_timeout_updates_cache(self, disable_test_mode):
        """
        Test that storage check timeout properly updates cache to unhealthy.
        """
        # Mock asyncio.wait_for to raise TimeoutError
        with patch("asyncio.wait_for") as mock_wait:
            mock_wait.side_effect = asyncio.TimeoutError()

            result = await common.check_storage_available()

            # Should return False
            assert result is False

            # Cache should be updated to unhealthy
            assert common._storage_health_cache["healthy"] is False
            assert common._storage_health_cache["last_error"] == "Storage unavailable"

    @pytest.mark.asyncio
    async def test_storage_check_exception_updates_cache(self, mock_storage_check, disable_test_mode):
        """
        Test that storage check exception properly updates cache to unhealthy.
        """
        # Make storage check raise exception
        mock_storage_check.side_effect = IOError("Disk error")

        result = await common.check_storage_available()

        # Should return False
        assert result is False

        # Cache should be updated to unhealthy
        assert common._storage_health_cache["healthy"] is False
        assert common._storage_health_cache["last_error"] == "Storage unavailable"

    @pytest.mark.asyncio
    async def test_test_mode_bypasses_lock(self):
        """
        Test that VLOG_TEST_MODE=1 bypasses storage check and lock.
        """
        # Ensure test mode is enabled (it should already be from conftest.py)
        original_value = os.environ.get("VLOG_TEST_MODE")
        os.environ["VLOG_TEST_MODE"] = "1"

        try:
            # Even with no storage, should return True immediately
            result = await common.check_storage_available()
            assert result is True

            # Cache should not be updated in test mode
            # (We reset the cache before each test via fixture)
            assert common._storage_health_cache["last_check"] is None
        finally:
            # Restore original value
            if original_value is not None:
                os.environ["VLOG_TEST_MODE"] = original_value
            elif "VLOG_TEST_MODE" in os.environ:
                del os.environ["VLOG_TEST_MODE"]


class TestStorageHealthCacheLock:
    """Test the lock behavior specifically."""

    @pytest.mark.asyncio
    async def test_lock_prevents_interleaved_access(self, mock_storage_check, disable_test_mode):
        """
        Test that the lock prevents interleaved reads and writes.
        """
        access_log = []

        # Create a custom mock that logs access times
        def logged_check():
            access_log.append(("check_start", datetime.now(timezone.utc)))
            import time
            time.sleep(0.05)  # Simulate I/O
            access_log.append(("check_end", datetime.now(timezone.utc)))
            return True

        mock_storage_check.side_effect = logged_check

        # Launch 3 concurrent requests
        tasks = [common.check_storage_available() for _ in range(3)]
        await asyncio.gather(*tasks)

        # Verify that checks don't overlap
        # With the lock, only one check should happen
        check_starts = [t for action, t in access_log if action == "check_start"]
        check_ends = [t for action, t in access_log if action == "check_end"]

        # Should have exactly 1 check (due to lock and cache)
        assert len(check_starts) == 1, (
            f"Expected 1 check start, got {len(check_starts)}"
        )
        assert len(check_ends) == 1, (
            f"Expected 1 check end, got {len(check_ends)}"
        )

    @pytest.mark.asyncio
    async def test_lock_is_reentrant_safe(self, mock_storage_check, disable_test_mode):
        """
        Test that the lock doesn't deadlock with nested calls.
        Note: asyncio.Lock is NOT reentrant, so this test ensures
        we don't have nested calls to check_storage_available.
        """
        mock_storage_check.return_value = True

        # This should not deadlock
        result = await common.check_storage_available()
        assert result is True

        # Calling again should use cache (not re-acquire lock while holding it)
        result2 = await common.check_storage_available()
        assert result2 is True


class TestRequireStorageAvailable:
    """Test the FastAPI dependency for storage availability."""

    @pytest.mark.asyncio
    async def test_require_storage_available_success(self, mock_storage_check, disable_test_mode):
        """Test that require_storage_available() succeeds when storage is healthy."""
        mock_storage_check.return_value = True
        common._storage_health_cache["healthy"] = True
        common._storage_health_cache["last_check"] = datetime.now(timezone.utc)

        # Should not raise
        await common.require_storage_available()

    @pytest.mark.asyncio
    async def test_require_storage_available_failure(self, mock_storage_check, disable_test_mode):
        """Test that require_storage_available() raises HTTPException when storage is unhealthy."""
        from fastapi import HTTPException

        mock_storage_check.return_value = False

        # Force cache to be stale so it runs a new check
        common._storage_health_cache["last_check"] = None

        # Should raise HTTPException with status 503
        with pytest.raises(HTTPException) as exc_info:
            await common.require_storage_available()

        assert exc_info.value.status_code == 503
        assert "storage" in exc_info.value.detail.lower()
        assert exc_info.value.headers.get("Retry-After") == "30"

