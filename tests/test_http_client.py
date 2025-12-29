"""
Tests for worker/http_client.py error handling.
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import httpx
import pytest

from worker.http_client import (
    CIRCUIT_BREAKER_BASE_RESET_SECONDS,
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    CircuitBreakerOpen,
    WorkerAPIClient,
    WorkerAPIError,
)


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


class TestCircuitBreaker:
    """Test circuit breaker functionality (Issue #453)."""

    def test_circuit_breaker_initial_state(self):
        """Test that circuit breaker starts closed."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")
        assert client._circuit_open is False
        assert client._consecutive_failures == 0
        assert client._circuit_open_count == 0

    def test_record_success_resets_state(self):
        """Test that successful requests reset circuit breaker state."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # Simulate some failures
        client._consecutive_failures = 2
        client._circuit_open_count = 1

        # Record success
        client._record_success()

        assert client._consecutive_failures == 0
        assert client._circuit_open is False
        assert client._circuit_open_count == 0

    def test_record_failure_increments_counter(self):
        """Test that failures increment the counter."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        client._record_failure()
        assert client._consecutive_failures == 1
        assert client._circuit_open is False  # Not yet at threshold

        client._record_failure()
        assert client._consecutive_failures == 2
        assert client._circuit_open is False  # Still not at threshold

    def test_circuit_opens_after_threshold(self):
        """Test that circuit opens after reaching failure threshold."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # Record failures up to threshold
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            client._record_failure()

        assert client._circuit_open is True
        assert client._circuit_open_count == 1
        assert client._circuit_open_until is not None
        # Should be approximately BASE_RESET_SECONDS in the future
        expected_reset = datetime.now() + timedelta(seconds=CIRCUIT_BREAKER_BASE_RESET_SECONDS)
        assert abs((client._circuit_open_until - expected_reset).total_seconds()) < 1

    def test_check_circuit_breaker_raises_when_open(self):
        """Test that checking open circuit raises CircuitBreakerOpen."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # Open the circuit
        client._circuit_open = True
        client._circuit_open_until = datetime.now() + timedelta(seconds=30)

        with pytest.raises(CircuitBreakerOpen) as exc_info:
            client._check_circuit_breaker()

        error = exc_info.value
        assert error.retry_after > 0
        assert error.retry_after <= 30

    def test_check_circuit_breaker_half_open_after_timeout(self):
        """Test that circuit enters half-open state after reset time passes."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # Open the circuit with expired reset time
        client._circuit_open = True
        client._circuit_open_until = datetime.now() - timedelta(seconds=1)

        # Should not raise, should enter half-open state
        client._check_circuit_breaker()

        assert client._circuit_open is False  # Half-open allows probe

    def test_exponential_backoff_for_reset_time(self):
        """Test that reset time doubles each time circuit opens."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # First circuit open
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            client._record_failure()

        first_reset = client._circuit_open_until
        first_duration = (first_reset - datetime.now()).total_seconds()

        # Reset and open again
        client._circuit_open = False
        client._consecutive_failures = 0

        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            client._record_failure()

        second_reset = client._circuit_open_until
        second_duration = (second_reset - datetime.now()).total_seconds()

        # Second duration should be approximately double (with some tolerance for timing)
        assert second_duration >= first_duration * 1.5  # Allow some slack

    @pytest.mark.asyncio
    async def test_request_checks_circuit_breaker(self):
        """Test that _request checks circuit breaker before making request."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # Open the circuit
        client._circuit_open = True
        client._circuit_open_until = datetime.now() + timedelta(seconds=30)

        # Request should fail immediately without making HTTP call
        with pytest.raises(CircuitBreakerOpen):
            await client._request("GET", "/api/test")

    @pytest.mark.asyncio
    async def test_request_opens_circuit_after_retries_exhausted(self):
        """Test that circuit opens after all retries are exhausted."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key", max_retries=2)

        # Mock HTTP client to always fail with connection error
        mock_http_client = mock.AsyncMock()
        mock_http_client.request.side_effect = httpx.ConnectError("Connection refused")

        with mock.patch.object(client, "_get_client", return_value=mock_http_client):
            with pytest.raises(WorkerAPIError):
                await client._request("GET", "/api/test", timeout=1)

        # After exhausting retries, failure should be recorded
        assert client._consecutive_failures == 1

        # Need to fail CIRCUIT_BREAKER_FAILURE_THRESHOLD times to open
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD - 1):
            with mock.patch.object(client, "_get_client", return_value=mock_http_client):
                with pytest.raises(WorkerAPIError):
                    await client._request("GET", "/api/test", timeout=1)

        assert client._circuit_open is True

    @pytest.mark.asyncio
    async def test_successful_request_resets_circuit(self):
        """Test that successful request resets circuit breaker."""
        client = WorkerAPIClient("http://test.example.com", "test-api-key")

        # Simulate some failures
        client._consecutive_failures = 2

        # Mock successful response
        mock_response = mock.Mock()
        mock_response.json.return_value = {"status": "ok"}
        mock_response.raise_for_status = mock.Mock()

        mock_http_client = mock.AsyncMock()
        mock_http_client.request.return_value = mock_response

        with mock.patch.object(client, "_get_client", return_value=mock_http_client):
            result = await client._request("GET", "/api/test")

        assert result == {"status": "ok"}
        assert client._consecutive_failures == 0
        assert client._circuit_open is False

    def test_circuit_breaker_open_is_worker_api_error_subclass(self):
        """Test that CircuitBreakerOpen is a subclass of WorkerAPIError."""
        error = CircuitBreakerOpen(retry_after=30)
        assert isinstance(error, WorkerAPIError)
        assert error.status_code == 0
        assert "Circuit breaker open" in error.message
