"""
WebSocket Connection Manager.

Provides infrastructure for WebSocket connections with:
- Connection registry with per-stream, per-user, and global limits
- Graceful shutdown handling for deployments
- Heartbeat/ping-pong for connection health
- Session validation on connect + periodic revalidation
- Origin header validation for security

Related Issue: #530

Error Handling Patterns (Issue #561):
-------------------------------------
1. Critical operations (registration, validation): Log ERROR, raise exception
2. Optional operations (broadcasts, sends): Log DEBUG, return result indicating failure
3. Cleanup operations: Never raise - catch all, log, continue
4. WebSocket close codes:
   - 1000: Normal closure
   - 1001: Going away (shutdown, session expired)
   - 1008: Policy violation (feature disabled, not found)
   - 1011: Internal error
   - 4001: Authentication required
   - 4003: Origin validation failed
   - 4029: Connection limit exceeded

Usage:
    manager = WebSocketManager()

    async with manager.connection(websocket, stream_id, user) as conn:
        # Connection is managed - heartbeat, session revalidation handled
        async for message in conn.receive_messages():
            # Process messages
            pass
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Dict, Optional, Set
from urllib.parse import urlparse

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from api.audit import AuditAction, log_audit
from api.auth.sessions import validate_session_token
from config import (
    CORS_ALLOWED_ORIGINS,
    TRUSTED_PROXIES,
    WS_ALLOWED_ORIGINS,
    WS_BROADCAST_MAX_FAILURES,
    WS_HEARTBEAT_INTERVAL,
    WS_MAX_CONNECTIONS_GLOBAL,
    WS_MAX_CONNECTIONS_PER_STREAM,
    WS_MAX_CONNECTIONS_PER_USER_PER_STREAM,
    WS_SESSION_REVALIDATION_INTERVAL,
)

logger = logging.getLogger(__name__)


class WebSocketError(Exception):
    """Base exception for WebSocket errors."""

    pass


class ConnectionLimitError(WebSocketError):
    """Connection limit reached."""

    def __init__(self, message: str, limit_type: str):
        super().__init__(message)
        self.limit_type = limit_type


class OriginValidationError(WebSocketError):
    """Origin header validation failed."""

    pass


class AuthenticationError(WebSocketError):
    """WebSocket authentication failed."""

    pass


class SessionExpiredError(WebSocketError):
    """Session expired during connection."""

    pass


@dataclass
class ConnectionInfo:
    """Information about a WebSocket connection."""

    connection_id: str
    stream_id: int
    user_id: str
    websocket: WebSocket
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_session_check: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BroadcastResult:
    """Result of a broadcast operation (Issue #554)."""

    sent_count: int
    failed_count: int
    early_exit: bool = False


class WebSocketManager:
    """
    Manages WebSocket connections with limits, health checks, and lifecycle management.

    Thread-safe via asyncio.Lock for all connection tracking operations.
    """

    def __init__(self):
        # Connection tracking
        # stream_id -> set of connection_ids
        self._stream_connections: Dict[int, Set[str]] = {}
        # (stream_id, user_id) -> set of connection_ids
        self._user_stream_connections: Dict[tuple, Set[str]] = {}
        # connection_id -> ConnectionInfo
        self._connections: Dict[str, ConnectionInfo] = {}

        self._total_connections: int = 0
        self._lock = asyncio.Lock()

        # Shutdown flag for graceful shutdown
        self._shutting_down: bool = False

        # Callbacks for message handling
        self._message_handlers: Dict[str, Callable] = {}

        # Cleanup tasks tracking (Issue #554)
        self._cleanup_tasks: Set[asyncio.Task] = set()

    @property
    def total_connections(self) -> int:
        """Get total active connections."""
        return self._total_connections

    @property
    def is_shutting_down(self) -> bool:
        """Check if manager is shutting down."""
        return self._shutting_down

    def validate_origin(self, websocket: WebSocket) -> bool:
        """
        Validate the Origin header to prevent cross-site WebSocket hijacking.

        Args:
            websocket: The WebSocket connection

        Returns:
            True if origin is valid, False otherwise

        Security:
            This prevents CSWSH (Cross-Site WebSocket Hijacking) attacks.
            Browser-initiated WebSocket connections always include the Origin header.
        """
        origin = websocket.headers.get("origin")

        # If no allowed origins configured, only allow same-origin requests
        # (Origin header will match the Host header)
        allowed_origins = WS_ALLOWED_ORIGINS or CORS_ALLOWED_ORIGINS

        if not allowed_origins:
            # Same-origin only: Origin must match the Host
            host = websocket.headers.get("host", "")
            if not origin:
                # No Origin header - could be same-origin or non-browser client
                # Be permissive for non-browser clients (they don't send Origin)
                return True

            # Parse origin to extract host
            try:
                parsed = urlparse(origin)
                origin_host = parsed.netloc
                # Compare with host header (may include port)
                return origin_host == host or origin_host.split(":")[0] == host.split(":")[0]
            except Exception:
                return False

        # Check against allowed origins
        if "*" in allowed_origins:
            return True

        if not origin:
            # No origin - allow (non-browser clients)
            return True

        return origin in allowed_origins

    async def register_connection(
        self,
        websocket: WebSocket,
        stream_id: int,
        user: dict,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> ConnectionInfo:
        """
        Register a new WebSocket connection.

        Args:
            websocket: The WebSocket connection
            stream_id: The stream this connection is for
            user: The authenticated user
            client_ip: Client IP for audit
            user_agent: User agent for audit

        Returns:
            ConnectionInfo for the registered connection

        Raises:
            ConnectionLimitError: If any connection limit is exceeded
            OriginValidationError: If origin validation fails
        """
        # Validate origin first
        if not self.validate_origin(websocket):
            origin = websocket.headers.get("origin", "none")
            logger.warning(
                "WebSocket origin validation failed",
                extra={"origin": origin, "stream_id": stream_id, "user_id": user["id"]},
            )
            log_audit(
                AuditAction.WEBSOCKET_AUTH_FAILURE,
                client_ip=client_ip,
                user_agent=user_agent,
                resource_type="live_stream",
                resource_id=stream_id,
                details={
                    "reason": "origin_validation_failed",
                    "origin": origin,
                    "user_id": user["id"],
                },
                success=False,
            )
            raise OriginValidationError(f"Invalid origin: {origin}")

        connection_id = str(uuid.uuid4())[:12]
        user_id = user["id"]

        async with self._lock:
            # Check shutdown state
            if self._shutting_down:
                raise WebSocketError("Server is shutting down")

            # Check global limit
            if self._total_connections >= WS_MAX_CONNECTIONS_GLOBAL:
                logger.warning(
                    "Global WebSocket connection limit reached",
                    extra={"total": self._total_connections, "limit": WS_MAX_CONNECTIONS_GLOBAL},
                )
                raise ConnectionLimitError(
                    f"Server connection limit reached ({WS_MAX_CONNECTIONS_GLOBAL})",
                    limit_type="global",
                )

            # Check per-stream limit
            stream_conns = self._stream_connections.get(stream_id, set())
            if len(stream_conns) >= WS_MAX_CONNECTIONS_PER_STREAM:
                raise ConnectionLimitError(
                    f"Stream connection limit reached ({WS_MAX_CONNECTIONS_PER_STREAM})",
                    limit_type="stream",
                )

            # Check per-user-per-stream limit
            user_stream_key = (stream_id, user_id)
            user_stream_conns = self._user_stream_connections.get(user_stream_key, set())
            if len(user_stream_conns) >= WS_MAX_CONNECTIONS_PER_USER_PER_STREAM:
                raise ConnectionLimitError(
                    f"User connection limit for this stream reached ({WS_MAX_CONNECTIONS_PER_USER_PER_STREAM})",
                    limit_type="user_stream",
                )

            # Register connection
            now = datetime.now(timezone.utc)
            conn_info = ConnectionInfo(
                connection_id=connection_id,
                stream_id=stream_id,
                user_id=user_id,
                websocket=websocket,
                connected_at=now,
                last_activity=now,
                last_session_check=now,
            )

            self._connections[connection_id] = conn_info

            # Add to stream connections
            if stream_id not in self._stream_connections:
                self._stream_connections[stream_id] = set()
            self._stream_connections[stream_id].add(connection_id)

            # Add to user-stream connections
            if user_stream_key not in self._user_stream_connections:
                self._user_stream_connections[user_stream_key] = set()
            self._user_stream_connections[user_stream_key].add(connection_id)

            self._total_connections += 1

        # Audit log
        log_audit(
            AuditAction.WEBSOCKET_CONNECT,
            client_ip=client_ip,
            user_agent=user_agent,
            resource_type="live_stream",
            resource_id=stream_id,
            details={
                "connection_id": connection_id,
                "user_id": user_id,
                "username": user.get("username"),
            },
        )

        logger.debug(
            "WebSocket connection registered",
            extra={
                "connection_id": connection_id,
                "stream_id": stream_id,
                "user_id": user_id,
                "total_connections": self._total_connections,
            },
        )

        return conn_info

    async def unregister_connection(self, connection_id: str) -> None:
        """
        Unregister a WebSocket connection.

        This method is designed to never raise exceptions to ensure cleanup always succeeds.

        Args:
            connection_id: The connection ID to unregister
        """
        try:
            async with self._lock:
                if connection_id not in self._connections:
                    return

                conn_info = self._connections.pop(connection_id)
                stream_id = conn_info.stream_id
                user_id = conn_info.user_id

                # Remove from stream connections
                if stream_id in self._stream_connections:
                    self._stream_connections[stream_id].discard(connection_id)
                    if not self._stream_connections[stream_id]:
                        del self._stream_connections[stream_id]

                # Remove from user-stream connections
                user_stream_key = (stream_id, user_id)
                if user_stream_key in self._user_stream_connections:
                    self._user_stream_connections[user_stream_key].discard(connection_id)
                    if not self._user_stream_connections[user_stream_key]:
                        del self._user_stream_connections[user_stream_key]

                self._total_connections = max(0, self._total_connections - 1)

            logger.debug(
                "WebSocket connection unregistered",
                extra={
                    "connection_id": connection_id,
                    "stream_id": stream_id,
                    "user_id": user_id,
                    "total_connections": self._total_connections,
                },
            )
        except Exception as e:
            # Log but never raise - cleanup must succeed
            logger.error(
                f"Error unregistering WebSocket connection {connection_id}: {e}",
                exc_info=True,
            )

    async def get_stream_connection_count(self, stream_id: int) -> int:
        """Get current connection count for a stream."""
        async with self._lock:
            return len(self._stream_connections.get(stream_id, set()))

    async def get_user_stream_connection_count(self, stream_id: int, user_id: str) -> int:
        """Get current connection count for a user on a stream."""
        async with self._lock:
            return len(self._user_stream_connections.get((stream_id, user_id), set()))

    async def get_connection(self, connection_id: str) -> Optional[ConnectionInfo]:
        """Get connection info by ID."""
        async with self._lock:
            return self._connections.get(connection_id)

    async def get_stream_connections(self, stream_id: int) -> list[ConnectionInfo]:
        """Get all connections for a stream."""
        async with self._lock:
            conn_ids = self._stream_connections.get(stream_id, set())
            return [self._connections[cid] for cid in conn_ids if cid in self._connections]

    async def _send_to_one(
        self,
        conn: ConnectionInfo,
        message_json: str,
    ) -> tuple[str, bool]:
        """
        Send message to a single connection with timeout.

        Returns:
            (connection_id, success) tuple
        """
        try:
            if conn.websocket.client_state == WebSocketState.CONNECTED:
                await asyncio.wait_for(
                    conn.websocket.send_text(message_json),
                    timeout=5.0,
                )
                return (conn.connection_id, True)
            else:
                return (conn.connection_id, False)
        except asyncio.TimeoutError:
            logger.debug(f"Send timeout for connection {conn.connection_id}")
            return (conn.connection_id, False)
        except Exception as e:
            logger.debug(f"Failed to send to connection {conn.connection_id}: {e}")
            return (conn.connection_id, False)

    async def broadcast_to_stream(
        self,
        stream_id: int,
        message: dict,
        exclude_connection: Optional[str] = None,
        priority: str = "normal",
    ) -> BroadcastResult:
        """
        Broadcast a message to all connections on a stream.

        Uses bounded concurrency (100 concurrent sends) to avoid memory spikes.
        For non-critical priority, stops scheduling new sends after failure threshold.

        Args:
            stream_id: The stream to broadcast to
            message: The message dict to send (will be JSON serialized)
            exclude_connection: Optional connection ID to exclude
            priority: "critical" (never exit early) or "normal" (may exit early)

        Returns:
            BroadcastResult with sent/failed counts and early exit flag
        """
        connections = await self.get_stream_connections(stream_id)
        allow_early_exit = priority != "critical"

        # Filter excluded connection
        targets = [
            conn for conn in connections
            if not (exclude_connection and conn.connection_id == exclude_connection)
        ]

        if not targets:
            return BroadcastResult(sent_count=0, failed_count=0, early_exit=False)

        # Serialize message once, with error handling
        try:
            message_json = json.dumps(message)
        except (TypeError, ValueError) as exc:
            logger.warning(
                f"Failed to JSON serialize broadcast message for stream {stream_id}: {exc}",
                extra={"stream_id": stream_id},
            )
            return BroadcastResult(sent_count=0, failed_count=len(targets), early_exit=False)

        # Track results with thread-safe counters for early exit
        sent_count = 0
        failed_count = 0
        failed_connection_ids: list[str] = []
        early_exit = False
        lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(100)  # Bounded concurrency

        async def send_one(conn: ConnectionInfo) -> None:
            nonlocal sent_count, failed_count, early_exit

            # Check early exit before acquiring semaphore
            if allow_early_exit and early_exit:
                return

            async with semaphore:
                # Check again after acquiring semaphore
                if allow_early_exit and early_exit:
                    return

                conn_id, success = await self._send_to_one(conn, message_json)

                async with lock:
                    if success:
                        sent_count += 1
                    else:
                        failed_count += 1
                        failed_connection_ids.append(conn_id)

                        # Trigger early exit for subsequent sends
                        if allow_early_exit and failed_count >= WS_BROADCAST_MAX_FAILURES:
                            if not early_exit:
                                early_exit = True
                                logger.warning(
                                    f"Broadcast early exit: {failed_count} failures "
                                    f"(threshold: {WS_BROADCAST_MAX_FAILURES}) for stream {stream_id}",
                                    extra={"stream_id": stream_id, "sent": sent_count, "failed": failed_count},
                                )

        # Run all sends with bounded concurrency
        await asyncio.gather(
            *[send_one(conn) for conn in targets],
            return_exceptions=True,
        )

        # Schedule cleanup of failed connections (tracked for shutdown)
        if failed_connection_ids:
            task = asyncio.create_task(
                self._cleanup_failed_connections(failed_connection_ids)
            )
            self._cleanup_tasks.add(task)
            task.add_done_callback(self._on_cleanup_task_done)

        return BroadcastResult(
            sent_count=sent_count,
            failed_count=failed_count,
            early_exit=early_exit,
        )

    def _on_cleanup_task_done(self, task: asyncio.Task) -> None:
        """Callback to handle cleanup task completion and log any exceptions."""
        self._cleanup_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(f"Cleanup task failed with exception: {exc}")

    async def _cleanup_failed_connections(self, connection_ids: list[str]) -> None:
        """
        Clean up connections that failed during broadcast.

        Uses bounded parallelism (10 concurrent) to avoid O(n * timeout) cleanup time.
        Catches all exceptions except CancelledError to support cooperative shutdown.
        """
        semaphore = asyncio.Semaphore(10)  # Max 10 concurrent cleanups

        async def cleanup_one(conn_id: str) -> None:
            async with semaphore:
                try:
                    await asyncio.wait_for(
                        self.close_connection(conn_id, code=1001, reason="Connection failed"),
                        timeout=2.0,  # Short timeout, parallelism compensates
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout closing connection {conn_id}, forcing unregister")
                    await self.unregister_connection(conn_id)
                except asyncio.CancelledError:
                    raise  # Allow shutdown to proceed
                except Exception as e:
                    logger.debug(f"Error cleaning up connection {conn_id}: {e}")

        await asyncio.gather(
            *[cleanup_one(conn_id) for conn_id in connection_ids],
            return_exceptions=True,
        )

    async def send_to_connection(self, connection_id: str, message: dict) -> bool:
        """
        Send a message to a specific connection.

        Args:
            connection_id: The connection to send to
            message: The message dict to send

        Returns:
            True if sent successfully, False otherwise
        """
        conn = await self.get_connection(connection_id)
        if not conn:
            return False

        try:
            if conn.websocket.client_state == WebSocketState.CONNECTED:
                await asyncio.wait_for(
                    conn.websocket.send_text(json.dumps(message)),
                    timeout=5.0,
                )
                return True
        except asyncio.TimeoutError:
            logger.debug(f"Send timeout for connection {connection_id}")
        except Exception as e:
            logger.debug(f"Failed to send to connection {connection_id}: {e}")

        return False

    async def close_connection(
        self,
        connection_id: str,
        code: int = 1000,
        reason: str = "Connection closed",
    ) -> None:
        """Close a specific connection."""
        conn = await self.get_connection(connection_id)
        if conn and conn.websocket.client_state == WebSocketState.CONNECTED:
            try:
                await conn.websocket.close(code=code, reason=reason)
            except Exception as e:
                logger.debug(f"Error closing connection {connection_id}: {e}")

        await self.unregister_connection(connection_id)

    async def close_user_connections(
        self,
        stream_id: int,
        user_id: str,
        code: int = 1000,
        reason: str = "User removed",
    ) -> int:
        """
        Close all connections for a user on a stream (e.g., when banned).

        Returns:
            Number of connections closed
        """
        async with self._lock:
            user_stream_key = (stream_id, user_id)
            conn_ids = list(self._user_stream_connections.get(user_stream_key, set()))

        closed_count = 0
        for conn_id in conn_ids:
            await self.close_connection(conn_id, code=code, reason=reason)
            closed_count += 1

        return closed_count

    async def initiate_shutdown(self, timeout: float = 30.0) -> None:
        """
        Initiate graceful shutdown of all connections.

        Args:
            timeout: Maximum time to wait for connections to close
        """
        logger.info("Initiating WebSocket manager shutdown")
        self._shutting_down = True
        start_time = asyncio.get_event_loop().time()

        # Allocate 1/3 of timeout for cleanup tasks, 2/3 for closing connections
        cleanup_timeout = timeout / 3

        # Wait for cleanup tasks before closing connections (Issue #554)
        if self._cleanup_tasks:
            logger.info(f"Waiting for {len(self._cleanup_tasks)} cleanup tasks")
            done, pending = await asyncio.wait(self._cleanup_tasks, timeout=cleanup_timeout)
            if pending:
                logger.warning(f"Cancelling {len(pending)} pending cleanup tasks after timeout")
                for task in pending:
                    task.cancel()

        # Get all connection IDs
        async with self._lock:
            conn_ids = list(self._connections.keys())

        # Send shutdown message to all connections
        for conn_id in conn_ids:
            await self.send_to_connection(conn_id, {
                "type": "shutdown",
                "message": "Server is shutting down",
            })

        # Close all connections
        for conn_id in conn_ids:
            await self.close_connection(
                conn_id,
                code=1001,
                reason="Server shutdown",
            )

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                logger.warning("Shutdown timeout reached, forcing remaining connections closed")
                break

        logger.info(f"WebSocket manager shutdown complete, closed {len(conn_ids)} connections")

    def update_activity(self, connection_id: str) -> None:
        """Update last activity timestamp for a connection (non-async for performance)."""
        if connection_id in self._connections:
            self._connections[connection_id].last_activity = datetime.now(timezone.utc)


class ManagedWebSocketConnection:
    """
    Context manager for a managed WebSocket connection.

    Handles:
    - Session revalidation
    - Heartbeat ping/pong
    - Activity tracking
    - Clean disconnect handling
    """

    def __init__(
        self,
        manager: WebSocketManager,
        conn_info: ConnectionInfo,
        session_token: str,
    ):
        self.manager = manager
        self.conn_info = conn_info
        self.session_token = session_token
        self._closed = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._session_check_task: Optional[asyncio.Task] = None

    @property
    def connection_id(self) -> str:
        return self.conn_info.connection_id

    @property
    def stream_id(self) -> int:
        return self.conn_info.stream_id

    @property
    def user_id(self) -> str:
        return self.conn_info.user_id

    @property
    def websocket(self) -> WebSocket:
        return self.conn_info.websocket

    async def __aenter__(self) -> "ManagedWebSocketConnection":
        """Start background tasks for heartbeat and session checking."""
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._session_check_task = asyncio.create_task(self._session_check_loop())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Clean up connection and background tasks."""
        self._closed = True

        # Cancel background tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                # Expected when cancelling the heartbeat task during cleanup
                pass

        if self._session_check_task:
            self._session_check_task.cancel()
            try:
                await self._session_check_task
            except asyncio.CancelledError:
                # Expected when cancelling the session check task during cleanup
                pass

        # Unregister connection
        await self.manager.unregister_connection(self.connection_id)

    async def _heartbeat_loop(self) -> None:
        """Send periodic ping to keep connection alive and detect dead connections."""
        try:
            while not self._closed:
                await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
                if self._closed:
                    break

                try:
                    # Send ping
                    await self.websocket.send_json({"type": "ping"})
                except Exception as e:
                    logger.debug(f"Heartbeat failed for {self.connection_id}: {e}")
                    break
        except asyncio.CancelledError:
            # Expected during normal shutdown or connection cleanup
            pass

    async def _session_check_loop(self) -> None:
        """Periodically revalidate session to detect expired/revoked sessions."""
        try:
            while not self._closed:
                await asyncio.sleep(WS_SESSION_REVALIDATION_INTERVAL)
                if self._closed:
                    break

                try:
                    user = await validate_session_token(self.session_token)
                    if not user:
                        logger.info(f"Session expired for connection {self.connection_id}")
                        await self.websocket.send_json({
                            "type": "error",
                            "code": "session_expired",
                            "message": "Session expired",
                        })
                        await self.websocket.close(code=4001, reason="Session expired")
                        self._closed = True
                        break

                    self.conn_info.last_session_check = datetime.now(timezone.utc)
                except Exception as e:
                    logger.warning(f"Session check failed for {self.connection_id}: {e}")
        except asyncio.CancelledError:
            # Expected during normal shutdown or connection cleanup
            pass

    async def receive_json(self) -> Optional[dict]:
        """
        Receive a JSON message from the client.

        Returns:
            Parsed JSON dict, or None if connection closed
        """
        try:
            data = await self.websocket.receive_json()
            self.manager.update_activity(self.connection_id)

            # Handle pong response
            if data.get("type") == "pong":
                return None  # Don't propagate pong to caller

            return data
        except WebSocketDisconnect:
            self._closed = True
            return None
        except Exception as e:
            logger.debug(f"Error receiving from {self.connection_id}: {e}")
            self._closed = True
            return None

    async def receive_messages(self) -> AsyncIterator[dict]:
        """
        Async iterator for receiving messages.

        Yields:
            Parsed JSON messages from the client
        """
        while not self._closed:
            message = await self.receive_json()
            if message is None:
                if self._closed:
                    break
                continue  # Skip pong or other internal messages
            yield message

    async def send_json(self, data: dict) -> bool:
        """
        Send a JSON message to the client.

        Returns:
            True if sent successfully
        """
        try:
            await self.websocket.send_json(data)
            return True
        except Exception as e:
            logger.debug(f"Error sending to {self.connection_id}: {e}")
            return False

    async def send_error(self, code: str, message: str, retry_after: Optional[int] = None) -> bool:
        """Send an error message to the client."""
        data = {"type": "error", "code": code, "message": message}
        if retry_after is not None:
            data["retry_after"] = retry_after
        return await self.send_json(data)


# Global manager instance
websocket_manager = WebSocketManager()


async def authenticate_websocket(
    websocket: WebSocket,
    stream_id: int,
    session_cookie_name: str = "vlog_session",
) -> Optional[dict]:
    """
    Authenticate a WebSocket connection using session cookie.

    Args:
        websocket: The WebSocket connection
        stream_id: The stream ID for logging
        session_cookie_name: Name of the session cookie

    Returns:
        User dict if authenticated, None otherwise
    """
    # Get session token from cookies
    session_token = websocket.cookies.get(session_cookie_name)

    if not session_token:
        logger.debug(f"No session cookie for WebSocket to stream {stream_id}")
        return None

    # Validate session
    user = await validate_session_token(session_token)

    if not user:
        logger.debug(f"Invalid session for WebSocket to stream {stream_id}")
        return None

    return user


def get_client_ip(websocket: WebSocket) -> str:
    """
    Extract client IP from WebSocket connection.

    Security: X-Forwarded-For is only trusted when the direct client IP is in TRUSTED_PROXIES.
    This prevents attackers from spoofing the header to bypass IP-based rate limiting.

    Returns:
        Client IP address, or "unknown" if unable to determine (ensures rate limiting
        never receives None which could cause bypass or errors).
    """
    # Get direct client IP first
    direct_ip = websocket.client.host if websocket.client else None

    if not direct_ip:
        logger.warning("WebSocket connection has no client IP information")
        return "unknown"

    # Only trust X-Forwarded-For if request came from a trusted proxy
    if TRUSTED_PROXIES and direct_ip in TRUSTED_PROXIES:
        forwarded = websocket.headers.get("x-forwarded-for", "").strip()
        if forwarded:
            # X-Forwarded-For can contain multiple IPs: client, proxy1, proxy2, ...
            # The first one is the original client
            first_ip = forwarded.split(",")[0].strip()
            # Basic validation: must look like an IP address (contains . or :)
            if first_ip and ("." in first_ip or ":" in first_ip):
                return first_ip
            else:
                logger.warning(
                    f"Malformed X-Forwarded-For header: {forwarded[:50]}, using direct IP"
                )

    return direct_ip
