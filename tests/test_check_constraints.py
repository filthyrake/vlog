"""
Tests for database CHECK constraints on enum columns and range validations.

Tests that verify:
- Valid enum values are accepted by the database
- Invalid enum values are rejected at the database level
- Progress percent values are constrained to 0-100 range
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from api import database


class TestCheckConstraints:
    """Test CHECK constraints on enum columns."""

    @pytest.mark.asyncio
    async def test_videos_status_valid_values(self, test_database):
        """Test that valid video status values are accepted."""
        valid_statuses = ["pending", "processing", "ready", "failed"]
        
        for status in valid_statuses:
            query = database.videos.insert().values(
                title=f"Video with {status} status",
                slug=f"video-{status}",
                status=status
            )
            result = await test_database.execute(query)
            assert result is not None
            
            # Clean up
            await test_database.execute(
                database.videos.delete().where(database.videos.c.id == result)
            )

    @pytest.mark.asyncio
    async def test_videos_status_invalid_value(self, test_database):
        """Test that invalid video status values are rejected."""
        query = database.videos.insert().values(
            title="Video with invalid status",
            slug="video-invalid",
            status="invalid_status"
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_videos_thumbnail_source_valid_values(self, test_database):
        """Test that valid thumbnail_source values are accepted."""
        valid_sources = ["auto", "selected", "custom"]
        
        for source in valid_sources:
            query = database.videos.insert().values(
                title=f"Video with {source} thumbnail",
                slug=f"video-thumb-{source}",
                thumbnail_source=source
            )
            result = await test_database.execute(query)
            assert result is not None
            
            # Clean up
            await test_database.execute(
                database.videos.delete().where(database.videos.c.id == result)
            )

    @pytest.mark.asyncio
    async def test_videos_thumbnail_source_invalid_value(self, test_database):
        """Test that invalid thumbnail_source values are rejected."""
        query = database.videos.insert().values(
            title="Video with invalid thumbnail source",
            slug="video-thumb-invalid",
            thumbnail_source="invalid_source"
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_quality_progress_status_valid_values(self, test_database, sample_video):
        """Test that valid quality_progress status values are accepted."""
        # Create a transcoding job first
        job_query = database.transcoding_jobs.insert().values(video_id=sample_video["id"])
        job_id = await test_database.execute(job_query)
        
        valid_statuses = ["pending", "in_progress", "completed", "failed", "skipped"]
        
        for status in valid_statuses:
            query = database.quality_progress.insert().values(
                job_id=job_id,
                quality=f"{status}-720p",
                status=status
            )
            result = await test_database.execute(query)
            assert result is not None

    @pytest.mark.asyncio
    async def test_quality_progress_status_invalid_value(self, test_database, sample_video):
        """Test that invalid quality_progress status values are rejected."""
        # Create a transcoding job first
        job_query = database.transcoding_jobs.insert().values(video_id=sample_video["id"])
        job_id = await test_database.execute(job_query)
        
        query = database.quality_progress.insert().values(
            job_id=job_id,
            quality="720p",
            status="invalid_status"
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_transcriptions_status_valid_values(self, test_database, sample_video):
        """Test that valid transcription status values are accepted."""
        valid_statuses = ["pending", "processing", "completed", "failed"]
        
        for status in valid_statuses:
            # Clean up any existing transcription
            await test_database.execute(
                database.transcriptions.delete().where(
                    database.transcriptions.c.video_id == sample_video["id"]
                )
            )
            
            query = database.transcriptions.insert().values(
                video_id=sample_video["id"],
                status=status
            )
            result = await test_database.execute(query)
            assert result is not None

    @pytest.mark.asyncio
    async def test_transcriptions_status_invalid_value(self, test_database, sample_video):
        """Test that invalid transcription status values are rejected."""
        query = database.transcriptions.insert().values(
            video_id=sample_video["id"],
            status="invalid_status"
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_workers_status_valid_values(self, test_database):
        """Test that valid worker status values are accepted."""
        valid_statuses = ["active", "offline", "disabled"]
        
        for status in valid_statuses:
            query = database.workers.insert().values(
                worker_id=f"worker-{status}",
                status=status,
                registered_at=sa.func.now()
            )
            result = await test_database.execute(query)
            assert result is not None

    @pytest.mark.asyncio
    async def test_workers_status_invalid_value(self, test_database):
        """Test that invalid worker status values are rejected."""
        query = database.workers.insert().values(
            worker_id="worker-invalid",
            status="invalid_status",
            registered_at=sa.func.now()
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_workers_worker_type_valid_values(self, test_database):
        """Test that valid worker_type values are accepted."""
        valid_types = ["local", "remote"]
        
        for worker_type in valid_types:
            query = database.workers.insert().values(
                worker_id=f"worker-type-{worker_type}",
                worker_type=worker_type,
                registered_at=sa.func.now()
            )
            result = await test_database.execute(query)
            assert result is not None

    @pytest.mark.asyncio
    async def test_workers_worker_type_invalid_value(self, test_database):
        """Test that invalid worker_type values are rejected."""
        query = database.workers.insert().values(
            worker_id="worker-type-invalid",
            worker_type="invalid_type",
            registered_at=sa.func.now()
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_video_qualities_quality_valid_values(self, test_database, sample_video):
        """Test that valid quality values are accepted."""
        valid_qualities = ["2160p", "1440p", "1080p", "720p", "480p", "360p", "original"]
        
        for quality in valid_qualities:
            query = database.video_qualities.insert().values(
                video_id=sample_video["id"],
                quality=quality,
                width=1920,
                height=1080,
                bitrate=5000
            )
            result = await test_database.execute(query)
            assert result is not None

    @pytest.mark.asyncio
    async def test_video_qualities_quality_invalid_value(self, test_database, sample_video):
        """Test that invalid quality values are rejected."""
        query = database.video_qualities.insert().values(
            video_id=sample_video["id"],
            quality="invalid_quality",
            width=1920,
            height=1080,
            bitrate=5000
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_playback_sessions_quality_used_valid_values(self, test_database, sample_video, sample_viewer):
        """Test that valid quality_used values are accepted (including NULL)."""
        valid_qualities = ["2160p", "1440p", "1080p", "720p", "480p", "360p", "original", None]
        
        for quality in valid_qualities:
            query = database.playback_sessions.insert().values(
                video_id=sample_video["id"],
                viewer_id=sample_viewer["id"],
                session_token=f"session-{quality or 'null'}",
                quality_used=quality
            )
            result = await test_database.execute(query)
            assert result is not None

    @pytest.mark.asyncio
    async def test_playback_sessions_quality_used_invalid_value(self, test_database, sample_video, sample_viewer):
        """Test that invalid quality_used values are rejected."""
        query = database.playback_sessions.insert().values(
            video_id=sample_video["id"],
            viewer_id=sample_viewer["id"],
            session_token="session-invalid",
            quality_used="invalid_quality"
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)


class TestProgressPercentConstraints:
    """Test CHECK constraints on progress_percent columns."""

    @pytest.mark.asyncio
    async def test_transcoding_jobs_progress_percent_valid_range(self, test_database, sample_video):
        """Test that valid progress_percent values (0-100) are accepted."""
        valid_percents = [0, 25, 50, 75, 100]
        
        for percent in valid_percents:
            # Clean up existing job
            await test_database.execute(
                database.transcoding_jobs.delete().where(
                    database.transcoding_jobs.c.video_id == sample_video["id"]
                )
            )
            
            query = database.transcoding_jobs.insert().values(
                video_id=sample_video["id"],
                progress_percent=percent
            )
            result = await test_database.execute(query)
            assert result is not None

    @pytest.mark.asyncio
    async def test_transcoding_jobs_progress_percent_below_zero(self, test_database, sample_video):
        """Test that progress_percent values below 0 are rejected."""
        query = database.transcoding_jobs.insert().values(
            video_id=sample_video["id"],
            progress_percent=-1
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_transcoding_jobs_progress_percent_above_100(self, test_database):
        """Test that progress_percent values above 100 are rejected."""
        # First create a video
        video_query = database.videos.insert().values(
            title="Video for progress test",
            slug="video-progress-test"
        )
        video_id = await test_database.execute(video_query)
        
        query = database.transcoding_jobs.insert().values(
            video_id=video_id,
            progress_percent=101
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_quality_progress_progress_percent_valid_range(self, test_database, sample_video):
        """Test that valid progress_percent values (0-100) are accepted."""
        # Create a transcoding job first
        job_query = database.transcoding_jobs.insert().values(video_id=sample_video["id"])
        job_id = await test_database.execute(job_query)
        
        valid_percents = [0, 25, 50, 75, 100]
        
        for percent in valid_percents:
            query = database.quality_progress.insert().values(
                job_id=job_id,
                quality=f"720p-{percent}",
                progress_percent=percent
            )
            result = await test_database.execute(query)
            assert result is not None

    @pytest.mark.asyncio
    async def test_quality_progress_progress_percent_below_zero(self, test_database, sample_video):
        """Test that progress_percent values below 0 are rejected."""
        # Create a transcoding job first
        job_query = database.transcoding_jobs.insert().values(video_id=sample_video["id"])
        job_id = await test_database.execute(job_query)
        
        query = database.quality_progress.insert().values(
            job_id=job_id,
            quality="720p",
            progress_percent=-1
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)

    @pytest.mark.asyncio
    async def test_quality_progress_progress_percent_above_100(self, test_database, sample_video):
        """Test that progress_percent values above 100 are rejected."""
        # Create a transcoding job first
        job_query = database.transcoding_jobs.insert().values(video_id=sample_video["id"])
        job_id = await test_database.execute(job_query)
        
        query = database.quality_progress.insert().values(
            job_id=job_id,
            quality="720p",
            progress_percent=101
        )
        
        with pytest.raises((IntegrityError, sa.exc.IntegrityError)):
            await test_database.execute(query)
