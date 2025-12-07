"""
End-to-end upload tests for the VLog application.

These tests verify the complete upload flow from admin API to transcoding job creation,
catching regressions like race conditions between local worker and admin API.

Issue #260: Comprehensive regression testing
"""

import io
from datetime import datetime, timezone

import pytest

from api.database import quality_progress, transcoding_jobs, videos
from api.enums import VideoStatus


class TestEndToEndUpload:
    """Test the complete upload flow through the admin API."""

    @pytest.mark.asyncio
    async def test_upload_creates_video_and_transcoding_job(self, admin_client, test_database, test_storage):
        """
        Test that a video upload creates both a video record and transcoding job atomically.

        This is a regression test for the race condition where local worker could pick up
        a video file before the transcoding job was created.
        """
        # Upload a video
        file_content = b"fake video content for e2e testing"
        response = admin_client.post(
            "/api/videos",
            files={"file": ("e2e_test.mp4", io.BytesIO(file_content), "video/mp4")},
            data={"title": "E2E Test Video", "description": "Testing upload flow"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        video_id = data["video_id"]
        slug = data["slug"]

        # Verify video record was created
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video is not None
        assert video["title"] == "E2E Test Video"
        assert video["status"] == VideoStatus.PENDING
        assert video["slug"] == slug

        # Verify transcoding job was created
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
        assert job is not None, "Transcoding job should be created with video upload"
        assert job["current_step"] == "pending"
        assert job["progress_percent"] == 0
        assert job["attempt_number"] == 1
        assert job["max_attempts"] == 3

        # Verify upload file was saved
        upload_path = test_storage["uploads"] / f"{video_id}.mp4"
        assert upload_path.exists(), "Upload file should be saved to uploads directory"
        assert upload_path.read_bytes() == file_content

    @pytest.mark.asyncio
    async def test_upload_with_category(self, admin_client, test_database, test_storage, sample_category):
        """Test that upload correctly associates video with category."""
        file_content = b"fake video with category"
        response = admin_client.post(
            "/api/videos",
            files={"file": ("categorized.mp4", io.BytesIO(file_content), "video/mp4")},
            data={
                "title": "Categorized Video",
                "description": "Has category",
                "category_id": sample_category["id"],
            },
        )

        assert response.status_code == 200
        video_id = response.json()["video_id"]

        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["category_id"] == sample_category["id"]

    @pytest.mark.asyncio
    async def test_upload_creates_video_and_job_atomically(
        self, admin_client, test_database, test_storage
    ):
        """
        Test that upload creates both video and transcoding job together.

        This verifies the atomicity of the upload operation - both records
        must be created together to prevent race conditions with the worker.
        """
        initial_video_count = await test_database.fetch_val("SELECT COUNT(*) FROM videos")
        initial_job_count = await test_database.fetch_val("SELECT COUNT(*) FROM transcoding_jobs")

        file_content = b"test video"
        response = admin_client.post(
            "/api/videos",
            files={"file": ("test.mp4", io.BytesIO(file_content), "video/mp4")},
            data={"title": "Atomicity Test", "description": "test"},
        )

        assert response.status_code == 200
        video_id = response.json()["video_id"]

        # Verify both were created together
        final_video_count = await test_database.fetch_val("SELECT COUNT(*) FROM videos")
        final_job_count = await test_database.fetch_val("SELECT COUNT(*) FROM transcoding_jobs")

        assert final_video_count == initial_video_count + 1
        assert final_job_count == initial_job_count + 1

        # Verify they're linked
        job = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id)
        )
        assert job is not None


class TestReUpload:
    """Test re-upload functionality creates new transcoding jobs correctly."""

    @pytest.mark.asyncio
    async def test_reupload_resets_video_status(self, admin_client, test_database, test_storage, sample_video):
        """Test that re-uploading a video resets the video status to pending."""
        video_id = sample_video["id"]

        # Re-upload a new file
        file_content = b"new video content for reupload"
        response = admin_client.post(
            f"/api/videos/{video_id}/re-upload",
            files={"file": ("reupload.mp4", io.BytesIO(file_content), "video/mp4")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["video_id"] == video_id
        assert data["status"] == "ok"

        # Verify video status was reset to pending
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["status"] == VideoStatus.PENDING

        # Verify a transcoding job exists for this video
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
        assert job is not None, "Transcoding job should exist after re-upload"
        assert job["current_step"] == "pending"


class TestRetryTranscoding:
    """Test retry functionality for failed videos."""

    @pytest.mark.asyncio
    async def test_retry_failed_video_resets_status(self, admin_client, test_database, test_storage, sample_category):
        """Test that retrying a failed video resets its status to pending."""
        # Create a failed video
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Failed Video",
                slug="failed-video-retry-test",
                category_id=sample_category["id"],
                status=VideoStatus.FAILED,
                error_message="Simulated failure",
                created_at=now,
            )
        )

        # Create the upload file that would trigger retry
        upload_path = test_storage["uploads"] / f"{video_id}.mp4"
        upload_path.write_bytes(b"video content")

        # Retry the video
        response = admin_client.post(f"/api/videos/{video_id}/retry")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

        # Verify video status was reset to pending
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["status"] == VideoStatus.PENDING
        assert video["error_message"] is None

    @pytest.mark.asyncio
    async def test_retry_requires_source_file(self, admin_client, test_database, sample_category):
        """Test that retry fails if source file doesn't exist."""
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Failed Video No Source",
                slug="failed-video-no-source",
                category_id=sample_category["id"],
                status=VideoStatus.FAILED,
                error_message="Original failure",
                created_at=now,
            )
        )

        # Don't create an upload file - retry should fail
        response = admin_client.post(f"/api/videos/{video_id}/retry")
        assert response.status_code == 400
        assert "Source file" in response.json()["detail"]


