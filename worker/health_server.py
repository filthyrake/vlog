"""
Health check HTTP server for remote transcoding workers.

Provides Kubernetes-compatible health endpoints:
- /health (liveness): Process is running
- /ready (readiness): Worker can accept jobs (API connected, FFmpeg available)

Runs on port 8080 by default (configurable via VLOG_WORKER_HEALTH_PORT).
"""

import asyncio
import shutil
from http import HTTPStatus
from typing import Callable, Optional

from config import WORKER_API_URL

# Default health check port
DEFAULT_HEALTH_PORT = 8080


class HealthServer:
    """Simple async HTTP health server for worker liveness/readiness probes."""

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

    def set_ready(self, ready: bool):
        """Set readiness state (called after successful API connection)."""
        self._is_ready = ready

    def set_heartbeat_status(self, ok: bool):
        """Update heartbeat status (called after each heartbeat)."""
        self._last_heartbeat_ok = ok

    async def _check_ffmpeg(self) -> bool:
        """Check if FFmpeg is available."""
        return shutil.which("ffmpeg") is not None

    async def _handle_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle incoming HTTP request."""
        try:
            # Read request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            request_text = request_line.decode("utf-8", errors="replace")

            # Parse path from request
            parts = request_text.split()
            path = parts[1] if len(parts) > 1 else "/"

            # Drain remaining headers (we don't need them)
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line == b"\r\n" or line == b"\n" or line == b"":
                    break

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

            # Send response
            response = (
                f"HTTP/1.1 {status.value} {status.phrase}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
                f"{body}"
            )
            writer.write(response.encode())
            await writer.drain()

        except asyncio.TimeoutError:
            pass
        except Exception:
            # Return 500 on any error
            error_response = (
                "HTTP/1.1 500 Internal Server Error\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: 25\r\n"
                "Connection: close\r\n"
                "\r\n"
                '{"error": "server error"}'
            )
            writer.write(error_response.encode())
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
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
