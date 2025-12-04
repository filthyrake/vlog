"""
Tests for Pydantic schema validation.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from api.schemas import (
    WHISPER_LANGUAGES,
    AnalyticsOverview,
    CategoryCreate,
    CategoryResponse,
    DailyViews,
    PlaybackEnd,
    PlaybackHeartbeat,
    PlaybackSessionCreate,
    QualityBreakdown,
    QualityProgressResponse,
    TranscodingProgressResponse,
    TranscriptionResponse,
    TranscriptionTrigger,
    TranscriptionUpdate,
    TrendDataPoint,
    TrendsResponse,
    VideoAnalyticsSummary,
    VideoCreate,
    VideoListResponse,
    VideoQualityResponse,
    VideoResponse,
)


class TestCategorySchemas:
    """Tests for category-related schemas."""

    def test_category_create_valid(self):
        """Test valid category creation."""
        data = CategoryCreate(name="Test Category", description="A test category")
        assert data.name == "Test Category"
        assert data.description == "A test category"

    def test_category_create_minimal(self):
        """Test category creation with only required fields."""
        data = CategoryCreate(name="Minimal")
        assert data.name == "Minimal"
        assert data.description == ""

    def test_category_create_empty_name_fails(self):
        """Test that empty category name fails validation."""
        with pytest.raises(ValidationError) as exc_info:
            CategoryCreate(name="")
        assert "min_length" in str(exc_info.value).lower() or "at least 1" in str(exc_info.value)

    def test_category_create_name_too_long_fails(self):
        """Test that overly long category name fails validation."""
        with pytest.raises(ValidationError):
            CategoryCreate(name="x" * 101)  # Max is 100

    def test_category_create_description_too_long_fails(self):
        """Test that overly long description fails validation."""
        with pytest.raises(ValidationError):
            CategoryCreate(name="Test", description="x" * 1001)  # Max is 1000

    def test_category_response(self):
        """Test category response schema."""
        now = datetime.now(timezone.utc)
        data = CategoryResponse(
            id=1,
            name="Test",
            slug="test",
            description="A test",
            created_at=now,
            video_count=5,
        )
        assert data.id == 1
        assert data.video_count == 5


class TestVideoSchemas:
    """Tests for video-related schemas."""

    def test_video_create_valid(self):
        """Test valid video creation."""
        data = VideoCreate(
            title="My Video",
            description="A description",
            category_id=1,
        )
        assert data.title == "My Video"
        assert data.category_id == 1

    def test_video_create_minimal(self):
        """Test video creation with only required fields."""
        data = VideoCreate(title="Minimal Video")
        assert data.title == "Minimal Video"
        assert data.description == ""
        assert data.category_id is None

    def test_video_create_empty_title_fails(self):
        """Test that empty title fails validation."""
        with pytest.raises(ValidationError):
            VideoCreate(title="")

    def test_video_create_title_too_long_fails(self):
        """Test that title exceeding max length fails."""
        with pytest.raises(ValidationError):
            VideoCreate(title="x" * 256)  # Max is 255

    def test_video_create_description_too_long_fails(self):
        """Test that description exceeding max length fails."""
        with pytest.raises(ValidationError):
            VideoCreate(title="Test", description="x" * 5001)  # Max is 5000

    def test_video_quality_response(self):
        """Test video quality response schema."""
        data = VideoQualityResponse(
            quality="1080p",
            width=1920,
            height=1080,
            bitrate=5000,
        )
        assert data.quality == "1080p"
        assert data.width == 1920

    def test_video_response_full(self):
        """Test full video response schema."""
        now = datetime.now(timezone.utc)
        data = VideoResponse(
            id=1,
            title="Test Video",
            slug="test-video",
            description="A description",
            category_id=1,
            category_name="Test Category",
            category_slug="test-category",
            duration=120.5,
            source_width=1920,
            source_height=1080,
            status="ready",
            error_message=None,
            created_at=now,
            published_at=now,
            thumbnail_url="/videos/test-video/thumbnail.jpg",
            stream_url="/videos/test-video/master.m3u8",
            captions_url="/videos/test-video/captions.vtt",
            transcription_status="completed",
            qualities=[
                VideoQualityResponse(quality="1080p", width=1920, height=1080, bitrate=5000),
            ],
        )
        assert data.id == 1
        assert len(data.qualities) == 1

    def test_video_list_response(self):
        """Test video list response schema."""
        now = datetime.now(timezone.utc)
        data = VideoListResponse(
            id=1,
            title="Test",
            slug="test",
            description="Desc",
            category_id=1,
            category_name="Cat",
            duration=60.0,
            status="ready",
            created_at=now,
            published_at=now,
            thumbnail_url="/videos/test/thumbnail.jpg",
        )
        assert data.id == 1
        assert data.duration == 60.0


class TestPlaybackSchemas:
    """Tests for playback/analytics schemas."""

    def test_playback_session_create(self):
        """Test playback session creation."""
        data = PlaybackSessionCreate(video_id=1, quality="1080p")
        assert data.video_id == 1
        assert data.quality == "1080p"

    def test_playback_session_create_minimal(self):
        """Test playback session creation without quality."""
        data = PlaybackSessionCreate(video_id=1)
        assert data.video_id == 1
        assert data.quality is None

    def test_playback_heartbeat(self):
        """Test playback heartbeat schema."""
        data = PlaybackHeartbeat(
            session_token="abc123",
            position=45.5,
            quality="720p",
            playing=True,
        )
        assert data.session_token == "abc123"
        assert data.position == 45.5
        assert data.playing is True

    def test_playback_heartbeat_defaults(self):
        """Test playback heartbeat with defaults."""
        data = PlaybackHeartbeat(session_token="abc", position=10.0)
        assert data.quality is None
        assert data.playing is True

    def test_playback_end(self):
        """Test playback end schema."""
        data = PlaybackEnd(
            session_token="abc123",
            position=120.0,
            completed=True,
        )
        assert data.completed is True

    def test_playback_end_defaults(self):
        """Test playback end with defaults."""
        data = PlaybackEnd(session_token="abc", position=50.0)
        assert data.completed is False


class TestAnalyticsSchemas:
    """Tests for analytics response schemas."""

    def test_analytics_overview(self):
        """Test analytics overview schema."""
        data = AnalyticsOverview(
            total_views=1000,
            unique_viewers=500,
            total_watch_time_hours=250.5,
            completion_rate=0.75,
            avg_watch_duration_seconds=300.0,
            views_today=50,
            views_this_week=300,
            views_this_month=800,
        )
        assert data.total_views == 1000
        assert data.completion_rate == 0.75

    def test_video_analytics_summary(self):
        """Test video analytics summary schema."""
        data = VideoAnalyticsSummary(
            video_id=1,
            title="Test",
            slug="test",
            thumbnail_url="/videos/test/thumbnail.jpg",
            total_views=100,
            unique_viewers=50,
            total_watch_time_seconds=5000.0,
            avg_watch_duration_seconds=100.0,
            completion_rate=0.8,
            peak_quality="1080p",
        )
        assert data.video_id == 1
        assert data.peak_quality == "1080p"

    def test_quality_breakdown(self):
        """Test quality breakdown schema."""
        data = QualityBreakdown(quality="1080p", percentage=0.65)
        assert data.quality == "1080p"
        assert data.percentage == 0.65

    def test_daily_views(self):
        """Test daily views schema."""
        data = DailyViews(date="2024-01-15", views=150)
        assert data.date == "2024-01-15"
        assert data.views == 150

    def test_trends_response(self):
        """Test trends response schema."""
        data = TrendsResponse(
            period="30d",
            data=[
                TrendDataPoint(date="2024-01-15", views=100, unique_viewers=50, watch_time_hours=10.5),
                TrendDataPoint(date="2024-01-16", views=120, unique_viewers=60, watch_time_hours=12.0),
            ],
        )
        assert data.period == "30d"
        assert len(data.data) == 2


class TestTranscodingSchemas:
    """Tests for transcoding progress schemas."""

    def test_quality_progress_response(self):
        """Test quality progress response schema."""
        data = QualityProgressResponse(
            name="1080p",
            status="in_progress",
            progress=45,
        )
        assert data.name == "1080p"
        assert data.progress == 45

    def test_transcoding_progress_response_minimal(self):
        """Test transcoding progress with minimal data."""
        data = TranscodingProgressResponse(
            status="pending",
        )
        assert data.status == "pending"
        assert data.progress_percent == 0
        assert data.qualities == []
        assert data.attempt == 1
        assert data.max_attempts == 3

    def test_transcoding_progress_response_full(self):
        """Test transcoding progress with full data."""
        now = datetime.now(timezone.utc)
        data = TranscodingProgressResponse(
            status="processing",
            current_step="transcode",
            progress_percent=60,
            qualities=[
                QualityProgressResponse(name="1080p", status="completed", progress=100),
                QualityProgressResponse(name="720p", status="in_progress", progress=50),
            ],
            attempt=2,
            max_attempts=3,
            started_at=now,
            last_error="Previous attempt failed",
        )
        assert data.current_step == "transcode"
        assert len(data.qualities) == 2


class TestTranscriptionSchemas:
    """Tests for transcription schemas."""

    def test_transcription_response_none(self):
        """Test transcription response with no transcription."""
        data = TranscriptionResponse(status="none")
        assert data.status == "none"
        assert data.language is None
        assert data.text is None

    def test_transcription_response_completed(self):
        """Test transcription response with completed transcription."""
        now = datetime.now(timezone.utc)
        data = TranscriptionResponse(
            status="completed",
            language="en",
            text="Hello world, this is a transcription.",
            vtt_url="/videos/test/captions.vtt",
            word_count=6,
            duration_seconds=45.5,
            started_at=now,
            completed_at=now,
        )
        assert data.status == "completed"
        assert data.word_count == 6

    def test_transcription_trigger_valid_language(self):
        """Test transcription trigger with valid language."""
        data = TranscriptionTrigger(language="en")
        assert data.language == "en"

    def test_transcription_trigger_valid_language_case_insensitive(self):
        """Test transcription trigger normalizes language case."""
        data = TranscriptionTrigger(language="EN")
        assert data.language == "en"

    def test_transcription_trigger_no_language(self):
        """Test transcription trigger with no language (auto-detect)."""
        data = TranscriptionTrigger()
        assert data.language is None

    def test_transcription_trigger_invalid_language_fails(self):
        """Test transcription trigger with invalid language fails."""
        with pytest.raises(ValidationError) as exc_info:
            TranscriptionTrigger(language="invalid")
        assert "invalid language code" in str(exc_info.value).lower()

    def test_transcription_trigger_all_whisper_languages(self):
        """Test that all Whisper language codes are accepted."""
        for lang in WHISPER_LANGUAGES:
            data = TranscriptionTrigger(language=lang)
            assert data.language == lang

    def test_transcription_update_valid(self):
        """Test transcription update with valid text."""
        data = TranscriptionUpdate(text="Updated transcript text.")
        assert data.text == "Updated transcript text."

    def test_transcription_update_empty_fails(self):
        """Test transcription update with empty text fails."""
        with pytest.raises(ValidationError):
            TranscriptionUpdate(text="")

    def test_transcription_update_too_long_fails(self):
        """Test transcription update exceeding max length fails."""
        with pytest.raises(ValidationError):
            TranscriptionUpdate(text="x" * 500001)  # Max is 500000
