"""
Integration tests for remote transcoder worker.

Tests the full lifecycle of a remote worker:
- Registration and API key management
- Job claiming and file download
- Transcoding with progress updates
- HLS output upload and job completion
- Error recovery and retry logic
"""

import io
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from api.database import transcoding_jobs, video_qualities, videos, workers
from api.enums import VideoStatus


class TestRemoteTranscoderLifecycle:
    """Test the complete remote transcoder workflow."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_full_transcode_workflow(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test the complete remote transcoding workflow:
        1. Video upload creates job
        2. Worker claims job
        3. Worker downloads source file
        4. Worker uploads transcoded HLS output
        5. Worker marks job complete
        """
        # Create a pending video with source file
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Remote Transcode Test",
                slug="remote-transcode-test",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        # Create source file
        source_content = b"fake video content for remote transcoding"
        source_path = test_storage["uploads"] / f"{video_id}.mp4"
        source_path.write_bytes(source_content)

        # Create transcoding job
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="pending",
                progress_percent=0,
                attempt_number=1,
                max_attempts=3,
            )
        )

        headers = {"X-Worker-API-Key": registered_worker["api_key"]}

        # Step 1: Worker claims the job
        claim_response = worker_client.post("/api/worker/claim", headers=headers)
        assert claim_response.status_code == 200
        claim_data = claim_response.json()
        assert claim_data["video_id"] == video_id
        assert claim_data["job_id"] == job_id

        # Step 2: Worker downloads source file
        download_response = worker_client.get(
            f"/api/worker/source/{video_id}",
            headers=headers,
        )
        assert download_response.status_code == 200
        assert download_response.content == source_content

        # Step 3: Worker sends progress updates
        progress_response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            json={
                "current_step": "transcode",
                "progress_percent": 50,
                "quality_progress": [
                    {"name": "720p", "status": "in_progress", "progress": 75},
                    {"name": "480p", "status": "completed", "progress": 100},
                ],
            },
            headers=headers,
        )
        assert progress_response.status_code == 200

        # Step 4: Worker uploads HLS output
        # First upload the 720p quality
        quality_tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=quality_tar_buffer, mode="w:gz") as tar:
            # Add 720p playlist
            playlist_content = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n"
            playlist_info = tarfile.TarInfo(name="720p.m3u8")
            playlist_info.size = len(playlist_content)
            tar.addfile(playlist_info, io.BytesIO(playlist_content))

        quality_tar_buffer.seek(0)

        quality_upload_response = worker_client.post(
            f"/api/worker/upload/{video_id}/quality/720p",
            files={"file": ("720p.tar.gz", quality_tar_buffer, "application/gzip")},
            headers=headers,
        )
        assert quality_upload_response.status_code == 200

        # Then upload finalize files (master playlist, thumbnail)
        finalize_tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=finalize_tar_buffer, mode="w:gz") as tar:
            # Add master playlist
            master_content = b"#EXTM3U\n#EXT-X-VERSION:3\n"
            master_info = tarfile.TarInfo(name="master.m3u8")
            master_info.size = len(master_content)
            tar.addfile(master_info, io.BytesIO(master_content))

            # Add thumbnail
            thumb_content = b"fake thumbnail"
            thumb_info = tarfile.TarInfo(name="thumbnail.jpg")
            thumb_info.size = len(thumb_content)
            tar.addfile(thumb_info, io.BytesIO(thumb_content))

        finalize_tar_buffer.seek(0)

        finalize_upload_response = worker_client.post(
            f"/api/worker/upload/{video_id}/finalize",
            files={"file": ("finalize.tar.gz", finalize_tar_buffer, "application/gzip")},
            headers=headers,
        )
        assert finalize_upload_response.status_code == 200

        # Verify HLS files were extracted
        slug = (await test_database.fetch_one(videos.select().where(videos.c.id == video_id)))["slug"]
        video_dir = test_storage["videos"] / slug
        assert (video_dir / "master.m3u8").exists()
        assert (video_dir / "720p.m3u8").exists()
        assert (video_dir / "thumbnail.jpg").exists()

        # Step 5: Worker marks job complete
        complete_response = worker_client.post(
            f"/api/worker/{job_id}/complete",
            json={"qualities": [{"name": "720p", "width": 1280, "height": 720, "bitrate": 2500}]},
            headers=headers,
        )
        assert complete_response.status_code == 200

        # Verify video status updated
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["status"] == VideoStatus.READY

        # Verify quality records created
        qualities = await test_database.fetch_all(
            video_qualities.select().where(video_qualities.c.video_id == video_id)
        )
        assert len(qualities) == 1
        assert qualities[0]["quality"] == "720p"
        assert qualities[0]["width"] == 1280
        assert qualities[0]["height"] == 720

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_claim_expired_during_processing(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test that expired claims are handled gracefully.

        When a claim expires, progress updates and completion should fail
        with appropriate error messages.
        """
        # Create video and job
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Expired Claim Test",
                slug="expired-claim-test",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="pending",
                progress_percent=0,
                attempt_number=1,
                max_attempts=3,
            )
        )

        headers = {"X-Worker-API-Key": registered_worker["api_key"]}

        # Claim the job
        claim_response = worker_client.post("/api/worker/claim", headers=headers)
        assert claim_response.status_code == 200

        # Manually expire the claim by setting claim_expires_at to the past
        await test_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(claim_expires_at=now - timedelta(seconds=1))
        )

        # Try to send progress update with expired claim
        progress_response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            json={"current_step": "transcode", "progress_percent": 50},
            headers=headers,
        )
        # Should fail with appropriate error
        assert progress_response.status_code in [400, 403, 409]  # Client error expected

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_worker_heartbeat_updates_status(
        self,
        worker_client,
        registered_worker,
        test_database,
    ):
        """Test that worker heartbeats update the last_seen timestamp."""
        headers = {"X-Worker-API-Key": registered_worker["api_key"]}

        # Get initial last_heartbeat
        initial_worker = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == registered_worker["worker_id"])
        )
        initial_last_heartbeat = initial_worker["last_heartbeat"]

        # Send heartbeat (timestamp will be updated by the database)
        heartbeat_response = worker_client.post(
            "/api/worker/heartbeat",
            json={"status": "idle"},
            headers=headers,
        )
        assert heartbeat_response.status_code == 200

        # Verify last_heartbeat updated
        updated_worker = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == registered_worker["worker_id"])
        )
        assert updated_worker["last_heartbeat"] > initial_last_heartbeat


class TestRemoteTranscoderErrorRecovery:
    """Test error recovery scenarios for remote transcoder."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_job_retry_after_failure(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test that failed jobs can be retried by another worker claim.
        """
        # Create video and job
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Retry Test",
                slug="retry-test",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="pending",
                progress_percent=0,
                attempt_number=1,
                max_attempts=3,
            )
        )

        headers = {"X-Worker-API-Key": registered_worker["api_key"]}

        # Claim the job
        claim_response = worker_client.post("/api/worker/claim", headers=headers)
        assert claim_response.status_code == 200

        # Report failure
        fail_response = worker_client.post(
            f"/api/worker/{job_id}/fail",
            json={"error_message": "Test failure for retry"},
            headers=headers,
        )
        assert fail_response.status_code == 200

        # Verify job is reset and can be claimed again
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
        assert job["current_step"] is None  # Reset to unclaimed state
        assert job["attempt_number"] == 2  # Incremented
        assert job["worker_id"] is None  # Released

        # Another worker should be able to claim it
        claim_response2 = worker_client.post("/api/worker/claim", headers=headers)
        assert claim_response2.status_code == 200
        assert claim_response2.json()["job_id"] == job_id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_max_retries_marks_video_failed(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test that exceeding max retries marks the video as failed.
        """
        # Create video and job with max attempts reached
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Max Retries Test",
                slug="max-retries-test",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="pending",
                progress_percent=0,
                attempt_number=3,  # Last attempt
                max_attempts=3,
            )
        )

        headers = {"X-Worker-API-Key": registered_worker["api_key"]}

        # Claim the job
        claim_response = worker_client.post("/api/worker/claim", headers=headers)
        assert claim_response.status_code == 200

        # Report failure on last attempt
        fail_response = worker_client.post(
            f"/api/worker/{job_id}/fail",
            json={"error_message": "Final failure"},
            headers=headers,
        )
        assert fail_response.status_code == 200

        # Verify video marked as failed
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["status"] == VideoStatus.FAILED


