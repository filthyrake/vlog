"""
Tests for worker claim expiration detection in all API operations.

Verifies that all worker job operations (download, upload, complete, fail)
properly detect and handle claim expiration (409 status codes).
"""

import io
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from api.database import transcoding_jobs, videos
from api.enums import VideoStatus


class TestClaimExpirationDetection:
    """Test claim expiration detection across all worker operations."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_download_source_detects_expired_claim(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test that download_source operation detects expired claims.

        When a claim expires before download completes, the worker should
        detect the 409 status and abort the operation.
        """
        # Create video and job
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Download Expiration Test",
                slug="download-expiration-test",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        # Create source file
        source_content = b"test video content for download expiration test"
        source_path = test_storage["uploads"] / f"{video_id}.mp4"
        source_path.write_bytes(source_content)

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

        # Expire the claim before download attempt
        await test_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(claim_expires_at=now - timedelta(seconds=1))
        )

        # Try to download with expired claim
        download_response = worker_client.get(
            f"/api/worker/source/{video_id}",
            headers=headers,
        )
        # Should fail with 403 or 409 indicating claim expired or unauthorized
        assert download_response.status_code in [403, 409]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_upload_quality_detects_expired_claim(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test that upload_quality operation detects expired claims.

        When a claim expires during quality upload, the worker should
        detect the 409 status and abort the operation.
        """
        # Create video and job
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Upload Expiration Test",
                slug="upload-expiration-test",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        # Create source file
        source_path = test_storage["uploads"] / f"{video_id}.mp4"
        source_path.write_bytes(b"test content")

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

        # Expire the claim before upload attempt
        await test_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(claim_expires_at=now - timedelta(seconds=1))
        )

        # Try to upload quality with expired claim
        quality_tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=quality_tar_buffer, mode="w:gz") as tar:
            playlist_content = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n"
            playlist_info = tarfile.TarInfo(name="720p.m3u8")
            playlist_info.size = len(playlist_content)
            tar.addfile(playlist_info, io.BytesIO(playlist_content))

        quality_tar_buffer.seek(0)

        upload_response = worker_client.post(
            f"/api/worker/upload/{video_id}/quality/720p",
            files={"file": ("720p.tar.gz", quality_tar_buffer, "application/gzip")},
            headers=headers,
        )
        # Should fail with 403 or 409 indicating claim expired
        assert upload_response.status_code in [403, 409]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_upload_finalize_detects_expired_claim(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test that upload_finalize operation detects expired claims.

        When a claim expires during finalize upload, the worker should
        detect the 409 status and abort the operation.
        """
        # Create video and job
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Finalize Expiration Test",
                slug="finalize-expiration-test",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        # Create source file
        source_path = test_storage["uploads"] / f"{video_id}.mp4"
        source_path.write_bytes(b"test content")

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

        # Expire the claim before finalize attempt
        await test_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(claim_expires_at=now - timedelta(seconds=1))
        )

        # Try to upload finalize with expired claim
        finalize_tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=finalize_tar_buffer, mode="w:gz") as tar:
            master_content = b"#EXTM3U\n#EXT-X-VERSION:3\n"
            master_info = tarfile.TarInfo(name="master.m3u8")
            master_info.size = len(master_content)
            tar.addfile(master_info, io.BytesIO(master_content))

        finalize_tar_buffer.seek(0)

        finalize_response = worker_client.post(
            f"/api/worker/upload/{video_id}/finalize",
            files={"file": ("finalize.tar.gz", finalize_tar_buffer, "application/gzip")},
            headers=headers,
        )
        # Should fail with 403 or 409 indicating claim expired
        assert finalize_response.status_code in [403, 409]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_complete_job_detects_expired_claim(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test that complete_job operation detects expired claims.

        When a claim expires before job completion, the worker should
        detect the 409 status and not mark the job as complete.
        """
        # Create video and job
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Complete Expiration Test",
                slug="complete-expiration-test",
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

        # Expire the claim before completion attempt
        await test_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(claim_expires_at=now - timedelta(seconds=1))
        )

        # Try to complete job with expired claim
        complete_response = worker_client.post(
            f"/api/worker/{job_id}/complete",
            json={
                "qualities": [
                    {"name": "720p", "width": 1280, "height": 720, "bitrate": 2500}
                ]
            },
            headers=headers,
        )
        # Should fail with 403 or 409 indicating claim expired
        assert complete_response.status_code in [403, 409]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_fail_job_detects_expired_claim(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test that fail_job operation detects expired claims.

        When a claim expires before reporting failure, the worker should
        detect the 409 status. This is less critical since the job is
        already failed, but proper detection prevents confusion.
        """
        # Create video and job
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Fail Expiration Test",
                slug="fail-expiration-test",
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

        # Expire the claim before fail attempt
        await test_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(claim_expires_at=now - timedelta(seconds=1))
        )

        # Try to fail job with expired claim
        fail_response = worker_client.post(
            f"/api/worker/{job_id}/fail",
            json={"error_message": "Test failure", "retry": True},
            headers=headers,
        )
        # Should fail with 403 or 409 indicating claim expired
        assert fail_response.status_code in [403, 409]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_progress_update_detects_expired_claim(
        self,
        worker_client,
        registered_worker,
        test_database,
        test_storage,
    ):
        """
        Test that progress update operations detect expired claims.

        This was already tested in test_remote_transcoder.py but included
        here for completeness to verify all operations have consistent
        claim expiration detection.
        """
        # Create video and job
        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Progress Expiration Test",
                slug="progress-expiration-test",
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

        # Expire the claim before progress update
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
        # Should fail with 403 or 409 indicating claim expired
        assert progress_response.status_code in [403, 409]
