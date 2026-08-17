"""
Tests for structured JSON logging configuration (Issue #208).
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


class TestSanitizeUserAgent:
    """Test User-Agent sanitization for log injection prevention."""

    def test_normal_user_agent_unchanged(self):
        """Normal User-Agent strings should pass through unchanged."""
        from api.logging_config import sanitize_user_agent

        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        assert sanitize_user_agent(ua) == ua

    def test_removes_control_characters(self):
        """Control characters should be stripped to prevent log injection."""
        from api.logging_config import sanitize_user_agent

        # Null byte
        assert sanitize_user_agent("Mozilla\x00evil") == "Mozillaevil"
        # Newline (log injection)
        assert sanitize_user_agent("Mozilla\nevil") == "Mozillaevil"
        # Carriage return (log injection)
        assert sanitize_user_agent("Mozilla\revil") == "Mozillaevil"
        # Tab
        assert sanitize_user_agent("Mozilla\tevil") == "Mozillaevil"
        # DEL character
        assert sanitize_user_agent("Mozilla\x7fevil") == "Mozillaevil"
        # Multiple control chars
        assert sanitize_user_agent("A\x00\x01\x1f\x7fB") == "AB"

    def test_crlf_injection_prevented(self):
        """CRLF injection attempts should be neutralized."""
        from api.logging_config import sanitize_user_agent

        # Classic log injection attempt
        malicious = "Mozilla\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: text/html"
        result = sanitize_user_agent(malicious)
        assert "\r" not in result
        assert "\n" not in result
        assert result == "MozillaHTTP/1.1 200 OKContent-Type: text/html"

    def test_truncates_long_user_agents(self):
        """Very long User-Agent strings should be truncated."""
        from api.logging_config import sanitize_user_agent

        long_ua = "a" * 1000
        result = sanitize_user_agent(long_ua)
        assert len(result) == 512  # Default max length
        assert result == "a" * 512

    def test_custom_max_length(self):
        """Custom max_length should be respected."""
        from api.logging_config import sanitize_user_agent

        ua = "a" * 100
        assert len(sanitize_user_agent(ua, max_length=50)) == 50
        assert len(sanitize_user_agent(ua, max_length=200)) == 100  # Original length

    def test_none_returns_empty_string(self):
        """None input should return empty string."""
        from api.logging_config import sanitize_user_agent

        assert sanitize_user_agent(None) == ""

    def test_empty_string_returns_empty_string(self):
        """Empty string input should return empty string."""
        from api.logging_config import sanitize_user_agent

        assert sanitize_user_agent("") == ""

    def test_whitespace_only(self):
        """Whitespace-only input should be preserved (spaces are not control chars)."""
        from api.logging_config import sanitize_user_agent

        assert sanitize_user_agent("   ") == "   "

    def test_unicode_preserved(self):
        """Unicode characters should be preserved."""
        from api.logging_config import sanitize_user_agent

        ua = "Mozilla/5.0 (日本語) WebKit"
        assert sanitize_user_agent(ua) == ua


class TestSafeJSONEncoder:
    """Test SafeJSONEncoder handles non-serializable objects gracefully."""

    def test_datetime_serialization(self):
        """Datetime objects should be serialized to ISO format."""
        from datetime import datetime, timezone

        from api.logging_config import SafeJSONEncoder

        encoder = SafeJSONEncoder()
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = encoder.default(dt)
        assert result == "2024-01-15T10:30:00+00:00"

    def test_path_serialization(self):
        """Path objects should be serialized to strings."""
        from api.logging_config import SafeJSONEncoder

        encoder = SafeJSONEncoder()
        result = encoder.default(Path("/tmp/test.log"))
        assert result == "/tmp/test.log"

    def test_bytes_serialization(self):
        """Bytes should be replaced with placeholder."""
        from api.logging_config import SafeJSONEncoder

        encoder = SafeJSONEncoder()
        assert encoder.default(b"binary data") == "<binary data>"
        assert encoder.default(bytearray(b"more binary")) == "<binary data>"

    def test_object_with_dict_serialization(self):
        """Objects with __dict__ should be converted to string."""
        from api.logging_config import SafeJSONEncoder

        class CustomObject:
            def __init__(self):
                self.value = 42

            def __str__(self):
                return "CustomObject(value=42)"

        encoder = SafeJSONEncoder()
        obj = CustomObject()
        result = encoder.default(obj)
        assert result == "CustomObject(value=42)"

    def test_unserializable_fallback(self):
        """Truly unserializable objects should get a type placeholder or string repr."""
        from api.logging_config import SafeJSONEncoder

        encoder = SafeJSONEncoder()

        # Lambda has __dict__ so it gets str() representation
        result = encoder.default(lambda x: x)
        # Should contain function info (str representation of lambda)
        assert "function" in result or "lambda" in result


class TestSetupLoggingIdempotency:
    """Test that setup_logging is idempotent."""

    def test_multiple_calls_are_idempotent(self):
        """Multiple calls to setup_logging should not reconfigure."""
        import api.logging_config as log_config

        # Reset the flag for testing
        original_flag = log_config._logging_configured
        log_config._logging_configured = False

        try:
            with patch.dict(os.environ, {"VLOG_TEST_MODE": "1", "VLOG_LOG_FORMAT": "text"}):
                # First call should configure
                log_config.setup_logging(log_format="text")
                assert log_config._logging_configured is True

                # Get handler count after first setup
                root = logging.getLogger()
                handler_count_after_first = len(root.handlers)

                # Second call should be a no-op
                log_config.setup_logging(log_format="json")  # Different format

                # Handler count should be the same (no duplicate handlers)
                assert len(root.handlers) == handler_count_after_first
        finally:
            # Restore original state
            log_config._logging_configured = original_flag


class TestSecureRotatingFileHandler:
    """Test SecureRotatingFileHandler sets correct permissions."""

    def test_file_permissions_are_restrictive(self):
        """Log files should be created with 0600 permissions."""
        from api.logging_config import SecureRotatingFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"

            handler = SecureRotatingFileHandler(
                filename=str(log_file),
                maxBytes=1024,
                backupCount=1,
            )

            # Write something to create the file
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="test message",
                args=(),
                exc_info=None,
            )
            handler.emit(record)
            handler.close()

            # Check permissions (owner read/write only)
            mode = log_file.stat().st_mode & 0o777
            assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


class TestVLogJsonFormatter:
    """Test VLogJsonFormatter output format."""

    def test_json_output_contains_required_fields(self):
        """JSON log output should contain timestamp, level, logger, message."""
        from api.logging_config import VLogJsonFormatter

        formatter = VLogJsonFormatter()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        log_data = json.loads(output)

        assert "timestamp" in log_data
        assert log_data["level"] == "INFO"
        assert log_data["logger"] == "test.logger"
        assert log_data["message"] == "Test message"

    def test_format_failure_returns_fallback_json(self):
        """If formatting fails, a fallback JSON should be returned."""
        from api.logging_config import VLogJsonFormatter

        formatter = VLogJsonFormatter()

        # Create a record that might cause issues
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test %s message",
            args=("formatted",),
            exc_info=None,
        )

        # Force a failure by making the parent format raise
        with patch.object(formatter.__class__.__bases__[0], "format", side_effect=Exception("format error")):
            output = formatter.format(record)

        # Should still be valid JSON
        log_data = json.loads(output)
        assert "Failed to format log record" in log_data["message"]
        assert log_data["original_message"] == "Test formatted message"


class TestRequestContext:
    """Test request context management."""

    def test_set_and_clear_context(self):
        """Context should be settable and clearable."""
        from api.logging_config import (
            clear_request_context,
            client_ip_var,
            request_id_var,
            set_request_context,
            user_agent_var,
        )

        # Initially None
        assert request_id_var.get() is None

        # Set context
        set_request_context(
            request_id="test-123",
            client_ip="192.168.1.1",
            user_agent="TestBrowser",
        )

        assert request_id_var.get() == "test-123"
        assert client_ip_var.get() == "192.168.1.1"
        assert user_agent_var.get() == "TestBrowser"

        # Clear context
        clear_request_context()

        assert request_id_var.get() is None
        assert client_ip_var.get() is None
        assert user_agent_var.get() is None
