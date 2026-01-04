"""
Tests for chapter auto-detection functionality (Issue #493).

Tests cover:
- Schema validation for AutoDetectChaptersRequest
- FFprobe metadata chapter extraction
- Transcription-based chapter generation
- Chapter filtering by minimum length
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.chapter_detection import (
    InternalDetectedChapter,
    extract_chapters_from_metadata,
    filter_chapters_by_length,
    generate_chapters_from_transcription,
)
from api.schemas import (
    AutoDetectChaptersRequest,
    ChapterDetectionSource,
)


class TestChapterDetectionSchemas:
    """Tests for chapter detection request/response schemas."""

    def test_default_values(self):
        """Test that AutoDetectChaptersRequest has sensible defaults."""
        request = AutoDetectChaptersRequest()

        assert request.source == ChapterDetectionSource.METADATA
        assert request.min_chapter_length == 60
        assert request.replace_existing is False

    def test_source_enum_values(self):
        """Test ChapterDetectionSource enum values."""
        assert ChapterDetectionSource.METADATA.value == "metadata"
        assert ChapterDetectionSource.TRANSCRIPTION.value == "transcription"
        assert ChapterDetectionSource.BOTH.value == "both"

    def test_min_chapter_length_validation(self):
        """Test min_chapter_length bounds."""
        # Valid values
        AutoDetectChaptersRequest(min_chapter_length=10)
        AutoDetectChaptersRequest(min_chapter_length=600)

        # Invalid values
        with pytest.raises(ValueError):
            AutoDetectChaptersRequest(min_chapter_length=5)  # Too low

        with pytest.raises(ValueError):
            AutoDetectChaptersRequest(min_chapter_length=700)  # Too high


class TestMetadataChapterExtraction:
    """Tests for ffprobe-based chapter extraction."""

    @pytest.mark.asyncio
    async def test_extract_chapters_from_metadata_no_source_file(self, tmp_path):
        """Test graceful handling when source video file doesn't exist."""
        with patch("api.chapter_detection.UPLOADS_DIR", tmp_path):
            chapters = await extract_chapters_from_metadata(
                video_id=999,
            )

            # Should return empty list, not raise an error
            assert chapters == []

    @pytest.mark.asyncio
    async def test_extract_chapters_from_metadata_with_chapters(self, tmp_path):
        """Test extraction of embedded chapters via ffprobe."""
        # Create a mock source file
        source_file = tmp_path / "1.mp4"
        source_file.write_bytes(b"fake video content")

        # Mock ffprobe output with chapters
        mock_ffprobe_output = {
            "chapters": [
                {
                    "start_time": "0.000000",
                    "end_time": "60.000000",
                    "tags": {"title": "Introduction"},
                },
                {
                    "start_time": "60.000000",
                    "end_time": "180.000000",
                    "tags": {"title": "Main Content"},
                },
                {
                    "start_time": "180.000000",
                    "end_time": "240.000000",
                    "tags": {"title": "Conclusion"},
                },
            ]
        }

        async def mock_communicate():
            return (json.dumps(mock_ffprobe_output).encode(), b"")

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = mock_communicate
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch("api.chapter_detection.UPLOADS_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                chapters = await extract_chapters_from_metadata(
                    video_id=1,
                )

                assert len(chapters) == 3
                assert chapters[0].title == "Introduction"
                assert chapters[0].start_time == 0.0
                assert chapters[0].end_time == 60.0
                assert chapters[0].source == "metadata"

                assert chapters[1].title == "Main Content"
                assert chapters[1].start_time == 60.0
                assert chapters[1].end_time == 180.0

                assert chapters[2].title == "Conclusion"
                assert chapters[2].start_time == 180.0
                assert chapters[2].end_time == 240.0

    @pytest.mark.asyncio
    async def test_extract_chapters_ffprobe_no_chapters(self, tmp_path):
        """Test handling when ffprobe returns no chapters."""
        source_file = tmp_path / "1.mp4"
        source_file.write_bytes(b"fake video content")

        # Mock ffprobe output without chapters
        mock_ffprobe_output = {"chapters": []}

        async def mock_communicate():
            return (json.dumps(mock_ffprobe_output).encode(), b"")

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = mock_communicate
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch("api.chapter_detection.UPLOADS_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                chapters = await extract_chapters_from_metadata(
                    video_id=1,
                )

                assert chapters == []

    @pytest.mark.asyncio
    async def test_extract_chapters_fallback_title(self, tmp_path):
        """Test that chapters get numbered fallback titles when no title tag."""
        source_file = tmp_path / "1.mp4"
        source_file.write_bytes(b"fake video content")

        # Mock ffprobe output with chapters lacking titles
        mock_ffprobe_output = {
            "chapters": [
                {"start_time": "0.0", "end_time": "60.0", "tags": {}},
                {"start_time": "60.0", "end_time": "120.0"},
            ]
        }

        async def mock_communicate():
            return (json.dumps(mock_ffprobe_output).encode(), b"")

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = mock_communicate
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch("api.chapter_detection.UPLOADS_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                chapters = await extract_chapters_from_metadata(
                    video_id=1,
                )

                assert len(chapters) == 2
                assert chapters[0].title == "Chapter 1"
                assert chapters[1].title == "Chapter 2"


