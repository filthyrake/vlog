"""
Tests for the Worker API endpoints.

Tests worker registration, heartbeat, job claiming, and file transfer endpoints.
"""

from datetime import datetime, timezone

import pytest

from api.database import transcoding_jobs, videos


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

    @pytest.mark.asyncio
    async def test_progress_update_with_metadata(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test progress update can save video metadata."""
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

        # Update progress with metadata after probing
        response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "current_step": "probe",
                "progress_percent": 8,
                "duration": 120.5,
                "source_width": 1920,
                "source_height": 1080,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

        # Verify metadata was saved to video table
        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == sample_pending_video["id"])
        )
        assert video["duration"] == 120.5
        assert video["source_width"] == 1920
        assert video["source_height"] == 1080

    @pytest.mark.asyncio
    async def test_progress_update_metadata_persists_after_failure(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test metadata persists even if worker crashes after probe."""
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

        # Worker probes and updates metadata
        worker_client.post(
            f"/api/worker/{job_id}/progress",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "current_step": "probe",
                "progress_percent": 8,
                "duration": 180.0,
                "source_width": 3840,
                "source_height": 2160,
            },
        )

        # Simulate worker crash - verify metadata was saved
        video = await test_database.fetch_one(
            videos.select().where(videos.c.id == sample_pending_video["id"])
        )
        assert video["duration"] == 180.0
        assert video["source_width"] == 3840
        assert video["source_height"] == 2160

        # Job can be reclaimed by another worker and metadata is still there
        await test_database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(claimed_at=None, claim_expires_at=None)
        )

        # Reclaim the job
        response = worker_client.post(
            "/api/worker/claim",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 200
        claim_data = response.json()
        assert claim_data["video_duration"] == 180.0
        assert claim_data["source_width"] == 3840
        assert claim_data["source_height"] == 2160


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
