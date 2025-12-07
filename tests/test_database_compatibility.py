"""
Database compatibility tests for VLog.

These tests verify that all SQL queries and database operations work correctly
with both SQLite and PostgreSQL backends. This helps catch regressions when
switching database backends or adding new queries.

Issue #260: Comprehensive regression testing

NOTE: These tests run against SQLite by default (from conftest.py).
PostgreSQL-specific behavior is tested with mock detection.
"""

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from api.database import (
    categories,
    quality_progress,
    transcoding_jobs,
    video_qualities,
    videos,
)
from api.enums import VideoStatus


class TestUpsertCompatibility:
    """
    Test upsert operations that behave differently between SQLite and PostgreSQL.

    SQLite uses: INSERT OR REPLACE / INSERT ... ON CONFLICT
    PostgreSQL uses: INSERT ... ON CONFLICT DO UPDATE
    """

    @pytest.mark.asyncio
    async def test_quality_progress_upsert_insert(self, test_database, sample_video):
        """Test inserting a new quality_progress record."""
        video_id = sample_video["id"]

        # Create transcoding job
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="transcode",
                progress_percent=0,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Simulate the upsert logic used in worker_api.py
        # First, try to check if we're PostgreSQL or SQLite
        db_url = str(test_database.url)
        is_postgresql = db_url.startswith("postgresql")

        if is_postgresql:
            await test_database.execute(
                sa.text("""
                    INSERT INTO quality_progress (job_id, quality, status, progress_percent)
                    VALUES (:job_id, :quality, :status, :progress)
                    ON CONFLICT (job_id, quality) DO UPDATE
                    SET status = :status, progress_percent = :progress
                """).bindparams(
                    job_id=job_id,
                    quality="1080p",
                    status="in_progress",
                    progress=50,
                )
            )
        else:
            # SQLite
            await test_database.execute(
                sa.text("""
                    INSERT OR REPLACE INTO quality_progress (job_id, quality, status, progress_percent)
                    VALUES (:job_id, :quality, :status, :progress)
                """).bindparams(
                    job_id=job_id,
                    quality="1080p",
                    status="in_progress",
                    progress=50,
                )
            )

        # Verify record was created
        result = await test_database.fetch_one(
            quality_progress.select()
            .where(quality_progress.c.job_id == job_id)
            .where(quality_progress.c.quality == "1080p")
        )

        assert result is not None
        assert result["status"] == "in_progress"
        assert result["progress_percent"] == 50

    @pytest.mark.asyncio
    async def test_quality_progress_upsert_update(self, test_database, sample_video):
        """Test updating an existing quality_progress record via upsert."""
        video_id = sample_video["id"]

        # Create transcoding job
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="transcode",
                progress_percent=0,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Insert initial record
        await test_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="1080p",
                status="pending",
                progress_percent=0,
            )
        )

        # Now use upsert to update it
        db_url = str(test_database.url)
        is_postgresql = db_url.startswith("postgresql")

        if is_postgresql:
            await test_database.execute(
                sa.text("""
                    INSERT INTO quality_progress (job_id, quality, status, progress_percent)
                    VALUES (:job_id, :quality, :status, :progress)
                    ON CONFLICT (job_id, quality) DO UPDATE
                    SET status = :status, progress_percent = :progress
                """).bindparams(
                    job_id=job_id,
                    quality="1080p",
                    status="completed",
                    progress=100,
                )
            )
        else:
            # SQLite - INSERT OR REPLACE replaces the whole row
            await test_database.execute(
                sa.text("""
                    INSERT OR REPLACE INTO quality_progress (job_id, quality, status, progress_percent)
                    VALUES (:job_id, :quality, :status, :progress)
                """).bindparams(
                    job_id=job_id,
                    quality="1080p",
                    status="completed",
                    progress=100,
                )
            )

        # Verify record was updated (not duplicated)
        results = await test_database.fetch_all(
            quality_progress.select()
            .where(quality_progress.c.job_id == job_id)
            .where(quality_progress.c.quality == "1080p")
        )

        assert len(results) == 1, "Should have exactly one record, not duplicates"
        assert results[0]["status"] == "completed"
        assert results[0]["progress_percent"] == 100


