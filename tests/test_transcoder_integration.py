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
from databases import Database

from api.database import (
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
async def integration_database(test_db_url: str) -> AsyncGenerator[Database, None]:
    """Create a test database for integration tests.

    Uses the test_db_url fixture from conftest.py which creates
    a unique PostgreSQL database for each test.
    """
    # Connect async database
    database = Database(test_db_url)
    await database.connect()

    yield database

    await database.disconnect()


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

        job = await integration_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))

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

        job = await integration_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))

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
        job = await integration_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
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



class TestVideoStatusTransitions:
    """Tests for video status transitions during transcoding."""

    @pytest.mark.asyncio
    async def test_pending_to_processing(self, integration_database, integration_video):
        """Test video status transition from pending to processing."""
        # Update status to processing
        await integration_database.execute(
            videos.update().where(videos.c.id == integration_video["id"]).values(status=VideoStatus.PROCESSING)
        )

        video = await integration_database.fetch_one(videos.select().where(videos.c.id == integration_video["id"]))
        assert video["status"] == VideoStatus.PROCESSING

    @pytest.mark.asyncio
    async def test_processing_to_ready(self, integration_database, integration_video):
        """Test video status transition from processing to ready."""
        now = datetime.now(timezone.utc)

        # Set to processing first
        await integration_database.execute(
            videos.update().where(videos.c.id == integration_video["id"]).values(status=VideoStatus.PROCESSING)
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

        video = await integration_database.fetch_one(videos.select().where(videos.c.id == integration_video["id"]))
        assert video["status"] == VideoStatus.READY
        assert video["duration"] == 120.5
        assert video["source_width"] == 1920
        assert video["source_height"] == 1080

    @pytest.mark.asyncio
    async def test_processing_to_failed(self, integration_database, integration_video):
        """Test video status transition from processing to failed."""
        # Set to processing first
        await integration_database.execute(
            videos.update().where(videos.c.id == integration_video["id"]).values(status=VideoStatus.PROCESSING)
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

        video = await integration_database.fetch_one(videos.select().where(videos.c.id == integration_video["id"]))
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

        job = await integration_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
        assert job["attempt_number"] == 2
        assert job["last_error"] == "First attempt failed"

    @pytest.mark.asyncio
    async def test_ready_preserves_existing_published_at(self, integration_database):
        """Test that marking a video ready preserves existing published_at (re-transcode scenario).

        This tests the expected behavior documented in the transcoder: when a video
        already has a published_at date (from a previous transcode), marking it ready
        again should NOT overwrite the date.
        """
        # Create a video with an existing published_at (simulating a re-transcode)
        original_published = datetime.now(timezone.utc) - timedelta(days=30)
        video_id = await integration_database.execute(
            videos.insert().values(
                title="Re-transcode Test Video",
                slug="retranscode-test-video",
                status=VideoStatus.PENDING,
                published_at=original_published,
                created_at=datetime.now(timezone.utc),
            )
        )

        # Simulate transcoder behavior: only set published_at if not already set
        video = await integration_database.fetch_one(videos.select().where(videos.c.id == video_id))
        video_updates = {"status": VideoStatus.READY, "duration": 120.0}
        if video["published_at"] is None:
            video_updates["published_at"] = datetime.now(timezone.utc)

        await integration_database.execute(videos.update().where(videos.c.id == video_id).values(**video_updates))

        # Verify published_at is preserved
        updated_video = await integration_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert updated_video["status"] == VideoStatus.READY
        assert updated_video["published_at"] is not None
        db_published = updated_video["published_at"]
        time_diff = abs((db_published - original_published).total_seconds())
        assert time_diff < 1, f"published_at changed: was {original_published}, now {updated_video['published_at']}"

    @pytest.mark.asyncio
    async def test_ready_sets_published_at_when_null(self, integration_database):
        """Test that marking a video ready sets published_at when it was NULL (new upload)."""
        # Create a video without published_at (simulating a new upload)
        video_id = await integration_database.execute(
            videos.insert().values(
                title="New Upload Test Video",
                slug="new-upload-test-video",
                status=VideoStatus.PENDING,
                published_at=None,
                created_at=datetime.now(timezone.utc),
            )
        )

        # Simulate transcoder behavior: set published_at since it's NULL
        video = await integration_database.fetch_one(videos.select().where(videos.c.id == video_id))
        now = datetime.now(timezone.utc)
        video_updates = {"status": VideoStatus.READY, "duration": 60.0}
        if video["published_at"] is None:
            video_updates["published_at"] = now

        await integration_database.execute(videos.update().where(videos.c.id == video_id).values(**video_updates))

        # Verify published_at is now set
        updated_video = await integration_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert updated_video["status"] == VideoStatus.READY
        assert updated_video["published_at"] is not None


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

        video = await integration_database.fetch_one(videos.select().where(videos.c.id == integration_video["id"]))
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
    async def test_full_pipeline_success_mocked(self, integration_database, integration_video, integration_temp_dir):
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
            videos.update().where(videos.c.id == integration_video["id"]).values(status=VideoStatus.PROCESSING)
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
        video = await integration_database.fetch_one(videos.select().where(videos.c.id == integration_video["id"]))
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
    async def test_pipeline_failure_and_recovery(self, integration_database, integration_video, integration_temp_dir):
        """Test pipeline failure and recovery from checkpoint."""
        now = datetime.now(timezone.utc)

        # Setup
        source_file = integration_temp_dir["uploads"] / f"{integration_video['id']}.mp4"
        source_file.write_bytes(b"fake video content")

        video_dir = integration_temp_dir["videos"] / integration_video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)

        # Start processing
        await integration_database.execute(
            videos.update().where(videos.c.id == integration_video["id"]).values(status=VideoStatus.PROCESSING)
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
        video = await integration_database.fetch_one(videos.select().where(videos.c.id == integration_video["id"]))
        assert video["status"] == VideoStatus.READY

        job = await integration_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
        assert job["attempt_number"] == 2
        assert job["worker_id"] == "worker-2"


# ============================================================================
# Local Worker Job Claiming Tests
# ============================================================================


class TestLocalWorkerJobClaiming:
    """Tests for local worker job claiming (get_existing_job function)."""

    @pytest.mark.asyncio
    async def test_local_worker_claims_unclaimed_job(self, integration_database, integration_video):
        """Test that local worker can successfully claim an unclaimed job."""
        from unittest.mock import patch

        import worker.transcoder as transcoder_module

        # Create an unclaimed job (claimed_at is NULL)
        now = datetime.now(timezone.utc)
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id=None,
                current_step=None,
                progress_percent=0,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
                claimed_at=None,
                claim_expires_at=None,
            )
        )

        # Patch the database in the transcoder module
        with patch.object(transcoder_module, "database", integration_database):
            job = await transcoder_module.get_existing_job(integration_video["id"])

            # Verify job was returned and claimed
            assert job is not None
            assert job["id"] == job_id
            assert job["video_id"] == integration_video["id"]

        # Verify claim fields were set in the database
        updated_job = await integration_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert updated_job["worker_id"] == "LOCAL_WORKER"
        assert updated_job["claimed_at"] is not None
        assert updated_job["claim_expires_at"] is not None

    @pytest.mark.asyncio
    async def test_local_worker_cannot_claim_job_claimed_by_remote_worker(
        self, integration_database, integration_video
    ):
        """Test that local worker cannot claim a job already claimed by a remote worker."""
        from unittest.mock import patch

        import worker.transcoder as transcoder_module

        # Create a job claimed by a remote worker with non-expired claim
        now = datetime.now(timezone.utc)
        claim_expires = now + timedelta(minutes=30)  # Claim expires in 30 minutes
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="remote-worker-001",
                current_step="transcode",
                progress_percent=50,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
                claimed_at=now,
                claim_expires_at=claim_expires,
            )
        )

        # Patch the database in the transcoder module
        with patch.object(transcoder_module, "database", integration_database):
            job = await transcoder_module.get_existing_job(integration_video["id"])

            # Should return None because job is claimed by another worker
            assert job is None

        # Verify the original claim is unchanged
        unchanged_job = await integration_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert unchanged_job["worker_id"] == "remote-worker-001"

    @pytest.mark.asyncio
    async def test_local_worker_can_reclaim_own_job(self, integration_database, integration_video):
        """Test that local worker can reclaim a job it previously claimed."""
        from unittest.mock import patch

        import worker.transcoder as transcoder_module

        # Create a job previously claimed by local worker
        now = datetime.now(timezone.utc)
        old_claim_expires = now + timedelta(minutes=10)  # Old claim still valid
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="LOCAL_WORKER",  # Same as local worker ID
                current_step="transcode",
                progress_percent=50,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
                claimed_at=now,
                claim_expires_at=old_claim_expires,
            )
        )

        # Patch the database in the transcoder module
        with patch.object(transcoder_module, "database", integration_database):
            job = await transcoder_module.get_existing_job(integration_video["id"])

            # Should return the job since it's claimed by the same worker
            assert job is not None
            assert job["id"] == job_id
            assert job["worker_id"] == "LOCAL_WORKER"

        # Verify claim was extended (new claim_expires_at)
        updated_job = await integration_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert updated_job["claim_expires_at"] > old_claim_expires

    @pytest.mark.asyncio
    async def test_local_worker_can_claim_expired_remote_claim(self, integration_database, integration_video):
        """Test that local worker can claim a job with an expired claim from a remote worker."""
        from unittest.mock import patch

        import worker.transcoder as transcoder_module

        # Create a job with expired claim from remote worker
        now = datetime.now(timezone.utc)
        expired_claim = now - timedelta(minutes=5)  # Claim expired 5 minutes ago
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="remote-worker-001",
                current_step="transcode",
                progress_percent=50,
                started_at=now - timedelta(hours=1),
                last_checkpoint=now - timedelta(hours=1),
                attempt_number=1,
                max_attempts=3,
                claimed_at=now - timedelta(minutes=35),
                claim_expires_at=expired_claim,  # Expired!
            )
        )

        # Patch the database in the transcoder module
        with patch.object(transcoder_module, "database", integration_database):
            job = await transcoder_module.get_existing_job(integration_video["id"])

            # Should return the job since the claim has expired
            assert job is not None
            assert job["id"] == job_id

        # Verify local worker now owns the claim
        updated_job = await integration_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert updated_job["worker_id"] == "LOCAL_WORKER"
        assert updated_job["claim_expires_at"] > now

    @pytest.mark.asyncio
    async def test_local_worker_returns_none_for_completed_job(self, integration_database, integration_video):
        """Test that local worker returns None for completed jobs."""
        from unittest.mock import patch

        import worker.transcoder as transcoder_module

        # Create a completed job
        now = datetime.now(timezone.utc)
        await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="completed-worker",
                current_step="finished",
                progress_percent=100,
                started_at=now - timedelta(hours=1),
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
                claimed_at=now - timedelta(hours=1),
                claim_expires_at=now - timedelta(minutes=30),
                completed_at=now,  # Job is completed
            )
        )

        # Patch the database in the transcoder module
        with patch.object(transcoder_module, "database", integration_database):
            job = await transcoder_module.get_existing_job(integration_video["id"])

            # Should return None because job is completed
            assert job is None

    @pytest.mark.asyncio
    async def test_local_worker_returns_none_for_nonexistent_job(self, integration_database, integration_video):
        """Test that local worker returns None when no job exists."""
        from unittest.mock import patch

        import worker.transcoder as transcoder_module

        # Don't create any job for this video

        # Patch the database in the transcoder module
        with patch.object(transcoder_module, "database", integration_database):
            job = await transcoder_module.get_existing_job(integration_video["id"])

            # Should return None because no job exists
            assert job is None


