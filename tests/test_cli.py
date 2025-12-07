"""
Tests for the CLI module error handling and response validation.
"""

import os
from pathlib import Path
from unittest import mock

import httpx
import pytest

# Import CLI functions and classes
from cli.main import CLIError, ProgressFileWrapper, safe_json_response, validate_file, validate_url


class TestProgressFileWrapper:
    """Test the ProgressFileWrapper class for upload progress tracking."""

    def test_wrapper_reads_and_updates_progress(self, tmp_path):
        """Test that ProgressFileWrapper reads data and updates progress."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"Hello, World!")

        # Mock progress and task_id
        mock_progress = mock.Mock()
        task_id = 1

        with open(test_file, "rb") as f:
            wrapper = ProgressFileWrapper(f, mock_progress, task_id)

            # Read some data
            data = wrapper.read(5)
            assert data == b"Hello"
            mock_progress.update.assert_called_once_with(task_id, advance=5)

            # Read more data
            mock_progress.reset_mock()
            data = wrapper.read(8)
            assert data == b", World!"
            mock_progress.update.assert_called_once_with(task_id, advance=8)

    def test_wrapper_forwards_seek(self, tmp_path):
        """Test that ProgressFileWrapper forwards seek to underlying file."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"0123456789")

        mock_progress = mock.Mock()
        task_id = 1

        with open(test_file, "rb") as f:
            wrapper = ProgressFileWrapper(f, mock_progress, task_id)

            # Seek to position 5
            wrapper.seek(5)
            data = wrapper.read(5)
            assert data == b"56789"

    def test_wrapper_forwards_tell(self, tmp_path):
        """Test that ProgressFileWrapper forwards tell to underlying file."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"0123456789")

        mock_progress = mock.Mock()
        task_id = 1

        with open(test_file, "rb") as f:
            wrapper = ProgressFileWrapper(f, mock_progress, task_id)

            assert wrapper.tell() == 0
            wrapper.read(5)
            assert wrapper.tell() == 5

    def test_wrapper_context_manager(self, tmp_path):
        """Test that ProgressFileWrapper works as a context manager."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"Test content")

        mock_progress = mock.Mock()
        task_id = 1

        with open(test_file, "rb") as f:
            with ProgressFileWrapper(f, mock_progress, task_id) as wrapper:
                data = wrapper.read()
                assert data == b"Test content"

    def test_wrapper_has_close_method(self, tmp_path):
        """Test that ProgressFileWrapper has a close() method."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"Test content")

        mock_progress = mock.Mock()
        task_id = 1

        with open(test_file, "rb") as f:
            wrapper = ProgressFileWrapper(f, mock_progress, task_id)
            # Verify close method exists and can be called
            assert hasattr(wrapper, "close")
            assert callable(wrapper.close)

    def test_wrapper_empty_read_at_eof(self, tmp_path):
        """Test that empty reads at EOF don't update progress."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"data")

        mock_progress = mock.Mock()
        task_id = 1

        with open(test_file, "rb") as f:
            wrapper = ProgressFileWrapper(f, mock_progress, task_id)
            # Read all data
            data = wrapper.read()
            assert data == b"data"
            mock_progress.update.assert_called_once_with(task_id, advance=4)

            # Reset mock and try empty read at EOF
            mock_progress.reset_mock()
            data = wrapper.read()
            assert data == b""
            # Verify progress was NOT updated for empty read
            mock_progress.update.assert_not_called()


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
        mock_response.text = "x" * 600  # 600 characters

        with pytest.raises(CLIError) as exc_info:
            safe_json_response(mock_response)
        # Should be truncated to ERROR_DETAIL_MAX_LENGTH (500) with ellipsis
        error_detail = str(exc_info.value).split(": ", 1)[1]
        assert len(error_detail) == 500
        assert error_detail.endswith("...")


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

        with mock.patch.object(Path, "stat", return_value=mock_stat):
            file_size = validate_file(test_file)
            captured = capsys.readouterr()
            assert "Warning: Large file detected" in captured.out
            assert "11.00 GB" in captured.out
            assert file_size == 11 * 1024 * 1024 * 1024