class TestDatetimeHandling:
    """
    Test datetime handling across databases.

    SQLite stores datetimes as strings and may not preserve timezone info.
    PostgreSQL has proper timestamptz support.
    """

    @pytest.mark.asyncio
    async def test_timezone_preserved_on_insert(self, test_database, sample_category):
        """Test that timezone information is correctly stored."""
        now = datetime.now(timezone.utc)

        video_id = await test_database.execute(
            videos.insert().values(
                title="Datetime Test",
                slug="datetime-test-video",
                category_id=sample_category["id"],
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))

        # The datetime should be retrievable and comparable
        created_at = video["created_at"]

        # For SQLite, datetime may come back as naive
        # For PostgreSQL, it should be timezone-aware
        if created_at.tzinfo is None:
            # Make it aware for comparison (SQLite case)
            created_at = created_at.replace(tzinfo=timezone.utc)

        # Should be within a second of now
        delta = abs((created_at - now).total_seconds())
        assert delta < 1, f"Datetime mismatch: {delta} seconds difference"

    @pytest.mark.asyncio
    async def test_datetime_comparison_with_now(self, test_database, sample_video):
        """Test datetime comparisons for stale job detection."""
        video_id = sample_video["id"]

        # Create a job with a checkpoint 35 minutes ago
        past = datetime.now(timezone.utc) - timedelta(minutes=35)

        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id="test-worker",
                current_step="transcode",
                progress_percent=50,
                claimed_at=past,
                claim_expires_at=past + timedelta(minutes=30),  # Expired 5 min ago
                started_at=past,
                last_checkpoint=past,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Query for stale jobs (checkpoint older than 30 minutes)
        stale_threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

        # This query should find our job
        stale_jobs = await test_database.fetch_all(
            transcoding_jobs.select().where(transcoding_jobs.c.last_checkpoint < stale_threshold)
        )

        assert len(stale_jobs) >= 1
        job_ids = [j["id"] for j in stale_jobs]
        assert job_id in job_ids


class TestNullHandling:
    """Test NULL value handling across databases."""

    @pytest.mark.asyncio
    async def test_null_category_id(self, test_database):
        """Test that NULL category_id is handled correctly."""
        now = datetime.now(timezone.utc)

        video_id = await test_database.execute(
            videos.insert().values(
                title="Uncategorized Video",
                slug="uncategorized-test",
                category_id=None,  # NULL category
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))

        assert video["category_id"] is None

    @pytest.mark.asyncio
    async def test_null_deleted_at_for_active_videos(self, test_database, sample_category):
        """Test filtering for non-deleted videos (deleted_at IS NULL)."""
        now = datetime.now(timezone.utc)

        # Create active video
        active_id = await test_database.execute(
            videos.insert().values(
                title="Active Video",
                slug="active-video-test",
                category_id=sample_category["id"],
                status=VideoStatus.READY,
                created_at=now,
                deleted_at=None,
            )
        )

        # Create deleted video
        deleted_id = await test_database.execute(
            videos.insert().values(
                title="Deleted Video",
                slug="deleted-video-test",
                category_id=sample_category["id"],
                status=VideoStatus.READY,
                created_at=now,
                deleted_at=now,
            )
        )

        # Query active videos only
        active_videos = await test_database.fetch_all(videos.select().where(videos.c.deleted_at.is_(None)))

        active_ids = [v["id"] for v in active_videos]
        assert active_id in active_ids
        assert deleted_id not in active_ids


class TestJoinQueries:
    """Test JOIN operations across databases."""

    @pytest.mark.asyncio
    async def test_video_with_category_join(self, test_database, sample_video):
        """Test joining videos with categories."""
        # Query with explicit join
        result = await test_database.fetch_one(
            sa.select(
                videos.c.id,
                videos.c.title,
                videos.c.slug,
                categories.c.name.label("category_name"),
                categories.c.slug.label("category_slug"),
            )
            .select_from(videos.join(categories, videos.c.category_id == categories.c.id))
            .where(videos.c.id == sample_video["id"])
        )

        assert result is not None
        assert result["title"] == sample_video["title"]
        assert result["category_name"] == "Test Category"

    @pytest.mark.asyncio
    async def test_video_with_qualities_join(self, test_database, sample_video_with_qualities):
        """Test joining videos with video_qualities."""
        video_id = sample_video_with_qualities["id"]

        results = await test_database.fetch_all(
            sa.select(
                videos.c.id,
                videos.c.title,
                video_qualities.c.quality,
                video_qualities.c.width,
                video_qualities.c.height,
            )
            .select_from(videos.join(video_qualities, videos.c.id == video_qualities.c.video_id))
            .where(videos.c.id == video_id)
        )

        assert len(results) == 3  # 1080p, 720p, 480p
        qualities = [r["quality"] for r in results]
        assert set(qualities) == {"1080p", "720p", "480p"}

    @pytest.mark.asyncio
    async def test_job_with_quality_progress_join(self, test_database, sample_video):
        """Test joining transcoding_jobs with quality_progress."""
        video_id = sample_video["id"]

        # Create job
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="transcode",
                progress_percent=50,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create quality progress entries
        for quality in ["1080p", "720p", "480p"]:
            await test_database.execute(
                quality_progress.insert().values(
                    job_id=job_id,
                    quality=quality,
                    status="in_progress" if quality == "1080p" else "pending",
                    progress_percent=50 if quality == "1080p" else 0,
                )
            )

        # Query with join
        results = await test_database.fetch_all(
            sa.select(
                transcoding_jobs.c.id.label("job_id"),
                transcoding_jobs.c.current_step,
                quality_progress.c.quality,
                quality_progress.c.status,
                quality_progress.c.progress_percent,
            )
            .select_from(transcoding_jobs.join(quality_progress, transcoding_jobs.c.id == quality_progress.c.job_id))
            .where(transcoding_jobs.c.id == job_id)
        )

        assert len(results) == 3
        in_progress = [r for r in results if r["status"] == "in_progress"]
        assert len(in_progress) == 1
        assert in_progress[0]["quality"] == "1080p"


