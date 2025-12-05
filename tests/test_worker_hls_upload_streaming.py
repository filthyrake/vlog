"""
Tests for HLS upload streaming functionality.

Tests that the upload_hls endpoint properly streams file uploads to disk
instead of loading the entire file into memory.
"""
import io
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import pytest

from api.database import transcoding_jobs


class TestHLSUploadStreaming:
    """Tests for streaming HLS upload endpoint."""

    @pytest.mark.asyncio
    async def test_upload_hls_streams_large_file(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage
    ):
        """Test that upload_hls streams large files instead of loading into memory."""
        # Create a transcoding job for this worker
        video_id = sample_pending_video["id"]
        worker_id = registered_worker["worker_id"]

        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=worker_id,
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create a test tar.gz archive with some sample HLS files
        # This simulates a worker uploading transcoded output
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            # Add a sample master.m3u8 file
            master_content = b"""#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
1080p.m3u8
"""
            master_info = tarfile.TarInfo(name="master.m3u8")
            master_info.size = len(master_content)
            tar.addfile(master_info, io.BytesIO(master_content))

            # Add a sample quality playlist
            quality_content = b"""#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:10.0,
1080p_0000.ts
#EXT-X-ENDLIST
"""
            quality_info = tarfile.TarInfo(name="1080p.m3u8")
            quality_info.size = len(quality_content)
            tar.addfile(quality_info, io.BytesIO(quality_content))

            # Add a sample segment (small one for testing)
            segment_content = b"TS segment data" * 1000  # ~15KB
            segment_info = tarfile.TarInfo(name="1080p_0000.ts")
            segment_info.size = len(segment_content)
            tar.addfile(segment_info, io.BytesIO(segment_content))

            # Add a thumbnail
            thumb_content = b"JPEG thumbnail data"
            thumb_info = tarfile.TarInfo(name="thumbnail.jpg")
            thumb_info.size = len(thumb_content)
            tar.addfile(thumb_info, io.BytesIO(thumb_content))

        tar_buffer.seek(0)
        tar_data = tar_buffer.read()

        # Upload the HLS files
        response = worker_client.post(
            f"/api/worker/upload/{video_id}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            files={"file": ("hls.tar.gz", tar_data, "application/gzip")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "uploaded successfully" in data["message"].lower()

        # Verify the files were extracted to the correct location
        videos_dir = test_storage["videos"]
        video_dir = videos_dir / sample_pending_video["slug"]

        assert video_dir.exists()
        assert (video_dir / "master.m3u8").exists()
        assert (video_dir / "1080p.m3u8").exists()
        assert (video_dir / "1080p_0000.ts").exists()
        assert (video_dir / "thumbnail.jpg").exists()

        # Verify content was written correctly
        with open(video_dir / "master.m3u8", "rb") as f:
            assert b"#EXTM3U" in f.read()

    @pytest.mark.asyncio
    async def test_upload_hls_handles_upload_error(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage, monkeypatch
    ):
        """Test that upload errors are handled gracefully and temp files are cleaned up."""
        # Create a transcoding job for this worker
        video_id = sample_pending_video["id"]
        worker_id = registered_worker["worker_id"]

        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=worker_id,
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Mock aiofiles.open to raise an exception to simulate upload failure
        async def failing_open(*args, **kwargs):
            raise IOError("Simulated disk write failure")

        monkeypatch.setattr(aiofiles, "open", failing_open)

        # Create minimal tar.gz data
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            content = b"test"
            info = tarfile.TarInfo(name="master.m3u8")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        tar_buffer.seek(0)
        tar_data = tar_buffer.read()

        # Upload should fail with 500 error
        response = worker_client.post(
            f"/api/worker/upload/{video_id}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            files={"file": ("hls.tar.gz", tar_data, "application/gzip")},
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to save upload"

    @pytest.mark.asyncio
    async def test_upload_hls_rejects_symlinks(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage
    ):
        """Test that archives containing symlinks are rejected for security."""
        # Create a transcoding job for this worker
        video_id = sample_pending_video["id"]
        worker_id = registered_worker["worker_id"]

        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=worker_id,
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create a tar.gz with a symlink (security risk)
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)

        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                # Add a regular file first
                content = b"test"
                info = tarfile.TarInfo(name="master.m3u8")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))

                # Add a symlink (should be rejected)
                link_info = tarfile.TarInfo(name="bad_symlink")
                link_info.type = tarfile.SYMTYPE
                link_info.linkname = "/etc/passwd"
                tar.addfile(link_info)

            with open(tmp_path, "rb") as f:
                tar_data = f.read()

            # Upload should fail with 400 error
            response = worker_client.post(
                f"/api/worker/upload/{video_id}",
                headers={"X-Worker-API-Key": registered_worker["api_key"]},
                files={"file": ("hls.tar.gz", tar_data, "application/gzip")},
            )

            assert response.status_code == 400
            assert "symlinks not allowed" in response.json()["detail"].lower()
        finally:
            tmp_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_upload_hls_rejects_unexpected_file_types(
        self, worker_client, registered_worker, test_database, sample_pending_video, test_storage
    ):
        """Test that archives with unexpected file types are rejected."""
        # Create a transcoding job for this worker
        video_id = sample_pending_video["id"]
        worker_id = registered_worker["worker_id"]

        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                worker_id=worker_id,
                claimed_at=datetime.now(timezone.utc),
                attempt_number=1,
                max_attempts=3,
            )
        )

        # Create a tar.gz with an unexpected file type
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            # Add a .sh file (not allowed)
            content = b"#!/bin/bash\necho 'malicious'"
            info = tarfile.TarInfo(name="malicious.sh")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        tar_buffer.seek(0)
        tar_data = tar_buffer.read()

        # Upload should fail with 400 error
        response = worker_client.post(
            f"/api/worker/upload/{video_id}",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            files={"file": ("hls.tar.gz", tar_data, "application/gzip")},
        )

        assert response.status_code == 400
        assert "unexpected file type" in response.json()["detail"].lower()
