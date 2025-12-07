"""
Tests for error message truncation utilities.

Validates the truncate_error function and consistent error length limits.
"""

from api.errors import truncate_error
from config import ERROR_DETAIL_MAX_LENGTH, ERROR_SUMMARY_MAX_LENGTH


class TestTruncateError:
    """Tests for the truncate_error function."""

    def test_truncate_error_short_message(self):
        """Test that short messages are not truncated."""
        msg = "Short error"
        result = truncate_error(msg)
        assert result == msg

    def test_truncate_error_exact_length(self):
        """Test that messages at exact max length are not truncated."""
        msg = "a" * ERROR_DETAIL_MAX_LENGTH
        result = truncate_error(msg)
        assert result == msg
        assert len(result) == ERROR_DETAIL_MAX_LENGTH

    def test_truncate_error_long_message(self):
        """Test that long messages are truncated with ellipsis."""
        msg = "a" * (ERROR_DETAIL_MAX_LENGTH + 100)
        result = truncate_error(msg)
        assert result.endswith("...")
        assert len(result) == ERROR_DETAIL_MAX_LENGTH
        # Should be max_length - 3 chars + "..." = max_length total
        assert result == "a" * (ERROR_DETAIL_MAX_LENGTH - 3) + "..."

    def test_truncate_error_empty_string(self):
        """Test that empty string is handled."""
        result = truncate_error("")
        assert result == ""

    def test_truncate_error_custom_length(self):
        """Test truncation with custom max length."""
        msg = "a" * 200
        max_len = 50
        result = truncate_error(msg, max_len)
        assert len(result) == max_len
        assert result.endswith("...")
        assert result == "a" * (max_len - 3) + "..."

    def test_truncate_error_summary_length(self):
        """Test truncation with ERROR_SUMMARY_MAX_LENGTH."""
        msg = "a" * (ERROR_SUMMARY_MAX_LENGTH + 50)
        result = truncate_error(msg, ERROR_SUMMARY_MAX_LENGTH)
        assert len(result) == ERROR_SUMMARY_MAX_LENGTH
        assert result.endswith("...")

    def test_truncate_error_preserves_content(self):
        """Test that truncation preserves the beginning of the message."""
        msg = "Error occurred while processing file.mp4 at line 123"
        result = truncate_error(msg, 20)
        assert result.startswith("Error occurred wh")
        assert result.endswith("...")
        assert len(result) == 20


class TestErrorConstants:
    """Tests for error message length constants."""

    def test_error_summary_max_length(self):
        """Test that ERROR_SUMMARY_MAX_LENGTH has expected value."""
        assert ERROR_SUMMARY_MAX_LENGTH == 100

    def test_error_detail_max_length(self):
        """Test that ERROR_DETAIL_MAX_LENGTH has expected value."""
        assert ERROR_DETAIL_MAX_LENGTH == 500

    def test_summary_less_than_detail(self):
        """Test that summary length is less than detail length."""
        assert ERROR_SUMMARY_MAX_LENGTH < ERROR_DETAIL_MAX_LENGTH
