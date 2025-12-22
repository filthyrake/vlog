"""Tests for worker health check functionality."""

import asyncio
import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from worker.http_client import WorkerAPIError


@pytest.fixture
def mock_config():
    """Mock config values for testing."""
    with (
        patch("worker.health_check.WORKER_API_URL", "http://test-api:9002"),
        patch("worker.health_check.WORKER_API_KEY", "test-key"),
    ):
        yield


class TestFFmpegCheck:
    """Tests for FFmpeg availability check."""

    def test_ffmpeg_available(self, mock_config):
        """Test that FFmpeg check passes when FFmpeg is available."""
        from worker.health_check import check_ffmpeg

        # Mock subprocess to return successful FFmpeg version
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ffmpeg version 4.4.0")

            success, error = check_ffmpeg()

            assert success is True
            assert error == ""
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0][0] == "ffmpeg"

    def test_ffmpeg_not_found(self, mock_config):
        """Test that FFmpeg check fails when FFmpeg is not found."""
        from worker.health_check import check_ffmpeg

        with patch("subprocess.run", side_effect=FileNotFoundError):
            success, error = check_ffmpeg()

            assert success is False
            assert "not found" in error.lower()

    def test_ffmpeg_timeout(self, mock_config):
        """Test that FFmpeg check fails on timeout."""
        from worker.health_check import check_ffmpeg

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 5)):
            success, error = check_ffmpeg()

            assert success is False
            assert "timed out" in error.lower()

    def test_ffmpeg_non_zero_exit(self, mock_config):
        """Test that FFmpeg check fails on non-zero exit code."""
        from worker.health_check import check_ffmpeg

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="error")

            success, error = check_ffmpeg()

            assert success is False
            assert "non-zero exit code" in error.lower()

    def test_ffmpeg_invalid_output(self, mock_config):
        """Test that FFmpeg check fails when output is invalid."""
        from worker.health_check import check_ffmpeg

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="invalid output")

            success, error = check_ffmpeg()

            assert success is False
            assert "version info" in error.lower()


class TestGPUCheck:
    """Tests for GPU availability check."""

    def test_gpu_check_nvidia_available(self, mock_config):
        """Test GPU check with NVIDIA GPU available."""
        from worker.health_check import check_gpu_optional

        with patch.dict(os.environ, {"VLOG_HWACCEL_TYPE": "nvidia"}), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="GPU 0: NVIDIA GeForce RTX 3090")

            success, warning = check_gpu_optional()

            assert success is True
            assert warning == ""

    def test_gpu_check_nvidia_not_found(self, mock_config):
        """Test GPU check when NVIDIA GPU is expected but not found."""
        from worker.health_check import check_gpu_optional

        with (
            patch.dict(os.environ, {"VLOG_HWACCEL_TYPE": "nvidia"}),
            patch("subprocess.run", side_effect=FileNotFoundError),
        ):
            success, warning = check_gpu_optional()

            assert success is True  # Non-critical check
            assert "nvidia" in warning.lower()
            assert "fallback" in warning.lower()

    def test_gpu_check_intel_available(self, mock_config):
        """Test GPU check with Intel GPU available."""
        from worker.health_check import check_gpu_optional

        with (
            patch.dict(os.environ, {"VLOG_HWACCEL_TYPE": "intel"}),
            patch("glob.glob", return_value=["/dev/dri/renderD128"]),
        ):
            success, warning = check_gpu_optional()

            assert success is True
            assert warning == ""

    def test_gpu_check_intel_not_found(self, mock_config):
        """Test GPU check when Intel GPU is expected but not found."""
        from worker.health_check import check_gpu_optional

        with patch.dict(os.environ, {"VLOG_HWACCEL_TYPE": "intel"}), patch("glob.glob", return_value=[]):
            success, warning = check_gpu_optional()

            assert success is True  # Non-critical check
            assert "intel" in warning.lower()
            assert "fallback" in warning.lower()

    def test_gpu_check_auto_mode_no_gpu(self, mock_config):
        """Test GPU check in auto mode with no GPU."""
        from worker.health_check import check_gpu_optional

        with (
            patch.dict(os.environ, {"VLOG_HWACCEL_TYPE": "auto"}),
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("glob.glob", return_value=[]),
        ):
            success, warning = check_gpu_optional()

            assert success is True
            assert warning == ""  # Auto mode doesn't warn about missing GPU

    def test_gpu_check_disabled(self, mock_config):
        """Test GPU check when hardware acceleration is disabled."""
        from worker.health_check import check_gpu_optional

        with patch.dict(os.environ, {"VLOG_HWACCEL_TYPE": "none"}):
            success, warning = check_gpu_optional()

            assert success is True
            assert warning == ""


