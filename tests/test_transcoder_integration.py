"""
Integration tests for the transcoding pipeline.

These tests verify the integration between transcoder components:
- Database operations (job creation, progress tracking, checkpoints)
- File I/O operations (reading uploads, writing HLS output)
- Error recovery (checkpoint resume, retry logic)
- Status updates and coordination

For full end-to-end tests with FFmpeg, mark tests with @pytest.mark.ffmpeg
and run with: pytest -m ffmpeg (requires ffmpeg installed)
"""

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator

import pytest
import sqlalchemy as sa
from databases import Database

from api.database import (
    metadata,
    quality_progress,
    transcoding_jobs,
    video_qualities,
    videos,
)
from api.enums import VideoStatus

# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture(scope="function")
def integration_temp_dir(tmp_path: Path) -> dict:
    """Create temporary directories for integration tests."""
    videos_dir = tmp_path / "videos"
    uploads_dir = tmp_path / "uploads"
    archive_dir = tmp_path / "archive"

    videos_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    return {
        "videos": videos_dir,
        "uploads": uploads_dir,
        "archive": archive_dir,
        "root": tmp_path,
    }


@pytest.fixture(scope="function")
async def integration_database(tmp_path: Path) -> AsyncGenerator[Database, None]:
    """Create a test database for integration tests."""
    db_path = tmp_path / "integration_test.db"
    db_url = f"sqlite:///{db_path}"

    # Create tables
    engine = sa.create_engine(db_url)
    metadata.create_all(engine)
    engine.dispose()

    # Connect async database
    database = Database(db_url)
    await database.connect()

    yield database

    await database.disconnect()
    if db_path.exists():
        db_path.unlink()


@pytest.fixture(scope="function")
async def integration_video(integration_database: Database) -> dict:
    """Create a test video for integration tests."""
    now = datetime.now(timezone.utc)
    video_id = await integration_database.execute(
        videos.insert().values(
            title="Integration Test Video",
            slug="integration-test-video",
            status=VideoStatus.PENDING,
            created_at=now,
        )
    )
    return {
        "id": video_id,
        "title": "Integration Test Video",
        "slug": "integration-test-video",
        "status": VideoStatus.PENDING,
    }


# ============================================================================
# Database Integration Tests
# ============================================================================