class TestRemoteTranscoderConcurrency:
    """Test concurrent job claiming and processing."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_multiple_workers_claim_different_jobs(
        self,
        worker_client,
        worker_admin_headers,
        test_database,
        test_storage,
    ):
        """
        Test that multiple workers can claim different jobs simultaneously.
        """
        # Register two workers

        worker1_data = {
            "worker_name": "worker-1",
        }
        worker2_data = {
            "worker_name": "worker-2",
        }

        # Create two pending videos and jobs
        now = datetime.now(timezone.utc)
        video_id1 = await test_database.execute(
            videos.insert().values(
                title="Multi Worker Test 1",
                slug="multi-worker-test-1",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )
        video_id2 = await test_database.execute(
            videos.insert().values(
                title="Multi Worker Test 2",
                slug="multi-worker-test-2",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        job_id1 = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id1,
                current_step="pending",
                progress_percent=0,
                attempt_number=1,
                max_attempts=3,
            )
        )
        job_id2 = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id2,
                current_step="pending",
                progress_percent=0,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Register workers and get API keys
        register_resp1 = worker_client.post(
            "/api/worker/register",
            json=worker1_data,
            headers=worker_admin_headers,
        )
        assert register_resp1.status_code == 200
        api_key1 = register_resp1.json()["api_key"]

        register_resp2 = worker_client.post(
            "/api/worker/register",
            json=worker2_data,
            headers=worker_admin_headers,
        )
        assert register_resp2.status_code == 200
        api_key2 = register_resp2.json()["api_key"]

        # Both workers claim jobs
        claim1 = worker_client.post(
            "/api/worker/claim",
            headers={"X-Worker-API-Key": api_key1},
        )
        claim2 = worker_client.post(
            "/api/worker/claim",
            headers={"X-Worker-API-Key": api_key2},
        )

        assert claim1.status_code == 200
        assert claim2.status_code == 200

        # Verify they claimed different jobs
        claimed_job1 = claim1.json()["job_id"]
        claimed_job2 = claim2.json()["job_id"]
        assert claimed_job1 != claimed_job2
        assert {claimed_job1, claimed_job2} == {job_id1, job_id2}


class TestRemoteTranscoderFileDownload:
    """Test file download functionality for remote workers."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_download_requires_claimed_job(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """Test that download requires a valid claim on the job."""
        # Create video and job without claiming
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Download Auth Test",
                slug="download-auth-test",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        source_content = b"protected content"
        source_path = test_storage["uploads"] / f"{video_id}.mp4"
        source_path.write_bytes(source_content)

        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="pending",
                progress_percent=0,
                attempt_number=1,
                max_attempts=3,
            )
        )

        headers = {"X-Worker-API-Key": registered_worker["api_key"]}

        # Try to download without claiming - should fail
        download_response = worker_client.get(
            f"/api/worker/source/{video_id}",
            headers=headers,
        )
        # Should require claim first (403 = not your job)
        assert download_response.status_code == 403

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_download_large_file_streaming(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """Test that large file downloads use streaming."""
        # Create video with "large" source file
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Large File Test",
                slug="large-file-test",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        # Create a 1MB source file
        source_content = b"x" * (1024 * 1024)
        source_path = test_storage["uploads"] / f"{video_id}.mp4"
        source_path.write_bytes(source_content)

        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                current_step="pending",
                progress_percent=0,
                attempt_number=1,
                max_attempts=3,
            )
        )

        headers = {"X-Worker-API-Key": registered_worker["api_key"]}

        # Claim the job
        claim_response = worker_client.post("/api/worker/claim", headers=headers)
        assert claim_response.status_code == 200

        # Download the file
        download_response = worker_client.get(
            f"/api/worker/source/{video_id}",
            headers=headers,
        )
        assert download_response.status_code == 200
        assert len(download_response.content) == len(source_content)
        assert download_response.content == source_content
