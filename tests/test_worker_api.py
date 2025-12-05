"""
Tests for the Worker API endpoints.

Tests worker registration, heartbeat, job claiming, and file transfer endpoints.
Covers authentication edge cases, key hashing, source file download, and path traversal prevention.
"""

import io
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from api.database import transcoding_jobs, videos, worker_api_keys, workers
from api.worker_auth import get_key_prefix, hash_api_key

# ============================================================================
# Authentication Edge Cases (Issue #119)
# ============================================================================


class TestAuthenticationEdgeCases:
    """Tests for API key authentication edge cases."""

    def test_missing_api_key(self, worker_client):
        """Test request without API key returns 401."""
        response = worker_client.post("/api/worker/heartbeat", json={"status": "active"})
        assert response.status_code == 401
        assert "Missing API key" in response.json()["detail"]

    def test_invalid_api_key_format(self, worker_client):
        """Test request with malformed API key returns 401."""
        response = worker_client.post(
            "/api/worker/heartbeat",
            json={"status": "active"},
            headers={"X-Worker-API-Key": "x"},  # Too short to have prefix
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_api_key(self, worker_client, test_database, registered_worker):
        """Test expired API key returns 401 with appropriate message."""
        # Set expiration to the past
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        await test_database.execute(
            worker_api_keys.update()
            .where(worker_api_keys.c.key_prefix == get_key_prefix(registered_worker["api_key"]))
            .values(expires_at=past_time)
        )

        response = worker_client.post(
            "/api/worker/heartbeat",
            json={"status": "active"},
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_revoked_api_key(self, worker_client, test_database, registered_worker):
        """Test revoked API key returns 401."""
        # Revoke the key
        await test_database.execute(
            worker_api_keys.update()
            .where(worker_api_keys.c.key_prefix == get_key_prefix(registered_worker["api_key"]))
            .values(revoked_at=datetime.now(timezone.utc))
        )

        response = worker_client.post(
            "/api/worker/heartbeat",
            json={"status": "active"},
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 401
        assert "Invalid API key" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_disabled_worker(self, worker_client, test_database, registered_worker):
        """Test disabled worker returns 403."""
        # Get the worker's database ID through the API key
        key_record = await test_database.fetch_one(
            worker_api_keys.select()
            .where(worker_api_keys.c.key_prefix == get_key_prefix(registered_worker["api_key"]))
        )

        # Disable the worker
        await test_database.execute(
            workers.update()
            .where(workers.c.id == key_record["worker_id"])
            .values(status="disabled")
        )

        response = worker_client.post(
            "/api/worker/heartbeat",
            json={"status": "active"},
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_api_key_last_used_updated(self, worker_client, test_database, registered_worker):
        """Test that last_used_at is updated on successful authentication."""
        # Make authenticated request
        response = worker_client.post(
            "/api/worker/heartbeat",
            json={"status": "active"},
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 200

        # Check last_used_at was updated
        key_record_after = await test_database.fetch_one(
            worker_api_keys.select()
            .where(worker_api_keys.c.key_prefix == get_key_prefix(registered_worker["api_key"]))
        )
        assert key_record_after["last_used_at"] is not None


class TestKeyHashingFunctions:
    """Tests for API key hashing utilities (Issue #119)."""

    def test_hash_api_key_produces_consistent_hash(self):
        """Test that hashing the same key twice produces the same result."""
        key = "test-api-key-abcdefgh12345678"
        hash1 = hash_api_key(key)
        hash2 = hash_api_key(key)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 produces 64 hex characters

    def test_hash_api_key_different_inputs_different_outputs(self):
        """Test that different keys produce different hashes."""
        key1 = "test-api-key-abcdefgh12345678"
        key2 = "test-api-key-12345678abcdefgh"
        assert hash_api_key(key1) != hash_api_key(key2)

    def test_get_key_prefix_extracts_first_8_chars(self):
        """Test key prefix extraction returns first 8 characters."""
        key = "abcdefghijklmnopqrstuvwxyz"
        prefix = get_key_prefix(key)
        assert prefix == "abcdefgh"
        assert len(prefix) == 8

    def test_get_key_prefix_short_key(self):
        """Test prefix extraction with key shorter than 8 chars."""
        key = "short"
        prefix = get_key_prefix(key)
        assert prefix == "short"  # Returns entire key if < 8 chars


# ============================================================================
# Source File Download (Issue #119)
# ============================================================================


class TestSourceDownload:
    """Tests for source file download endpoint."""

    @pytest.mark.asyncio
    async def test_download_source_success(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage
    ):
        """Test successful source file download."""
        # Create transcoding job and claim it
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create source file
        source_content = b"fake video content for download test"
        source_file = test_storage["uploads"] / f"{sample_pending_video['id']}.mp4"
        source_file.write_bytes(source_content)

        # Claim the job
        claim_response = worker_client.post(
            "/api/worker/claim",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert claim_response.status_code == 200

        # Download source
        response = worker_client.get(
            f"/api/worker/source/{sample_pending_video['id']}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 200
        assert response.content == source_content

    @pytest.mark.asyncio
    async def test_download_source_not_your_job(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage
    ):
        """Test downloading source for a job claimed by another worker fails."""
        # Create a job claimed by a different worker
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id="different-worker-uuid",
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create source file
        source_file = test_storage["uploads"] / f"{sample_pending_video['id']}.mp4"
        source_file.write_bytes(b"fake video content")

        response = worker_client.get(
            f"/api/worker/source/{sample_pending_video['id']}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 403
        assert "Not your job" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_download_source_no_job(self, worker_client, registered_worker, sample_pending_video):
        """Test downloading source when no job exists fails."""
        response = worker_client.get(
            f"/api/worker/source/{sample_pending_video['id']}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_download_source_file_not_found(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test downloading when source file doesn't exist returns 404."""
        # Create and claim job, but don't create source file
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Claim the job
        worker_client.post(
            "/api/worker/claim",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )

        # Try to download non-existent source
        response = worker_client.get(
            f"/api/worker/source/{sample_pending_video['id']}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 404
        assert "Source file not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_download_source_finds_different_extensions(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage
    ):
        """Test that source download finds files with different video extensions."""
        # Create and claim job
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create source file with .mkv extension
        source_content = b"mkv video content"
        source_file = test_storage["uploads"] / f"{sample_pending_video['id']}.mkv"
        source_file.write_bytes(source_content)

        # Claim the job
        worker_client.post(
            "/api/worker/claim",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )

        response = worker_client.get(
            f"/api/worker/source/{sample_pending_video['id']}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
        )
        assert response.status_code == 200
        assert response.content == source_content


# ============================================================================
# Path Traversal Prevention (Issue #119)
# ============================================================================


class TestPathTraversalPrevention:
    """Tests for path traversal prevention in HLS upload."""

    def _create_tar_with_file(self, name: str, content: bytes) -> bytes:
        """Helper to create a tar.gz archive with a single file."""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_buffer.seek(0)
        return tar_buffer.read()

    @pytest.mark.asyncio
    async def test_path_traversal_with_dotdot(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage
    ):
        """Test that ../path traversal is blocked."""
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id=registered_worker["worker_id"],
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create tar with path traversal attempt
        tar_data = self._create_tar_with_file("../../../etc/passwd.m3u8", b"malicious")

        response = worker_client.post(
            f"/api/worker/upload/{sample_pending_video['id']}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            files={"file": ("hls.tar.gz", tar_data, "application/gzip")},
        )
        assert response.status_code == 400
        assert "path traversal" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_path_traversal_with_absolute_path(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage
    ):
        """Test that absolute paths in archive are blocked."""
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id=registered_worker["worker_id"],
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create tar with absolute path
        tar_data = self._create_tar_with_file("/etc/passwd.m3u8", b"malicious")

        response = worker_client.post(
            f"/api/worker/upload/{sample_pending_video['id']}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            files={"file": ("hls.tar.gz", tar_data, "application/gzip")},
        )
        assert response.status_code == 400
        # Either path traversal or unexpected file type error
        assert "path traversal" in response.json()["detail"].lower() or "cannot resolve" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_hardlink_blocked(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage
    ):
        """Test that hardlinks in archive are blocked."""
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id=registered_worker["worker_id"],
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create tar with hardlink
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            info = tarfile.TarInfo(name="evil_link.m3u8")
            info.type = tarfile.LNKTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        tar_buffer.seek(0)

        response = worker_client.post(
            f"/api/worker/upload/{sample_pending_video['id']}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            files={"file": ("hls.tar.gz", tar_buffer.read(), "application/gzip")},
        )
        assert response.status_code == 400
        assert "symlinks not allowed" in response.json()["detail"].lower()


# ============================================================================
# Original Test Classes
# ============================================================================


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

    @pytest.mark.asyncio
    async def test_heartbeat_idle_status(self, worker_client, registered_worker, test_database):
        """Test heartbeat with idle status."""
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"status": "idle"},
        )
        assert response.status_code == 200

        # Verify the status was actually stored in the database
        worker = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == registered_worker["worker_id"])
        )
        assert worker["status"] == "idle"

    @pytest.mark.asyncio
    async def test_heartbeat_busy_status(self, worker_client, registered_worker, test_database):
        """Test heartbeat with busy status."""
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"status": "busy"},
        )
        assert response.status_code == 200

        # Verify the status was actually stored in the database
        worker = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == registered_worker["worker_id"])
        )
        assert worker["status"] == "busy"

    @pytest.mark.asyncio
    async def test_heartbeat_status_transition(self, worker_client, registered_worker, test_database):
        """Test status transitions from idle to busy to idle."""

        # Start with idle
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"status": "idle"},
        )
        assert response.status_code == 200
        worker = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == registered_worker["worker_id"])
        )
        assert worker["status"] == "idle"

        # Transition to busy
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"status": "busy"},
        )
        assert response.status_code == 200
        worker = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == registered_worker["worker_id"])
        )
        assert worker["status"] == "busy"

        # Return to idle
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"status": "idle"},
        )
        assert response.status_code == 200
        worker = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == registered_worker["worker_id"])
        )
        assert worker["status"] == "idle"


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
    async def test_claim_job_success(self, worker_client, registered_worker, test_database, sample_pending_video):
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
    async def test_progress_update_success(self, worker_client, registered_worker, test_database, sample_pending_video):
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
    async def test_progress_update_expired_claim(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test updating progress on an expired claim fails with 409."""
        from datetime import timedelta

        # Create a job with an expired claim
        now = datetime.now(timezone.utc)
        expired_time = now - timedelta(minutes=5)  # Claim expired 5 minutes ago

        job_id = await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=sample_pending_video["id"],
                worker_id=registered_worker["worker_id"],
                claimed_at=now - timedelta(minutes=35),  # Claimed 35 minutes ago
                claim_expires_at=expired_time,  # Expired
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Try to update progress - should fail with 409
        response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "current_step": "transcode",
                "progress_percent": 50,
            },
        )
        assert response.status_code == 409
        assert "expired" in response.json()["detail"].lower()

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
                "progress_percent": 10,
                "duration": 120.5,
                "source_width": 1920,
                "source_height": 1080,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

        # Verify metadata was saved to video table
        video = await test_database.fetch_one(videos.select().where(videos.c.id == sample_pending_video["id"]))
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
        video = await test_database.fetch_one(videos.select().where(videos.c.id == sample_pending_video["id"]))
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

    @pytest.mark.asyncio
    async def test_progress_update_metadata_validation(
        self, worker_client, registered_worker, test_database, sample_pending_video
    ):
        """Test that metadata validation works correctly."""
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

        # Test invalid duration (negative)
        response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "current_step": "probe",
                "progress_percent": 8,
                "duration": -10.0,
            },
        )
        assert response.status_code == 422  # Validation error

        # Test invalid width (zero)
        response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "current_step": "probe",
                "progress_percent": 8,
                "source_width": 0,
            },
        )
        assert response.status_code == 422  # Validation error

        # Test invalid height (negative)
        response = worker_client.post(
            f"/api/worker/{job_id}/progress",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "current_step": "probe",
                "progress_percent": 8,
                "source_height": -100,
            },
        )
        assert response.status_code == 422  # Validation error


class TestJobCompletion:
    """Tests for job completion endpoint."""

    @pytest.mark.asyncio
    async def test_complete_job_success(self, worker_client, registered_worker, test_database, sample_pending_video):
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
        video = await test_database.fetch_one(videos.select().where(videos.c.id == sample_pending_video["id"]))
        assert video["status"] == "ready"


class TestJobFailure:
    """Tests for job failure endpoint."""

    @pytest.mark.asyncio
    async def test_fail_job_with_retry(self, worker_client, registered_worker, test_database, sample_pending_video):
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
    async def test_fail_job_final(self, worker_client, registered_worker, test_database, sample_pending_video):
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
        response = worker_client.post(f"/api/workers/{registered_worker['worker_id']}/revoke")
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
    async def test_shutdown_releases_claimed_jobs(self, test_database, registered_worker, sample_pending_video):
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
            videos.update().where(videos.c.id == sample_pending_video["id"]).values(status="processing")
        )

        # Update worker's current job
        worker_record = await test_database.fetch_one(
            workers.select().where(workers.c.worker_id == registered_worker["worker_id"])
        )
        await test_database.execute(
            workers.update().where(workers.c.id == worker_record["id"]).values(current_job_id=job_id)
        )

        # Verify job is claimed
        job = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
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
        job_after = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
        assert job_after["claimed_at"] is None
        assert job_after["claim_expires_at"] is None
        assert job_after["worker_id"] is None
        assert job_after["current_step"] is None

        # Verify video status was reset to pending
        video_after = await test_database.fetch_one(videos.select().where(videos.c.id == sample_pending_video["id"]))
        assert video_after["status"] == "pending"

        # Verify worker's current_job_id was cleared
        worker_after = await test_database.fetch_one(workers.select().where(workers.c.id == worker_record["id"]))
        assert worker_after["current_job_id"] is None

    @pytest.mark.asyncio
    async def test_shutdown_ignores_completed_jobs(self, test_database, registered_worker, sample_pending_video):
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
        job_after = await test_database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
        assert job_after["claimed_at"] is not None  # Should still be set
        assert job_after["completed_at"] is not None  # Should still be set
        assert job_after["worker_id"] == registered_worker["worker_id"]
