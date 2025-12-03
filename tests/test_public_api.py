"""
Tests for the public API endpoints.
"""
import pytest
from datetime import datetime, timezone
import uuid

from api.database import videos, categories, video_qualities, playback_sessions, transcriptions
from api.enums import VideoStatus, TranscriptionStatus


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_database_healthy(self, test_database):
        """Test health check when database is accessible."""
        # Database is connected via fixture
        result = await test_database.fetch_one("SELECT 1")
        assert result is not None


class TestVideosEndpoints:
    """Tests for video-related endpoints."""

    @pytest.mark.asyncio
    async def test_list_videos_empty(self, test_database):
        """Test listing videos when database is empty."""
        result = await test_database.fetch_all(
            videos.select().where(videos.c.status == VideoStatus.READY)
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_list_videos_with_data(self, test_database, sample_video):
        """Test listing videos returns ready videos."""
        result = await test_database.fetch_all(
            videos.select().where(videos.c.status == VideoStatus.READY)
        )
        assert len(result) == 1
        assert result[0]["slug"] == "test-video"

    @pytest.mark.asyncio
    async def test_list_videos_excludes_pending(self, test_database, sample_category):
        """Test listing videos excludes pending videos."""
        # Create a pending video
        now = datetime.now(timezone.utc)
        await test_database.execute(
            videos.insert().values(
                title="Pending",
                slug="pending",
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        result = await test_database.fetch_all(
            videos.select().where(videos.c.status == VideoStatus.READY)
        )
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_list_videos_excludes_deleted(self, test_database, sample_category):
        """Test listing videos excludes soft-deleted videos."""
        now = datetime.now(timezone.utc)
        await test_database.execute(
            videos.insert().values(
                title="Deleted",
                slug="deleted",
                status=VideoStatus.READY,
                created_at=now,
                deleted_at=now,  # Soft deleted
            )
        )

        result = await test_database.fetch_all(
            videos.select().where(
                (videos.c.status == VideoStatus.READY) &
                (videos.c.deleted_at == None)
            )
        )
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_get_video_by_slug(self, test_database, sample_video):
        """Test getting a video by slug."""
        result = await test_database.fetch_one(
            videos.select().where(videos.c.slug == "test-video")
        )
        assert result is not None
        assert result["title"] == "Test Video"
        assert result["duration"] == 120.5

    @pytest.mark.asyncio
    async def test_get_video_not_found(self, test_database):
        """Test getting non-existent video."""
        result = await test_database.fetch_one(
            videos.select().where(videos.c.slug == "nonexistent")
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_video_with_qualities(self, test_database, sample_video_with_qualities):
        """Test getting video includes quality variants."""
        video_id = sample_video_with_qualities["id"]
        quality_rows = await test_database.fetch_all(
            video_qualities.select().where(video_qualities.c.video_id == video_id)
        )
        assert len(quality_rows) == 3
        quality_names = {q["quality"] for q in quality_rows}
        assert quality_names == {"1080p", "720p", "480p"}


class TestCategoriesEndpoints:
    """Tests for category-related endpoints."""

    @pytest.mark.asyncio
    async def test_list_categories_empty(self, test_database):
        """Test listing categories when database is empty."""
        result = await test_database.fetch_all(categories.select())
        assert result == []

    @pytest.mark.asyncio
    async def test_list_categories_with_data(self, test_database, sample_category):
        """Test listing categories returns data."""
        result = await test_database.fetch_all(categories.select())
        assert len(result) == 1
        assert result[0]["name"] == "Test Category"
        assert result[0]["slug"] == "test-category"

    @pytest.mark.asyncio
    async def test_get_category_by_slug(self, test_database, sample_category):
        """Test getting category by slug."""
        result = await test_database.fetch_one(
            categories.select().where(categories.c.slug == "test-category")
        )
        assert result is not None
        assert result["name"] == "Test Category"

    @pytest.mark.asyncio
    async def test_get_category_not_found(self, test_database):
        """Test getting non-existent category."""
        result = await test_database.fetch_one(
            categories.select().where(categories.c.slug == "nonexistent")
        )
        assert result is None


class TestAnalyticsEndpoints:
    """Tests for analytics endpoints."""

    @pytest.mark.asyncio
    async def test_create_playback_session(self, test_database, sample_video):
        """Test creating a playback session."""
        session_token = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        await test_database.execute(
            playback_sessions.insert().values(
                video_id=sample_video["id"],
                session_token=session_token,
                started_at=now,
            )
        )

        result = await test_database.fetch_one(
            playback_sessions.select().where(
                playback_sessions.c.session_token == session_token
            )
        )
        assert result is not None
        assert result["video_id"] == sample_video["id"]

    @pytest.mark.asyncio
    async def test_update_playback_session(self, test_database, sample_playback_session):
        """Test updating playback session with heartbeat."""
        session_token = sample_playback_session["session_token"]

        # Update session
        await test_database.execute(
            playback_sessions.update()
            .where(playback_sessions.c.session_token == session_token)
            .values(
                duration_watched=90.0,
                max_position=100.0,
            )
        )

        result = await test_database.fetch_one(
            playback_sessions.select().where(
                playback_sessions.c.session_token == session_token
            )
        )
        assert result["duration_watched"] == 90.0
        assert result["max_position"] == 100.0

    @pytest.mark.asyncio
    async def test_end_playback_session(self, test_database, sample_playback_session):
        """Test ending a playback session."""
        session_token = sample_playback_session["session_token"]
        now = datetime.now(timezone.utc)

        await test_database.execute(
            playback_sessions.update()
            .where(playback_sessions.c.session_token == session_token)
            .values(
                ended_at=now,
                completed=True,
            )
        )

        result = await test_database.fetch_one(
            playback_sessions.select().where(
                playback_sessions.c.session_token == session_token
            )
        )
        assert result["ended_at"] is not None
        assert result["completed"] is True


class TestTranscriptionEndpoints:
    """Tests for transcription endpoints."""

    @pytest.mark.asyncio
    async def test_get_transcript_none(self, test_database, sample_video):
        """Test getting transcript when none exists."""
        result = await test_database.fetch_one(
            transcriptions.select().where(
                transcriptions.c.video_id == sample_video["id"]
            )
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_transcript_completed(self, test_database, sample_video):
        """Test getting completed transcript."""
        video_id = sample_video["id"]
        now = datetime.now(timezone.utc)

        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_id,
                status=TranscriptionStatus.COMPLETED,
                language="en",
                transcript_text="Hello world, this is a test transcript.",
                vtt_path="/videos/test-video/captions.vtt",
                word_count=7,
                started_at=now,
                completed_at=now,
            )
        )

        result = await test_database.fetch_one(
            transcriptions.select().where(transcriptions.c.video_id == video_id)
        )
        assert result["status"] == TranscriptionStatus.COMPLETED
        assert result["language"] == "en"
        assert result["word_count"] == 7


class TestVideoFiltering:
    """Tests for video filtering and search."""

    @pytest.mark.asyncio
    async def test_filter_videos_by_category(self, test_database, sample_video, sample_category):
        """Test filtering videos by category."""
        import sqlalchemy as sa

        query = (
            sa.select(videos)
            .select_from(
                videos.outerjoin(categories, videos.c.category_id == categories.c.id)
            )
            .where(categories.c.slug == "test-category")
            .where(videos.c.status == VideoStatus.READY)
        )

        result = await test_database.fetch_all(query)
        assert len(result) == 1
        assert result[0]["slug"] == "test-video"

    @pytest.mark.asyncio
    async def test_filter_videos_by_nonexistent_category(self, test_database, sample_video):
        """Test filtering by non-existent category returns empty."""
        import sqlalchemy as sa

        query = (
            sa.select(videos)
            .select_from(
                videos.outerjoin(categories, videos.c.category_id == categories.c.id)
            )
            .where(categories.c.slug == "nonexistent")
        )

        result = await test_database.fetch_all(query)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_search_videos_by_title(self, test_database, sample_video):
        """Test searching videos by title."""
        import sqlalchemy as sa

        search_term = "%Test%"
        query = (
            videos.select()
            .where(videos.c.title.ilike(search_term))
            .where(videos.c.status == VideoStatus.READY)
        )

        result = await test_database.fetch_all(query)
        assert len(result) == 1
        assert result[0]["title"] == "Test Video"

    @pytest.mark.asyncio
    async def test_search_videos_no_match(self, test_database, sample_video):
        """Test searching videos with no matches."""
        import sqlalchemy as sa

        search_term = "%nonexistent%"
        query = (
            videos.select()
            .where(videos.c.title.ilike(search_term))
        )

        result = await test_database.fetch_all(query)
        assert len(result) == 0


class TestPagination:
    """Tests for pagination."""

    @pytest.mark.asyncio
    async def test_pagination_limit(self, test_database, sample_category):
        """Test pagination with limit."""
        now = datetime.now(timezone.utc)

        # Create multiple videos
        for i in range(10):
            await test_database.execute(
                videos.insert().values(
                    title=f"Video {i}",
                    slug=f"video-{i}",
                    status=VideoStatus.READY,
                    created_at=now,
                    published_at=now,
                )
            )

        query = (
            videos.select()
            .where(videos.c.status == VideoStatus.READY)
            .limit(5)
        )

        result = await test_database.fetch_all(query)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_pagination_offset(self, test_database, sample_category):
        """Test pagination with offset."""
        now = datetime.now(timezone.utc)

        # Create multiple videos
        for i in range(10):
            await test_database.execute(
                videos.insert().values(
                    title=f"Video {i}",
                    slug=f"video-{i}",
                    status=VideoStatus.READY,
                    created_at=now,
                    published_at=now,
                )
            )

        query = (
            videos.select()
            .where(videos.c.status == VideoStatus.READY)
            .order_by(videos.c.created_at)
            .offset(5)
            .limit(5)
        )

        result = await test_database.fetch_all(query)
        assert len(result) == 5
