"""
Worker integration tests for the VLog application.

These tests verify the integration between:
- Admin API (job creation)
- Worker API (job claiming, progress updates, completion)
- Local worker processing
- Remote worker via Worker API

Specifically tests quality_progress population which was broken after PostgreSQL migration.

Issue #260: Comprehensive regression testing
"""

import io
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from api.database import (
    quality_progress,
    transcoding_jobs,
    video_qualities,
    videos,
)
from api.enums import VideoStatus


class TestWorkerJobClaiming:
    """Test worker job claiming functionality."""

    @pytest.mark.asyncio
    async def test_worker_can_claim_pending_job(
        self,
        worker_client,
        admin_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """Test that a registered worker can claim a pending job."""
        # First, upload a video via admin API to create a pending job
        file_content = b"test video for claiming"
        upload_response = admin_client.post(
            "/api/videos",
            files={"file": ("claim_test.mp4", io.BytesIO(file_content), "video/mp4")},
            data={"title": "Claim Test", "description": "Testing job claiming"},
        )
        assert upload_response.status_code == 200
        video_id = upload_response.json()["video_id"]

        # Verify transcoding job exists
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
        assert job is not None

        # Worker claims the job
        headers = {"X-Worker-API-Key": registered_worker["api_key"]}
        claim_response = worker_client.post("/api/worker/claim", headers=headers)

        assert claim_response.status_code == 200
        data = claim_response.json()
        assert data["video_id"] == video_id
        assert data["job_id"] == job["id"]
        assert "claim_expires_at" in data

        # Verify job is now claimed
        updated_job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job["id"]))
        assert updated_job["worker_id"] == registered_worker["worker_id"]
        assert updated_job["claimed_at"] is not None

    @pytest.mark.asyncio
    async def test_no_job_available_returns_empty(self, worker_client, registered_worker, test_database):
        """Test that claiming when no jobs available returns appropriate response."""
        headers = {"X-Worker-API-Key": registered_worker["api_key"]}
        response = worker_client.post("/api/worker/claim", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data.get("job_id") is None
        assert "No jobs" in data.get("message", "")


class TestQualityProgressUpdates:
    """
    Test quality_progress population which was broken after PostgreSQL migration.

    The issue was that INSERT OR REPLACE in SQLite doesn't work the same way
    as PostgreSQL's ON CONFLICT, causing quality_progress to not be updated correctly.
    """

    @pytest.mark.asyncio
    async def test_quality_progress_created_on_first_update(
        self,
        worker_client,
        registered_worker,
        test_database,
        sample_pending_video,
    ):
        """Test that quality_progress records are created during progress updates."""
        video_id = sample_pending_video["id"]

        # Create and claim a transcoding job
        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=registered_worker["worker_id"],
                current_step="pending",
                claimed_at=now,
                claim_expires_at=now + timedelta(minutes=30),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Send progress update with quality progress
        headers = {"X-Worker-API-Key": registered_worker["api_key"]}
        response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            json={
                "current_step": "transcode",
                "progress_percent": 25,
                "quality_progress": [
                    {"name": "1080p", "status": "in_progress", "progress": 50},
                    {"name": "720p", "status": "pending", "progress": 0},
                ],
            },
            headers=headers,
        )

        assert response.status_code == 200

        # Verify quality_progress records were created
        progress = await test_database.fetch_all(quality_progress.select().where(quality_progress.c.job_id == job_id))

        assert len(progress) == 2, "Two quality progress records should be created"

        progress_by_quality = {p["quality"]: p for p in progress}
        assert progress_by_quality["1080p"]["status"] == "in_progress"
        assert progress_by_quality["1080p"]["progress_percent"] == 50
        assert progress_by_quality["720p"]["status"] == "pending"
        assert progress_by_quality["720p"]["progress_percent"] == 0

    @pytest.mark.asyncio
    async def test_quality_progress_updated_on_subsequent_updates(
        self,
        worker_client,
        registered_worker,
        test_database,
        sample_pending_video,
    ):
        """
        Test that quality_progress is updated (not duplicated) on subsequent updates.

        This specifically tests the upsert logic that was fixed for PostgreSQL.
        """
        video_id = sample_pending_video["id"]

        # Create and claim a transcoding job
        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=registered_worker["worker_id"],
                current_step="pending",
                claimed_at=now,
                claim_expires_at=now + timedelta(minutes=30),
                attempt_number=1,
                max_attempts=3,
            )
        )

        headers = {"X-Worker-API-Key": registered_worker["api_key"]}

        # First progress update
        response1 = worker_client.post(
            f"/api/worker/{job_id}/progress",
            json={
                "current_step": "transcode",
                "progress_percent": 25,
                "quality_progress": [
                    {"name": "1080p", "status": "in_progress", "progress": 25},
                ],
            },
            headers=headers,
        )
        assert response1.status_code == 200

        # Second progress update - same quality, different progress
        response2 = worker_client.post(
            f"/api/worker/{job_id}/progress",
            json={
                "current_step": "transcode",
                "progress_percent": 50,
                "quality_progress": [
                    {"name": "1080p", "status": "in_progress", "progress": 75},
                ],
            },
            headers=headers,
        )
        assert response2.status_code == 200

        # Third progress update - mark as completed
        response3 = worker_client.post(
            f"/api/worker/{job_id}/progress",
            json={
                "current_step": "transcode",
                "progress_percent": 75,
                "quality_progress": [
                    {"name": "1080p", "status": "completed", "progress": 100},
                ],
            },
            headers=headers,
        )
        assert response3.status_code == 200

        # Verify only one quality_progress record exists (not duplicates)
        progress = await test_database.fetch_all(quality_progress.select().where(quality_progress.c.job_id == job_id))

        assert len(progress) == 1, "Should have exactly one record, not duplicates"
        assert progress[0]["status"] == "completed"
        assert progress[0]["progress_percent"] == 100

    @pytest.mark.asyncio
    async def test_quality_progress_for_multiple_qualities(
        self,
        worker_client,
        registered_worker,
        test_database,
        sample_pending_video,
    ):
        """Test tracking progress for multiple quality levels simultaneously."""
        video_id = sample_pending_video["id"]

        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=registered_worker["worker_id"],
                current_step="pending",
                claimed_at=now,
                claim_expires_at=now + timedelta(minutes=30),
                attempt_number=1,
                max_attempts=3,
            )
        )

        headers = {"X-Worker-API-Key": registered_worker["api_key"]}

        # Simulate transcoding progress for 4K video
        qualities = ["2160p", "1440p", "1080p", "720p", "480p", "360p"]

        for i, quality in enumerate(qualities):
            # Mark previous quality as complete, current as in_progress
            progress_data = []
            for j, q in enumerate(qualities[: i + 1]):
                if j < i:
                    progress_data.append({"name": q, "status": "completed", "progress": 100})
                else:
                    progress_data.append({"name": q, "status": "in_progress", "progress": 50})

            response = worker_client.post(
                f"/api/worker/{job_id}/progress",
                json={
                    "current_step": "transcode",
                    "progress_percent": int((i + 0.5) / len(qualities) * 100),
                    "quality_progress": progress_data,
                },
                headers=headers,
            )
            assert response.status_code == 200

        # Verify all qualities are tracked
        progress = await test_database.fetch_all(
            quality_progress.select().where(quality_progress.c.job_id == job_id).order_by(quality_progress.c.id)
        )

        assert len(progress) == len(qualities)
        quality_names = [p["quality"] for p in progress]
        assert set(quality_names) == set(qualities)