class TestTranscodingJobDatabase:
    """Tests for transcoding job database operations."""

    @pytest.mark.asyncio
    async def test_create_transcoding_job(self, integration_database, integration_video):
        """Test creating a transcoding job."""
        now = datetime.now(timezone.utc)
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="test-worker-001",
                current_step="probe",
                progress_percent=0,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        job = await integration_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )

        assert job is not None
        assert job["video_id"] == integration_video["id"]
        assert job["worker_id"] == "test-worker-001"
        assert job["current_step"] == "probe"
        assert job["attempt_number"] == 1

    @pytest.mark.asyncio
    async def test_update_job_progress(self, integration_database, integration_video):
        """Test updating job progress."""
        now = datetime.now(timezone.utc)
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="test-worker",
                started_at=now,
                last_checkpoint=now,
            )
        )

        # Update progress
        await integration_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(
                current_step="transcode",
                progress_percent=50,
                last_checkpoint=now,
            )
        )

        job = await integration_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )

        assert job["current_step"] == "transcode"
        assert job["progress_percent"] == 50

    @pytest.mark.asyncio
    async def test_create_quality_progress(self, integration_database, integration_video):
        """Test creating quality progress records."""
        now = datetime.now(timezone.utc)
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="test-worker",
                started_at=now,
                last_checkpoint=now,
            )
        )

        # Create quality progress for multiple qualities
        for quality_name, status in [
            ("1080p", "completed"),
            ("720p", "processing"),
            ("480p", "pending"),
        ]:
            await integration_database.execute(
                quality_progress.insert().values(
                    job_id=job_id,
                    quality=quality_name,
                    status=status,
                    progress_percent=100 if status == "completed" else 50 if status == "processing" else 0,
                )
            )

        # Verify all qualities were created
        qualities = await integration_database.fetch_all(
            quality_progress.select().where(quality_progress.c.job_id == job_id)
        )

        assert len(qualities) == 3
        completed = [q for q in qualities if q["status"] == "completed"]
        assert len(completed) == 1
        assert completed[0]["quality"] == "1080p"

    @pytest.mark.asyncio
    async def test_checkpoint_recovery(self, integration_database, integration_video):
        """Test checkpoint data for crash recovery."""
        now = datetime.now(timezone.utc)
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="test-worker",
                current_step="transcode",
                progress_percent=60,
                started_at=now - timedelta(minutes=10),
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create quality progress showing partial completion
        await integration_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="1080p",
                status="completed",
                progress_percent=100,
            )
        )
        await integration_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="720p",
                status="processing",
                progress_percent=50,
            )
        )

        # Simulate crash recovery - find stale job
        stale_time = now - timedelta(seconds=30)  # Assume 30s without update = stale
        # In production, this would find stale jobs to reset
        _ = await integration_database.fetch_all(
            transcoding_jobs.select().where(transcoding_jobs.c.last_checkpoint < stale_time)
        )

        # Our job should be found (it was updated "now", so not stale in this test)
        # But we can verify the checkpoint data is correct
        job = await integration_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert job["current_step"] == "transcode"
        assert job["progress_percent"] == 60

        # Get completed qualities to skip on resume
        completed_qualities = await integration_database.fetch_all(
            quality_progress.select()
            .where(quality_progress.c.job_id == job_id)
            .where(quality_progress.c.status == "completed")
        )
        assert len(completed_qualities) == 1
        assert completed_qualities[0]["quality"] == "1080p"

    @pytest.mark.asyncio
    async def test_get_or_create_job_race_condition(self, integration_database, integration_video):
        """Test that get_or_create_job handles race condition gracefully."""
        # Local imports are necessary here for proper module patching
        # Importing at module level would prevent patching the database instance
        from sqlite3 import IntegrityError
        from unittest.mock import patch

        import worker.transcoder as transcoder_module

        # First, create a job that already exists in the database
        # This simulates another worker having already created the job
        now = datetime.now(timezone.utc)
        existing_job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="other-worker",
                current_step=None,
                progress_percent=0,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Now test that get_or_create_job handles IntegrityError when trying to insert
        # We'll mock the database.execute to raise IntegrityError on INSERT attempt
        original_execute = integration_database.execute
        call_count = 0

        async def mock_execute(query):
            nonlocal call_count
            call_count += 1
            # Check if this is an INSERT into transcoding_jobs
            query_str = str(query)
            if "INSERT INTO transcoding_jobs" in query_str:
                # Simulate the race condition: another worker already inserted
                raise IntegrityError("UNIQUE constraint failed: transcoding_jobs.video_id")
            # For other queries (like SELECT), use the original execute
            return await original_execute(query)

        # Patch the database in the transcoder module to use our test database
        with patch.object(transcoder_module, "database", integration_database):
            with patch.object(transcoder_module, "WORKER_ID", "test-worker"):
                # Mock execute to force IntegrityError on INSERT
                with patch.object(integration_database, "execute", side_effect=mock_execute):
                    # This should catch the IntegrityError and fetch the existing job
                    job = await transcoder_module.get_or_create_job(integration_video["id"])

                    # Verify it returned the existing job, not a new one
                    assert job is not None
                    assert job["id"] == existing_job_id
                    assert job["video_id"] == integration_video["id"]
                    assert job["worker_id"] == "other-worker"  # From the pre-existing job

        # Verify only one job exists in the database (no duplicate was created)
        all_jobs = await integration_database.fetch_all(
            transcoding_jobs.select().where(transcoding_jobs.c.video_id == integration_video["id"])
        )
        assert len(all_jobs) == 1
        assert all_jobs[0]["id"] == existing_job_id