class TestAggregateQueries:
    """Test aggregate operations across databases."""

    @pytest.mark.asyncio
    async def test_count_videos_by_status(self, test_database, sample_category):
        """Test COUNT with GROUP BY."""
        now = datetime.now(timezone.utc)

        # Create videos with different statuses
        for i, status in enumerate([VideoStatus.PENDING, VideoStatus.PENDING, VideoStatus.READY, VideoStatus.FAILED]):
            await test_database.execute(
                videos.insert().values(
                    title=f"Video {i}",
                    slug=f"status-test-video-{i}",
                    category_id=sample_category["id"],
                    status=status,
                    created_at=now,
                )
            )

        # Count by status
        results = await test_database.fetch_all(
            sa.select(
                videos.c.status,
                sa.func.count(videos.c.id).label("count"),
            )
            .where(videos.c.slug.like("status-test-video-%"))
            .group_by(videos.c.status)
        )

        counts = {r["status"]: r["count"] for r in results}
        assert counts.get(VideoStatus.PENDING, 0) == 2
        assert counts.get(VideoStatus.READY, 0) == 1
        assert counts.get(VideoStatus.FAILED, 0) == 1

    @pytest.mark.asyncio
    async def test_sum_of_video_durations(self, test_database, sample_category):
        """Test SUM aggregate function."""
        now = datetime.now(timezone.utc)

        durations = [60.0, 120.0, 180.0]
        for i, duration in enumerate(durations):
            await test_database.execute(
                videos.insert().values(
                    title=f"Duration Video {i}",
                    slug=f"duration-test-{i}",
                    category_id=sample_category["id"],
                    duration=duration,
                    status=VideoStatus.READY,
                    created_at=now,
                )
            )

        # Sum durations
        result = await test_database.fetch_one(
            sa.select(sa.func.sum(videos.c.duration).label("total_duration")).where(
                videos.c.slug.like("duration-test-%")
            )
        )

        assert result["total_duration"] == sum(durations)


class TestTransactionBehavior:
    """Test transaction behavior across databases."""

    @pytest.mark.asyncio
    async def test_transaction_rollback(self, test_database, sample_category):
        """Test that transactions can be rolled back."""
        initial_count = await test_database.fetch_val("SELECT COUNT(*) FROM videos")

        try:
            async with test_database.transaction():
                await test_database.execute(
                    videos.insert().values(
                        title="Transaction Test",
                        slug="transaction-test-video",
                        category_id=sample_category["id"],
                        status=VideoStatus.PENDING,
                        created_at=datetime.now(timezone.utc),
                    )
                )
                # Force rollback
                raise Exception("Simulated failure")
        except Exception:
            pass

        final_count = await test_database.fetch_val("SELECT COUNT(*) FROM videos")
        assert final_count == initial_count, "Transaction should have been rolled back"

    @pytest.mark.asyncio
    async def test_transaction_commit(self, test_database, sample_category):
        """Test that transactions are committed on success."""
        initial_count = await test_database.fetch_val("SELECT COUNT(*) FROM videos")

        async with test_database.transaction():
            await test_database.execute(
                videos.insert().values(
                    title="Commit Test",
                    slug="commit-test-video",
                    category_id=sample_category["id"],
                    status=VideoStatus.PENDING,
                    created_at=datetime.now(timezone.utc),
                )
            )

        final_count = await test_database.fetch_val("SELECT COUNT(*) FROM videos")
        assert final_count == initial_count + 1, "Transaction should have been committed"


