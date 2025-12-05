"""
Tests for the public API endpoints.

Includes both database-level tests and HTTP-level tests using FastAPI TestClient.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

from api.database import categories, playback_sessions, transcriptions, video_qualities, videos
from api.enums import TranscriptionStatus, VideoStatus

# ============================================================================
# HTTP-Level Tests using FastAPI TestClient
# ============================================================================


class TestPublicAPIHTTP:
    """HTTP-level tests for public API endpoints using TestClient."""

    def test_health_check(self, public_client):
        """Test health check endpoint returns valid response."""
        response = public_client.get("/health")
        assert response.status_code in [200, 503]  # May fail if storage not mocked
        data = response.json()
        assert "status" in data
        assert "checks" in data

    def test_list_videos_empty(self, public_client):
        """Test listing videos when database is empty."""
        response = public_client.get("/api/videos")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_videos_returns_ready_only(self, public_client, test_database, sample_category):
        """Test listing videos only returns ready videos."""
        now = datetime.now(timezone.utc)
        # Create videos with different statuses
        await test_database.execute(
            videos.insert().values(
                title="Ready Video",
                slug="ready-video",
                description="A ready video",
                duration=60.0,
                status=VideoStatus.READY,
                created_at=now,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Pending Video",
                slug="pending-video",
                description="A pending video",
                duration=0,
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        response = public_client.get("/api/videos")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slug"] == "ready-video"

    @pytest.mark.asyncio
    async def test_list_videos_excludes_deleted(self, public_client, test_database, sample_category):
        """Test listing videos excludes soft-deleted videos."""
        now = datetime.now(timezone.utc)
        await test_database.execute(
            videos.insert().values(
                title="Active Video",
                slug="active-video",
                description="An active video",
                duration=60.0,
                status=VideoStatus.READY,
                created_at=now,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Deleted Video",
                slug="deleted-video",
                description="A deleted video",
                duration=60.0,
                status=VideoStatus.READY,
                created_at=now,
                published_at=now,
                deleted_at=now,
            )
        )

        response = public_client.get("/api/videos")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slug"] == "active-video"

    @pytest.mark.asyncio
    async def test_get_video_by_slug(self, public_client, sample_video):
        """Test getting a video by slug."""
        response = public_client.get("/api/videos/test-video")
        assert response.status_code == 200
        data = response.json()
        assert data["slug"] == "test-video"
        assert data["title"] == "Test Video"
        assert data["duration"] == 120.5

    def test_get_video_not_found(self, public_client):
        """Test getting non-existent video returns 404."""
        response = public_client.get("/api/videos/nonexistent")
        assert response.status_code == 404
        assert response.json()["detail"] == "Video not found"

    @pytest.mark.asyncio
    async def test_get_video_rejects_deleted(self, public_client, sample_deleted_video):
        """Test that deleted videos return 404 (issue #76)."""
        response = public_client.get(f"/api/videos/{sample_deleted_video['slug']}")
        assert response.status_code == 404
        assert response.json()["detail"] == "Video not found"

    @pytest.mark.asyncio
    async def test_get_video_progress_rejects_deleted(self, public_client, sample_deleted_video):
        """Test that progress for deleted videos returns 404 (issue #76)."""
        response = public_client.get(f"/api/videos/{sample_deleted_video['slug']}/progress")
        assert response.status_code == 404
        assert response.json()["detail"] == "Video not found"

    @pytest.mark.asyncio
    async def test_get_transcript_rejects_deleted(self, public_client, sample_deleted_video):
        """Test that transcript for deleted videos returns 404 (issue #76)."""
        response = public_client.get(f"/api/videos/{sample_deleted_video['slug']}/transcript")
        assert response.status_code == 404
        assert response.json()["detail"] == "Video not found"

    def test_list_categories(self, public_client):
        """Test listing categories."""
        response = public_client.get("/api/categories")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_list_categories_with_data(self, public_client, sample_category):
        """Test listing categories returns data."""
        response = public_client.get("/api/categories")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(c["slug"] == "test-category" for c in data)

    @pytest.mark.asyncio
    async def test_get_category_by_slug(self, public_client, sample_category):
        """Test getting a category by slug."""
        response = public_client.get("/api/categories/test-category")
        assert response.status_code == 200
        data = response.json()
        assert data["slug"] == "test-category"
        assert data["name"] == "Test Category"

    def test_get_category_not_found(self, public_client):
        """Test getting non-existent category returns 404."""
        response = public_client.get("/api/categories/nonexistent")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_filter_videos_by_category(self, public_client, sample_video, sample_category):
        """Test filtering videos by category slug."""
        response = public_client.get("/api/videos?category=test-category")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slug"] == "test-video"

    def test_filter_videos_by_nonexistent_category(self, public_client):
        """Test filtering by non-existent category returns empty."""
        response = public_client.get("/api/videos?category=nonexistent")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_search_videos(self, public_client, sample_video):
        """Test searching videos by title."""
        response = public_client.get("/api/videos?search=Test")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test Video"

    def test_search_videos_no_match(self, public_client):
        """Test searching with no matches returns empty."""
        response = public_client.get("/api/videos?search=nonexistent")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_pagination(self, public_client, test_database, sample_category):
        """Test pagination with limit and offset."""
        now = datetime.now(timezone.utc)
        for i in range(5):
            await test_database.execute(
                videos.insert().values(
                    title=f"Video {i}",
                    slug=f"video-{i}",
                    description=f"Description for video {i}",
                    duration=60.0,
                    status=VideoStatus.READY,
                    created_at=now,
                    published_at=now,
                )
            )

        # Test limit
        response = public_client.get("/api/videos?limit=3")
        assert response.status_code == 200
        assert len(response.json()) == 3

        # Test offset
        response = public_client.get("/api/videos?limit=3&offset=3")
        assert response.status_code == 200
        assert len(response.json()) == 2


class TestAnalyticsHTTP:
    """HTTP-level tests for analytics endpoints."""

    @pytest.mark.asyncio
    async def test_start_analytics_session(self, public_client, sample_video):
        """Test starting an analytics session."""
        response = public_client.post(
            "/api/analytics/session",
            json={"video_id": sample_video["id"], "quality": "1080p"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_token" in data

    @pytest.mark.asyncio
    async def test_start_session_invalid_video(self, public_client, sample_video):
        """Test starting session with non-existent video fails."""
        # Use an ID that's guaranteed not to exist
        nonexistent_id = -(sample_video["id"] + 1000)
        response = public_client.post(
            "/api/analytics/session",
            json={"video_id": nonexistent_id, "quality": "1080p"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_start_session_rejects_deleted_video(self, public_client, sample_deleted_video):
        """Test starting session with deleted video fails."""
        response = public_client.post(
            "/api/analytics/session",
            json={"video_id": sample_deleted_video["id"], "quality": "1080p"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_analytics_heartbeat(self, public_client, sample_playback_session):
        """Test sending analytics heartbeat."""
        response = public_client.post(
            "/api/analytics/heartbeat",
            json={
                "session_token": sample_playback_session["session_token"],
                "position": 30.0,
                "playing": True,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_heartbeat_invalid_session(self, public_client):
        """Test heartbeat with invalid session returns 404."""
        response = public_client.post(
            "/api/analytics/heartbeat",
            json={
                "session_token": "invalid-token",
                "position": 30.0,
                "playing": True,
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_end_analytics_session(self, public_client, sample_playback_session):
        """Test ending an analytics session."""
        response = public_client.post(
            "/api/analytics/end",
            json={
                "session_token": sample_playback_session["session_token"],
                "position": 120.0,
                "completed": True,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_end_session_invalid_token(self, public_client):
        """Test ending session with invalid token returns 404."""
        response = public_client.post(
            "/api/analytics/end",
            json={
                "session_token": "invalid-token",
                "position": 120.0,
                "completed": True,
            },
        )
        assert response.status_code == 404


# ============================================================================
# Database-Level Tests (existing tests)
# ============================================================================


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
        result = await test_database.fetch_all(videos.select().where(videos.c.status == VideoStatus.READY))
        assert result == []

    @pytest.mark.asyncio
    async def test_list_videos_with_data(self, test_database, sample_video):
        """Test listing videos returns ready videos."""
        result = await test_database.fetch_all(videos.select().where(videos.c.status == VideoStatus.READY))
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

        result = await test_database.fetch_all(videos.select().where(videos.c.status == VideoStatus.READY))
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
            videos.select().where((videos.c.status == VideoStatus.READY) & (videos.c.deleted_at.is_(None)))
        )
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_get_video_by_slug(self, test_database, sample_video):
        """Test getting a video by slug."""
        result = await test_database.fetch_one(videos.select().where(videos.c.slug == "test-video"))
        assert result is not None
        assert result["title"] == "Test Video"
        assert result["duration"] == 120.5

    @pytest.mark.asyncio
    async def test_get_video_not_found(self, test_database):
        """Test getting non-existent video."""
        result = await test_database.fetch_one(videos.select().where(videos.c.slug == "nonexistent"))
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
        result = await test_database.fetch_one(categories.select().where(categories.c.slug == "test-category"))
        assert result is not None
        assert result["name"] == "Test Category"

    @pytest.mark.asyncio
    async def test_get_category_not_found(self, test_database):
        """Test getting non-existent category."""
        result = await test_database.fetch_one(categories.select().where(categories.c.slug == "nonexistent"))
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
            playback_sessions.select().where(playback_sessions.c.session_token == session_token)
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
            playback_sessions.select().where(playback_sessions.c.session_token == session_token)
        )
        assert result["duration_watched"] == 90.0
        assert result["max_position"] == 100.0

    @pytest.mark.asyncio
    async def test_end_playback_session(self, test_database, sample_video):
        """Test ending a playback session."""
        session_token = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        await test_database.execute(
            playback_sessions.insert().values(
                video_id=sample_video["id"],
                session_token=session_token,
                started_at=now,
            )
        )

        await test_database.execute(
            playback_sessions.update()
            .where(playback_sessions.c.session_token == session_token)
            .values(
                ended_at=now,
                completed=True,
            )
        )

        result = await test_database.fetch_one(
            playback_sessions.select().where(playback_sessions.c.session_token == session_token)
        )
        assert result["ended_at"] is not None
        assert result["completed"] is True

    @pytest.mark.asyncio
    async def test_session_validation_accepts_ready_video(self, test_database, sample_video):
        """Test that session validation accepts ready videos."""
        # Verify the video can be found with the validation query
        video = await test_database.fetch_one(
            videos.select().where(
                videos.c.id == sample_video["id"],
                videos.c.status == VideoStatus.READY,
                videos.c.deleted_at.is_(None),
            )
        )
        assert video is not None
        assert video["id"] == sample_video["id"]
        assert video["status"] == VideoStatus.READY

    @pytest.mark.asyncio
    async def test_session_validation_rejects_nonexistent_video(self, test_database, sample_video):
        """Test that session validation rejects non-existent videos."""
        # Use an ID that's guaranteed not to exist (negative of existing max ID)
        nonexistent_id = -(sample_video["id"] + 1000)

        # Verify the video cannot be found with the validation query
        video = await test_database.fetch_one(
            videos.select().where(
                videos.c.id == nonexistent_id,
                videos.c.status == VideoStatus.READY,
                videos.c.deleted_at.is_(None),
            )
        )
        assert video is None

    @pytest.mark.asyncio
    async def test_session_validation_rejects_pending_video(self, test_database, sample_pending_video):
        """Test that session validation rejects pending videos."""
        # Verify the video cannot be found with the validation query
        video = await test_database.fetch_one(
            videos.select().where(
                videos.c.id == sample_pending_video["id"],
                videos.c.status == VideoStatus.READY,
                videos.c.deleted_at.is_(None),
            )
        )
        assert video is None

    @pytest.mark.asyncio
    async def test_session_validation_rejects_deleted_video(self, test_database, sample_deleted_video):
        """Test that session validation rejects soft-deleted videos."""
        # Verify the video cannot be found with the validation query
        video = await test_database.fetch_one(
            videos.select().where(
                videos.c.id == sample_deleted_video["id"],
                videos.c.status == VideoStatus.READY,
                videos.c.deleted_at.is_(None),
            )
        )
        assert video is None

    @pytest.mark.asyncio
    async def test_session_token_unique_constraint(self, test_database, sample_video):
        """Test that session_token has a unique constraint.

        Note: Uses sqlite3.IntegrityError because the databases library passes through
        the underlying SQLite driver exceptions. This is appropriate since VLog uses
        SQLite exclusively.
        """
        session_token = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Insert first session
        await test_database.execute(
            playback_sessions.insert().values(
                video_id=sample_video["id"],
                session_token=session_token,
                started_at=now,
            )
        )

        # Try to insert second session with the same session_token
        # This should raise an IntegrityError due to the unique constraint
        with pytest.raises(sqlite3.IntegrityError):
            await test_database.execute(
                playback_sessions.insert().values(
                    video_id=sample_video["id"],
                    session_token=session_token,  # Same token - should fail
                    started_at=now,
                )
            )


class TestTranscriptionEndpoints:
    """Tests for transcription endpoints."""

    @pytest.mark.asyncio
    async def test_get_transcript_none(self, test_database, sample_video):
        """Test getting transcript when none exists."""
        result = await test_database.fetch_one(
            transcriptions.select().where(transcriptions.c.video_id == sample_video["id"])
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

        result = await test_database.fetch_one(transcriptions.select().where(transcriptions.c.video_id == video_id))
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
            .select_from(videos.outerjoin(categories, videos.c.category_id == categories.c.id))
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
            .select_from(videos.outerjoin(categories, videos.c.category_id == categories.c.id))
            .where(categories.c.slug == "nonexistent")
        )

        result = await test_database.fetch_all(query)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_search_videos_by_title(self, test_database, sample_video):
        """Test searching videos by title."""
        search_term = "%Test%"
        query = videos.select().where(videos.c.title.ilike(search_term)).where(videos.c.status == VideoStatus.READY)

        result = await test_database.fetch_all(query)
        assert len(result) == 1
        assert result[0]["title"] == "Test Video"

    @pytest.mark.asyncio
    async def test_search_videos_no_match(self, test_database, sample_video):
        """Test searching videos with no matches."""
        search_term = "%nonexistent%"
        query = videos.select().where(videos.c.title.ilike(search_term))

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

        query = videos.select().where(videos.c.status == VideoStatus.READY).limit(5)

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
            videos.select().where(videos.c.status == VideoStatus.READY).order_by(videos.c.created_at).offset(5).limit(5)
        )

        result = await test_database.fetch_all(query)
        assert len(result) == 5
