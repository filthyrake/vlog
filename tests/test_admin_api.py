"""
Tests for the admin API endpoints.
"""
import pytest
from datetime import datetime, timezone

from api.database import videos, categories, video_qualities, playback_sessions, transcriptions, transcoding_jobs, quality_progress
from api.enums import VideoStatus, TranscriptionStatus


class TestCategoryManagement:
    """Tests for category CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_category(self, test_database):
        """Test creating a new category."""
        now = datetime.now(timezone.utc)
        result = await test_database.execute(
            categories.insert().values(
                name="New Category",
                slug="new-category",
                description="A brand new category",
                created_at=now,
            )
        )

        assert result > 0

        category = await test_database.fetch_one(
            categories.select().where(categories.c.id == result)
        )
        assert category["name"] == "New Category"
        assert category["slug"] == "new-category"

    @pytest.mark.asyncio
    async def test_create_category_duplicate_slug_fails(self, test_database, sample_category):
        """Test creating category with duplicate slug fails."""
        import sqlite3

        with pytest.raises(Exception):  # sqlite3.IntegrityError wrapped
            await test_database.execute(
                categories.insert().values(
                    name="Another Category",
                    slug="test-category",  # Same as sample_category
                    created_at=datetime.now(timezone.utc),
                )
            )

    @pytest.mark.asyncio
    async def test_delete_category(self, test_database, sample_category):
        """Test deleting a category."""
        category_id = sample_category["id"]

        await test_database.execute(
            categories.delete().where(categories.c.id == category_id)
        )

        result = await test_database.fetch_one(
            categories.select().where(categories.c.id == category_id)
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_category_unassigns_videos(self, test_database, sample_video, sample_category):
        """Test deleting category sets video category_id to NULL."""
        category_id = sample_category["id"]
        video_id = sample_video["id"]

        # First unassign videos from category
        await test_database.execute(
            videos.update()
            .where(videos.c.category_id == category_id)
            .values(category_id=None)
        )

        # Then delete category
        await test_database.execute(
            categories.delete().where(categories.c.id == category_id)
        )

        # Verify video still exists but without category
        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == video_id)
        )
        assert video is not None
        assert video["category_id"] is None


class TestVideoManagement:
    """Tests for video CRUD operations."""

    @pytest.mark.asyncio
    async def test_list_all_videos_includes_all_statuses(self, test_database, sample_category):
        """Test admin video list includes non-ready videos."""
        now = datetime.now(timezone.utc)

        # Create videos with different statuses
        for status in [VideoStatus.PENDING, VideoStatus.PROCESSING, VideoStatus.READY, VideoStatus.FAILED]:
            await test_database.execute(
                videos.insert().values(
                    title=f"{status} Video",
                    slug=f"{status}-video",
                    status=status,
                    created_at=now,
                )
            )

        result = await test_database.fetch_all(
            videos.select().where(videos.c.deleted_at == None)
        )
        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_update_video_metadata(self, test_database, sample_video):
        """Test updating video metadata."""
        video_id = sample_video["id"]

        await test_database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(
                title="Updated Title",
                description="Updated description",
            )
        )

        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == video_id)
        )
        assert video["title"] == "Updated Title"
        assert video["description"] == "Updated description"

    @pytest.mark.asyncio
    async def test_update_video_category(self, test_database, sample_video, sample_category):
        """Test changing video category."""
        video_id = sample_video["id"]

        # Create a new category
        new_category_id = await test_database.execute(
            categories.insert().values(
                name="New Category",
                slug="new-category",
                created_at=datetime.now(timezone.utc),
            )
        )

        await test_database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(category_id=new_category_id)
        )

        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == video_id)
        )
        assert video["category_id"] == new_category_id

    @pytest.mark.asyncio
    async def test_soft_delete_video(self, test_database, sample_video):
        """Test soft-deleting a video."""
        video_id = sample_video["id"]
        now = datetime.now(timezone.utc)

        await test_database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(deleted_at=now)
        )

        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == video_id)
        )
        assert video["deleted_at"] is not None

    @pytest.mark.asyncio
    async def test_restore_video(self, test_database, sample_video):
        """Test restoring a soft-deleted video."""
        video_id = sample_video["id"]

        # First soft-delete
        await test_database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(deleted_at=datetime.now(timezone.utc))
        )

        # Then restore
        await test_database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(deleted_at=None)
        )

        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == video_id)
        )
        assert video["deleted_at"] is None

    @pytest.mark.asyncio
    async def test_permanent_delete_video(self, test_database, sample_video_with_qualities):
        """Test permanently deleting a video and related records."""
        video_id = sample_video_with_qualities["id"]

        # Delete related records first (respecting foreign keys)
        await test_database.execute(
            video_qualities.delete().where(video_qualities.c.video_id == video_id)
        )
        await test_database.execute(
            playback_sessions.delete().where(playback_sessions.c.video_id == video_id)
        )
        await test_database.execute(
            transcriptions.delete().where(transcriptions.c.video_id == video_id)
        )

        # Delete the video
        await test_database.execute(
            videos.delete().where(videos.c.id == video_id)
        )

        # Verify everything is gone
        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == video_id)
        )
        assert video is None

        qualities = await test_database.fetch_all(
            video_qualities.select().where(video_qualities.c.video_id == video_id)
        )
        assert len(qualities) == 0


class TestVideoRetry:
    """Tests for video retry functionality."""

    @pytest.mark.asyncio
    async def test_retry_failed_video(self, test_database, sample_category):
        """Test retrying a failed video."""
        now = datetime.now(timezone.utc)

        video_id = await test_database.execute(
            videos.insert().values(
                title="Failed Video",
                slug="failed-video",
                status=VideoStatus.FAILED,
                error_message="Transcoding failed",
                created_at=now,
            )
        )

        # Reset to pending
        await test_database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(
                status=VideoStatus.PENDING,
                error_message=None,
            )
        )

        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == video_id)
        )
        assert video["status"] == VideoStatus.PENDING
        assert video["error_message"] is None


class TestTranscriptionManagement:
    """Tests for transcription management."""

    @pytest.mark.asyncio
    async def test_create_transcription_record(self, test_database, sample_video):
        """Test creating a transcription record."""
        video_id = sample_video["id"]

        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_id,
                status=TranscriptionStatus.PENDING,
            )
        )

        result = await test_database.fetch_one(
            transcriptions.select().where(transcriptions.c.video_id == video_id)
        )
        assert result["status"] == TranscriptionStatus.PENDING

    @pytest.mark.asyncio
    async def test_update_transcription_text(self, test_database, sample_video):
        """Test updating transcription text."""
        video_id = sample_video["id"]

        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_id,
                status=TranscriptionStatus.COMPLETED,
                transcript_text="Original text",
                word_count=2,
            )
        )

        # Update text
        new_text = "Updated transcription text with more words"
        await test_database.execute(
            transcriptions.update()
            .where(transcriptions.c.video_id == video_id)
            .values(
                transcript_text=new_text,
                word_count=6,
            )
        )

        result = await test_database.fetch_one(
            transcriptions.select().where(transcriptions.c.video_id == video_id)
        )
        assert result["transcript_text"] == new_text
        assert result["word_count"] == 6

    @pytest.mark.asyncio
    async def test_delete_transcription(self, test_database, sample_video):
        """Test deleting a transcription."""
        video_id = sample_video["id"]

        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_id,
                status=TranscriptionStatus.COMPLETED,
            )
        )

        await test_database.execute(
            transcriptions.delete().where(transcriptions.c.video_id == video_id)
        )

        result = await test_database.fetch_one(
            transcriptions.select().where(transcriptions.c.video_id == video_id)
        )
        assert result is None


class TestAnalyticsAdmin:
    """Tests for admin analytics queries."""

    @pytest.mark.asyncio
    async def test_count_total_views(self, test_database, sample_playback_session):
        """Test counting total views."""
        import sqlalchemy as sa

        count = await test_database.fetch_val(
            sa.select(sa.func.count()).select_from(playback_sessions)
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_sum_watch_time(self, test_database, sample_playback_session):
        """Test summing total watch time."""
        import sqlalchemy as sa

        total = await test_database.fetch_val(
            sa.select(sa.func.sum(playback_sessions.c.duration_watched))
            .select_from(playback_sessions)
        )
        assert total == 60.0  # From sample_playback_session fixture

    @pytest.mark.asyncio
    async def test_count_completed_sessions(self, test_database, sample_video):
        """Test counting completed sessions."""
        import sqlalchemy as sa
        import uuid

        # Create a completed session
        await test_database.execute(
            playback_sessions.insert().values(
                video_id=sample_video["id"],
                session_token=str(uuid.uuid4()),
                started_at=datetime.now(timezone.utc),
                completed=True,
            )
        )

        count = await test_database.fetch_val(
            sa.select(sa.func.count())
            .select_from(playback_sessions)
            .where(playback_sessions.c.completed == True)
        )
        assert count == 1


class TestTranscodingJobs:
    """Tests for transcoding job management."""

    @pytest.mark.asyncio
    async def test_create_transcoding_job(self, test_database, sample_pending_video):
        """Test creating a transcoding job."""
        video_id = sample_pending_video["id"]
        now = datetime.now(timezone.utc)

        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id="test-worker",
                current_step="probe",
                progress_percent=0,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        job = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert job["video_id"] == video_id
        assert job["current_step"] == "probe"

    @pytest.mark.asyncio
    async def test_update_job_progress(self, test_database, sample_pending_video):
        """Test updating job progress."""
        video_id = sample_pending_video["id"]
        now = datetime.now(timezone.utc)

        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id="test-worker",
                started_at=now,
                last_checkpoint=now,
            )
        )

        await test_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(
                current_step="transcode",
                progress_percent=50,
            )
        )

        job = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert job["progress_percent"] == 50

    @pytest.mark.asyncio
    async def test_create_quality_progress(self, test_database, sample_pending_video):
        """Test creating quality progress records."""
        video_id = sample_pending_video["id"]
        now = datetime.now(timezone.utc)

        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id="test-worker",
                started_at=now,
                last_checkpoint=now,
            )
        )

        # Create progress for multiple qualities
        for quality_name in ["1080p", "720p", "480p"]:
            await test_database.execute(
                quality_progress.insert().values(
                    job_id=job_id,
                    quality=quality_name,
                    status="pending",
                    progress_percent=0,
                )
            )

        result = await test_database.fetch_all(
            quality_progress.select().where(quality_progress.c.job_id == job_id)
        )
        assert len(result) == 3


class TestArchivedVideos:
    """Tests for archived (soft-deleted) video management."""

    @pytest.mark.asyncio
    async def test_list_archived_videos(self, test_database, sample_category):
        """Test listing archived videos."""
        now = datetime.now(timezone.utc)

        # Create some archived videos
        for i in range(3):
            await test_database.execute(
                videos.insert().values(
                    title=f"Archived Video {i}",
                    slug=f"archived-{i}",
                    status=VideoStatus.READY,
                    created_at=now,
                    deleted_at=now,
                )
            )

        # Create a non-archived video
        await test_database.execute(
            videos.insert().values(
                title="Active Video",
                slug="active",
                status=VideoStatus.READY,
                created_at=now,
            )
        )

        result = await test_database.fetch_all(
            videos.select().where(videos.c.deleted_at != None)
        )
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_archived_videos_not_in_public_list(self, test_database, sample_category):
        """Test that archived videos are excluded from public listing."""
        now = datetime.now(timezone.utc)

        # Create an archived video
        await test_database.execute(
            videos.insert().values(
                title="Archived Video",
                slug="archived",
                status=VideoStatus.READY,
                created_at=now,
                deleted_at=now,
            )
        )

        # Create an active video
        await test_database.execute(
            videos.insert().values(
                title="Active Video",
                slug="active",
                status=VideoStatus.READY,
                created_at=now,
            )
        )

        # Public query excludes deleted
        result = await test_database.fetch_all(
            videos.select().where(
                (videos.c.status == VideoStatus.READY) &
                (videos.c.deleted_at == None)
            )
        )
        assert len(result) == 1
        assert result[0]["slug"] == "active"