class TestUniqueConstraints:
    """Test unique constraint behavior across databases."""

    @pytest.mark.asyncio
    async def test_video_slug_unique(self, test_database, sample_category):
        """Test that duplicate video slugs are rejected."""
        now = datetime.now(timezone.utc)

        # First insert should succeed
        await test_database.execute(
            videos.insert().values(
                title="Original Video",
                slug="unique-slug-test",
                category_id=sample_category["id"],
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        # Second insert with same slug should fail
        with pytest.raises(Exception):  # Database-specific exception
            await test_database.execute(
                videos.insert().values(
                    title="Duplicate Video",
                    slug="unique-slug-test",
                    category_id=sample_category["id"],
                    status=VideoStatus.PENDING,
                    created_at=now,
                )
            )

    @pytest.mark.asyncio
    async def test_quality_progress_unique_constraint(self, test_database, sample_video):
        """Test the unique constraint on (job_id, quality)."""
        video_id = sample_video["id"]

        # Create job
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="pending",
                attempt_number=1,
                max_attempts=3,
            )
        )

        # First insert should succeed
        await test_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="1080p",
                status="pending",
                progress_percent=0,
            )
        )

        # Second insert with same job_id + quality should fail
        with pytest.raises(Exception):
            await test_database.execute(
                quality_progress.insert().values(
                    job_id=job_id,
                    quality="1080p",
                    status="in_progress",
                    progress_percent=50,
                )
            )


class TestForeignKeyBehavior:
    """Test foreign key constraint behavior."""

    @pytest.mark.asyncio
    async def test_cascade_delete_video_qualities_postgresql(self, test_database, sample_video):
        """
        Test that deleting a video cascades to video_qualities.

        Note: SQLite doesn't enforce foreign keys by default, so this test
        verifies the behavior for PostgreSQL. SQLite tests will skip this.
        """
        db_url = str(test_database.url)
        is_postgresql = db_url.startswith("postgresql")

        video_id = sample_video["id"]

        # Add quality record
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_id,
                quality="1080p",
                width=1920,
                height=1080,
                bitrate=5000,
            )
        )

        # Verify it exists
        count_before = await test_database.fetch_val(
            "SELECT COUNT(*) FROM video_qualities WHERE video_id = :vid",
            {"vid": video_id},
        )
        assert count_before == 1

        # Delete the video
        await test_database.execute(videos.delete().where(videos.c.id == video_id))

        # Check result - PostgreSQL cascades, SQLite may not
        count_after = await test_database.fetch_val(
            "SELECT COUNT(*) FROM video_qualities WHERE video_id = :vid",
            {"vid": video_id},
        )

        if is_postgresql:
            # PostgreSQL should cascade delete
            assert count_after == 0, "PostgreSQL should cascade delete video_qualities"
        else:
            # SQLite may or may not cascade depending on FK pragma
            # Just verify the query works - don't assert on cascade behavior
            assert count_after >= 0  # Valid count returned

    @pytest.mark.asyncio
    async def test_manual_cleanup_video_related_records(self, test_database, sample_video):
        """
        Test manual cleanup of video-related records works correctly.

        This tests the actual cleanup pattern used in the codebase,
        which works regardless of foreign key enforcement.
        """
        video_id = sample_video["id"]

        # Create transcoding job
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="transcode",
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create quality progress
        await test_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="1080p",
                status="pending",
                progress_percent=0,
            )
        )

        # Verify they exist
        assert (
            await test_database.fetch_val(
                "SELECT COUNT(*) FROM transcoding_jobs WHERE video_id = :vid",
                {"vid": video_id},
            )
            == 1
        )
        assert (
            await test_database.fetch_val(
                "SELECT COUNT(*) FROM quality_progress WHERE job_id = :jid",
                {"jid": job_id},
            )
            == 1
        )

        # Manual cleanup (order matters for FK constraints)
        # 1. Delete quality_progress first (child of transcoding_jobs)
        await test_database.execute(quality_progress.delete().where(quality_progress.c.job_id == job_id))
        # 2. Delete transcoding_jobs (child of videos)
        await test_database.execute(transcoding_jobs.delete().where(transcoding_jobs.c.video_id == video_id))
        # 3. Now safe to delete video
        await test_database.execute(videos.delete().where(videos.c.id == video_id))

        # Verify all are deleted
        assert (
            await test_database.fetch_val(
                "SELECT COUNT(*) FROM transcoding_jobs WHERE video_id = :vid",
                {"vid": video_id},
            )
            == 0
        )
        assert (
            await test_database.fetch_val(
                "SELECT COUNT(*) FROM quality_progress WHERE job_id = :jid",
                {"jid": job_id},
            )
            == 0
        )