class TestValidateUrl:
    """Test the validate_url function."""

    def test_valid_http_url(self):
        """Test validation of a valid HTTP URL."""
        url = "http://example.com/video"
        result = validate_url(url)
        assert result == url

    def test_valid_https_url(self):
        """Test validation of a valid HTTPS URL."""
        url = "https://youtube.com/watch?v=test123"
        result = validate_url(url)
        assert result == url

    def test_valid_url_with_port(self):
        """Test validation of URL with port number."""
        url = "https://example.com:8080/video"
        result = validate_url(url)
        assert result == url

    def test_valid_url_with_path_and_query(self):
        """Test validation of URL with path and query parameters."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share"
        result = validate_url(url)
        assert result == url

    def test_invalid_scheme_ftp(self):
        """Test that FTP URLs are rejected."""
        url = "ftp://example.com/video.mp4"
        with pytest.raises(CLIError) as exc_info:
            validate_url(url)
        assert "Invalid URL scheme" in str(exc_info.value)
        assert "ftp" in str(exc_info.value)
        assert "http or https" in str(exc_info.value)

    def test_invalid_scheme_file(self):
        """Test that file URLs are rejected."""
        url = "file:///path/to/video.mp4"
        with pytest.raises(CLIError) as exc_info:
            validate_url(url)
        assert "Invalid URL scheme" in str(exc_info.value)
        assert "file" in str(exc_info.value)

    def test_invalid_scheme_empty(self):
        """Test that URLs without scheme are rejected."""
        url = "example.com/video"
        with pytest.raises(CLIError) as exc_info:
            validate_url(url)
        assert "missing scheme" in str(exc_info.value)
        assert "http:// or https://" in str(exc_info.value)

    def test_missing_domain(self):
        """Test that URLs without domain are rejected."""
        url = "http://"
        with pytest.raises(CLIError) as exc_info:
            validate_url(url)
        assert "missing domain" in str(exc_info.value)

    def test_missing_domain_with_path(self):
        """Test that URLs with path but no domain are rejected."""
        url = "http:///path/to/video"
        with pytest.raises(CLIError) as exc_info:
            validate_url(url)
        assert "missing domain" in str(exc_info.value)

    def test_javascript_url(self):
        """Test that javascript URLs are rejected."""
        url = "javascript:alert('xss')"
        with pytest.raises(CLIError) as exc_info:
            validate_url(url)
        assert "Invalid URL scheme" in str(exc_info.value)

    def test_data_url(self):
        """Test that data URLs are rejected."""
        url = "data:text/html,<script>alert('xss')</script>"
        with pytest.raises(CLIError) as exc_info:
            validate_url(url)
        assert "Invalid URL scheme" in str(exc_info.value)


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

    def test_upload_timeout_is_set(self):
        """Test that UPLOAD_TIMEOUT is configured."""
        from cli.main import UPLOAD_TIMEOUT

        assert UPLOAD_TIMEOUT > 0
        assert UPLOAD_TIMEOUT == 7200  # Default value (2 hours)

    def test_upload_timeout_env_var(self):
        """Test that upload timeout can be configured via environment variable."""
        import importlib

        import cli.main

        try:
            with mock.patch.dict(os.environ, {"VLOG_UPLOAD_TIMEOUT": "14400"}):
                importlib.reload(cli.main)
                assert cli.main.UPLOAD_TIMEOUT == 14400  # 4 hours
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


class TestCmdList:
    """Test the cmd_list command."""

    def test_list_videos_success(self, capsys):
        """Test listing videos successfully."""
        from cli.main import cmd_list

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {
                "id": 1,
                "title": "Test Video",
                "slug": "test-video",
                "status": "ready",
                "category_name": "Test Cat",
            },
            {
                "id": 2,
                "title": "Another Video with a Very Long Title That Will Be Truncated",
                "slug": "another-video",
                "status": "processing",
                "category_name": None,
            },
        ]

        args = mock.Mock()
        args.status = None
        args.archived = False

        with mock.patch("httpx.get", return_value=mock_response):
            cmd_list(args)

        captured = capsys.readouterr()
        assert "Test Video" in captured.out
        assert "ready" in captured.out
        assert "Another Video" in captured.out
        assert "processing" in captured.out

    def test_list_videos_empty(self, capsys):
        """Test listing videos when none exist."""
        from cli.main import cmd_list

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = []

        args = mock.Mock()
        args.status = None
        args.archived = False

        with mock.patch("httpx.get", return_value=mock_response):
            cmd_list(args)

        captured = capsys.readouterr()
        assert "No videos found" in captured.out

    def test_list_videos_with_status_filter(self):
        """Test listing videos with status filter."""
        from cli.main import cmd_list

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = []

        args = mock.Mock()
        args.status = "pending"
        args.archived = False

        with mock.patch("httpx.get", return_value=mock_response) as mock_get:
            cmd_list(args)
            # Verify the status filter is passed
            call_args = mock_get.call_args
            assert call_args[1]["params"]["status"] == "pending"

    def test_list_videos_connection_error(self, capsys):
        """Test handling of connection errors in list command."""

        import httpx

        from cli.main import cmd_list

        args = mock.Mock()
        args.status = None
        args.archived = False

        with mock.patch("httpx.get", side_effect=httpx.ConnectError("Connection refused")):
            with pytest.raises(SystemExit) as exc_info:
                cmd_list(args)
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Could not connect" in captured.out

    def test_list_videos_timeout(self, capsys):
        """Test handling of timeout errors in list command."""
        import httpx

        from cli.main import cmd_list

        args = mock.Mock()
        args.status = None
        args.archived = False

        with mock.patch("httpx.get", side_effect=httpx.TimeoutException("Timeout")):
            with pytest.raises(SystemExit) as exc_info:
                cmd_list(args)
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "timed out" in captured.out

    def test_list_videos_api_error(self, capsys):
        """Test handling of API errors in list command."""
        from cli.main import cmd_list

        mock_response = mock.Mock()
        mock_response.is_success = False
        mock_response.status_code = 500
        mock_response.json.return_value = {"detail": "Internal server error"}
        mock_response.text = '{"detail": "Internal server error"}'

        args = mock.Mock()
        args.status = None
        args.archived = False

        with mock.patch("httpx.get", return_value=mock_response):
            with pytest.raises(SystemExit) as exc_info:
                cmd_list(args)
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Internal server error" in captured.out

    def test_list_archived_videos_success(self, capsys):
        """Test listing archived videos successfully."""
        from cli.main import cmd_list

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "videos": [
                {
                    "id": 1,
                    "title": "Deleted Video",
                    "slug": "deleted-video",
                    "deleted_at": "2024-01-15T10:30:00",
                    "created_at": "2024-01-01T00:00:00",
                },
                {
                    "id": 2,
                    "title": "Another Deleted Video with a Very Long Title That Will Be Truncated",
                    "slug": "another-deleted-video-with-long-slug",
                    "deleted_at": "2024-01-16T12:00:00",
                    "created_at": "2024-01-02T00:00:00",
                },
            ],
            "total": 2,
        }

        args = mock.Mock()
        args.status = None
        args.archived = True

        with mock.patch("httpx.get", return_value=mock_response) as mock_get:
            cmd_list(args)
            # Verify the archived endpoint is called
            call_args = mock_get.call_args
            assert "/videos/archived" in call_args[0][0]

        captured = capsys.readouterr()
        assert "Archived videos (2 total)" in captured.out
        assert "Deleted Video" in captured.out
        assert "deleted-video" in captured.out
        assert "2024-01-15T10:30:00" in captured.out

    def test_list_archived_videos_empty(self, capsys):
        """Test listing archived videos when none exist."""
        from cli.main import cmd_list

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {"videos": [], "total": 0}

        args = mock.Mock()
        args.status = None
        args.archived = True

        with mock.patch("httpx.get", return_value=mock_response):
            cmd_list(args)

        captured = capsys.readouterr()
        assert "No archived videos found" in captured.out

    def test_list_archived_ignores_status_filter(self, capsys):
        """Test that --status is ignored when --archived is used."""
        from cli.main import cmd_list

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {"videos": [], "total": 0}

        args = mock.Mock()
        args.status = "pending"  # This should be ignored
        args.archived = True

        with mock.patch("httpx.get", return_value=mock_response):
            cmd_list(args)

        captured = capsys.readouterr()
        assert "Warning: --status is ignored" in captured.out
        assert "No archived videos found" in captured.out


class TestCmdCategories:
    """Test the cmd_categories command."""

    def test_list_categories_success(self, capsys):
        """Test listing categories successfully."""
        from cli.main import cmd_categories

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = [
            {"id": 1, "name": "Movies", "slug": "movies", "video_count": 10},
            {"id": 2, "name": "TV Shows", "slug": "tv-shows", "video_count": 5},
        ]

        args = mock.Mock()
        args.create = None
        args.description = None

        with mock.patch("httpx.get", return_value=mock_response):
            cmd_categories(args)

        captured = capsys.readouterr()
        assert "Movies" in captured.out
        assert "TV Shows" in captured.out
        assert "movies" in captured.out
        assert "10" in captured.out

    def test_list_categories_empty(self, capsys):
        """Test listing categories when none exist."""
        from cli.main import cmd_categories

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = []

        args = mock.Mock()
        args.create = None
        args.description = None

        with mock.patch("httpx.get", return_value=mock_response):
            cmd_categories(args)

        captured = capsys.readouterr()
        assert "No categories found" in captured.out

    def test_create_category_success(self, capsys):
        """Test creating a category successfully."""
        from cli.main import cmd_categories

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "id": 1,
            "name": "New Category",
            "slug": "new-category",
        }

        args = mock.Mock()
        args.create = "New Category"
        args.description = "A test category"

        with mock.patch("httpx.post", return_value=mock_response):
            cmd_categories(args)

        captured = capsys.readouterr()
        assert "Created category" in captured.out
        assert "New Category" in captured.out
        assert "new-category" in captured.out


class TestCmdDelete:
    """Test the cmd_delete command."""

    def test_delete_video_success(self, capsys):
        """Test deleting a video successfully."""
        from cli.main import cmd_delete

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {"status": "ok"}

        args = mock.Mock()
        args.video_id = 1

        with mock.patch("httpx.delete", return_value=mock_response):
            cmd_delete(args)

        captured = capsys.readouterr()
        assert "Video 1 deleted" in captured.out

    def test_delete_video_not_found(self, capsys):
        """Test deleting a non-existent video."""
        from cli.main import cmd_delete

        mock_response = mock.Mock()
        mock_response.is_success = False
        mock_response.status_code = 404
        mock_response.json.return_value = {"detail": "Video not found"}
        mock_response.text = '{"detail": "Video not found"}'

        args = mock.Mock()
        args.video_id = 999

        with mock.patch("httpx.delete", return_value=mock_response):
            with pytest.raises(SystemExit) as exc_info:
                cmd_delete(args)
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Video not found" in captured.out


class TestCmdUpload:
    """Test the cmd_upload command."""

    def test_upload_file_not_found(self, capsys, tmp_path):
        """Test upload with non-existent file."""
        from cli.main import cmd_upload

        args = mock.Mock()
        args.file = str(tmp_path / "nonexistent.mp4")
        args.title = "Test"
        args.description = ""
        args.category = None

        with pytest.raises(SystemExit) as exc_info:
            cmd_upload(args)
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "File not found" in captured.out

    def test_upload_success(self, capsys, tmp_path):
        """Test successful upload."""
        from cli.main import cmd_upload

        # Create a test file
        test_file = tmp_path / "test_video.mp4"
        test_file.write_bytes(b"fake video content")

        args = mock.Mock()
        args.file = str(test_file)
        args.title = "Test Video"
        args.description = "A test video"
        args.category = None

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "video_id": 1,
            "slug": "test-video",
        }

        # Mock the httpx.Client context manager
        mock_client = mock.Mock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)

        with mock.patch("httpx.Client", return_value=mock_client):
            cmd_upload(args)

        captured = capsys.readouterr()
        assert "Success" in captured.out
        assert "test-video" in captured.out

    def test_upload_with_category_lookup(self, capsys, tmp_path):
        """Test upload with category lookup."""
        from cli.main import cmd_upload

        # Create a test file
        test_file = tmp_path / "test_video.mp4"
        test_file.write_bytes(b"fake video content")

        args = mock.Mock()
        args.file = str(test_file)
        args.title = "Test Video"
        args.description = ""
        args.category = "Movies"

        # Mock category lookup
        mock_cat_response = mock.Mock()
        mock_cat_response.is_success = True
        mock_cat_response.json.return_value = [
            {"id": 1, "name": "Movies", "slug": "movies"},
        ]

        # Mock upload
        mock_upload_response = mock.Mock()
        mock_upload_response.is_success = True
        mock_upload_response.json.return_value = {
            "video_id": 1,
            "slug": "test-video",
        }

        mock_client = mock.Mock()
        mock_client.post.return_value = mock_upload_response
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)

        with mock.patch("httpx.get", return_value=mock_cat_response):
            with mock.patch("httpx.Client", return_value=mock_client):
                cmd_upload(args)

        captured = capsys.readouterr()
        assert "Success" in captured.out

    def test_upload_category_not_found(self, capsys, tmp_path):
        """Test upload with non-existent category."""
        from cli.main import cmd_upload

        # Create a test file
        test_file = tmp_path / "test_video.mp4"
        test_file.write_bytes(b"fake video content")

        args = mock.Mock()
        args.file = str(test_file)
        args.title = "Test Video"
        args.description = ""
        args.category = "NonExistent"

        # Mock category lookup returning empty
        mock_cat_response = mock.Mock()
        mock_cat_response.is_success = True
        mock_cat_response.json.return_value = [
            {"id": 1, "name": "Movies", "slug": "movies"},
        ]

        # Mock upload
        mock_upload_response = mock.Mock()
        mock_upload_response.is_success = True
        mock_upload_response.json.return_value = {
            "video_id": 1,
            "slug": "test-video",
        }

        mock_client = mock.Mock()
        mock_client.post.return_value = mock_upload_response
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)

        with mock.patch("httpx.get", return_value=mock_cat_response):
            with mock.patch("httpx.Client", return_value=mock_client):
                cmd_upload(args)

        captured = capsys.readouterr()
        assert "Warning" in captured.out
        assert "not found" in captured.out
        assert "Success" in captured.out  # Upload should still succeed

    def test_upload_timeout_exception(self, capsys, tmp_path):
        """Test upload handling of timeout exceptions."""
        import httpx

        from cli.main import cmd_upload

        # Create a test file
        test_file = tmp_path / "test_video.mp4"
        test_file.write_bytes(b"fake video content")

        args = mock.Mock()
        args.file = str(test_file)
        args.title = "Test Video"
        args.description = ""
        args.category = None

        # Mock the httpx.Client to raise timeout
        mock_client = mock.Mock()
        mock_client.post.side_effect = httpx.TimeoutException("Upload timed out")
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)

        with mock.patch("httpx.Client", return_value=mock_client):
            with pytest.raises(SystemExit) as exc_info:
                cmd_upload(args)
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Upload timed out" in captured.out
        assert "VLOG_UPLOAD_TIMEOUT" in captured.out

    def test_upload_uses_timeout(self, tmp_path):
        """Test that upload uses the configured timeout."""
        from cli.main import cmd_upload

        # Create a test file
        test_file = tmp_path / "test_video.mp4"
        test_file.write_bytes(b"fake video content")

        args = mock.Mock()
        args.file = str(test_file)
        args.title = "Test Video"
        args.description = ""
        args.category = None

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "video_id": 1,
            "slug": "test-video",
        }

        mock_client = mock.Mock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)

        with mock.patch("httpx.Client", return_value=mock_client) as mock_client_class:
            cmd_upload(args)
            # Verify that httpx.Client was called with a timeout
            call_args = mock_client_class.call_args
            assert call_args is not None
            assert "timeout" in call_args[1]
            # Verify it's not None (previously was None)
            assert call_args[1]["timeout"] is not None

    def test_upload_with_progress_wrapper(self, capsys, tmp_path):
        """Test that upload uses ProgressFileWrapper for tracking."""
        from cli.main import ProgressFileWrapper, cmd_upload

        # Create a test file
        test_file = tmp_path / "test_video.mp4"
        test_file.write_bytes(b"fake video content")

        args = mock.Mock()
        args.file = str(test_file)
        args.title = "Test Video"
        args.description = ""
        args.category = None

        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "video_id": 1,
            "slug": "test-video",
        }

        mock_client = mock.Mock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)

        # We'll track if ProgressFileWrapper was instantiated
        with mock.patch("httpx.Client", return_value=mock_client):
            with mock.patch("cli.main.ProgressFileWrapper", wraps=ProgressFileWrapper) as mock_wrapper:
                cmd_upload(args)
                # Verify ProgressFileWrapper was called
                assert mock_wrapper.call_count == 1

        captured = capsys.readouterr()
        assert "Success" in captured.out


class TestCmdDownload:
    """Test the cmd_download command."""

    def test_download_ytdlp_not_found(self, capsys):
        """Test download when yt-dlp is not installed."""

        from cli.main import cmd_download

        args = mock.Mock()
        args.url = "https://youtube.com/watch?v=test"
        args.title = None
        args.description = ""
        args.category = None

        with mock.patch("subprocess.run", side_effect=FileNotFoundError("yt-dlp not found")):
            with pytest.raises(SystemExit) as exc_info:
                cmd_download(args)
            assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "yt-dlp is not installed" in captured.out

    def test_download_timeout(self, capsys, tmp_path):
        """Test download timeout handling."""
        import subprocess

        from cli.main import cmd_download

        args = mock.Mock()
        args.url = "https://youtube.com/watch?v=test"
        args.title = None
        args.description = ""
        args.category = None

        # First call for version check
        version_result = mock.Mock()
        version_result.returncode = 0

        # Use side_effect to return different results for different calls
        def run_side_effect(*args, **kwargs):
            if "--version" in args[0]:
                return version_result
            raise subprocess.TimeoutExpired(args[0], 3600)

        with mock.patch("subprocess.run", side_effect=run_side_effect):
            with mock.patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                mock_tmpdir.return_value.__enter__ = mock.Mock(return_value=str(tmp_path))
                mock_tmpdir.return_value.__exit__ = mock.Mock(return_value=False)
                with pytest.raises(SystemExit) as exc_info:
                    cmd_download(args)
                assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "timed out" in captured.out

    def test_download_upload_timeout_exception(self, capsys, tmp_path):
        """Test download command handling of upload timeout exceptions."""

    def test_download_invalid_url_scheme(self, capsys):
        """Test download with invalid URL scheme."""
        from cli.main import cmd_download

        args = mock.Mock()
        args.url = "ftp://example.com/video.mp4"
        args.title = None
        args.description = ""
        args.category = None

        with pytest.raises(SystemExit) as exc_info:
            cmd_download(args)
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Invalid URL scheme" in captured.out
        assert "ftp" in captured.out

    def test_download_invalid_url_missing_domain(self, capsys):
        """Test download with URL missing domain."""
        from cli.main import cmd_download

        args = mock.Mock()
        args.url = "http://"
        args.title = None
        args.description = ""
        args.category = None

        with pytest.raises(SystemExit) as exc_info:
            cmd_download(args)
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "missing domain" in captured.out

    def test_download_valid_url_format(self, capsys, tmp_path):
        """Test download with valid URL format passes URL validation."""
        from cli.main import cmd_download

        args = mock.Mock()
        args.url = "https://youtube.com/watch?v=test"
        args.title = None
        args.description = ""
        args.category = None

        # First call for version check
        version_result = mock.Mock()
        version_result.returncode = 0

        # Second call for download
        download_result = mock.Mock()
        download_result.returncode = 0

        def run_side_effect(*args, **kwargs):
            if "--version" in args[0]:
                return version_result
            return download_result

        # Create fake downloaded file
        test_file = tmp_path / "downloaded_video.mp4"
        test_file.write_bytes(b"fake video content")

        # Mock the httpx.Client to raise timeout during upload
        mock_client = mock.Mock()
        mock_client.post.side_effect = httpx.TimeoutException("Upload timed out")
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)

        with mock.patch("subprocess.run", side_effect=run_side_effect):
            with mock.patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                mock_tmpdir.return_value.__enter__ = mock.Mock(return_value=str(tmp_path))
                mock_tmpdir.return_value.__exit__ = mock.Mock(return_value=False)
                with mock.patch("httpx.Client", return_value=mock_client):
                    with pytest.raises(SystemExit) as exc_info:
                        cmd_download(args)
                    assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "Upload timed out" in captured.out
        assert "VLOG_UPLOAD_TIMEOUT" in captured.out
        # Mock yt-dlp version check to pass
        version_result = mock.Mock()
        version_result.returncode = 0

        # Mock the actual download to fail (we're just testing URL validation)
        download_result = mock.Mock()
        download_result.returncode = 1
        download_result.stderr = "Some download error"

        def run_side_effect(cmd, **kwargs):
            if "--version" in cmd:
                return version_result
            return download_result

        with mock.patch("subprocess.run", side_effect=run_side_effect):
            with mock.patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                mock_tmpdir.return_value.__enter__ = mock.Mock(return_value="/tmp/test")
                mock_tmpdir.return_value.__exit__ = mock.Mock(return_value=False)

                # Should get past URL validation and fail at download stage
                with pytest.raises(SystemExit) as exc_info:
                    cmd_download(args)
                assert exc_info.value.code == 1

        captured = capsys.readouterr()
        # Should see the "Downloading" message (URL validation passed)
        assert "Downloading" in captured.out
        # Should see download error (not URL validation error)
        assert "Error downloading" in captured.out

    def test_download_with_progress_wrapper(self, capsys, tmp_path):
        """Test that download upload phase uses ProgressFileWrapper for tracking."""
        from cli.main import ProgressFileWrapper, cmd_download

        args = mock.Mock()
        args.url = "https://youtube.com/watch?v=test"
        args.title = "Test Video"
        args.description = ""
        args.category = None

        # Mock yt-dlp version check
        version_result = mock.Mock()
        version_result.returncode = 0

        # Mock successful download
        download_result = mock.Mock()
        download_result.returncode = 0

        def run_side_effect(*args, **kwargs):
            if "--version" in args[0]:
                return version_result
            return download_result

        # Create fake downloaded file
        test_file = tmp_path / "downloaded_video.mp4"
        test_file.write_bytes(b"fake video content")

        # Mock successful upload
        mock_response = mock.Mock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "video_id": 1,
            "slug": "test-video",
        }

        mock_client = mock.Mock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)

        with mock.patch("subprocess.run", side_effect=run_side_effect):
            with mock.patch("tempfile.TemporaryDirectory") as mock_tmpdir:
                mock_tmpdir.return_value.__enter__ = mock.Mock(return_value=str(tmp_path))
                mock_tmpdir.return_value.__exit__ = mock.Mock(return_value=False)
                with mock.patch("httpx.Client", return_value=mock_client):
                    # Track if ProgressFileWrapper was instantiated
                    with mock.patch("cli.main.ProgressFileWrapper", wraps=ProgressFileWrapper) as mock_wrapper:
                        cmd_download(args)
                        # Verify ProgressFileWrapper was called for upload phase
                        assert mock_wrapper.call_count == 1

        captured = capsys.readouterr()
        assert "Success" in captured.out


class TestMainParser:
    """Test the main argument parser."""

    def test_upload_command_parser(self):
        """Test upload command argument parsing."""
        import sys

        from cli.main import main

        # Test help works
        with mock.patch.object(sys, "argv", ["vlog", "upload", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_list_command_parser(self):
        """Test list command argument parsing."""
        import sys

        from cli.main import main

        with mock.patch.object(sys, "argv", ["vlog", "list", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_categories_command_parser(self):
        """Test categories command argument parsing."""
        import sys

        from cli.main import main

        with mock.patch.object(sys, "argv", ["vlog", "categories", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_delete_command_parser(self):
        """Test delete command argument parsing."""
        import sys

        from cli.main import main

        with mock.patch.object(sys, "argv", ["vlog", "delete", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_download_command_parser(self):
        """Test download command argument parsing."""
        import sys

        from cli.main import main

        with mock.patch.object(sys, "argv", ["vlog", "download", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_missing_command_fails(self):
        """Test that missing command shows help."""
        import sys

        from cli.main import main

        with mock.patch.object(sys, "argv", ["vlog"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 2  # argparse error code
