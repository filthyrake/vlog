"""
Tests for the CLI module error handling and response validation.
"""
import os
from pathlib import Path
from unittest import mock

import pytest

# Import CLI functions and classes
from cli.main import CLIError, safe_json_response, validate_file


class TestCLIError:
    """Test the custom CLIError exception."""

    def test_cli_error_can_be_raised(self):
        """Test that CLIError can be instantiated and raised."""
        with pytest.raises(CLIError) as exc_info:
            raise CLIError("Test error")
        assert str(exc_info.value) == "Test error"


class TestSafeJsonResponse:
    """Test the safe_json_response function."""

    def test_successful_json_response(self):
        """Test parsing a successful JSON response."""
        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {"id": 1, "name": "test"}

        result = safe_json_response(mock_response)
        assert result == {"id": 1, "name": "test"}

    def test_successful_non_json_response_raises_error(self):
        """Test that a successful non-JSON response raises CLIError."""
        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "Not JSON content"

        with pytest.raises(CLIError) as exc_info:
            safe_json_response(mock_response)
        assert "Invalid JSON response" in str(exc_info.value)
        assert "Not JSON content" in str(exc_info.value)

    def test_error_response_with_json_detail(self):
        """Test parsing an error response with JSON detail."""
        mock_response = mock.Mock()
        mock_response.is_success = False
        mock_response.status_code = 404
        mock_response.json.return_value = {"detail": "Not found"}
        mock_response.text = '{"detail": "Not found"}'

        with pytest.raises(CLIError) as exc_info:
            safe_json_response(mock_response)
        assert "API error (404)" in str(exc_info.value)
        assert "Not found" in str(exc_info.value)

    def test_error_response_with_json_no_detail(self):
        """Test parsing an error response with JSON but no detail field."""
        mock_response = mock.Mock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "Server error"}
        mock_response.text = '{"error": "Server error"}'

        with pytest.raises(CLIError) as exc_info:
            safe_json_response(mock_response)
        assert "API error (500)" in str(exc_info.value)
        # Should fall back to response.text
        assert "Server error" in str(exc_info.value)

    def test_error_response_non_json(self):
        """Test parsing an error response that's not JSON (e.g., HTML)."""
        mock_response = mock.Mock()
        mock_response.is_success = False
        mock_response.status_code = 502
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "<html><body>Bad Gateway</body></html>"

        with pytest.raises(CLIError) as exc_info:
            safe_json_response(mock_response)
        assert "API error (502)" in str(exc_info.value)
        assert "Bad Gateway" in str(exc_info.value)

    def test_error_response_empty_text(self):
        """Test parsing an error response with empty text."""
        mock_response = mock.Mock()
        mock_response.is_success = False
        mock_response.status_code = 503
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = ""

        with pytest.raises(CLIError) as exc_info:
            safe_json_response(mock_response, default_error="Service unavailable")
        assert "API error (503)" in str(exc_info.value)
        assert "Service unavailable" in str(exc_info.value)

    def test_error_response_long_text_truncated(self):
        """Test that long error text is truncated."""
        mock_response = mock.Mock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "x" * 300  # 300 characters

        with pytest.raises(CLIError) as exc_info:
            safe_json_response(mock_response)
        # Should be truncated to exactly 200 chars
        assert len(str(exc_info.value).split(": ", 1)[1]) == 200