class TestRetranscode:
    """Test retranscode operations."""

    @pytest.mark.asyncio
    async def test_retranscode_queues_video(self, admin_client, test_database, test_storage, sample_category):
        """Test that retranscode queues a video for re-processing."""
        now = datetime.now(timezone.utc)

        # Create a ready video with source file
        video_id = await test_database.execute(
            videos.insert().values(
                title="Ready Video",
                slug="ready-video-retranscode",
                category_id=sample_category["id"],
                status=VideoStatus.READY,
                source_height=1080,
                created_at=now,
                published_at=now,
            )
        )

        # Create upload file
        upload_path = test_storage["uploads"] / f"{video_id}.mp4"
        upload_path.write_bytes(b"video content")

        # Request retranscode
        response = admin_client.post(
            f"/api/videos/{video_id}/retranscode",
            json={"qualities": ["all"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

        # Verify video was reset to pending
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["status"] == VideoStatus.PENDING

        # Verify transcoding job was created
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
        assert job is not None
        assert job["current_step"] == "pending"


class TestConcurrentUploadRaceCondition:
    """
    Tests for race conditions during concurrent operations.

    These tests specifically address the issue where local worker could
    detect an upload file via inotify before the database transaction
    completing.
    """

    @pytest.mark.asyncio
    async def test_video_and_job_created_before_file_accessible(self, admin_client, test_database, test_storage):
        """
        Verify that database records are committed before file is fully written.

        The upload endpoint should:
        1. Create video record
        2. Create transcoding job
        3. Write file to disk (atomically)

        This ordering ensures the local worker won't find a file without a job.
        """
        file_content = b"race condition test video"
        response = admin_client.post(
            "/api/videos",
            files={"file": ("race_test.mp4", io.BytesIO(file_content), "video/mp4")},
            data={"title": "Race Test", "description": "Testing race conditions"},
        )

        assert response.status_code == 200
        video_id = response.json()["video_id"]

        # At this point, we should have:
        # 1. A video record
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video is not None

        # 2. A transcoding job
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
        assert job is not None

        # 3. The upload file
        upload_path = test_storage["uploads"] / f"{video_id}.mp4"
        assert upload_path.exists()

        # All three must exist together - this is the invariant that
        # prevents the race condition

    @pytest.mark.asyncio
    async def test_multiple_rapid_uploads_all_get_jobs(self, admin_client, test_database, test_storage):
        """
        Test that rapid successive uploads all get transcoding jobs.

        This tests for any timing-related issues with job creation.
        """
        video_ids = []

        for i in range(5):
            file_content = f"rapid upload test {i}".encode()
            response = admin_client.post(
                "/api/videos",
                files={"file": (f"rapid_{i}.mp4", io.BytesIO(file_content), "video/mp4")},
                data={"title": f"Rapid Upload {i}", "description": "Rapid test"},
            )
            assert response.status_code == 200
            video_ids.append(response.json()["video_id"])

        # Verify all videos have transcoding jobs
        for video_id in video_ids:
            job = await test_database.fetch_one(
                transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id)
            )
            assert job is not None, f"Video {video_id} should have a transcoding job"


class TestProgressTracking:
    """Test that progress tracking is set up correctly during upload."""

    @pytest.mark.asyncio
    async def test_new_upload_has_zero_progress(self, admin_client, test_database, test_storage):
        """Test that a new upload starts with zero progress."""
        file_content = b"progress tracking test"
        response = admin_client.post(
            "/api/videos",
            files={"file": ("progress.mp4", io.BytesIO(file_content), "video/mp4")},
            data={"title": "Progress Test", "description": "Testing progress"},
        )

        assert response.status_code == 200
        video_id = response.json()["video_id"]

        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))

        assert job["progress_percent"] == 0
        assert job["current_step"] == "pending"
        assert job["started_at"] is None  # Not started yet
        assert job["completed_at"] is None
        assert job["last_error"] is None

    @pytest.mark.asyncio
    async def test_admin_progress_endpoint_returns_job_info(self, admin_client, test_database, sample_pending_video):
        """Test that the progress endpoint returns transcoding job info."""
        video_id = sample_pending_video["id"]

        # Create a transcoding job with some progress
        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="transcode",
                progress_percent=50,
                started_at=now,
                last_checkpoint=now,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Update video status to PROCESSING so quality_progress is returned
        await test_database.execute(
            videos.update().where(videos.c.id == video_id).values(status=VideoStatus.PROCESSING)
        )

        # Create some quality progress
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

        # Check progress via admin API
        response = admin_client.get(f"/api/videos/{video_id}/progress")
        assert response.status_code == 200

        data = response.json()

        # Verify response matches TranscodingProgressResponse schema
        assert "status" in data
        assert "progress_percent" in data
        assert "current_step" in data
        assert "qualities" in data
        assert isinstance(data["qualities"], list)

        # Verify our quality_progress entries are included
        assert len(data["qualities"]) == 2
        quality_names = {q["name"] for q in data["qualities"]}
        assert quality_names == {"1080p", "720p"}

        # Verify quality data matches what we inserted
        for quality in data["qualities"]:
            assert "name" in quality
            assert "status" in quality
            assert "progress" in quality
            if quality["name"] == "1080p":
                assert quality["status"] == "completed"
                assert quality["progress"] == 100
            elif quality["name"] == "720p":
                assert quality["status"] == "in_progress"
                assert quality["progress"] == 50