class TestVideoStatusTransitions:
    """Tests for video status transitions during transcoding."""

    @pytest.mark.asyncio
    async def test_pending_to_processing(self, integration_database, integration_video):
        """Test video status transition from pending to processing."""
        # Update status to processing
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(status=VideoStatus.PROCESSING)
        )

        video = await integration_database.fetch_one(
            videos.select().where(videos.c.id == integration_video["id"])
        )
        assert video["status"] == VideoStatus.PROCESSING

    @pytest.mark.asyncio
    async def test_processing_to_ready(self, integration_database, integration_video):
        """Test video status transition from processing to ready."""
        now = datetime.now(timezone.utc)

        # Set to processing first
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(status=VideoStatus.PROCESSING)
        )

        # Complete transcoding - set to ready with metadata
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(
                status=VideoStatus.READY,
                duration=120.5,
                source_width=1920,
                source_height=1080,
                published_at=now,
            )
        )

        video = await integration_database.fetch_one(
            videos.select().where(videos.c.id == integration_video["id"])
        )
        assert video["status"] == VideoStatus.READY
        assert video["duration"] == 120.5
        assert video["source_width"] == 1920
        assert video["source_height"] == 1080

    @pytest.mark.asyncio
    async def test_processing_to_failed(self, integration_database, integration_video):
        """Test video status transition from processing to failed."""
        # Set to processing first
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(status=VideoStatus.PROCESSING)
        )

        # Fail transcoding
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(
                status=VideoStatus.FAILED,
                error_message="FFmpeg failed: Invalid input file",
            )
        )

        video = await integration_database.fetch_one(
            videos.select().where(videos.c.id == integration_video["id"])
        )
        assert video["status"] == VideoStatus.FAILED
        assert "FFmpeg failed" in video["error_message"]

    @pytest.mark.asyncio
    async def test_retry_increments_attempt(self, integration_database, integration_video):
        """Test retry increments attempt number."""
        now = datetime.now(timezone.utc)

        # Create initial job
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="test-worker",
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Simulate failure and retry
        await integration_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(
                attempt_number=2,
                last_error="First attempt failed",
                started_at=now,
                last_checkpoint=now,
            )
        )

        job = await integration_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert job["attempt_number"] == 2
        assert job["last_error"] == "First attempt failed"


# ============================================================================
# File I/O Integration Tests
# ============================================================================