class TestValidateFile:
    """Test the validate_file function."""

    def test_valid_file(self, tmp_path):
        """Test validation of a valid file."""
        test_file = tmp_path / "test_video.mp4"
        test_file.write_bytes(b"test content")

        file_size = validate_file(test_file)
        assert file_size == 12  # "test content" is 12 bytes

    def test_file_not_found(self, tmp_path):
        """Test validation of non-existent file."""
        test_file = tmp_path / "nonexistent.mp4"

        with pytest.raises(CLIError) as exc_info:
            validate_file(test_file)
        assert "File not found" in str(exc_info.value)

    def test_path_is_directory(self, tmp_path):
        """Test validation when path is a directory."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()

        with pytest.raises(CLIError) as exc_info:
            validate_file(test_dir)
        assert "Path is not a file" in str(exc_info.value)

    def test_file_not_readable(self, tmp_path):
        """Test validation when file is not readable."""
        test_file = tmp_path / "test_video.mp4"
        test_file.write_bytes(b"test content")

        # Mock os.access to return False
        with mock.patch("os.access", return_value=False):
            with pytest.raises(CLIError) as exc_info:
                validate_file(test_file)
            assert "File is not readable" in str(exc_info.value)

    def test_empty_file(self, tmp_path):
        """Test validation of empty file."""
        test_file = tmp_path / "empty.mp4"
        test_file.touch()

        with pytest.raises(CLIError) as exc_info:
            validate_file(test_file)
        assert "File is empty" in str(exc_info.value)

    def test_large_file_warning(self, tmp_path, capsys):
        """Test that large files generate a warning."""
        test_file = tmp_path / "large.mp4"
        test_file.write_bytes(b"x")

        # Get real stat to copy attributes from
        real_stat = test_file.stat()

        # Mock Path.stat to return a stat result with large size
        mock_stat = mock.Mock()
        # Copy specific stat attributes needed by validate_file
        mock_stat.st_mode = real_stat.st_mode
        mock_stat.st_size = 11 * 1024 * 1024 * 1024  # 11GB

        with mock.patch.object(Path, 'stat', return_value=mock_stat):
            file_size = validate_file(test_file)
            captured = capsys.readouterr()
            assert "Warning: Large file detected" in captured.out
            assert "11.00 GB" in captured.out
            assert file_size == 11 * 1024 * 1024 * 1024


class TestTimeoutConfiguration:
    """Test that timeout constants are properly configured."""

    def test_default_api_timeout_is_set(self):
        """Test that DEFAULT_API_TIMEOUT is configured."""
        from cli.main import DEFAULT_API_TIMEOUT
        assert DEFAULT_API_TIMEOUT > 0
        assert DEFAULT_API_TIMEOUT == 30  # Default value

    def test_download_timeout_is_set(self):
        """Test that DOWNLOAD_TIMEOUT is configured."""
        from cli.main import DOWNLOAD_TIMEOUT
        assert DOWNLOAD_TIMEOUT > 0
        assert DOWNLOAD_TIMEOUT == 3600  # Default value

    def test_api_timeout_env_var(self):
        """Test that API timeout can be configured via environment variable."""
        import importlib

        import cli.main

        try:
            with mock.patch.dict(os.environ, {"VLOG_API_TIMEOUT": "60"}):
                importlib.reload(cli.main)
                from cli.main import DEFAULT_API_TIMEOUT
                assert DEFAULT_API_TIMEOUT == 60
        finally:
            # Restore original state
            importlib.reload(cli.main)


class TestAPIBaseConfiguration:
    """Test that API_BASE URL is properly configured."""

    def test_default_api_base(self):
        """Test that API_BASE uses default localhost."""
        from cli.main import API_BASE
        assert "localhost" in API_BASE or "127.0.0.1" in API_BASE
        assert "/api" in API_BASE

    def test_api_base_env_var(self):
        """Test that API_BASE can be configured via environment variable."""
        import importlib

        import cli.main

        try:
            with mock.patch.dict(os.environ, {"VLOG_ADMIN_API_URL": "http://example.com:8080"}):
                importlib.reload(cli.main)
                from cli.main import API_BASE
                assert API_BASE == "http://example.com:8080/api"
        finally:
            # Restore original state
            importlib.reload(cli.main)

    def test_api_base_strips_trailing_slash(self):
        """Test that trailing slashes are handled correctly."""
        import importlib

        import cli.main

        try:
            with mock.patch.dict(os.environ, {"VLOG_ADMIN_API_URL": "http://example.com:8080/"}):
                importlib.reload(cli.main)
                from cli.main import API_BASE
                assert API_BASE == "http://example.com:8080/api"
        finally:
            # Restore original state
            importlib.reload(cli.main)


class TestHTTPErrorHandling:
    """Test HTTP error scenarios in CLI commands."""

    @pytest.mark.skip(reason="Integration test - to be implemented")
    def test_connect_error_handling(self):
        """Test that ConnectError is handled gracefully."""
        # This is more of an integration test and would be tested
        # by mocking httpx calls in the actual command functions
        pass

    @pytest.mark.skip(reason="Integration test - to be implemented")
    def test_timeout_error_handling(self):
        """Test that TimeoutException is handled gracefully."""
        # This is more of an integration test and would be tested
        # by mocking httpx calls in the actual command functions
        pass