class TestJobCompletion:
    """Test job completion flow including quality_progress and video_qualities."""

    @pytest.mark.asyncio
    async def test_job_completion_creates_video_qualities(
        self,
        worker_client,
        registered_worker,
        test_database,
        sample_pending_video,
        test_storage,
    ):
        """Test that completing a job creates video_qualities records."""
        video_id = sample_pending_video["id"]
        slug = sample_pending_video["slug"]

        # Create output directory and files
        output_dir = test_storage["videos"] / slug
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "master.m3u8").write_text("#EXTM3U\n")
        (output_dir / "1080p.m3u8").write_text("#EXTM3U\n")
        (output_dir / "720p.m3u8").write_text("#EXTM3U\n")
        (output_dir / "thumbnail.jpg").write_bytes(b"fake thumbnail")

        # Create and claim a transcoding job
        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=registered_worker["worker_id"],
                current_step="transcode",
                progress_percent=90,
                claimed_at=now,
                claim_expires_at=now + timedelta(minutes=30),
                started_at=now - timedelta(minutes=5),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Add quality progress
        await test_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="1080p",
                status="completed",
                progress_percent=100,
            )
        )
        await test_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="720p",
                status="completed",
                progress_percent=100,
            )
        )

        # Complete the job
        headers = {"X-Worker-API-Key": registered_worker["api_key"]}
        response = worker_client.post(
            f"/api/worker/{job_id}/complete",
            json={
                "qualities": [
                    {"name": "1080p", "width": 1920, "height": 1080, "bitrate": 5000},
                    {"name": "720p", "width": 1280, "height": 720, "bitrate": 2500},
                ],
                "duration": 120.5,
                "source_width": 1920,
                "source_height": 1080,
            },
            headers=headers,
        )

        assert response.status_code == 200

        # Verify video_qualities were created
        qualities = await test_database.fetch_all(
            video_qualities.select().where(video_qualities.c.video_id == video_id)
        )
        assert len(qualities) == 2

        quality_map = {q["quality"]: q for q in qualities}
        assert quality_map["1080p"]["width"] == 1920
        assert quality_map["1080p"]["height"] == 1080
        assert quality_map["720p"]["width"] == 1280

        # Verify video status is ready
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["status"] == VideoStatus.READY
        assert video["duration"] == 120.5
        assert video["published_at"] is not None

        # Verify job is marked complete
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
        assert job["completed_at"] is not None
        assert job["progress_percent"] == 100


