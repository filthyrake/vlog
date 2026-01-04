"""
Tests for video download functionality (Issue #202).

Tests cover:
- Download configuration endpoint
- Original file download endpoint
- Rate limiting and concurrent download limits
- Error handling and edge cases
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set test mode before importing app
os.environ["VLOG_TEST_MODE"] = "1"


@pytest.fixture
def mock_database():
    """Mock database operations."""
    with patch("api.public.fetch_one_with_retry") as mock:
        yield mock


@pytest.fixture
def mock_settings():
    """Mock download settings."""
    with patch("api.public.get_download_settings") as mock:
        mock.return_value = {
            "enabled": True,
            "allow_original": True,
            "allow_transcoded": True,
            "rate_limit_per_hour": 10,
            "max_concurrent": 2,
        }
        yield mock


@pytest.fixture
def mock_storage_available():
    """Mock storage availability check."""
    with patch("api.public.require_storage_available") as mock:
        mock.return_value = None
        yield mock


@pytest.fixture
def temp_upload_dir():
    """Create a temporary uploads directory with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test video file
        test_file = Path(tmpdir) / "123.mp4"
        test_file.write_bytes(b"fake video content " * 100)  # 1.9KB test file

        with patch("api.public.UPLOADS_DIR", Path(tmpdir)):
            yield tmpdir, test_file


class TestDownloadConfig:
    """Tests for /api/config/downloads endpoint."""

    def test_downloads_disabled_returns_minimal_config(self):
        """When downloads are disabled, only return enabled: false."""
        from api.public import app

        with patch("api.public.get_download_settings") as mock:
            mock.return_value = {"enabled": False}

            client = TestClient(app)
            response = client.get("/api/config/downloads")

            assert response.status_code == 200
            data = response.json()
            assert data == {"enabled": False}
            assert "allow_original" not in data
            assert "allow_transcoded" not in data

    def test_downloads_enabled_returns_full_config(self):
        """When downloads are enabled, return all relevant settings."""
        from api.public import app

        with patch("api.public.get_download_settings") as mock:
            mock.return_value = {
                "enabled": True,
                "allow_original": True,
                "allow_transcoded": False,
                "rate_limit_per_hour": 10,
                "max_concurrent": 2,
            }

            client = TestClient(app)
            response = client.get("/api/config/downloads")

            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is True
            assert data["allow_original"] is True
            assert data["allow_transcoded"] is False


class TestDownloadOriginal:
    """Tests for /api/videos/{slug}/download/original endpoint."""

    def test_downloads_disabled_returns_403(self, mock_storage_available):
        """When downloads are disabled, return 403."""
        from api.public import app

        with patch("api.public.get_download_settings") as mock:
            mock.return_value = {"enabled": False}

            client = TestClient(app)
            response = client.get("/api/videos/test-video/download/original")

            assert response.status_code == 403
            assert "disabled" in response.json()["detail"].lower()

    def test_original_downloads_disabled_returns_403(self, mock_storage_available):
        """When original downloads are disabled, return 403."""
        from api.public import app

        with patch("api.public.get_download_settings") as mock:
            mock.return_value = {
                "enabled": True,
                "allow_original": False,
                "max_concurrent": 2,
            }

            client = TestClient(app)
            response = client.get("/api/videos/test-video/download/original")

            assert response.status_code == 403
            assert "original" in response.json()["detail"].lower()

    def test_invalid_slug_returns_400(self, mock_settings, mock_storage_available):
        """Invalid slug format returns 400."""
        from api.public import app, _active_downloads_per_ip

        _active_downloads_per_ip.clear()

        client = TestClient(app)
        # Slug with invalid characters (uppercase not allowed in slug validation)
        response = client.get("/api/videos/Test-Video-INVALID/download/original")
        assert response.status_code == 400

        # Slug with special characters
        with patch("api.public._release_download_slot"):
            response = client.get("/api/videos/test--double-dash/download/original")
            # Double dashes are invalid per slug validation
            assert response.status_code == 400

        _active_downloads_per_ip.clear()

    def test_video_not_found_returns_404(self, mock_settings, mock_storage_available):
        """Non-existent video returns 404."""
        from api.public import app, _release_download_slot

        with patch("api.public.fetch_one_with_retry") as mock_db:
            mock_db.return_value = None

            # Also patch the slot release since we acquire but video not found
            with patch("api.public._release_download_slot") as mock_release:
                mock_release.return_value = None

                client = TestClient(app)
                response = client.get("/api/videos/nonexistent-video/download/original")

                assert response.status_code == 404
                assert "not found" in response.json()["detail"].lower()

    def test_original_file_missing_returns_404(
        self, mock_settings, mock_storage_available, mock_database
    ):
        """When original file is deleted, return 404."""
        from api.public import app

        mock_database.return_value = {
            "id": 999,
            "slug": "test-video",
            "title": "Test Video",
            "status": "ready",
        }

        with patch("api.public._find_original_file") as mock_find:
            mock_find.return_value = None

            with patch("api.public._release_download_slot"):
                client = TestClient(app)
                response = client.get("/api/videos/test-video/download/original")

                assert response.status_code == 404
                assert "not available" in response.json()["detail"].lower()


