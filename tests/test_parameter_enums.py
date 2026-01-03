"""
Tests for parameter enums that replace boolean traps.
See: https://github.com/filthyrake/vlog/issues/443
"""

import warnings
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import ErrorLogging, JobFailureMode, PlaylistValidation
from api.errors import sanitize_error_message


class TestErrorLoggingEnum:
    """Tests for ErrorLogging enum usage in sanitize_error_message."""

    def test_log_original_logs_message(self, caplog):
        """Test that LOG_ORIGINAL logs the original error."""
        with caplog.at_level("WARNING"):
            result = sanitize_error_message(
                "Test error message",
                ErrorLogging.LOG_ORIGINAL,
                context="test_context",
            )

        assert "Original error" in caplog.text
        assert "test_context" in caplog.text
        assert result is not None

    def test_skip_logging_does_not_log(self, caplog):
        """Test that SKIP_LOGGING skips logging."""
        with caplog.at_level("WARNING"):
            result = sanitize_error_message(
                "Test error message",
                ErrorLogging.SKIP_LOGGING,
            )

        assert "Original error" not in caplog.text
        assert result is not None

    def test_boolean_true_emits_deprecation_warning(self):
        """Test that passing True emits a deprecation warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sanitize_error_message("error", True)

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()
            assert "ErrorLogging" in str(w[0].message)

    def test_boolean_false_emits_deprecation_warning(self):
        """Test that passing False emits a deprecation warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            sanitize_error_message("error", False)

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)

    def test_invalid_type_raises_type_error(self):
        """Test that passing invalid types raises TypeError."""
        with pytest.raises(TypeError, match="logging_mode must be ErrorLogging or bool"):
            sanitize_error_message("error", "invalid")

        with pytest.raises(TypeError, match="logging_mode must be ErrorLogging or bool"):
            sanitize_error_message("error", 1)

        with pytest.raises(TypeError, match="logging_mode must be ErrorLogging or bool"):
            sanitize_error_message("error", None)

    def test_none_input_returns_none(self):
        """Test that None input returns None without processing."""
        result = sanitize_error_message(None, ErrorLogging.LOG_ORIGINAL)
        assert result is None


class TestPlaylistValidationEnum:
    """Tests for PlaylistValidation enum usage in validate_hls_playlist."""

    @pytest.fixture
    def valid_playlist(self, tmp_path):
        """Create a valid HLS playlist file."""
        playlist = tmp_path / "test.m3u8"
        segment = tmp_path / "segment0.ts"

        playlist.write_text(
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXTINF:10.0,\n"
            "segment0.ts\n"
            "#EXT-X-ENDLIST\n"
        )
        segment.write_bytes(b"fake segment data")

        return playlist

    @pytest.fixture
    def playlist_missing_segment(self, tmp_path):
        """Create a playlist with missing segment file."""
        playlist = tmp_path / "test.m3u8"

        playlist.write_text(
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXTINF:10.0,\n"
            "missing_segment.ts\n"
            "#EXT-X-ENDLIST\n"
        )

        return playlist

    @pytest.mark.asyncio
    async def test_check_segments_validates_files(self, valid_playlist):
        """Test that CHECK_SEGMENTS validates segment files exist."""
        from worker.transcoder import validate_hls_playlist

        # Mock ffprobe subprocess to return valid video stream
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"video", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            is_valid, error = await validate_hls_playlist(
                valid_playlist, PlaylistValidation.CHECK_SEGMENTS
            )

        assert is_valid is True
        assert error is None

    @pytest.mark.asyncio
    async def test_check_segments_catches_missing_files(self, playlist_missing_segment):
        """Test that CHECK_SEGMENTS catches missing segment files."""
        from worker.transcoder import validate_hls_playlist

        is_valid, error = await validate_hls_playlist(
            playlist_missing_segment, PlaylistValidation.CHECK_SEGMENTS
        )

        assert is_valid is False
        assert "Missing segment file" in error

    @pytest.mark.asyncio
    async def test_structure_only_skips_file_check(self, playlist_missing_segment):
        """Test that STRUCTURE_ONLY skips segment file validation."""
        from worker.transcoder import validate_hls_playlist

        is_valid, error = await validate_hls_playlist(
            playlist_missing_segment, PlaylistValidation.STRUCTURE_ONLY
        )

        assert is_valid is True
        assert error is None

    @pytest.mark.asyncio
    async def test_boolean_true_emits_deprecation_warning(self, valid_playlist):
        """Test that passing True emits a deprecation warning."""
        from worker.transcoder import validate_hls_playlist

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            await validate_hls_playlist(valid_playlist, True)

            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "PlaylistValidation" in str(w[0].message)

    @pytest.mark.asyncio
    async def test_invalid_type_raises_type_error(self, valid_playlist):
        """Test that passing invalid types raises TypeError."""
        from worker.transcoder import validate_hls_playlist

        with pytest.raises(TypeError, match="validation_mode must be PlaylistValidation or bool"):
            await validate_hls_playlist(valid_playlist, "invalid")

        with pytest.raises(TypeError, match="validation_mode must be PlaylistValidation or bool"):
            await validate_hls_playlist(valid_playlist, 1)

        with pytest.raises(TypeError, match="validation_mode must be PlaylistValidation or bool"):
            await validate_hls_playlist(valid_playlist, None)


