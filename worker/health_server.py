"""
Health check HTTP server for remote transcoding workers.

Provides Kubernetes-compatible health endpoints:
- /health (liveness): Process is running
- /ready (readiness): Worker can accept jobs (API connected, FFmpeg available)

Runs on port 8080 by default (configurable via VLOG_WORKER_HEALTH_PORT).
"""

import asyncio
import logging
import shutil
from http import HTTPStatus
from typing import Callable, Optional

from config import WORKER_API_URL

logger = logging.getLogger(__name__)

# Default health check port
DEFAULT_HEALTH_PORT = 8080


class HealthServer:
    """Simple async HTTP health server for worker liveness/readiness probes."""

    # Timeout for writing response to client (prevents slow-read attacks)
    RESPONSE_WRITE_TIMEOUT = 5.0

    def __init__(
        self,
        port: int = DEFAULT_HEALTH_PORT,
        api_check_fn: Optional[Callable[[], bool]] = None,
    ):
        """
        Initialize health server.

        Args:
            port: Port to listen on (default: 8080)
            api_check_fn: Optional callback that returns True if API is connected
        """
        self.port = port
        self.api_check_fn = api_check_fn
        self._server: Optional[asyncio.Server] = None
        self._is_ready = False
        self._last_heartbeat_ok = False
        # Cache FFmpeg availability to avoid repeated PATH lookups on every healthcheck
        self._ffmpeg_available: Optional[bool] = None

    def set_ready(self, ready: bool):
        """Set readiness state (called after successful API connection)."""
        self._is_ready = ready

    def set_heartbeat_status(self, ok: bool):
        """Update heartbeat status (called after each heartbeat)."""
        self._last_heartbeat_ok = ok

    async def _check_ffmpeg(self) -> bool:
        """Check if FFmpeg is available (cached after first check)."""
        if self._ffmpeg_available is None:
            self._ffmpeg_available = shutil.which("ffmpeg") is not None
        return self._ffmpeg_available

    def clear_ffmpeg_cache(self):
        """Clear the FFmpeg availability cache (useful for testing)."""
        self._ffmpeg_available = None

    # Maximum time for entire request parsing (request line + headers)
    REQUEST_PARSE_TIMEOUT = 10.0
    # Maximum number of header lines to prevent slowloris-style attacks
    MAX_HEADER_LINES = 50

    async def _parse_request(self, reader: asyncio.StreamReader) -> str:
        """Parse HTTP request and return the path.

        Returns:
            The request path (e.g., "/health")
        """
        # Read request line
        request_line = await reader.readline()
        request_text = request_line.decode("utf-8", errors="replace")

        # Parse path from request
        parts = request_text.split()
        path = parts[1] if len(parts) > 1 else "/"

        # Drain remaining headers (we don't need them)
        # Limit header count to prevent resource exhaustion
        header_count = 0
        while header_count < self.MAX_HEADER_LINES:
            line = await reader.readline()
            if line == b"\r\n" or line == b"\n" or line == b"":
                break
            header_count += 1
        else:
            # Loop exited due to hitting MAX_HEADER_LINES, not finding end of headers
            # This indicates a potential attack or malformed request
            logger.warning(f"Request exceeded MAX_HEADER_LINES ({self.MAX_HEADER_LINES})")
            raise ValueError("Too many headers")

        return path

    async def _handle_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle incoming HTTP request."""
        try:
            # Wrap entire request parsing in overall timeout
            # This prevents slowloris attacks where client sends data very slowly
            path = await asyncio.wait_for(
                self._parse_request(reader),
                timeout=self.REQUEST_PARSE_TIMEOUT
            )

            # Handle endpoints
            if path == "/health":
                # Liveness check - just verify process is running
                status = HTTPStatus.OK
                body = '{"status": "alive"}'
            elif path == "/ready":
                # Readiness check - verify worker can accept jobs
                checks = {
                    "ffmpeg": await self._check_ffmpeg(),
                    "api_connected": self._is_ready and self._last_heartbeat_ok,
                }
                all_ok = all(checks.values())
                status = HTTPStatus.OK if all_ok else HTTPStatus.SERVICE_UNAVAILABLE
                body = (
                    f'{{"status": "ready", "checks": {{"ffmpeg": {str(checks["ffmpeg"]).lower()}, '
                    f'"api_connected": {str(checks["api_connected"]).lower()}}}}}'
                )
            elif path == "/":
                # Root endpoint with basic info
                status = HTTPStatus.OK
                body = f'{{"service": "vlog-worker", "api_url": "{WORKER_API_URL}"}}'
            else:
                status = HTTPStatus.NOT_FOUND
                body = '{"error": "not found"}'

            # Send response with timeout to prevent slow-read attacks
            response = (
                f"HTTP/1.1 {status.value} {status.phrase}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
                f"{body}"
            )
            writer.write(response.encode())
            await asyncio.wait_for(writer.drain(), timeout=self.RESPONSE_WRITE_TIMEOUT)

        except asyncio.TimeoutError:
            # Client took too long to send request or receive response
            logger.debug("Health check request timed out")
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            # Client disconnected - no need to send error response
            pass
        except (OSError, UnicodeDecodeError) as e:
            # Network errors or malformed request encoding
            # Note: UnicodeDecodeError must be caught before ValueError (it's a subclass)
            logger.debug(f"Health check request error: {type(e).__name__}: {e}")
            # Return 500 if we can still write to the connection
            try:
                error_response = (
                    "HTTP/1.1 500 Internal Server Error\r\n"
                    "Content-Type: application/json\r\n"
                    "Content-Length: 25\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                    '{"error": "server error"}'
                )
                writer.write(error_response.encode())
                await asyncio.wait_for(
                    writer.drain(), timeout=self.RESPONSE_WRITE_TIMEOUT
                )
            except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError, OSError):
                pass  # Client disconnected or timed out during error response
        except ValueError as e:
            # Malformed request (e.g., too many headers)
            logger.debug(f"Health check request error: {e}")
            try:
                bad_request_body = '{"error": "bad request"}'
                error_response = (
                    "HTTP/1.1 400 Bad Request\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(bad_request_body)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                    f"{bad_request_body}"
                )
                writer.write(error_response.encode())
                await asyncio.wait_for(
                    writer.drain(), timeout=self.RESPONSE_WRITE_TIMEOUT
                )
            except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError, OSError):
                pass  # Client disconnected or timed out during error response
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError, OSError):
                # Connection may already be closed by client
                pass

    async def start(self):
        """Start the health server."""
        self._server = await asyncio.start_server(
            self._handle_request, "0.0.0.0", self.port
        )
        print(f"  Health server listening on port {self.port}")

    async def stop(self):
        """Stop the health server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