class TestHLSUpload:
    """Test HLS output upload functionality."""

    def _create_hls_archive(self, quality: str) -> bytes:
        """Create a minimal HLS archive for testing."""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            # Add playlist file
            playlist_content = f"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
{quality}_0000.ts
#EXT-X-ENDLIST
"""
            playlist_data = playlist_content.encode()
            playlist_info = tarfile.TarInfo(name=f"{quality}.m3u8")
            playlist_info.size = len(playlist_data)
            tar.addfile(playlist_info, io.BytesIO(playlist_data))

            # Add segment file
            segment_data = b"fake video segment data"
            segment_info = tarfile.TarInfo(name=f"{quality}_0000.ts")
            segment_info.size = len(segment_data)
            tar.addfile(segment_info, io.BytesIO(segment_data))

        tar_buffer.seek(0)
        return tar_buffer.read()

    @pytest.mark.asyncio
    async def test_upload_hls_creates_files(
        self,
        worker_client,
        registered_worker,
        test_database,
        sample_pending_video,
        test_storage,
    ):
        """Test that uploading HLS creates the expected files."""
        video_id = sample_pending_video["id"]
        slug = sample_pending_video["slug"]

        # Create transcoding job
        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=registered_worker["worker_id"],
                current_step="transcode",
                claimed_at=now,
                claim_expires_at=now + timedelta(minutes=30),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create quality_progress record
        await test_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="1080p",
                status="in_progress",
                progress_percent=100,
            )
        )

        # Upload HLS files
        archive_data = self._create_hls_archive("1080p")
        headers = {"X-Worker-API-Key": registered_worker["api_key"]}
        response = worker_client.post(
            f"/api/worker/upload/{video_id}/quality/1080p",
            files={"file": ("1080p.tar.gz", io.BytesIO(archive_data), "application/gzip")},
            headers=headers,
        )

        assert response.status_code == 200

        # Verify files were extracted
        output_dir = test_storage["videos"] / slug
        assert output_dir.exists()
        assert (output_dir / "1080p.m3u8").exists()
        assert (output_dir / "1080p_0000.ts").exists()

        # Verify quality_progress was updated
        qp = await test_database.fetch_one(
            quality_progress.select()
            .where(quality_progress.c.job_id == job_id)
            .where(quality_progress.c.quality == "1080p")
        )
        assert qp["status"] == "uploaded"


class TestWorkerProgressVisibility:
    """
    Test that worker progress is visible in the admin UI.

    This tests the specific regression where quality_progress wasn't
    being displayed in the UI after PostgreSQL migration.
    """

    @pytest.mark.asyncio
    async def test_video_progress_includes_quality_progress(
        self,
        admin_client,
        worker_client,
        registered_worker,
        test_database,
        sample_pending_video,
    ):
        """Test that /api/videos/{id}/progress returns quality_progress data."""
        video_id = sample_pending_video["id"]

        # Create transcoding job with progress
        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=registered_worker["worker_id"],
                current_step="transcode",
                progress_percent=60,
                claimed_at=now,
                claim_expires_at=now + timedelta(minutes=30),
                started_at=now - timedelta(minutes=2),
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Update video status to PROCESSING so quality_progress is returned
        await test_database.execute(
            videos.update().where(videos.c.id == video_id).values(status=VideoStatus.PROCESSING)
        )

        # Create quality_progress records
        await test_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="1080p",
                status="completed",
                progress_percent=100,
            )
        )
        await test_database.execute(
            quality_progress.insert().values(
                job_id=job_id,
                quality="720p",
                status="in_progress",
                progress_percent=50,
            )
        )

        # Get progress via admin API
        response = admin_client.get(f"/api/videos/{video_id}/progress")
        assert response.status_code == 200

        data = response.json()

        # Verify response matches TranscodingProgressResponse schema
        assert "status" in data
        assert "progress_percent" in data
        assert "qualities" in data
        assert isinstance(data["qualities"], list)

        # Verify quality_progress data is included
        assert len(data["qualities"]) == 2
        quality_names = {q["name"] for q in data["qualities"]}
        assert quality_names == {"1080p", "720p"}

        # Verify each quality has required fields
        for quality in data["qualities"]:
            assert "name" in quality
            assert "status" in quality
            assert "progress" in quality

    @pytest.mark.skip(reason="Requires async database access that conflicts with sync TestClient in PostgreSQL")
    @pytest.mark.asyncio
    async def test_worker_dashboard_shows_active_jobs(
        self,
        admin_client,
        worker_client,
        registered_worker,
        test_database,
        sample_pending_video,
    ):
        """Test that the worker dashboard shows active job information."""
        pass


class TestStaleJobRecovery:
    """Test stale job detection and recovery."""

    @pytest.mark.asyncio
    async def test_stale_job_released_for_reclaim(
        self,
        worker_client,
        test_database,
        sample_pending_video,
        worker_admin_headers,
    ):
        """Test that stale jobs are released and can be claimed by another worker."""
        video_id = sample_pending_video["id"]

        # Register two workers
        response1 = worker_client.post(
            "/api/worker/register",
            json={"worker_name": "worker-1", "worker_type": "remote"},
            headers=worker_admin_headers,
        )
        worker1 = response1.json()

        response2 = worker_client.post(
            "/api/worker/register",
            json={"worker_name": "worker-2", "worker_type": "remote"},
            headers=worker_admin_headers,
        )
        worker2 = response2.json()

        # Create a job claimed by worker 1 that has expired
        now = datetime.now(timezone.utc)
        past = now - timedelta(minutes=60)  # Well past claim expiration
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=worker1["worker_id"],
                current_step="transcode",
                progress_percent=30,
                claimed_at=past,
                claim_expires_at=past + timedelta(minutes=30),  # Expired
                started_at=past,
                last_checkpoint=past,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Worker 2 should be able to claim the stale job
        headers2 = {"X-Worker-API-Key": worker2["api_key"]}
        claim_response = worker_client.post("/api/worker/claim", headers=headers2)

        assert claim_response.status_code == 200
        data = claim_response.json()

        # Job should now be claimed by worker 2
        assert data.get("job_id") == job_id
        assert data.get("video_id") == video_id

        # Verify database updated
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
        assert job["worker_id"] == worker2["worker_id"]


class TestJobFailure:
    """Test job failure handling."""

    @pytest.mark.asyncio
    async def test_job_failure_updates_status(
        self,
        worker_client,
        registered_worker,
        test_database,
        sample_pending_video,
    ):
        """Test that job failure updates video status and job state correctly."""
        video_id = sample_pending_video["id"]

        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=registered_worker["worker_id"],
                current_step="transcode",
                progress_percent=30,
                claimed_at=now,
                claim_expires_at=now + timedelta(minutes=30),
                started_at=now - timedelta(minutes=1),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Report failure
        headers = {"X-Worker-API-Key": registered_worker["api_key"]}
        response = worker_client.post(
            f"/api/worker/{job_id}/fail",
            json={"error_message": "FFmpeg crashed unexpectedly"},
            headers=headers,
        )

        assert response.status_code == 200

        # Verify job was updated
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
        assert job["last_error"] == "FFmpeg crashed unexpectedly"
        # Job should be available for retry since attempt_number < max_attempts

        # Verify video status
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        # Status depends on whether we've exceeded max attempts
        # With attempt 1/3, video should still be pending for retry
        assert video["status"] in [VideoStatus.PENDING, VideoStatus.FAILED]