class TestAPIConnectivityCheck:
    """Tests for Worker API connectivity check."""

    @pytest.mark.asyncio
    async def test_api_connectivity_success(self, mock_config):
        """Test API connectivity check succeeds."""
        from worker.health_check import check_api_connectivity

        with patch("worker.health_check.WorkerAPIClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.heartbeat = AsyncMock()
            mock_client.close = AsyncMock()
            mock_client_class.return_value = mock_client

            success, error = await check_api_connectivity()

            assert success is True
            assert error == ""
            mock_client.heartbeat.assert_called_once_with(status="idle")
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_connectivity_no_api_key(self):
        """Test API connectivity check fails when API key is missing."""
        from worker.health_check import check_api_connectivity

        with patch("worker.health_check.WORKER_API_KEY", ""):
            success, error = await check_api_connectivity()

            assert success is False
            assert "not configured" in error.lower()

    @pytest.mark.asyncio
    async def test_api_connectivity_auth_failed(self, mock_config):
        """Test API connectivity check fails on authentication error."""
        from worker.health_check import check_api_connectivity

        with patch("worker.health_check.WorkerAPIClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.heartbeat = AsyncMock(side_effect=WorkerAPIError(401, "Invalid API key"))
            mock_client.close = AsyncMock()
            mock_client_class.return_value = mock_client

            success, error = await check_api_connectivity()

            assert success is False
            assert "authentication failed" in error.lower()
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_connectivity_server_error(self, mock_config):
        """Test API connectivity check fails on server error."""
        from worker.health_check import check_api_connectivity

        with patch("worker.health_check.WorkerAPIClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.heartbeat = AsyncMock(side_effect=WorkerAPIError(500, "Internal server error"))
            mock_client.close = AsyncMock()
            mock_client_class.return_value = mock_client

            success, error = await check_api_connectivity()

            assert success is False
            assert "server error" in error.lower()
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_connectivity_timeout(self, mock_config):
        """Test API connectivity check fails on timeout."""
        from worker.health_check import check_api_connectivity

        with patch("worker.health_check.WorkerAPIClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.heartbeat = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_client.close = AsyncMock()
            mock_client_class.return_value = mock_client

            success, error = await check_api_connectivity()

            assert success is False
            assert "timed out" in error.lower()
            mock_client.close.assert_called_once()


class TestHealthCheckMain:
    """Tests for main health check function."""

    @pytest.mark.asyncio
    async def test_all_checks_pass(self, mock_config):
        """Test that health check returns 0 when all checks pass."""
        from worker.health_check import main

        with (
            patch("worker.health_check.check_ffmpeg", return_value=(True, "")),
            patch("worker.health_check.check_api_connectivity", return_value=(True, "")),
            patch("worker.health_check.check_gpu_optional", return_value=(True, "")),
        ):
            exit_code = await main()

            assert exit_code == 0

    @pytest.mark.asyncio
    async def test_ffmpeg_check_fails(self, mock_config):
        """Test that health check returns 1 when FFmpeg check fails."""
        from worker.health_check import main

        with (
            patch("worker.health_check.check_ffmpeg", return_value=(False, "FFmpeg not found")),
            patch("worker.health_check.check_api_connectivity", return_value=(True, "")),
            patch("worker.health_check.check_gpu_optional", return_value=(True, "")),
        ):
            exit_code = await main()

            assert exit_code == 1

    @pytest.mark.asyncio
    async def test_api_check_fails(self, mock_config):
        """Test that health check returns 1 when API check fails."""
        from worker.health_check import main

        with (
            patch("worker.health_check.check_ffmpeg", return_value=(True, "")),
            patch("worker.health_check.check_api_connectivity", return_value=(False, "API unreachable")),
            patch("worker.health_check.check_gpu_optional", return_value=(True, "")),
        ):
            exit_code = await main()

            assert exit_code == 1

    @pytest.mark.asyncio
    async def test_gpu_warning_does_not_fail(self, mock_config):
        """Test that GPU warnings don't cause health check to fail."""
        from worker.health_check import main

        with (
            patch("worker.health_check.check_ffmpeg", return_value=(True, "")),
            patch("worker.health_check.check_api_connectivity", return_value=(True, "")),
            patch(
                "worker.health_check.check_gpu_optional",
                return_value=(True, "GPU not found but CPU fallback available"),
            ),
        ):
            exit_code = await main()

            assert exit_code == 0  # Should still pass with warning

    @pytest.mark.asyncio
    async def test_multiple_checks_fail(self, mock_config):
        """Test that health check returns 1 when multiple checks fail."""
        from worker.health_check import main

        with (
            patch("worker.health_check.check_ffmpeg", return_value=(False, "FFmpeg error")),
            patch("worker.health_check.check_api_connectivity", return_value=(False, "API error")),
            patch("worker.health_check.check_gpu_optional", return_value=(True, "")),
        ):
            exit_code = await main()

            assert exit_code == 1
