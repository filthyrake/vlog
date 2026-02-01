"""
Tests for timezone-aware datetime handling.

Ensures that datetime comparisons work correctly with timezone-aware values
from PostgreSQL, and that the ensure_utc helper function handles various
timezone scenarios correctly.
"""

import zoneinfo
from datetime import datetime, timedelta, timezone

import pytest

from api.common import calculate_stream_offset_ms, ensure_utc


class TestEnsureUtc:
    """Test the ensure_utc helper function."""

    def test_none_returns_none(self):
        """Test that None input returns None."""
        assert ensure_utc(None) is None

    def test_naive_datetime_becomes_utc(self):
        """Test that naive datetime gets UTC timezone."""
        naive_dt = datetime(2024, 1, 1, 12, 0, 0)
        result = ensure_utc(naive_dt)

        assert result.tzinfo == timezone.utc
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1
        assert result.hour == 12
        assert result.minute == 0
        assert result.second == 0

    def test_utc_datetime_unchanged(self):
        """Test that UTC datetime remains unchanged."""
        utc_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = ensure_utc(utc_dt)

        assert result == utc_dt
        assert result.tzinfo == timezone.utc

    def test_non_utc_datetime_converted(self):
        """Test that non-UTC timezone-aware datetime is converted to UTC."""
        # Create a datetime in US Eastern time (EST is UTC-5 in winter)
        eastern = zoneinfo.ZoneInfo("America/New_York")
        eastern_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=eastern)
        result = ensure_utc(eastern_dt)

        # January 1, 2024 is in EST (UTC-5), so 12:00 EST = 17:00 UTC
        assert result.tzinfo == timezone.utc
        assert result.hour == 17

    def test_comparison_with_naive_and_aware(self):
        """Test that ensure_utc enables proper comparison."""
        # Simulate SQLite returning naive datetime
        naive_dt = datetime(2024, 1, 1, 10, 0, 0)

        # Current time as aware UTC
        current_utc = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Without ensure_utc, comparison would fail (can't compare naive and aware)
        # With ensure_utc, comparison works
        normalized = ensure_utc(naive_dt)

        assert normalized < current_utc
        assert (current_utc - normalized).total_seconds() == 7200  # 2 hours


class TestCalculateStreamOffsetMs:
    """Test the calculate_stream_offset_ms helper function."""

    def test_none_started_at_returns_none(self):
        """Test that None stream_started_at returns None."""
        current = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert calculate_stream_offset_ms(None, current) is None

    def test_normal_offset_calculation(self):
        """Test normal offset calculation with timezone-aware datetimes."""
        started = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        current = datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc)  # 1 minute later

        result = calculate_stream_offset_ms(started, current)
        assert result == 60000  # 60 seconds = 60000 ms

    def test_naive_started_at_handled(self):
        """Test that naive stream_started_at is normalized to UTC."""
        started_naive = datetime(2024, 1, 1, 12, 0, 0)  # naive
        current = datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc)

        result = calculate_stream_offset_ms(started_naive, current)
        assert result == 60000

    def test_naive_current_time_handled(self):
        """Test that naive current_time is normalized to UTC (prevents TypeError)."""
        started = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        current_naive = datetime(2024, 1, 1, 12, 1, 0)  # naive

        # This would crash without ensure_utc(current_time) in the implementation
        result = calculate_stream_offset_ms(started, current_naive)
        assert result == 60000

    def test_both_naive_datetimes_handled(self):
        """Test that both naive datetimes work correctly."""
        started_naive = datetime(2024, 1, 1, 12, 0, 0)
        current_naive = datetime(2024, 1, 1, 12, 2, 30)  # 2.5 minutes later

        result = calculate_stream_offset_ms(started_naive, current_naive)
        assert result == 150000  # 150 seconds = 150000 ms

    def test_non_utc_timezone_converted(self):
        """Test that non-UTC timezone-aware datetimes are converted correctly."""
        eastern = zoneinfo.ZoneInfo("America/New_York")
        # 12:00 EST = 17:00 UTC
        started_eastern = datetime(2024, 1, 1, 12, 0, 0, tzinfo=eastern)
        # 12:01 EST = 17:01 UTC
        current_eastern = datetime(2024, 1, 1, 12, 1, 0, tzinfo=eastern)

        result = calculate_stream_offset_ms(started_eastern, current_eastern)
        assert result == 60000  # 1 minute regardless of timezone

    def test_negative_offset_clamped_to_zero(self):
        """Test that negative offsets (clock skew) are clamped to 0."""
        # started_at is in the future (clock skew scenario)
        started = datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
        current = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # 1 hour earlier

        result = calculate_stream_offset_ms(started, current)
        assert result == 0  # Clamped to 0, not -3600000

    def test_large_offset_no_overflow(self):
        """Test that large offsets (multi-day streams) work correctly."""
        started = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        current = datetime(2024, 1, 8, 0, 0, 0, tzinfo=timezone.utc)  # 7 days later

        result = calculate_stream_offset_ms(started, current)
        expected = 7 * 24 * 60 * 60 * 1000  # 7 days in milliseconds
        assert result == expected


