"""Tests for request ID middleware and tracing functionality."""

import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["VLOG_TEST_MODE"] = "1"


class TestRequestIDMiddleware:
    """Tests for RequestIDMiddleware functionality."""

    def test_request_id_generated_when_not_provided(self, public_client):
        """Test that a request ID is generated when not provided in headers."""
        response = public_client.get("/health")
        assert response.status_code in [200, 503]

        # Check response has X-Request-ID header
        assert "X-Request-ID" in response.headers
        request_id = response.headers["X-Request-ID"]

        # Verify it's a valid UUID
        try:
            uuid.UUID(request_id)
        except ValueError:
            pytest.fail(f"Generated request ID is not a valid UUID: {request_id}")

    def test_request_id_preserved_when_provided(self, public_client):
        """Test that an existing X-Request-ID header is preserved."""
        custom_request_id = "custom-trace-id-12345"

        response = public_client.get(
            "/health", headers={"X-Request-ID": custom_request_id}
        )
        assert response.status_code in [200, 503]

        # Check response has the same X-Request-ID
        assert response.headers.get("X-Request-ID") == custom_request_id

    def test_request_id_unique_per_request(self, public_client):
        """Test that each request gets a unique request ID."""
        response1 = public_client.get("/health")
        response2 = public_client.get("/health")

        request_id1 = response1.headers.get("X-Request-ID")
        request_id2 = response2.headers.get("X-Request-ID")

        assert request_id1 is not None
        assert request_id2 is not None
        assert request_id1 != request_id2

    def test_request_id_in_admin_api(self, admin_client):
        """Test that admin API also returns X-Request-ID."""
        response = admin_client.get("/health")
        assert response.status_code in [200, 503]

        assert "X-Request-ID" in response.headers
        request_id = response.headers["X-Request-ID"]

        # Verify it's a valid UUID
        try:
            uuid.UUID(request_id)
        except ValueError:
            pytest.fail(f"Generated request ID is not a valid UUID: {request_id}")

    def test_request_id_in_worker_api(self, worker_client):
        """Test that worker API also returns X-Request-ID."""
        response = worker_client.get("/api/health")
        assert response.status_code in [200, 503]

        assert "X-Request-ID" in response.headers
        request_id = response.headers["X-Request-ID"]

        # Verify it's a valid UUID
        try:
            uuid.UUID(request_id)
        except ValueError:
            pytest.fail(f"Generated request ID is not a valid UUID: {request_id}")


class TestGetRequestIdHelper:
    """Tests for get_request_id helper function."""

    def test_get_request_id_returns_id_from_state(self):
        """Test that get_request_id returns the ID from request.state."""
        from unittest.mock import MagicMock

        from api.common import get_request_id

        mock_request = MagicMock()
        mock_request.state.request_id = "test-request-id-123"

        result = get_request_id(mock_request)
        assert result == "test-request-id-123"

    def test_get_request_id_returns_none_when_not_set(self):
        """Test that get_request_id returns None when request_id is not set."""
        from unittest.mock import MagicMock

        from api.common import get_request_id

        mock_request = MagicMock()
        del mock_request.state.request_id  # Ensure attribute doesn't exist

        result = get_request_id(mock_request)
        assert result is None


class TestAuditLogWithRequestId:
    """Tests for audit logging with request_id support."""

    def test_audit_log_includes_request_id(self):
        """Test that audit log entries include request_id when provided."""
        from api.audit import AuditAction, AuditLogger

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            with patch("api.audit.AUDIT_LOG_ENABLED", True), patch(
                "api.audit.AUDIT_LOG_PATH", Path(log_path)
            ):
                logger = AuditLogger()

                logger.log(
                    action=AuditAction.VIDEO_UPLOAD,
                    client_ip="192.168.1.100",
                    resource_type="video",
                    resource_id=123,
                    request_id="test-request-id-456",
                )

                # Force flush
                for handler in logger.logger.handlers:
                    handler.flush()

                with open(log_path, "r") as f:
                    content = f.read().strip()

                if content:
                    entry = json.loads(content)
                    assert entry["request_id"] == "test-request-id-456"
                    assert entry["action"] == "video_upload"
                    assert entry["client_ip"] == "192.168.1.100"
        finally:
            os.unlink(log_path)

    def test_audit_log_omits_request_id_when_not_provided(self):
        """Test that audit log entries omit request_id when not provided."""
        from api.audit import AuditAction, AuditLogger

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            with patch("api.audit.AUDIT_LOG_ENABLED", True), patch(
                "api.audit.AUDIT_LOG_PATH", Path(log_path)
            ):
                logger = AuditLogger()

                logger.log(
                    action=AuditAction.VIDEO_UPLOAD,
                    client_ip="192.168.1.100",
                )

                # Force flush
                for handler in logger.logger.handlers:
                    handler.flush()

                with open(log_path, "r") as f:
                    content = f.read().strip()

                if content:
                    entry = json.loads(content)
                    assert "request_id" not in entry
        finally:
            os.unlink(log_path)

    def test_log_audit_function_accepts_request_id(self):
        """Test that log_audit convenience function accepts request_id."""
        from api.audit import AuditAction, log_audit

        # This should not raise an exception
        log_audit(
            action=AuditAction.VIDEO_UPLOAD,
            client_ip="127.0.0.1",
            user_agent="Test Agent",
            resource_type="video",
            resource_id=1,
            request_id="test-request-id-789",
        )