class TestProbeStepWithPendingClaimedStates:
    """Tests for probe step execution with pending and claimed job states."""

    @pytest.mark.asyncio
    async def test_probe_runs_when_current_step_is_pending(self, integration_database, integration_video, integration_temp_dir):
        """Test that Step 1 (probe) runs when current_step is 'pending'."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import worker.transcoder as transcoder_module

        # Create a job with current_step='pending' (set by admin API)
        now = datetime.now(timezone.utc)
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="LOCAL_WORKER",
                current_step="pending",
                progress_percent=0,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create a dummy upload file
        upload_path = integration_temp_dir["uploads"] / f"{integration_video['id']}.mp4"
        upload_path.write_text("dummy video content")

        # Mock get_video_info to return test metadata
        mock_info = {
            "width": 1920,
            "height": 1080,
            "duration": 120.5,
        }

        # Mock necessary functions
        with (
            patch.object(transcoder_module, "database", integration_database),
            patch.object(transcoder_module, "UPLOADS_DIR", integration_temp_dir["uploads"]),
            patch.object(transcoder_module, "VIDEOS_DIR", integration_temp_dir["videos"]),
            patch.object(transcoder_module, "get_video_info", new_callable=AsyncMock, return_value=mock_info),
            patch.object(transcoder_module, "generate_thumbnail", new_callable=AsyncMock),
            patch.object(transcoder_module, "transcode_quality", new_callable=AsyncMock),
            patch.object(transcoder_module, "generate_master_playlist", return_value=None),
            patch.object(transcoder_module, "state", MagicMock(shutdown_requested=False)),
        ):
            # Process the video (this will run the probe step)
            result = await transcoder_module.process_video(integration_video["id"])

            # Verify probe ran successfully
            assert result is True

            # Verify video metadata was updated by probe step
            updated_video = await integration_database.fetch_one(
                videos.select().where(videos.c.id == integration_video["id"])
            )
            assert updated_video["duration"] == 120.5
            assert updated_video["source_width"] == 1920
            assert updated_video["source_height"] == 1080

            # Verify job progressed past probe step
            updated_job = await integration_database.fetch_one(
                transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
            )
            assert updated_job["current_step"] != "pending"
            assert updated_job["current_step"] != "probe"

    @pytest.mark.asyncio
    async def test_probe_runs_when_current_step_is_claimed(self, integration_database, integration_video, integration_temp_dir):
        """Test that Step 1 (probe) runs when current_step is 'claimed'."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import worker.transcoder as transcoder_module

        # Create a job with current_step='claimed' (set by worker API)
        now = datetime.now(timezone.utc)
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="LOCAL_WORKER",
                current_step="claimed",
                progress_percent=0,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create a dummy upload file
        upload_path = integration_temp_dir["uploads"] / f"{integration_video['id']}.mp4"
        upload_path.write_text("dummy video content")

        # Mock get_video_info to return test metadata
        mock_info = {
            "width": 1280,
            "height": 720,
            "duration": 90.0,
        }

        # Mock necessary functions
        with (
            patch.object(transcoder_module, "database", integration_database),
            patch.object(transcoder_module, "UPLOADS_DIR", integration_temp_dir["uploads"]),
            patch.object(transcoder_module, "VIDEOS_DIR", integration_temp_dir["videos"]),
            patch.object(transcoder_module, "get_video_info", new_callable=AsyncMock, return_value=mock_info),
            patch.object(transcoder_module, "generate_thumbnail", new_callable=AsyncMock),
            patch.object(transcoder_module, "transcode_quality", new_callable=AsyncMock),
            patch.object(transcoder_module, "generate_master_playlist", return_value=None),
            patch.object(transcoder_module, "state", MagicMock(shutdown_requested=False)),
        ):
            # Process the video (this will run the probe step)
            result = await transcoder_module.process_video(integration_video["id"])

            # Verify probe ran successfully
            assert result is True

            # Verify video metadata was updated by probe step
            updated_video = await integration_database.fetch_one(
                videos.select().where(videos.c.id == integration_video["id"])
            )
            assert updated_video["duration"] == 90.0
            assert updated_video["source_width"] == 1280
            assert updated_video["source_height"] == 720

            # Verify job progressed past probe and claimed steps
            updated_job = await integration_database.fetch_one(
                transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
            )
            assert updated_job["current_step"] not in ["pending", "claimed", "probe"]


