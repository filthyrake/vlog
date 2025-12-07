"""
Tests for cleanup_source_file function - cleans up orphaned source files
after permanent transcoding failure.

Issue: #265 - Orphaned source files not cleaned up on permanent job failure
"""

from pathlib import Path
from unittest.mock import patch

from config import SUPPORTED_VIDEO_EXTENSIONS


class TestCleanupSourceFile:
    """Tests for the cleanup_source_file function."""

    def test_cleanup_source_file_deletes_mp4(self, test_storage: dict, monkeypatch):
        """Test that cleanup_source_file deletes .mp4 source file."""
        import worker.transcoder

        monkeypatch.setattr(worker.transcoder, "UPLOADS_DIR", test_storage["uploads"])
        monkeypatch.setattr(worker.transcoder, "CLEANUP_SOURCE_ON_PERMANENT_FAILURE", True)

        from worker.transcoder import cleanup_source_file

        # Create a test source file
        video_id = 123
        source_file = test_storage["uploads"] / f"{video_id}.mp4"
        source_file.write_bytes(b"fake video content")
        assert source_file.exists()

        # Cleanup should delete the file
        result = cleanup_source_file(video_id)

        assert result is True
        assert not source_file.exists()

    def test_cleanup_source_file_deletes_mkv(self, test_storage: dict, monkeypatch):
        """Test that cleanup_source_file deletes .mkv source file."""
        import worker.transcoder

        monkeypatch.setattr(worker.transcoder, "UPLOADS_DIR", test_storage["uploads"])
        monkeypatch.setattr(worker.transcoder, "CLEANUP_SOURCE_ON_PERMANENT_FAILURE", True)

        from worker.transcoder import cleanup_source_file

        video_id = 456
        source_file = test_storage["uploads"] / f"{video_id}.mkv"
        source_file.write_bytes(b"fake mkv content")
        assert source_file.exists()

        result = cleanup_source_file(video_id)

        assert result is True
        assert not source_file.exists()

    def test_cleanup_source_file_deletes_webm(self, test_storage: dict, monkeypatch):
        """Test that cleanup_source_file deletes .webm source file."""
        import worker.transcoder

        monkeypatch.setattr(worker.transcoder, "UPLOADS_DIR", test_storage["uploads"])
        monkeypatch.setattr(worker.transcoder, "CLEANUP_SOURCE_ON_PERMANENT_FAILURE", True)

        from worker.transcoder import cleanup_source_file

        video_id = 789
        source_file = test_storage["uploads"] / f"{video_id}.webm"
        source_file.write_bytes(b"fake webm content")
        assert source_file.exists()

        result = cleanup_source_file(video_id)

        assert result is True
        assert not source_file.exists()

    def test_cleanup_disabled_by_config(self, test_storage: dict, monkeypatch):
        """Test that cleanup is skipped when CLEANUP_SOURCE_ON_PERMANENT_FAILURE is False."""
        import worker.transcoder

        monkeypatch.setattr(worker.transcoder, "UPLOADS_DIR", test_storage["uploads"])
        monkeypatch.setattr(worker.transcoder, "CLEANUP_SOURCE_ON_PERMANENT_FAILURE", False)

        from worker.transcoder import cleanup_source_file

        video_id = 123
        source_file = test_storage["uploads"] / f"{video_id}.mp4"
        source_file.write_bytes(b"fake video content")
        assert source_file.exists()

        # Cleanup should be skipped
        result = cleanup_source_file(video_id)

        assert result is False
        assert source_file.exists()  # File should still exist

    def test_cleanup_nonexistent_file(self, test_storage: dict, monkeypatch):
        """Test that cleanup returns False when source file doesn't exist."""
        import worker.transcoder

        monkeypatch.setattr(worker.transcoder, "UPLOADS_DIR", test_storage["uploads"])
        monkeypatch.setattr(worker.transcoder, "CLEANUP_SOURCE_ON_PERMANENT_FAILURE", True)

        from worker.transcoder import cleanup_source_file

        video_id = 999
        # Don't create any file

        result = cleanup_source_file(video_id)

        assert result is False

    def test_cleanup_handles_oserror(self, test_storage: dict, monkeypatch):
        """Test that cleanup handles OSError gracefully."""
        import worker.transcoder

        monkeypatch.setattr(worker.transcoder, "UPLOADS_DIR", test_storage["uploads"])
        monkeypatch.setattr(worker.transcoder, "CLEANUP_SOURCE_ON_PERMANENT_FAILURE", True)

        from worker.transcoder import cleanup_source_file

        video_id = 123
        source_file = test_storage["uploads"] / f"{video_id}.mp4"
        source_file.write_bytes(b"fake video content")

        # Mock unlink to raise OSError
        with patch.object(Path, "unlink", side_effect=OSError("Permission denied")):
            result = cleanup_source_file(video_id)

        assert result is False

    def test_cleanup_tries_all_extensions(self, test_storage: dict, monkeypatch):
        """Test that cleanup tries all supported extensions."""
        import worker.transcoder

        monkeypatch.setattr(worker.transcoder, "UPLOADS_DIR", test_storage["uploads"])
        monkeypatch.setattr(worker.transcoder, "CLEANUP_SOURCE_ON_PERMANENT_FAILURE", True)

        from worker.transcoder import cleanup_source_file

        video_id = 123

        # Only create an .avi file (not .mp4 which is first in list)
        avi_file = test_storage["uploads"] / f"{video_id}.avi"
        avi_file.write_bytes(b"fake avi content")
        assert avi_file.exists()

        result = cleanup_source_file(video_id)

        assert result is True
        assert not avi_file.exists()


class TestCleanupSourceFileConfigOption:
    """Tests for CLEANUP_SOURCE_ON_PERMANENT_FAILURE configuration."""

    def test_config_default_is_true(self):
        """Test that CLEANUP_SOURCE_ON_PERMANENT_FAILURE defaults to True."""
        from config import CLEANUP_SOURCE_ON_PERMANENT_FAILURE

        assert CLEANUP_SOURCE_ON_PERMANENT_FAILURE is True

    def test_config_imported_in_transcoder(self):
        """Test that the config is properly imported in transcoder module."""
        import worker.transcoder

        # Should not raise AttributeError
        assert hasattr(worker.transcoder, "CLEANUP_SOURCE_ON_PERMANENT_FAILURE")

    def test_config_imported_in_worker_api(self):
        """Test that the config is properly imported in worker_api module."""
        import api.worker_api

        # Should not raise AttributeError
        assert hasattr(api.worker_api, "CLEANUP_SOURCE_ON_PERMANENT_FAILURE")


class TestSupportedExtensions:
    """Tests for SUPPORTED_VIDEO_EXTENSIONS constant."""

    def test_supported_extensions_includes_common_formats(self):
        """Test that SUPPORTED_VIDEO_EXTENSIONS includes common video formats."""
        assert ".mp4" in SUPPORTED_VIDEO_EXTENSIONS
        assert ".mkv" in SUPPORTED_VIDEO_EXTENSIONS
        assert ".webm" in SUPPORTED_VIDEO_EXTENSIONS
        assert ".avi" in SUPPORTED_VIDEO_EXTENSIONS
        assert ".mov" in SUPPORTED_VIDEO_EXTENSIONS

    def test_all_extensions_start_with_dot(self):
        """Test that all extensions start with a dot."""
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            assert ext.startswith("."), f"Extension {ext} should start with dot"