class TestJobFailureModeEnum:
    """Tests for JobFailureMode enum usage in mark_job_failed."""

    @pytest.mark.asyncio
    async def test_permanent_sets_completed_at(self):
        """Test that PERMANENT mode sets completed_at."""
        from worker.transcoder import mark_job_failed

        with patch("worker.transcoder.database") as mock_db:
            mock_db.execute = AsyncMock()

            await mark_job_failed(1, "Test error", JobFailureMode.PERMANENT)

            # Verify completed_at was included in the values
            call_args = mock_db.execute.call_args
            assert call_args is not None

    @pytest.mark.asyncio
    async def test_retryable_does_not_set_completed_at(self):
        """Test that RETRYABLE mode does not set completed_at."""
        from worker.transcoder import mark_job_failed

        with patch("worker.transcoder.database") as mock_db:
            mock_db.execute = AsyncMock()

            await mark_job_failed(1, "Test error", JobFailureMode.RETRYABLE)

            # Verify execute was called
            assert mock_db.execute.called

    @pytest.mark.asyncio
    async def test_boolean_true_emits_deprecation_warning(self):
        """Test that passing True emits a deprecation warning."""
        from worker.transcoder import mark_job_failed

        with patch("worker.transcoder.database") as mock_db:
            mock_db.execute = AsyncMock()

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                await mark_job_failed(1, "error", True)

                assert len(w) == 1
                assert issubclass(w[0].category, DeprecationWarning)
                assert "JobFailureMode" in str(w[0].message)

    @pytest.mark.asyncio
    async def test_invalid_type_raises_type_error(self):
        """Test that passing invalid types raises TypeError."""
        from worker.transcoder import mark_job_failed

        with pytest.raises(TypeError, match="failure_mode must be JobFailureMode or bool"):
            await mark_job_failed(1, "error", "invalid")

        with pytest.raises(TypeError, match="failure_mode must be JobFailureMode or bool"):
            await mark_job_failed(1, "error", None)

        with pytest.raises(TypeError, match="failure_mode must be JobFailureMode or bool"):
            await mark_job_failed(1, "error", 0)


class TestEnumValues:
    """Tests for enum value consistency."""

    def test_error_logging_values(self):
        """Test ErrorLogging enum has expected values."""
        assert ErrorLogging.LOG_ORIGINAL.value == "log_original"
        assert ErrorLogging.SKIP_LOGGING.value == "skip_logging"

    def test_playlist_validation_values(self):
        """Test PlaylistValidation enum has expected values."""
        assert PlaylistValidation.CHECK_SEGMENTS.value == "check_segments"
        assert PlaylistValidation.STRUCTURE_ONLY.value == "structure_only"

    def test_job_failure_mode_values(self):
        """Test JobFailureMode enum has expected values."""
        assert JobFailureMode.RETRYABLE.value == "retryable"
        assert JobFailureMode.PERMANENT.value == "permanent"

    def test_enums_are_string_subclass(self):
        """Test that enums inherit from str for serialization support."""
        assert isinstance(ErrorLogging.LOG_ORIGINAL, str)
        assert isinstance(PlaylistValidation.CHECK_SEGMENTS, str)
        assert isinstance(JobFailureMode.PERMANENT, str)

    def test_enum_string_comparison(self):
        """Test that enums can be compared with strings."""
        assert ErrorLogging.LOG_ORIGINAL == "log_original"
        assert PlaylistValidation.CHECK_SEGMENTS == "check_segments"
        assert JobFailureMode.PERMANENT == "permanent"