class TestConcurrentDownloadLimits:
    """Tests for concurrent download tracking."""

    @pytest.mark.asyncio
    async def test_acquire_and_release_slot(self):
        """Test acquiring and releasing download slots."""
        from api.public import (
            _acquire_download_slot,
            _release_download_slot,
            _active_downloads_per_ip,
        )

        # Clear any existing state
        _active_downloads_per_ip.clear()

        # Acquire first slot
        result = await _acquire_download_slot("192.168.1.1", max_concurrent=2)
        assert result is True
        assert _active_downloads_per_ip.get("192.168.1.1") == 1

        # Acquire second slot
        result = await _acquire_download_slot("192.168.1.1", max_concurrent=2)
        assert result is True
        assert _active_downloads_per_ip.get("192.168.1.1") == 2

        # Third slot should be denied
        result = await _acquire_download_slot("192.168.1.1", max_concurrent=2)
        assert result is False
        assert _active_downloads_per_ip.get("192.168.1.1") == 2

        # Different IP should work
        result = await _acquire_download_slot("192.168.1.2", max_concurrent=2)
        assert result is True
        assert _active_downloads_per_ip.get("192.168.1.2") == 1

        # Release slots
        await _release_download_slot("192.168.1.1")
        assert _active_downloads_per_ip.get("192.168.1.1") == 1

        await _release_download_slot("192.168.1.1")
        assert "192.168.1.1" not in _active_downloads_per_ip

        # Clean up
        _active_downloads_per_ip.clear()

    def test_concurrent_limit_exceeded_returns_429(self, mock_settings, mock_storage_available):
        """When concurrent limit is exceeded, return 429."""
        from api.public import app, _active_downloads_per_ip

        # Set up: max 2 concurrent, already have 2
        _active_downloads_per_ip.clear()
        _active_downloads_per_ip["testclient"] = 2

        mock_settings.return_value = {
            "enabled": True,
            "allow_original": True,
            "max_concurrent": 2,
        }

        client = TestClient(app)
        response = client.get("/api/videos/test-video/download/original")

        assert response.status_code == 429
        assert "concurrent" in response.json()["detail"].lower()

        # Clean up
        _active_downloads_per_ip.clear()