class TestThumbnailGenerationOnRemoteWorkerCrash:
    """Tests for thumbnail generation when local worker resumes from crashed remote worker."""

    @pytest.mark.asyncio
    async def test_thumbnail_generated_when_missing_at_transcode_step(
        self, integration_database, integration_video, integration_temp_dir
    ):
        """Test that local worker generates missing thumbnail when resuming from crashed remote worker.

        Scenario:
        - Remote worker claimed job and updated current_step to 'transcode'
        - Remote worker generated thumbnail locally but crashed before uploading it
        - Local worker picks up the expired job
        - Local worker should detect missing thumbnail and generate it
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        import worker.transcoder as transcoder_module

        # Create a job at 'transcode' step (remote worker crashed after setting this)
        now = datetime.now(timezone.utc)
        await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="LOCAL_WORKER",
                current_step="transcode",
                progress_percent=10,
                started_at=now - timedelta(minutes=10),
                last_checkpoint=now - timedelta(minutes=5),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Set video metadata (would have been set by probe step)
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(
                duration=60.0,
                source_width=1920,
                source_height=1080,
            )
        )

        # Create upload file
        upload_path = integration_temp_dir["uploads"] / f"{integration_video['id']}.mp4"
        upload_path.write_text("dummy video content")

        # Create video output directory but NO thumbnail.jpg
        video_dir = integration_temp_dir["videos"] / integration_video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)

        # Mock generate_thumbnail to track if it was called
        thumbnail_generated = False

        async def mock_generate_thumbnail(source, output, time):
            nonlocal thumbnail_generated
            thumbnail_generated = True
            output.write_text("thumbnail content")

        # Mock necessary functions
        with (
            patch.object(transcoder_module, "database", integration_database),
            patch.object(transcoder_module, "UPLOADS_DIR", integration_temp_dir["uploads"]),
            patch.object(transcoder_module, "VIDEOS_DIR", integration_temp_dir["videos"]),
            patch.object(transcoder_module, "generate_thumbnail", new_callable=AsyncMock, side_effect=mock_generate_thumbnail),
            patch.object(transcoder_module, "transcode_quality", new_callable=AsyncMock),
            patch.object(transcoder_module, "generate_master_playlist", return_value=None),
            patch.object(transcoder_module, "state", MagicMock(shutdown_requested=False)),
        ):
            # Process the video (should detect missing thumbnail and generate it)
            result = await transcoder_module.process_video(integration_video["id"])

            # Verify processing succeeded
            assert result is True

            # Verify thumbnail was generated
            assert thumbnail_generated, "Thumbnail should have been generated when missing"
            thumb_path = video_dir / "thumbnail.jpg"
            assert thumb_path.exists(), "Thumbnail file should exist after generation"

    @pytest.mark.asyncio
    async def test_completed_qualities_preserved_when_thumbnail_missing(
        self, integration_database, integration_video, integration_temp_dir
    ):
        """Test that completed quality variants are preserved when thumbnail is regenerated.

        Scenario:
        - Remote worker had completed some quality variants
        - Remote worker crashed before uploading thumbnail
        - Local worker should generate missing thumbnail but preserve completed qualities
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        import worker.transcoder as transcoder_module

        # Create a job at 'transcode' step with some completed qualities
        now = datetime.now(timezone.utc)
        job_id = await integration_database.execute(
            transcoding_jobs.insert().values(
                video_id=integration_video["id"],
                worker_id="LOCAL_WORKER",
                current_step="transcode",
                progress_percent=50,
                started_at=now - timedelta(minutes=10),
                last_checkpoint=now - timedelta(minutes=5),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create quality progress records showing partial completion
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
                status="completed",
                progress_percent=100,
            )
        )

        # Set video metadata
        await integration_database.execute(
            videos.update()
            .where(videos.c.id == integration_video["id"])
            .values(
                duration=120.0,
                source_width=1920,
                source_height=1080,
            )
        )

        # Create upload file
        upload_path = integration_temp_dir["uploads"] / f"{integration_video['id']}.mp4"
        upload_path.write_text("dummy video content")

        # Create video output directory with completed quality files but NO thumbnail
        video_dir = integration_temp_dir["videos"] / integration_video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "1080p.m3u8").write_text("1080p playlist")
        (video_dir / "720p.m3u8").write_text("720p playlist")

        # Track transcode_quality calls (should not be called for completed qualities)
        transcoded_qualities = []

        async def mock_transcode_quality(*args, **kwargs):
            quality_name = args[6]  # quality["name"] is the 7th argument
            transcoded_qualities.append(quality_name)

        async def mock_generate_thumbnail(source, output, time):
            output.write_text("thumbnail content")

        # Mock necessary functions
        with (
            patch.object(transcoder_module, "database", integration_database),
            patch.object(transcoder_module, "UPLOADS_DIR", integration_temp_dir["uploads"]),
            patch.object(transcoder_module, "VIDEOS_DIR", integration_temp_dir["videos"]),
            patch.object(transcoder_module, "generate_thumbnail", new_callable=AsyncMock, side_effect=mock_generate_thumbnail),
            patch.object(transcoder_module, "transcode_quality", new_callable=AsyncMock, side_effect=mock_transcode_quality),
            patch.object(transcoder_module, "generate_master_playlist", return_value=None),
            patch.object(transcoder_module, "state", MagicMock(shutdown_requested=False)),
        ):
            # Process the video
            result = await transcoder_module.process_video(integration_video["id"])

            # Verify processing succeeded
            assert result is True

            # Verify thumbnail was generated
            thumb_path = video_dir / "thumbnail.jpg"
            assert thumb_path.exists(), "Thumbnail should have been generated"

            # Verify completed qualities were NOT re-transcoded
            assert "1080p" not in transcoded_qualities, "1080p should not be re-transcoded"
            assert "720p" not in transcoded_qualities, "720p should not be re-transcoded"

            # Verify original quality progress records are preserved
            qualities = await integration_database.fetch_all(
                quality_progress.select().where(quality_progress.c.job_id == job_id)
            )
            completed = [q for q in qualities if q["status"] == "completed"]
            assert len(completed) >= 2, "Completed quality records should be preserved"
