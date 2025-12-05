"""
Tests for timezone-aware datetime handling.

Ensures that datetime comparisons work correctly with timezone-naive values
from SQLite, preventing stale job detection issues across different timezones.
"""

from datetime import datetime, timedelta, timezone

import pytest

from api.common import ensure_utc


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
        import zoneinfo

        # Create a datetime in US Eastern time (UTC-5 or UTC-4 depending on DST)
        eastern = zoneinfo.ZoneInfo("America/New_York")
        eastern_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=eastern)
        result = ensure_utc(eastern_dt)

        # Should be converted to UTC (17:00 UTC in winter, 16:00 in summer)
        assert result.tzinfo == timezone.utc
        assert result.hour in [16, 17]  # Depends on DST

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


@pytest.mark.asyncio
class TestStaleJobDetectionWithTimezone:
    """Test stale job detection with timezone-naive datetimes."""

    async def test_stale_detection_with_naive_datetime(self, test_database, sample_video):
        """Test that stale detection works with naive datetimes from SQLite."""
        from api.common import ensure_utc
        from api.database import transcoding_jobs

        # Create a job with old checkpoint (35 minutes ago)
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=35)

        # Simulate SQLite by storing and retrieving (which makes it naive)
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_video["id"],
                worker_id="test-worker",
                current_step="transcode",
                started_at=stale_time,
                last_checkpoint=stale_time,
            )
        )

        # Retrieve the job (SQLite will return naive datetime)
        job = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.video_id == sample_video["id"])
        )

        # The retrieved last_checkpoint should be naive (no tzinfo)
        assert job["last_checkpoint"].tzinfo is None or job["last_checkpoint"].tzinfo == timezone.utc

        # Test stale detection with 30 minute threshold
        threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

        # Use ensure_utc to handle the naive datetime
        last_checkpoint = ensure_utc(job["last_checkpoint"])

        # Should be detected as stale (35 min > 30 min threshold)
        assert last_checkpoint < threshold

    async def test_not_stale_with_naive_datetime(self, test_database, sample_video):
        """Test that recent jobs are not detected as stale with naive datetimes."""
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

        # Use ensure_utc to handle the naive datetime
        last_checkpoint = ensure_utc(job["last_checkpoint"])

        # Should NOT be detected as stale (5 min < 30 min threshold)
        assert last_checkpoint >= threshold

    async def test_worker_offline_detection_with_naive_datetime(self, test_database):
        """Test that worker offline detection works with naive datetimes."""
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

        # Retrieve the worker (SQLite will return naive datetime)
        worker = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == "test-worker-123")
        )

        # Test offline detection with 30 minute threshold
        offline_threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

        # Use ensure_utc to handle the naive datetime
        last_heartbeat = ensure_utc(worker["last_heartbeat"])

        # Should be detected as offline (35 min > 30 min threshold)
        assert last_heartbeat < offline_threshold

    async def test_worker_active_with_naive_datetime(self, test_database):
        """Test that active workers are not detected as offline with naive datetimes."""
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
        worker = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == "test-worker-456")
        )

        # Test offline detection with 30 minute threshold
        offline_threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

        # Use ensure_utc to handle the naive datetime
        last_heartbeat = ensure_utc(worker["last_heartbeat"])

        # Should NOT be detected as offline (5 min < 30 min threshold)
        assert last_heartbeat >= offline_threshold