@pytest.mark.asyncio
class TestStaleJobDetectionWithTimezone:
    """Test stale job detection with timezone-aware datetimes from PostgreSQL."""

    async def test_stale_detection_with_datetime(self, test_database, sample_video):
        """Test that stale detection works with datetimes from PostgreSQL."""
        from api.common import ensure_utc
        from api.database import transcoding_jobs

        # Create a job with old checkpoint (35 minutes ago)
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=35)

        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_video["id"],
                worker_id="test-worker",
                current_step="transcode",
                started_at=stale_time,
                last_checkpoint=stale_time,
            )
        )

        # Retrieve the job (PostgreSQL returns timezone-aware datetime)
        job = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.video_id == sample_video["id"])
        )

        # The retrieved last_checkpoint should have timezone info from PostgreSQL
        assert job["last_checkpoint"].tzinfo is not None

        # Test stale detection with 30 minute threshold
        threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

        # Use ensure_utc to normalize the datetime
        last_checkpoint = ensure_utc(job["last_checkpoint"])

        # Should be detected as stale (35 min > 30 min threshold)
        assert last_checkpoint < threshold

    async def test_not_stale_with_datetime(self, test_database, sample_video):
        """Test that recent jobs are not detected as stale."""
        from api.common import ensure_utc
        from api.database import transcoding_jobs

        # Create a job with recent checkpoint (5 minutes ago)
        recent_time = datetime.now(timezone.utc) - timedelta(minutes=5)

        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_video["id"],
                worker_id="test-worker",
                current_step="transcode",
                started_at=recent_time,
                last_checkpoint=recent_time,
            )
        )

        # Retrieve the job
        job = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.video_id == sample_video["id"])
        )

        # Test stale detection with 30 minute threshold
        threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

        # Use ensure_utc to normalize the datetime
        last_checkpoint = ensure_utc(job["last_checkpoint"])

        # Should NOT be detected as stale (5 min < 30 min threshold)
        assert last_checkpoint >= threshold

    async def test_worker_offline_detection(self, test_database):
        """Test that worker offline detection works correctly."""
        from api.common import ensure_utc
        from api.database import workers

        # Create a worker with old heartbeat (35 minutes ago)
        old_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=35)

        await test_database.execute(
            workers.insert().values(
                worker_id="test-worker-123",
                worker_name="Test Worker",
                worker_type="remote",
                registered_at=old_heartbeat,
                last_heartbeat=old_heartbeat,
                status="active",
            )
        )

        # Retrieve the worker
        worker = await test_database.fetch_one(workers.select().where(workers.c.worker_id == "test-worker-123"))

        # Test offline detection with 30 minute threshold
        offline_threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

        # Use ensure_utc to normalize the datetime
        last_heartbeat = ensure_utc(worker["last_heartbeat"])

        # Should be detected as offline (35 min > 30 min threshold)
        assert last_heartbeat < offline_threshold

    async def test_worker_active(self, test_database):
        """Test that active workers are not detected as offline."""
        from api.common import ensure_utc
        from api.database import workers

        # Create a worker with recent heartbeat (5 minutes ago)
        recent_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=5)

        await test_database.execute(
            workers.insert().values(
                worker_id="test-worker-456",
                worker_name="Test Worker Active",
                worker_type="remote",
                registered_at=recent_heartbeat,
                last_heartbeat=recent_heartbeat,
                status="active",
            )
        )

        # Retrieve the worker
        worker = await test_database.fetch_one(workers.select().where(workers.c.worker_id == "test-worker-456"))

        # Test offline detection with 30 minute threshold
        offline_threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

        # Use ensure_utc to normalize the datetime
        last_heartbeat = ensure_utc(worker["last_heartbeat"])

        # Should NOT be detected as offline (5 min < 30 min threshold)
        assert last_heartbeat >= offline_threshold
