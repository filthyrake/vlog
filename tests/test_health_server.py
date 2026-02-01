"""
Tests for worker/health_server.py

Tests the Kubernetes-compatible health endpoints:
- /health (liveness): Process is running
- /ready (readiness): Worker can accept jobs (API connected, FFmpeg available)
"""

import asyncio
from unittest.mock import patch

import pytest


class TestHealthServer:
    """Tests for the HealthServer class."""

    @pytest.fixture
    async def health_server(self):
        """Create a health server instance for testing."""
        from worker.health_server import HealthServer

        # Use a random high port to avoid conflicts
        server = HealthServer(port=0)  # Port 0 lets OS assign a free port
        await server.start()

        # Get the actual port assigned
        actual_port = server._server.sockets[0].getsockname()[1]
        server._test_port = actual_port

        yield server

        await server.stop()

    async def _make_request(self, port: int, path: str) -> tuple[int, str]:
        """Make an HTTP request to the health server and return status code and body."""
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            request = f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()

            # Read response
            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response_text = response.decode("utf-8")

            # Parse status code
            first_line = response_text.split("\r\n")[0]
            status_code = int(first_line.split()[1])

            # Parse body (after double CRLF)
            body = response_text.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in response_text else ""

            return status_code, body
        finally:
            writer.close()
            await writer.wait_closed()

    # =========================================================================
    # Liveness Tests (/health)
    # =========================================================================

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_200(self, health_server):
        """Test that /health returns 200 when server is running."""
        status, body = await self._make_request(health_server._test_port, "/health")

        assert status == 200
        assert '"status": "alive"' in body

    @pytest.mark.asyncio
    async def test_health_endpoint_returns_json(self, health_server):
        """Test that /health returns valid JSON."""
        import json

        status, body = await self._make_request(health_server._test_port, "/health")

        assert status == 200
        parsed = json.loads(body)
        assert parsed == {"status": "alive"}

    # =========================================================================
    # Readiness Tests (/ready)
    # =========================================================================

    @pytest.mark.asyncio
    async def test_ready_endpoint_returns_503_when_not_ready(self, health_server):
        """Test that /ready returns 503 when worker is not ready."""
        # Server starts with _is_ready=False and _last_heartbeat_ok=False
        status, body = await self._make_request(health_server._test_port, "/ready")

        assert status == 503
        assert '"api_connected": false' in body

    @pytest.mark.asyncio
    async def test_ready_endpoint_returns_503_when_ffmpeg_missing(self, health_server):
        """Test that /ready returns 503 when FFmpeg is not available."""
        health_server.set_ready(True)
        health_server.set_heartbeat_status(True)
        # Clear FFmpeg cache to ensure our mock is used
        health_server.clear_ffmpeg_cache()

        with patch("shutil.which", return_value=None):
            status, body = await self._make_request(health_server._test_port, "/ready")

        assert status == 503
        assert '"ffmpeg": false' in body

    @pytest.mark.asyncio
    async def test_ready_endpoint_returns_200_when_all_checks_pass(self, health_server):
        """Test that /ready returns 200 when all dependencies are available."""
        health_server.set_ready(True)
        health_server.set_heartbeat_status(True)
        # Clear FFmpeg cache to ensure our mock is used
        health_server.clear_ffmpeg_cache()

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            status, body = await self._make_request(health_server._test_port, "/ready")

        assert status == 200
        assert '"status": "ready"' in body
        assert '"ffmpeg": true' in body
        assert '"api_connected": true' in body

    @pytest.mark.asyncio
    async def test_ready_endpoint_returns_503_when_heartbeat_fails(self, health_server):
        """Test that /ready returns 503 when heartbeat is failing."""
        health_server.set_ready(True)
        health_server.set_heartbeat_status(False)  # Heartbeat failed
        # Clear FFmpeg cache to ensure our mock is used
        health_server.clear_ffmpeg_cache()

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            status, body = await self._make_request(health_server._test_port, "/ready")

        assert status == 503
        assert '"api_connected": false' in body

    @pytest.mark.asyncio
    async def test_ready_endpoint_returns_valid_json(self, health_server):
        """Test that /ready returns valid JSON with check details."""
        import json

        health_server.set_ready(True)
        health_server.set_heartbeat_status(True)
        # Clear FFmpeg cache to ensure our mock is used
        health_server.clear_ffmpeg_cache()

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            status, body = await self._make_request(health_server._test_port, "/ready")

        parsed = json.loads(body)
        assert "status" in parsed
        assert "checks" in parsed
        assert "ffmpeg" in parsed["checks"]
        assert "api_connected" in parsed["checks"]

    # =========================================================================
    # Root Endpoint Tests (/)
    # =========================================================================

    @pytest.mark.asyncio
    async def test_root_endpoint_returns_200(self, health_server):
        """Test that / returns 200 with service info."""
        status, body = await self._make_request(health_server._test_port, "/")

        assert status == 200
        assert '"service": "vlog-worker"' in body

    @pytest.mark.asyncio
    async def test_root_endpoint_includes_api_url(self, health_server):
        """Test that / includes the configured API URL."""
        status, body = await self._make_request(health_server._test_port, "/")

        assert status == 200
        assert '"api_url":' in body

    # =========================================================================
    # 404 Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_unknown_path_returns_404(self, health_server):
        """Test that unknown paths return 404."""
        status, body = await self._make_request(health_server._test_port, "/unknown")

        assert status == 404
        assert '"error": "not found"' in body

    @pytest.mark.asyncio
    async def test_health_subpath_returns_404(self, health_server):
        """Test that /health/something returns 404."""
        status, body = await self._make_request(health_server._test_port, "/health/detailed")

        assert status == 404

    # =========================================================================
    # State Management Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_set_ready_updates_state(self, health_server):
        """Test that set_ready() updates the readiness state."""
        assert health_server._is_ready is False

        health_server.set_ready(True)
        assert health_server._is_ready is True

        health_server.set_ready(False)
        assert health_server._is_ready is False

    @pytest.mark.asyncio
    async def test_set_heartbeat_status_updates_state(self, health_server):
        """Test that set_heartbeat_status() updates the heartbeat state."""
        assert health_server._last_heartbeat_ok is False

        health_server.set_heartbeat_status(True)
        assert health_server._last_heartbeat_ok is True

        health_server.set_heartbeat_status(False)
        assert health_server._last_heartbeat_ok is False

    # =========================================================================
    # Server Lifecycle Tests
    # =========================================================================

    @pytest.mark.asyncio
    async def test_server_starts_and_stops(self):
        """Test that the server can start and stop cleanly."""
        from worker.health_server import HealthServer

        server = HealthServer(port=0)

        # Server should not be running initially
        assert server._server is None

        # Start the server
        await server.start()
        assert server._server is not None

        # Stop the server
        await server.stop()
        assert server._server is None

    @pytest.mark.asyncio
    async def test_server_stop_is_idempotent(self):
        """Test that calling stop() multiple times is safe."""
        from worker.health_server import HealthServer

        server = HealthServer(port=0)
        await server.start()
        await server.stop()

        # Calling stop again should not raise
        await server.stop()
        assert server._server is None

    # =========================================================================
    # Edge Cases
    # =========================================================================

    @pytest.mark.asyncio
    async def test_handles_slow_client_gracefully(self, health_server):
        """Test that server handles slow clients without blocking."""
        reader, writer = await asyncio.open_connection("127.0.0.1", health_server._test_port)
        try:
            # Send partial request very slowly
            writer.write(b"GET /health HTTP/1.1\r\n")
            await writer.drain()

            # Wait a bit but don't complete the request
            await asyncio.sleep(0.1)

            # Complete the request
            writer.write(b"Host: localhost\r\n\r\n")
            await writer.drain()

            # Should still get a response
            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            assert b"200" in response
        finally:
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_handles_malformed_request(self, health_server):
        """Test that server handles malformed requests gracefully."""
        reader, writer = await asyncio.open_connection("127.0.0.1", health_server._test_port)
        try:
            # Send malformed request (no HTTP version)
            writer.write(b"INVALID\r\n\r\n")
            await writer.drain()

            # Should get some response (likely 404 for "/" since path parsing may default)
            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            # Server should respond without crashing
            assert len(response) > 0
        finally:
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_concurrent_requests(self, health_server):
        """Test that server handles multiple concurrent requests."""
        async def make_health_request():
            status, _ = await self._make_request(health_server._test_port, "/health")
            return status

        # Make 10 concurrent requests
        tasks = [make_health_request() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should succeed
        assert all(status == 200 for status in results)

    @pytest.mark.asyncio
    async def test_rejects_too_many_headers(self, health_server):
        """Test that server returns 400 when too many headers are sent."""
        from worker.health_server import HealthServer

        reader, writer = await asyncio.open_connection("127.0.0.1", health_server._test_port)
        try:
            # Send request with more than MAX_HEADER_LINES headers
            writer.write(b"GET /health HTTP/1.1\r\n")
            for i in range(HealthServer.MAX_HEADER_LINES + 5):
                writer.write(f"X-Header-{i}: value{i}\r\n".encode())
            writer.write(b"\r\n")
            await writer.drain()

            # Should get 400 Bad Request
            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            assert b"400" in response
            assert b"bad request" in response.lower()
        finally:
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_accepts_max_headers(self, health_server):
        """Test that server accepts requests with exactly MAX_HEADER_LINES - 1 headers."""
        from worker.health_server import HealthServer

        reader, writer = await asyncio.open_connection("127.0.0.1", health_server._test_port)
        try:
            # Send request with MAX_HEADER_LINES - 1 headers (safe limit)
            writer.write(b"GET /health HTTP/1.1\r\n")
            for i in range(HealthServer.MAX_HEADER_LINES - 1):
                writer.write(f"X-Header-{i}: value{i}\r\n".encode())
            writer.write(b"\r\n")
            await writer.drain()

            # Should get 200 OK
            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            assert b"200" in response
        finally:
            writer.close()
            await writer.wait_closed()


class TestHealthServerFFmpegCheck:
    """Tests specifically for FFmpeg availability checking."""

    @pytest.mark.asyncio
    async def test_check_ffmpeg_returns_true_when_available(self):
        """Test _check_ffmpeg returns True when ffmpeg is in PATH."""
        from worker.health_server import HealthServer

        server = HealthServer()

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            result = await server._check_ffmpeg()

        assert result is True

    @pytest.mark.asyncio
    async def test_check_ffmpeg_returns_false_when_missing(self):
        """Test _check_ffmpeg returns False when ffmpeg is not in PATH."""
        from worker.health_server import HealthServer

        server = HealthServer()

        with patch("shutil.which", return_value=None):
            result = await server._check_ffmpeg()

        assert result is False

    @pytest.mark.asyncio
    async def test_ffmpeg_check_is_cached(self):
        """Test that FFmpeg check result is cached after first call."""
        from worker.health_server import HealthServer

        server = HealthServer()

        with patch("shutil.which", return_value="/usr/bin/ffmpeg") as mock_which:
            # First call should check
            result1 = await server._check_ffmpeg()
            assert result1 is True
            assert mock_which.call_count == 1

            # Second call should use cache
            result2 = await server._check_ffmpeg()
            assert result2 is True
            assert mock_which.call_count == 1  # Still 1, not called again

    @pytest.mark.asyncio
    async def test_clear_ffmpeg_cache_resets_cache(self):
        """Test that clear_ffmpeg_cache() allows re-checking FFmpeg."""
        from worker.health_server import HealthServer

        server = HealthServer()

        with patch("shutil.which", return_value="/usr/bin/ffmpeg") as mock_which:
            # First call
            result1 = await server._check_ffmpeg()
            assert result1 is True
            assert mock_which.call_count == 1

            # Clear cache
            server.clear_ffmpeg_cache()

            # Next call should check again
            result2 = await server._check_ffmpeg()
            assert result2 is True
            assert mock_which.call_count == 2

    @pytest.mark.asyncio
    async def test_ffmpeg_cache_reflects_changed_state(self):
        """Test that FFmpeg cache can be cleared to detect changed state."""
        from worker.health_server import HealthServer

        server = HealthServer()

        # First check: FFmpeg available
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            result1 = await server._check_ffmpeg()
            assert result1 is True

        # Clear cache and check again: FFmpeg removed
        server.clear_ffmpeg_cache()
        with patch("shutil.which", return_value=None):
            result2 = await server._check_ffmpeg()
            assert result2 is False


class TestHealthServerConstants:
    """Tests for health server constants and configuration."""

    def test_default_port_is_8080(self):
        """Test that the default health port is 8080."""
        from worker.health_server import DEFAULT_HEALTH_PORT

        assert DEFAULT_HEALTH_PORT == 8080

    def test_request_parse_timeout_is_reasonable(self):
        """Test that request parse timeout is set to a reasonable value."""
        from worker.health_server import HealthServer

        assert HealthServer.REQUEST_PARSE_TIMEOUT == 10.0

    def test_response_write_timeout_is_reasonable(self):
        """Test that response write timeout is set to prevent slow-read attacks."""
        from worker.health_server import HealthServer

        # Should be reasonable - long enough for normal clients, short enough to prevent attacks
        assert HealthServer.RESPONSE_WRITE_TIMEOUT == 5.0

    def test_max_header_lines_prevents_abuse(self):
        """Test that max header lines is set to prevent abuse."""
        from worker.health_server import HealthServer

        # Should be reasonable (not too high)
        assert HealthServer.MAX_HEADER_LINES <= 100
        assert HealthServer.MAX_HEADER_LINES >= 10

    def test_ffmpeg_cache_starts_empty(self):
        """Test that FFmpeg cache starts as None (uncached)."""
        from worker.health_server import HealthServer

        server = HealthServer()
        assert server._ffmpeg_available is None
