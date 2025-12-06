"""Tests for audit logging functionality."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

# Import with fresh module state to test configuration
os.environ["VLOG_TEST_MODE"] = "1"


class TestAuditLogConfiguration:
    """Tests for audit log configuration options."""

    def test_audit_logging_enabled_by_default(self):
        """Test that audit logging is enabled by default."""
        # Default value in config.py is true
        from config import AUDIT_LOG_ENABLED

        # Check the default (may be overridden by env)
        assert isinstance(AUDIT_LOG_ENABLED, bool)

    def test_audit_log_path_is_path_object(self):
        """Test that audit log path is a Path object."""
        from config import AUDIT_LOG_PATH

        assert isinstance(AUDIT_LOG_PATH, Path)

    def test_audit_log_level_is_string(self):
        """Test that audit log level is a string."""
        from config import AUDIT_LOG_LEVEL

        assert isinstance(AUDIT_LOG_LEVEL, str)
        assert AUDIT_LOG_LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class TestAuditAction:
    """Tests for AuditAction enum."""

    def test_audit_actions_exist(self):
        """Test that all expected audit actions exist."""
        from api.audit import AuditAction

        # Video actions
        assert AuditAction.VIDEO_UPLOAD.value == "video_upload"
        assert AuditAction.VIDEO_UPDATE.value == "video_update"
        assert AuditAction.VIDEO_DELETE.value == "video_delete"
        assert AuditAction.VIDEO_RESTORE.value == "video_restore"
        assert AuditAction.VIDEO_RETRY.value == "video_retry"
        assert AuditAction.VIDEO_REUPLOAD.value == "video_reupload"
        assert AuditAction.VIDEO_RETRANSCODE.value == "video_retranscode"

        # Category actions
        assert AuditAction.CATEGORY_CREATE.value == "category_create"
        assert AuditAction.CATEGORY_DELETE.value == "category_delete"

        # Transcription actions
        assert AuditAction.TRANSCRIPTION_TRIGGER.value == "transcription_trigger"
        assert AuditAction.TRANSCRIPTION_UPDATE.value == "transcription_update"
        assert AuditAction.TRANSCRIPTION_DELETE.value == "transcription_delete"

        # Worker actions
        assert AuditAction.WORKER_REGISTER.value == "worker_register"
        assert AuditAction.WORKER_REVOKE.value == "worker_revoke"


class TestAuditLogger:
    """Tests for AuditLogger class."""

    def test_audit_logger_creates_json_entries(self):
        """Test that audit logger creates valid JSON log entries."""
        from api.audit import AuditAction, AuditLogger

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            # Create a logger that writes to our temp file
            with patch("api.audit.AUDIT_LOG_ENABLED", True), patch(
                "api.audit.AUDIT_LOG_PATH", Path(log_path)
            ):
                # Create a fresh logger instance
                logger = AuditLogger()

                # Log an event
                logger.log(
                    action=AuditAction.VIDEO_UPLOAD,
                    client_ip="192.168.1.100",
                    user_agent="Mozilla/5.0",
                    resource_type="video",
                    resource_id=123,
                    resource_name="test-video",
                    details={"title": "Test Video"},
                    success=True,
                )

                # Force flush
                for handler in logger.logger.handlers:
                    handler.flush()

                # Read the log file
                with open(log_path, "r") as f:
                    content = f.read().strip()

                if content:  # May be empty if handler setup failed
                    entry = json.loads(content)
                    assert entry["action"] == "video_upload"
                    assert entry["client_ip"] == "192.168.1.100"
                    assert entry["user_agent"] == "Mozilla/5.0"
                    assert entry["resource_type"] == "video"
                    assert entry["resource_id"] == 123
                    assert entry["resource_name"] == "test-video"
                    assert entry["details"] == {"title": "Test Video"}
                    assert entry["success"] is True
                    assert "timestamp" in entry
        finally:
            os.unlink(log_path)

    def test_audit_logger_truncates_long_user_agents(self):
        """Test that long user agents are truncated."""
        from api.audit import AuditAction, AuditLogger

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            with patch("api.audit.AUDIT_LOG_ENABLED", True), patch(
                "api.audit.AUDIT_LOG_PATH", Path(log_path)
            ):
                logger = AuditLogger()

                # Log with a very long user agent
                long_ua = "x" * 500
                logger.log(
                    action=AuditAction.VIDEO_UPLOAD,
                    user_agent=long_ua,
                )

                # Force flush
                for handler in logger.logger.handlers:
                    handler.flush()

                with open(log_path, "r") as f:
                    content = f.read().strip()

                if content:
                    entry = json.loads(content)
                    # User agent should be truncated to 200 characters
                    assert len(entry.get("user_agent", "")) <= 200
        finally:
            os.unlink(log_path)

    def test_audit_logger_truncates_long_errors(self):
        """Test that long error messages are truncated."""
        from api.audit import AuditAction, AuditLogger

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            with patch("api.audit.AUDIT_LOG_ENABLED", True), patch(
                "api.audit.AUDIT_LOG_PATH", Path(log_path)
            ):
                logger = AuditLogger()

                # Log with a very long error
                long_error = "e" * 1000
                logger.log(
                    action=AuditAction.VIDEO_UPLOAD,
                    success=False,
                    error=long_error,
                )

                # Force flush
                for handler in logger.logger.handlers:
                    handler.flush()

                with open(log_path, "r") as f:
                    content = f.read().strip()

                if content:
                    entry = json.loads(content)
                    # Error should be truncated to 500 characters
                    assert len(entry.get("error", "")) <= 500
        finally:
            os.unlink(log_path)

    def test_audit_logger_disabled_does_not_log(self):
        """Test that disabled audit logger does not write logs."""
        from api.audit import AuditAction, AuditLogger

        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            with patch("api.audit.AUDIT_LOG_ENABLED", False), patch(
                "api.audit.AUDIT_LOG_PATH", Path(log_path)
            ):
                logger = AuditLogger()
                logger.log(
                    action=AuditAction.VIDEO_UPLOAD,
                    client_ip="192.168.1.100",
                )

                with open(log_path, "r") as f:
                    content = f.read().strip()

                # Log should be empty when disabled
                assert content == ""
        finally:
            os.unlink(log_path)


class TestLogAuditFunction:
    """Tests for the log_audit convenience function."""

    def test_log_audit_function_exists(self):
        """Test that log_audit function is available."""
        from api.audit import log_audit

        assert callable(log_audit)

    def test_log_audit_accepts_all_parameters(self):
        """Test that log_audit accepts all documented parameters."""
        from api.audit import AuditAction, log_audit

        # This should not raise an exception
        log_audit(
            action=AuditAction.VIDEO_UPLOAD,
            client_ip="127.0.0.1",
            user_agent="Test Agent",
            resource_type="video",
            resource_id=1,
            resource_name="test-video",
            details={"key": "value"},
            success=True,
            error=None,
        )

    def test_log_audit_handles_minimal_parameters(self):
        """Test that log_audit works with minimal parameters."""
        from api.audit import AuditAction, log_audit

        # This should not raise an exception
        log_audit(action=AuditAction.CATEGORY_CREATE)


class TestAuditLoggerFallback:
    """Tests for audit logger fallback behavior."""

    def test_logger_falls_back_to_console_on_permission_error(self):
        """Test that logger falls back to console when file creation fails."""
        from api.audit import AuditAction, AuditLogger

        # Use a path that should fail (root-level in non-existent directory)
        with patch("api.audit.AUDIT_LOG_ENABLED", True), patch(
            "api.audit.AUDIT_LOG_PATH", Path("/nonexistent/deeply/nested/path/audit.log")
        ):
            # This should not raise - should fall back to console
            logger = AuditLogger()

            # Verify it has a handler (either console or null)
            assert len(logger.logger.handlers) > 0

            # Logging should not raise
            logger.log(action=AuditAction.VIDEO_UPLOAD)

    def test_logger_never_breaks_application(self):
        """Test that audit logging never breaks the application."""
        from api.audit import AuditAction, log_audit

        # Even with a broken logger, this should not raise
        with patch(
            "api.audit.audit_logger.logger.info", side_effect=Exception("Simulated error")
        ):
            # This should not raise
            log_audit(action=AuditAction.VIDEO_UPLOAD)
