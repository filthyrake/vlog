"""
Tests for worker/http_client.py error handling.
"""

import tempfile
from pathlib import Path
from unittest import mock

import httpx
import pytest

from worker.http_client import WorkerAPIClient, WorkerAPIError


class TestUploadQualityExceptionHandling:
    """Test exception handling in upload_quality method."""

    @pytest.mark.asyncio
    async def test_timeout_exception_before_file_size_calculated(self):
        """
        Test that TimeoutException is handled correctly even when thrown
        before file_size_mb is calculated (e.g., during tarfile creation).

        This tests the fix for the UnboundLocalError bug where file_size_mb
        was referenced in the exception handler before being assigned.
        """
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # Create a temporary directory with test files
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # Create some test files
            playlist = output_dir / "1080p.m3u8"
            playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n")

            segment = output_dir / "1080p_001.ts"
            segment.write_bytes(b"fake segment data")

            # Mock tarfile.open to raise TimeoutException during tarfile creation
            # This simulates a timeout happening before file_size_mb is calculated
            with mock.patch("tarfile.open", side_effect=httpx.TimeoutException("Timeout during tar")):
                with pytest.raises(WorkerAPIError) as exc_info:
                    await client.upload_quality(
                        video_id=1,
                        quality_name="1080p",
                        output_dir=output_dir,
                    )

                # Verify the error is raised correctly
                error = exc_info.value
                assert error.status_code == 0
                assert "Upload timeout for 1080p" in error.message
                assert "(0.0MB)" in error.message  # Should show 0.0MB (default value)
                assert "Timeout during tar" in error.message

    @pytest.mark.asyncio
    async def test_timeout_exception_after_file_size_calculated(self):
        """
        Test that TimeoutException shows the correct file size when thrown
        during the actual upload (after file_size_mb is calculated).
        """
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # Create a temporary directory with test files
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # Create some test files
            playlist = output_dir / "1080p.m3u8"
            playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n")

            segment = output_dir / "1080p_001.ts"
            segment.write_bytes(b"x" * (2 * 1024 * 1024))  # 2MB of data

            # Mock the HTTP client to raise TimeoutException during upload
            mock_client = mock.AsyncMock()
            mock_client.post.side_effect = httpx.TimeoutException("Timeout during upload")

            with mock.patch.object(client, "_get_client", return_value=mock_client):
                with pytest.raises(WorkerAPIError) as exc_info:
                    await client.upload_quality(
                        video_id=1,
                        quality_name="1080p",
                        output_dir=output_dir,
                    )

                # Verify the error is raised correctly with proper message
                error = exc_info.value
                assert error.status_code == 0
                assert "Upload timeout for 1080p" in error.message
                # The key thing is that file_size_mb exists and formats correctly
                # The actual value will depend on tarfile compression
                assert "MB)" in error.message  # Verify size is included in message
                assert "Timeout during upload" in error.message

    @pytest.mark.asyncio
    async def test_http_status_error_handling(self):
        """Test that HTTPStatusError is handled correctly (regression test)."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # Create a temporary directory with test files
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # Create some test files
            playlist = output_dir / "1080p.m3u8"
            playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n")

            # Mock HTTP response for 500 error
            mock_response = mock.Mock()
            mock_response.status_code = 500
            mock_response.json.return_value = {"detail": "Internal server error"}

            mock_client = mock.AsyncMock()
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "Server error", request=mock.Mock(), response=mock_response
            )

            with mock.patch.object(client, "_get_client", return_value=mock_client):
                with pytest.raises(WorkerAPIError) as exc_info:
                    await client.upload_quality(
                        video_id=1,
                        quality_name="1080p",
                        output_dir=output_dir,
                    )

                # Verify error details
                error = exc_info.value
                assert error.status_code == 500
                assert "Internal server error" in error.message