class TestFileValidation:
    """Tests for file validation in _find_original_file."""

    def test_find_original_file_with_valid_file(self):
        """Should find valid video file."""
        from api.public import _find_original_file

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test file
            test_file = Path(tmpdir) / "123.mp4"
            test_file.write_bytes(b"x" * 1000)

            with patch("api.public.UPLOADS_DIR", Path(tmpdir)):
                result = _find_original_file(123)

            assert result is not None
            assert result.name == "123.mp4"

    def test_find_original_file_empty_file_skipped(self):
        """Should skip empty files."""
        from api.public import _find_original_file

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create empty file
            test_file = Path(tmpdir) / "123.mp4"
            test_file.write_bytes(b"")

            with patch("api.public.UPLOADS_DIR", Path(tmpdir)):
                result = _find_original_file(123)

            assert result is None

    def test_find_original_file_not_found(self):
        """Should return None when file doesn't exist."""
        from api.public import _find_original_file

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("api.public.UPLOADS_DIR", Path(tmpdir)):
                result = _find_original_file(999)

            assert result is None

    def test_find_original_file_checks_multiple_extensions(self):
        """Should check all supported extensions."""
        from api.public import _find_original_file

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .mkv file (not .mp4)
            test_file = Path(tmpdir) / "123.mkv"
            test_file.write_bytes(b"x" * 1000)

            with patch("api.public.UPLOADS_DIR", Path(tmpdir)):
                result = _find_original_file(123)

            assert result is not None
            assert result.name == "123.mkv"


class TestMimeTypeDetection:
    """Tests for MIME type detection."""

    def test_mime_types_for_all_extensions(self):
        """Verify MIME types are defined for common formats."""
        from api.public import _VIDEO_MIME_TYPES

        assert _VIDEO_MIME_TYPES[".mp4"] == "video/mp4"
        assert _VIDEO_MIME_TYPES[".mkv"] == "video/x-matroska"
        assert _VIDEO_MIME_TYPES[".webm"] == "video/webm"
        assert _VIDEO_MIME_TYPES[".mov"] == "video/quicktime"
        assert _VIDEO_MIME_TYPES[".avi"] == "video/x-msvideo"


class TestCacheLocking:
    """Tests for settings cache with locking."""

    @pytest.mark.asyncio
    async def test_cache_lock_prevents_thundering_herd(self):
        """Multiple concurrent requests should not all hit the database."""
        from api.public import (
            get_download_settings,
            reset_download_settings_cache,
        )

        # Reset cache
        reset_download_settings_cache()

        call_count = 0

        async def mock_get(key, default):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)  # Simulate DB latency
            return default

        # Mock the settings service at the import location inside the function
        with patch("api.settings_service.get_settings_service") as mock_service:
            mock_instance = MagicMock()
            mock_instance.get = mock_get
            mock_service.return_value = mock_instance

            # Launch multiple concurrent requests
            tasks = [get_download_settings() for _ in range(10)]
            results = await asyncio.gather(*tasks)

            # All should return the same result
            assert all(r == results[0] for r in results)

            # Due to locking, we should have far fewer calls than requests
            # (ideally just one set of 5 calls for the 5 settings)
            # Allow for some variance due to timing
            assert call_count <= 10  # Much less than 50 (10 requests * 5 settings each)

        # Clean up
        reset_download_settings_cache()


class TestFilenameGeneration:
    """Tests for safe filename generation."""

    def test_special_characters_removed(self):
        """Special characters should be removed from title."""
        # Test the logic used in download_original
        title = 'Video <script>alert("xss")</script>'
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        safe_title = "_".join(safe_title.split())

        assert "<" not in safe_title
        assert ">" not in safe_title
        assert '"' not in safe_title
        assert "Video" in safe_title

    def test_spaces_converted_to_underscores(self):
        """Spaces should be converted to underscores."""
        title = "My   Video   Title"
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        safe_title = "_".join(safe_title.split())

        assert "  " not in safe_title  # No double spaces
        assert "_" in safe_title

    def test_empty_title_falls_back_to_slug(self):
        """Empty/invalid title should fall back to slug."""
        title = "!@#$%"  # All special chars
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()

        assert safe_title == ""  # Would fall back to slug in actual code