class TestTranscriptionChapterGeneration:
    """Tests for transcription-based chapter generation."""

    @pytest.mark.asyncio
    async def test_generate_chapters_empty_transcript(self):
        """Test handling of empty transcript."""
        chapters = await generate_chapters_from_transcription(
            transcript_text="",
            video_duration=600,
            min_chapter_length=60,
        )

        assert chapters == []

    @pytest.mark.asyncio
    async def test_generate_chapters_whitespace_only(self):
        """Test handling of whitespace-only transcript."""
        chapters = await generate_chapters_from_transcription(
            transcript_text="   \n  \t  ",
            video_duration=600,
            min_chapter_length=60,
        )

        assert chapters == []

    @pytest.mark.asyncio
    async def test_generate_chapters_short_video(self):
        """Test that very short videos don't get chapters."""
        chapters = await generate_chapters_from_transcription(
            transcript_text="This is a short video about testing.",
            video_duration=30,  # Less than min_chapter_length
            min_chapter_length=60,
        )

        assert chapters == []

    @pytest.mark.asyncio
    async def test_generate_chapters_normal_transcript(self):
        """Test chapter generation from a normal transcript."""
        transcript = (
            "Welcome to this tutorial. Today we'll learn about Python. "
            "First, let's talk about variables. Variables store data. "
            "Next, we'll cover functions. Functions are reusable code blocks. "
            "Finally, let's discuss classes. Classes enable object-oriented programming. "
            "That concludes our tutorial. Thanks for watching."
        )

        chapters = await generate_chapters_from_transcription(
            transcript_text=transcript,
            video_duration=600,  # 10 minutes
            min_chapter_length=60,
        )

        # Should have multiple chapters
        assert len(chapters) >= 2

        # All chapters should have transcription source
        for chapter in chapters:
            assert chapter.source == "transcription"
            assert chapter.start_time >= 0
            assert chapter.end_time is not None
            assert chapter.end_time > chapter.start_time

    @pytest.mark.asyncio
    async def test_generate_chapters_respects_min_length(self):
        """Test that min_chapter_length affects number of chapters."""
        transcript = "Long transcript. " * 100

        # Longer min_chapter_length = fewer chapters
        chapters_60 = await generate_chapters_from_transcription(
            transcript_text=transcript,
            video_duration=600,
            min_chapter_length=60,
        )

        chapters_120 = await generate_chapters_from_transcription(
            transcript_text=transcript,
            video_duration=600,
            min_chapter_length=120,
        )

        assert len(chapters_60) >= len(chapters_120)


class TestChapterFiltering:
    """Tests for chapter length filtering."""

    def test_filter_empty_list(self):
        """Test filtering an empty list."""
        result = filter_chapters_by_length(
            chapters=[],
            min_chapter_length=60,
            video_duration=600,
        )

        assert result == []

    def test_filter_removes_close_chapters(self):
        """Test that chapters too close together are filtered."""
        chapters = [
            InternalDetectedChapter(title="Chapter 1", start_time=0, end_time=30),
            InternalDetectedChapter(title="Chapter 2", start_time=30, end_time=50),  # Only 30s later
            InternalDetectedChapter(title="Chapter 3", start_time=120, end_time=180),  # 90s from ch1
        ]

        result = filter_chapters_by_length(
            chapters=chapters,
            min_chapter_length=60,
            video_duration=600,
        )

        # Should keep chapter 1 and 3, skip chapter 2
        assert len(result) == 2
        assert result[0].title == "Chapter 1"
        assert result[1].title == "Chapter 3"

    def test_filter_removes_near_end_chapters(self):
        """Test that chapters too close to video end are filtered."""
        chapters = [
            InternalDetectedChapter(title="Chapter 1", start_time=0, end_time=100),
            InternalDetectedChapter(title="Chapter 2", start_time=580, end_time=600),  # Only 20s from end
        ]

        result = filter_chapters_by_length(
            chapters=chapters,
            min_chapter_length=60,
            video_duration=600,
        )

        # Should only keep chapter 1
        assert len(result) == 1
        assert result[0].title == "Chapter 1"

    def test_filter_sorts_by_start_time(self):
        """Test that chapters are sorted by start time."""
        chapters = [
            InternalDetectedChapter(title="Late Chapter", start_time=300, end_time=400),
            InternalDetectedChapter(title="Early Chapter", start_time=0, end_time=100),
            InternalDetectedChapter(title="Middle Chapter", start_time=150, end_time=250),
        ]

        result = filter_chapters_by_length(
            chapters=chapters,
            min_chapter_length=60,
            video_duration=600,
        )

        assert result[0].title == "Early Chapter"
        assert result[1].title == "Middle Chapter"
        assert result[2].title == "Late Chapter"


class TestInternalDetectedChapterDataclass:
    """Tests for the InternalDetectedChapter dataclass."""

    def test_default_source(self):
        """Test that default source is 'metadata'."""
        chapter = InternalDetectedChapter(title="Test", start_time=0.0)
        assert chapter.source == "metadata"

    def test_custom_source(self):
        """Test setting a custom source."""
        chapter = InternalDetectedChapter(title="Test", start_time=0.0, source="transcription")
        assert chapter.source == "transcription"

    def test_optional_end_time(self):
        """Test that end_time is optional."""
        chapter = InternalDetectedChapter(title="Test", start_time=0.0)
        assert chapter.end_time is None

        chapter_with_end = InternalDetectedChapter(title="Test", start_time=0.0, end_time=60.0)
        assert chapter_with_end.end_time == 60.0