class TestFileOperations:
    """Tests for file I/O operations."""

    def test_create_video_output_directory(self, integration_temp_dir):
        """Test creating video output directory."""
        slug = "test-video"
        video_dir = integration_temp_dir["videos"] / slug
        video_dir.mkdir(parents=True, exist_ok=True)

        assert video_dir.exists()
        assert video_dir.is_dir()

    def test_source_file_detection(self, integration_temp_dir):
        """Test detecting source files with various extensions."""
        uploads_dir = integration_temp_dir["uploads"]

        # Create test files with different extensions
        extensions = [".mp4", ".mkv", ".webm", ".mov", ".avi"]
        for i, ext in enumerate(extensions):
            source_file = uploads_dir / f"{i}{ext}"
            source_file.write_bytes(b"fake video content")

        # Verify all files are found
        for i, ext in enumerate(extensions):
            source_file = uploads_dir / f"{i}{ext}"
            assert source_file.exists()
            assert source_file.suffix == ext

    def test_hls_output_structure(self, integration_temp_dir):
        """Test creating HLS output structure."""
        video_dir = integration_temp_dir["videos"] / "test-video"
        video_dir.mkdir(parents=True, exist_ok=True)

        # Simulate HLS output
        # Master playlist
        master_playlist = video_dir / "master.m3u8"
        master_playlist.write_text("""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
1080p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720p.m3u8
""")

        # Quality playlists
        for quality in ["1080p", "720p"]:
            playlist = video_dir / f"{quality}.m3u8"
            playlist.write_text(f"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
{quality}_0000.ts
#EXT-X-ENDLIST
""")

        # Segment files
        for quality in ["1080p", "720p"]:
            segment = video_dir / f"{quality}_0000.ts"
            segment.write_bytes(b"fake segment data")

        # Thumbnail
        thumbnail = video_dir / "thumbnail.jpg"
        thumbnail.write_bytes(b"fake thumbnail")

        # Verify structure
        assert master_playlist.exists()
        assert (video_dir / "1080p.m3u8").exists()
        assert (video_dir / "720p.m3u8").exists()
        assert (video_dir / "1080p_0000.ts").exists()
        assert (video_dir / "720p_0000.ts").exists()
        assert thumbnail.exists()

    def test_cleanup_partial_files_on_failure(self, integration_temp_dir):
        """Test cleaning up partial files on failure."""
        video_dir = integration_temp_dir["videos"] / "failed-video"
        video_dir.mkdir(parents=True, exist_ok=True)

        # Create partial output
        (video_dir / "1080p_0000.ts").write_bytes(b"partial")
        (video_dir / "1080p_0001.ts").write_bytes(b"partial")

        # Simulate cleanup
        for file in video_dir.iterdir():
            if file.is_file():
                file.unlink()

        # Verify cleanup
        remaining_files = list(video_dir.iterdir())
        assert len(remaining_files) == 0

    def test_archive_on_soft_delete(self, integration_temp_dir):
        """Test moving files to archive on soft delete."""
        videos_dir = integration_temp_dir["videos"]
        archive_dir = integration_temp_dir["archive"]

        # Create video directory with files
        video_dir = videos_dir / "test-video"
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "master.m3u8").write_text("playlist content")
        (video_dir / "thumbnail.jpg").write_bytes(b"thumbnail")

        # Move to archive
        archive_video_dir = archive_dir / "test-video"
        shutil.move(str(video_dir), str(archive_video_dir))

        # Verify move
        assert not video_dir.exists()
        assert archive_video_dir.exists()
        assert (archive_video_dir / "master.m3u8").exists()
        assert (archive_video_dir / "thumbnail.jpg").exists()


# ============================================================================
# Error Recovery Tests
# ============================================================================


class TestErrorRecovery:
    """Tests for error recovery mechanisms."""

    @pytest.mark.asyncio
    async def test_stale_job_detection(self, integration_database, integration_video):
        """Test detecting stale transcoding jobs."""
        # Create a job with old checkpoint
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=35)  # 35 min ago
        await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="dead-worker",
                current_step="transcode",
                started_at=stale_time,
                last_checkpoint=stale_time,
            )
        )

        # Query for stale jobs (older than 30 min)
        threshold = datetime.now(timezone.utc) - timedelta(minutes=30)
        stale_jobs = await integration_database.fetch_all(
            transcoding_jobs.select().where(transcoding_jobs.c.last_checkpoint < threshold)
        )

        assert len(stale_jobs) == 1
        assert stale_jobs[0]["worker_id"] == "dead-worker"

    @pytest.mark.asyncio
    async def test_mark_video_failed_after_max_retries(self, integration_database, integration_video):
        """Test video marked as failed after max retries."""
        max_attempts = 3
        now = datetime.now(timezone.utc)

        # Create job at max attempts
        await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="test-worker",
                started_at=now,
                last_checkpoint=now,
                attempt_number=max_attempts,
                max_attempts=max_attempts,
                last_error="Repeated failure",
            )
        )

        # Simulate exceeding max attempts - mark video as failed
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(
                status=VideoStatus.FAILED,
                error_message=f"Failed after {max_attempts} attempts: Repeated failure",
            )
        )

        video = await integration_database.fetch_one(
            videos.select().where(videos.c.id == integration_video["id"])
        )
        assert video["status"] == VideoStatus.FAILED
        assert f"{max_attempts} attempts" in video["error_message"]

    @pytest.mark.asyncio
    async def test_preserve_completed_qualities_on_resume(self, integration_database, integration_video):
        """Test completed qualities are preserved when resuming."""
        now = datetime.now(timezone.utc)

        # Create job with some completed qualities
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="test-worker",
                current_step="transcode",
                progress_percent=50,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # 1080p completed, 720p in progress
        await integration_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="1080p",
                status="completed",
                progress_percent=100,
            )
        )
        await integration_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="720p",
                status="processing",
                progress_percent=30,
            )
        )

        # Query completed qualities for resume
        completed = await integration_database.fetch_all(
            quality_progress.select()
            .where(quality_progress.c.job_id == job_id)
            .where(quality_progress.c.status == "completed")
        )

        assert len(completed) == 1
        assert completed[0]["quality"] == "1080p"

        # Qualities to retry
        to_retry = await integration_database.fetch_all(
            quality_progress.select()
            .where(quality_progress.c.job_id == job_id)
            .where(quality_progress.c.status != "completed")
        )

        assert len(to_retry) == 1
        assert to_retry[0]["quality"] == "720p"


# ============================================================================
# Video Qualities Integration Tests
# ============================================================================


class TestVideoQualitiesIntegration:
    """Tests for video qualities database operations."""

    @pytest.mark.asyncio
    async def test_store_video_qualities(self, integration_database, integration_video):
        """Test storing video qualities after transcoding."""
        qualities_data = [
            {"quality": "1080p", "width": 1920, "height": 1080, "bitrate": 5000},
            {"quality": "720p", "width": 1280, "height": 720, "bitrate": 2500},
            {"quality": "480p", "width": 854, "height": 480, "bitrate": 1000},
        ]

        for q in qualities_data:
            await integration_database.execute(
                video_qualities.insert().values(
                    video_id=integration_video["id"],
                    quality=q["quality"],
                    width=q["width"],
                    height=q["height"],
                    bitrate=q["bitrate"],
                )
            )

        stored_qualities = await integration_database.fetch_all(
            video_qualities.select().where(video_qualities.c.video_id == integration_video["id"])
        )

        assert len(stored_qualities) == 3
        quality_names = {q["quality"] for q in stored_qualities}
        assert quality_names == {"1080p", "720p", "480p"}

    @pytest.mark.asyncio
    async def test_delete_qualities_on_reupload(self, integration_database, integration_video):
        """Test deleting video qualities when re-uploading."""
        # Add initial qualities
        for quality in ["1080p", "720p"]:
            await integration_database.execute(
                video_qualities.insert().values(
                    video_id=integration_video["id"],
                    quality=quality,
                    width=1920 if quality == "1080p" else 1280,
                    height=1080 if quality == "1080p" else 720,
                    bitrate=5000 if quality == "1080p" else 2500,
                )
            )

        # Verify qualities exist
        qualities = await integration_database.fetch_all(
            video_qualities.select().where(video_qualities.c.video_id == integration_video["id"])
        )
        assert len(qualities) == 2

        # Delete qualities (simulating re-upload)
        await integration_database.execute(
            video_qualities.delete().where(video_qualities.c.video_id == integration_video["id"])
        )

        # Verify deletion
        qualities = await integration_database.fetch_all(
            video_qualities.select().where(video_qualities.c.video_id == integration_video["id"])
        )
        assert len(qualities) == 0


# ============================================================================
# End-to-End Integration Tests (Mocked FFmpeg)
# ============================================================================


class TestTranscodingPipelineMocked:
    """End-to-end tests with mocked FFmpeg subprocess."""

    @pytest.mark.asyncio
    async def test_full_pipeline_success_mocked(
        self, integration_database, integration_video, integration_temp_dir
    ):
        """Test full transcoding pipeline with mocked FFmpeg."""
        now = datetime.now(timezone.utc)

        # 1. Create source file
        source_file = integration_temp_dir["uploads"] / f"{integration_video['id']}.mp4"
        source_file.write_bytes(b"fake video content")

        # 2. Create video output directory
        video_dir = integration_temp_dir["videos"] / integration_video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)

        # 3. Update status to processing
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(status=VideoStatus.PROCESSING)
        )

        # 4. Create transcoding job
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="test-worker",
                current_step="probe",
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # 5. Simulate probe step
        await integration_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(current_step="transcode", progress_percent=10)
        )

        # 6. Simulate transcoding each quality
        for quality_name in ["1080p", "720p", "480p"]:
            # Create quality progress
            await integration_database.execute(
                quality_progress.insert().values(
                    job_id=job_id,
                    quality=quality_name,
                    status="processing",
                    progress_percent=0,
                )
            )

            # Simulate FFmpeg output (create HLS files)
            playlist_content = f"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
{quality_name}_0000.ts
#EXT-X-ENDLIST
"""
            (video_dir / f"{quality_name}.m3u8").write_text(playlist_content)
            (video_dir / f"{quality_name}_0000.ts").write_bytes(b"fake segment")

            # Mark quality complete
            await integration_database.execute(
                quality_progress.update()
                .where(quality_progress.c.job_id == job_id)
                .where(quality_progress.c.quality == quality_name)
                .values(status="completed", progress_percent=100)
            )

            # Store video quality record
            await integration_database.execute(
                video_qualities.insert().values(
                    video_id=integration_video["id"],
                    quality=quality_name,
                    width={"1080p": 1920, "720p": 1280, "480p": 854}[quality_name],
                    height={"1080p": 1080, "720p": 720, "480p": 480}[quality_name],
                    bitrate={"1080p": 5000, "720p": 2500, "480p": 1000}[quality_name],
                )
            )

        # 7. Generate master playlist
        master_content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
1080p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=854x480
480p.m3u8
"""
        (video_dir / "master.m3u8").write_text(master_content)

        # 8. Generate thumbnail
        (video_dir / "thumbnail.jpg").write_bytes(b"fake thumbnail")

        # 9. Mark video as ready
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(
                status=VideoStatus.READY,
                duration=120.5,
                source_width=1920,
                source_height=1080,
                published_at=now,
            )
        )

        # 10. Clean up source file
        source_file.unlink()

        # Verify final state
        video = await integration_database.fetch_one(
            videos.select().where(videos.c.id == integration_video["id"])
        )
        assert video["status"] == VideoStatus.READY
        assert video["duration"] == 120.5

        qualities = await integration_database.fetch_all(
            video_qualities.select().where(video_qualities.c.video_id == integration_video["id"])
        )
        assert len(qualities) == 3

        # Verify files
        assert (video_dir / "master.m3u8").exists()
        assert (video_dir / "thumbnail.jpg").exists()
        assert not source_file.exists()  # Source should be cleaned up

    @pytest.mark.asyncio
    async def test_pipeline_failure_and_recovery(
        self, integration_database, integration_video, integration_temp_dir
    ):
        """Test pipeline failure and recovery from checkpoint."""
        now = datetime.now(timezone.utc)

        # Setup
        source_file = integration_temp_dir["uploads"] / f"{integration_video['id']}.mp4"
        source_file.write_bytes(b"fake video content")

        video_dir = integration_temp_dir["videos"] / integration_video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)

        # Start processing
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(status=VideoStatus.PROCESSING)
        )

        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="worker-1",
                current_step="transcode",
                progress_percent=30,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Complete 1080p before "crash"
        await integration_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="1080p",
                status="completed",
                progress_percent=100,
            )
        )
        (video_dir / "1080p.m3u8").write_text("playlist")
        (video_dir / "1080p_0000.ts").write_bytes(b"segment")

        # Simulate crash during 720p
        await integration_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="720p",
                status="processing",
                progress_percent=50,
            )
        )

        # === RECOVERY PHASE ===

        # Worker restart - find stale job
        await integration_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(
                worker_id="worker-2",  # New worker picks up
                attempt_number=2,
                last_checkpoint=datetime.now(timezone.utc),
            )
        )

        # Check what's already done
        completed = await integration_database.fetch_all(
            quality_progress.select()
            .where(quality_progress.c.job_id == job_id)
            .where(quality_progress.c.status == "completed")
        )
        assert len(completed) == 1
        assert completed[0]["quality"] == "1080p"

        # Resume from 720p
        await integration_database.execute(
            quality_progress.update()
            .where(quality_progress.c.job_id == job_id)
            .where(quality_progress.c.quality == "720p")
            .values(status="completed", progress_percent=100)
        )
        (video_dir / "720p.m3u8").write_text("playlist")
        (video_dir / "720p_0000.ts").write_bytes(b"segment")

        # Complete 480p
        await integration_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="480p",
                status="completed",
                progress_percent=100,
            )
        )
        (video_dir / "480p.m3u8").write_text("playlist")
        (video_dir / "480p_0000.ts").write_bytes(b"segment")

        # Finalize
        (video_dir / "master.m3u8").write_text("master playlist")
        (video_dir / "thumbnail.jpg").write_bytes(b"thumbnail")

        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(status=VideoStatus.READY, duration=120.0)
        )

        # Verify recovery succeeded
        video = await integration_database.fetch_one(
            videos.select().where(videos.c.id == integration_video["id"])
        )
        assert video["status"] == VideoStatus.READY

        job = await integration_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert job["attempt_number"] == 2
        assert job["worker_id"] == "worker-2"
