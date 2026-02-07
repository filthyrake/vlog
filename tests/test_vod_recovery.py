"""
Tests for VOD recording recovery mechanism (Issue #552).

Validates the background recovery loop that retries VOD creation
for orphaned streams stuck in 'ended' state without a VOD.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

os.environ["VLOG_TEST_MODE"] = "1"


class TestRecoverOrphanedVodStreams:
    """Tests for recover_orphaned_vod_streams()."""

    @pytest.fixture(autouse=True)
    def reset_module_state(self):
        """Reset module-level state between tests."""
        import api.live_tasks as lt

        lt._vod_recovery_retry_counts.clear()
        lt._vod_recovery_attempts = 0
        lt._vod_recovery_successes = 0
        lt._vod_recovery_failures = 0
        yield
        lt._vod_recovery_retry_counts.clear()
        lt._vod_recovery_attempts = 0
        lt._vod_recovery_successes = 0
        lt._vod_recovery_failures = 0

    @pytest.mark.asyncio
    async def test_recovers_orphaned_stream(self):
        """Test that an orphaned stream gets VOD recovery attempted."""
        from api.live_tasks import recover_orphaned_vod_streams

        orphaned_stream = {
            "id": 42,
            "slug": "test-stream",
            "status": "ended",
            "auto_record_vod": True,
            "vod_video_id": None,
            "ended_at": datetime.now(timezone.utc) - timedelta(seconds=600),
        }

        with (
            patch(
                "api.live_tasks.fetch_all_with_retry",
                new_callable=AsyncMock,
                return_value=[orphaned_stream],
            ),
            patch(
                "api.live_tasks.finalize_playlists_for_vod",
                new_callable=AsyncMock,
            ) as mock_finalize,
            patch(
                "api.live_tasks.trigger_vod_recording",
                new_callable=AsyncMock,
            ) as mock_trigger,
        ):
            result = await recover_orphaned_vod_streams()

        assert result["attempted"] == 1
        assert result["succeeded"] == 1
        assert result["failed"] == 0
        assert result["skipped"] == 0
        mock_finalize.assert_awaited_once_with(42)
        mock_trigger.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_skips_at_max_retries(self):
        """Test that streams at max retries are skipped."""
        import api.live_tasks as lt
        from api.live_tasks import recover_orphaned_vod_streams

        # Pre-fill retry counter to max
        lt._vod_recovery_retry_counts[42] = lt.LIVE_VOD_RECOVERY_MAX_RETRIES

        orphaned_stream = {
            "id": 42,
            "slug": "test-stream",
            "status": "ended",
            "auto_record_vod": True,
            "vod_video_id": None,
            "ended_at": datetime.now(timezone.utc) - timedelta(seconds=600),
        }

        with (
            patch(
                "api.live_tasks.fetch_all_with_retry",
                new_callable=AsyncMock,
                return_value=[orphaned_stream],
            ),
            patch(
                "api.live_tasks.finalize_playlists_for_vod",
                new_callable=AsyncMock,
            ) as mock_finalize,
            patch(
                "api.live_tasks.trigger_vod_recording",
                new_callable=AsyncMock,
            ) as mock_trigger,
        ):
            result = await recover_orphaned_vod_streams()

        assert result["skipped"] == 1
        assert result["attempted"] == 0
        mock_finalize.assert_not_awaited()
        mock_trigger.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_logs_error_once_at_max_retries(self):
        """Test that the max-retries error is logged exactly once."""
        import api.live_tasks as lt
        from api.live_tasks import recover_orphaned_vod_streams

        orphaned_stream = {
            "id": 42,
            "slug": "test-stream",
            "status": "ended",
            "auto_record_vod": True,
            "vod_video_id": None,
            "ended_at": datetime.now(timezone.utc) - timedelta(seconds=600),
        }

        # Set to exactly max retries (first time hitting the limit)
        lt._vod_recovery_retry_counts[42] = lt.LIVE_VOD_RECOVERY_MAX_RETRIES

        with (
            patch(
                "api.live_tasks.fetch_all_with_retry",
                new_callable=AsyncMock,
                return_value=[orphaned_stream],
            ),
            patch("api.live_tasks.finalize_playlists_for_vod", new_callable=AsyncMock),
            patch("api.live_tasks.trigger_vod_recording", new_callable=AsyncMock),
            patch("api.live_tasks.logger") as mock_logger,
        ):
            # First call at max retries — should log error
            await recover_orphaned_vod_streams()
            assert mock_logger.error.call_count == 1
            assert "exceeded max retries" in mock_logger.error.call_args[0][0]

            mock_logger.error.reset_mock()

            # Second call — retry count now past max, should NOT log again
            await recover_orphaned_vod_streams()
            assert mock_logger.error.call_count == 0

    @pytest.mark.asyncio
    async def test_exception_counted_as_failure(self):
        """Test that exceptions during VOD creation are counted as failures."""
        import api.live_tasks as lt
        from api.live_tasks import recover_orphaned_vod_streams

        orphaned_stream = {
            "id": 42,
            "slug": "test-stream",
            "status": "ended",
            "auto_record_vod": True,
            "vod_video_id": None,
            "ended_at": datetime.now(timezone.utc) - timedelta(seconds=600),
        }

        with (
            patch(
                "api.live_tasks.fetch_all_with_retry",
                new_callable=AsyncMock,
                return_value=[orphaned_stream],
            ),
            patch(
                "api.live_tasks.finalize_playlists_for_vod",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB timeout"),
            ),
            patch("api.live_tasks.trigger_vod_recording", new_callable=AsyncMock) as mock_trigger,
        ):
            result = await recover_orphaned_vod_streams()

        assert result["attempted"] == 1
        assert result["failed"] == 1
        assert result["succeeded"] == 0
        assert lt._vod_recovery_failures == 1
        # trigger_vod_recording should not be called if finalize fails
        mock_trigger.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_orphans_returns_zeros(self):
        """Test that no orphaned streams returns all-zero counts."""
        from api.live_tasks import recover_orphaned_vod_streams

        with patch(
            "api.live_tasks.fetch_all_with_retry",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await recover_orphaned_vod_streams()

        assert result == {"attempted": 0, "succeeded": 0, "failed": 0, "skipped": 0}

    @pytest.mark.asyncio
    async def test_success_clears_retry_counter(self):
        """Test that successful recovery removes the stream from retry tracker."""
        import api.live_tasks as lt
        from api.live_tasks import recover_orphaned_vod_streams

        # Pre-set some retry count
        lt._vod_recovery_retry_counts[42] = 2

        orphaned_stream = {
            "id": 42,
            "slug": "test-stream",
            "status": "ended",
            "auto_record_vod": True,
            "vod_video_id": None,
            "ended_at": datetime.now(timezone.utc) - timedelta(seconds=600),
        }

        with (
            patch(
                "api.live_tasks.fetch_all_with_retry",
                new_callable=AsyncMock,
                return_value=[orphaned_stream],
            ),
            patch("api.live_tasks.finalize_playlists_for_vod", new_callable=AsyncMock),
            patch("api.live_tasks.trigger_vod_recording", new_callable=AsyncMock),
        ):
            await recover_orphaned_vod_streams()

        assert 42 not in lt._vod_recovery_retry_counts

    @pytest.mark.asyncio
    async def test_global_counters_accumulate(self):
        """Test that global metric counters accumulate across calls."""
        import api.live_tasks as lt
        from api.live_tasks import recover_orphaned_vod_streams

        orphaned_stream = {
            "id": 42,
            "slug": "test-stream",
            "status": "ended",
            "auto_record_vod": True,
            "vod_video_id": None,
            "ended_at": datetime.now(timezone.utc) - timedelta(seconds=600),
        }

        with (
            patch(
                "api.live_tasks.fetch_all_with_retry",
                new_callable=AsyncMock,
                return_value=[orphaned_stream],
            ),
            patch("api.live_tasks.finalize_playlists_for_vod", new_callable=AsyncMock),
            patch("api.live_tasks.trigger_vod_recording", new_callable=AsyncMock),
        ):
            await recover_orphaned_vod_streams()
            await recover_orphaned_vod_streams()

        assert lt._vod_recovery_attempts == 2
        assert lt._vod_recovery_successes == 2


class TestGetLiveTaskMetrics:
    """Tests for metrics including VOD recovery keys."""

    def test_metrics_include_vod_recovery_keys(self):
        """Test that get_live_task_metrics includes VOD recovery counters."""
        from api.live_tasks import get_live_task_metrics

        metrics = get_live_task_metrics()

        assert "vod_recovery_attempts_total" in metrics
        assert "vod_recovery_successes_total" in metrics
        assert "vod_recovery_failures_total" in metrics


class TestVodRecoveryConfigDefaults:
    """Tests for VOD recovery config defaults."""

    def test_recovery_interval_range(self):
        """Test that recovery interval is within valid range."""
        from config import LIVE_VOD_RECOVERY_INTERVAL

        assert 30 <= LIVE_VOD_RECOVERY_INTERVAL <= 600

    def test_recovery_grace_period_range(self):
        """Test that grace period is within valid range."""
        from config import LIVE_VOD_RECOVERY_GRACE_PERIOD

        assert 60 <= LIVE_VOD_RECOVERY_GRACE_PERIOD <= 3600

    def test_recovery_max_retries_range(self):
        """Test that max retries is within valid range."""
        from config import LIVE_VOD_RECOVERY_MAX_RETRIES

        assert 1 <= LIVE_VOD_RECOVERY_MAX_RETRIES <= 20

    def test_grace_period_exceeds_interval(self):
        """Test that grace period is longer than check interval."""
        from config import LIVE_VOD_RECOVERY_GRACE_PERIOD, LIVE_VOD_RECOVERY_INTERVAL

        assert LIVE_VOD_RECOVERY_GRACE_PERIOD > LIVE_VOD_RECOVERY_INTERVAL
