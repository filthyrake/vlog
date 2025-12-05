"""
Tests for the Worker API endpoints.

Tests worker registration, heartbeat, job claiming, and file transfer endpoints.
"""

from datetime import datetime, timezone

import pytest

from api.database import transcoding_jobs, videos, workers


class TestWorkerRegistration:
    """Tests for worker registration endpoint."""

    def test_register_worker_success(self, worker_client):
        """Test successful worker registration."""
        response = worker_client.post(
            "/api/worker/register",
            json={"worker_name": "test-worker", "worker_type": "remote"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "worker_id" in data
        assert "api_key" in data
        assert len(data["api_key"]) > 20  # Should be a substantial key
        assert data["message"] is not None

    def test_register_worker_without_name(self, worker_client):
        """Test registration without specifying a name."""
        response = worker_client.post(
            "/api/worker/register",
            json={"worker_type": "remote"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "worker_id" in data
        assert "api_key" in data

    def test_register_local_worker(self, worker_client):
        """Test registering a local worker type."""
        response = worker_client.post(
            "/api/worker/register",
            json={"worker_name": "local-worker", "worker_type": "local"},
        )
        assert response.status_code == 200


class TestWorkerHeartbeat:
    """Tests for worker heartbeat endpoint."""

    def test_heartbeat_success(self, worker_client, registered_worker):
        """Test successful heartbeat."""
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"status": "active"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "server_time" in data

    def test_heartbeat_without_auth(self, worker_client):
        """Test heartbeat without API key fails."""
        response = worker_client.post(
            "/api/worker/heartbeat",
            json={"status": "active"},
        )
        assert response.status_code == 401

    def test_heartbeat_invalid_key(self, worker_client):
        """Test heartbeat with invalid API key fails."""
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": "invalid-key-12345678"},
            json={"status": "active"},
        )
        assert response.status_code == 401


class TestJobClaiming:
    """Tests for job claiming endpoint."""

    @pytest.mark.asyncio
    async def test_claim_job_no_jobs(self, worker_client, registered_worker):
        """Test claiming when no jobs available."""
        response = worker_client.post(
            "/api/worker/claim",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] is None
        assert "No jobs" in data["message"]

    @pytest.mark.asyncio
    async def test_claim_job_success(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test successful job claim."""
        # Create a transcoding job for the pending video
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                attempt_number=1,
                max_attempts=3,
            )
        )

        response = worker_client.post(
            "/api/worker/claim",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] is not None
        assert data["video_id"] == sample_pending_video["id"]
        assert data["video_slug"] == sample_pending_video["slug"]
        assert "claim_expires_at" in data

    def test_claim_job_without_auth(self, worker_client):
        """Test claiming without API key fails."""
        response = worker_client.post("/api/worker/claim")
        assert response.status_code == 401


class TestProgressUpdates:
    """Tests for progress update endpoint."""

    @pytest.mark.asyncio
    async def test_progress_update_success(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test successful progress update."""
        # Create and claim a job
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id=registered_worker["worker_id"],
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "current_step": "transcode",
                "progress_percent": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "claim_expires_at" in data

    @pytest.mark.asyncio
    async def test_progress_update_not_your_job(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test updating progress on a job you don't own fails."""
        # Create a job owned by a different worker
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id="different-worker-id",
                attempt_number=1,
                max_attempts=3,
            )
        )

        response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"progress_percent": 50},
        )
        assert response.status_code == 403


class TestJobCompletion:
    """Tests for job completion endpoint."""

    @pytest.mark.asyncio
    async def test_complete_job_success(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test successful job completion."""
        # Create and claim a job
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id=registered_worker["worker_id"],
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        response = worker_client.post(
            f"/api/worker/{job_id}/complete",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "qualities": [
                    {"name": "1080p", "width": 1920, "height": 1080, "bitrate": 5000},
                    {"name": "720p", "width": 1280, "height": 720, "bitrate": 2500},
                ],
                "duration": 120.0,
                "source_width": 1920,
                "source_height": 1080,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

        # Verify video is now ready
        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == sample_pending_video["id"])
        )
        assert video["status"] == "ready"


class TestJobFailure:
    """Tests for job failure endpoint."""

    @pytest.mark.asyncio
    async def test_fail_job_with_retry(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test failing a job with retry enabled."""
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id=registered_worker["worker_id"],
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        response = worker_client.post(
            f"/api/worker/{job_id}/fail",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"error_message": "Transcoding failed", "retry": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["will_retry"] is True
        assert data["attempt_number"] == 2

    @pytest.mark.asyncio
    async def test_fail_job_final(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test failing a job without retry."""
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id=registered_worker["worker_id"],
                claimed_at=datetime.now(timezone.utc),
                attempt_number=3,
                max_attempts=3,
            )
        )

        response = worker_client.post(
            f"/api/worker/{job_id}/fail",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"error_message": "Transcoding failed permanently", "retry": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["will_retry"] is False


class TestWorkerListing:
    """Tests for worker listing endpoint."""

    def test_list_workers_empty(self, worker_client):
        """Test listing workers when none registered."""
        response = worker_client.get("/api/workers")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 0

    def test_list_workers_with_data(self, worker_client, registered_worker):
        """Test listing workers with registered workers."""
        response = worker_client.get("/api/workers")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] >= 1
        assert any(w["worker_id"] == registered_worker["worker_id"] for w in data["workers"])


class TestWorkerRevocation:
    """Tests for worker revocation endpoint."""

    def test_revoke_worker(self, worker_client, registered_worker):
        """Test revoking a worker's API key."""
        response = worker_client.post(
            f"/api/workers/{registered_worker['worker_id']}/revoke"
        )
        assert response.status_code == 200

        # Verify the worker can no longer authenticate
        heartbeat = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"status": "active"},
        )
        assert heartbeat.status_code == 401

    def test_revoke_nonexistent_worker(self, worker_client):
        """Test revoking a non-existent worker fails."""
        response = worker_client.post("/api/workers/nonexistent-id/revoke")
        assert response.status_code == 404


class TestHealthCheck:
    """Tests for health check endpoint."""

    def test_health_check(self, worker_client):
        """Test health check endpoint."""
        response = worker_client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestGracefulShutdown:
    """Tests for graceful shutdown behavior."""

    @pytest.mark.asyncio
    async def test_shutdown_releases_claimed_jobs(
        self, test_database, registered_worker, sample_pending_video
    ):
        """Test that shutdown releases claimed jobs."""
        from fastapi import FastAPI

        from api.worker_api import lifespan

        # Create a claimed job
        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                claimed_at=now,
                claim_expires_at=now,
                worker_id=registered_worker["worker_id"],
                current_step="processing",
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Set video to processing
        await test_database.execute(
            videos.update()
            .where(videos.c.id == sample_pending_video["id"])
            .values(status="processing")
        )

        # Update worker's current job
        worker_record = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == registered_worker["worker_id"])
        )
        await test_database.execute(
            workers.update()
            .where(workers.c.id == worker_record["id"])
            .values(current_job_id=job_id)
        )

        # Verify job is claimed
        job = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert job["claimed_at"] is not None
        assert job["worker_id"] == registered_worker["worker_id"]

        # Disconnect test database temporarily
        await test_database.disconnect()

        # Simulate shutdown by running lifespan which will connect/disconnect its own connection
        app = FastAPI()
        async with lifespan(app):
            pass  # The shutdown logic runs when exiting the context

        # Reconnect test database for subsequent assertions
        await test_database.connect()

        # Verify job claim was released
        job_after = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert job_after["claimed_at"] is None
        assert job_after["claim_expires_at"] is None
        assert job_after["worker_id"] is None
        assert job_after["current_step"] is None

        # Verify video status was reset to pending
        video_after = await test_database.fetch_one(
            videos.select().where(videos.c.id == sample_pending_video["id"])
        )
        assert video_after["status"] == "pending"

        # Verify worker's current_job_id was cleared
        worker_after = await test_database.fetch_one(
            workers.select().where(workers.c.id == worker_record["id"])
        )
        assert worker_after["current_job_id"] is None

    @pytest.mark.asyncio
    async def test_shutdown_ignores_completed_jobs(
        self, test_database, registered_worker, sample_pending_video
    ):
        """Test that shutdown does not affect completed jobs."""
        from fastapi import FastAPI

        from api.worker_api import lifespan

        # Create a completed job
        now = datetime.now(timezone.utc)
        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                claimed_at=now,
                claim_expires_at=now,
                worker_id=registered_worker["worker_id"],
                completed_at=now,
                current_step="finalize",
                progress_percent=100,
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Disconnect test database temporarily
        await test_database.disconnect()

        # Simulate shutdown by running lifespan which will connect/disconnect its own connection
        app = FastAPI()
        async with lifespan(app):
            pass

        # Reconnect test database for subsequent assertions
        await test_database.connect()

        # Verify completed job was not modified
        job_after = await test_database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )
        assert job_after["claimed_at"] is not None  # Should still be set
        assert job_after["completed_at"] is not None  # Should still be set
        assert job_after["worker_id"] == registered_worker["worker_id"]
