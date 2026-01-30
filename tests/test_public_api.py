"""
Tests for the public API endpoints.

Includes both database-level tests and HTTP-level tests using FastAPI TestClient.
"""

import uuid
from datetime import datetime, timezone

import pytest

from api.database import categories, playback_sessions, transcriptions, video_qualities, video_tags, videos
from api.enums import TranscriptionStatus, VideoStatus
from api.errors import is_unique_violation

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
        data = response.json()
        assert data["videos"] == []
        assert data["has_more"] is False
        assert data["next_cursor"] is None

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
        assert len(data["videos"]) == 1
        assert data["videos"][0]["slug"] == "ready-video"

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
        assert len(data["videos"]) == 1
        assert data["videos"][0]["slug"] == "active-video"

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
        assert len(data["videos"]) == 1
        assert data["videos"][0]["slug"] == "test-video"

    def test_filter_videos_by_nonexistent_category(self, public_client):
        """Test filtering by non-existent category returns empty."""
        response = public_client.get("/api/videos?category=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["videos"] == []

    @pytest.mark.asyncio
    async def test_search_videos(self, public_client, sample_video):
        """Test searching videos by title."""
        response = public_client.get("/api/videos?search=Test")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["title"] == "Test Video"

    def test_search_videos_no_match(self, public_client):
        """Test searching with no matches returns empty."""
        response = public_client.get("/api/videos?search=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["videos"] == []

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
        data = response.json()
        assert len(data["videos"]) == 3
        assert data["has_more"] is True  # More videos exist

        # Test offset (legacy pagination)
        response = public_client.get("/api/videos?limit=3&offset=3")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        assert data["has_more"] is False  # No more videos


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

    def test_heartbeat_session_token_too_long(self, public_client):
        """Test heartbeat with session token exceeding max length (64 chars) fails."""
        response = public_client.post(
            "/api/analytics/heartbeat",
            json={
                "session_token": "a" * 65,  # Exceeds 64 character limit
                "position": 30.0,
                "playing": True,
            },
        )
        assert response.status_code == 422  # Validation error

    def test_end_session_token_too_long(self, public_client):
        """Test ending session with session token exceeding max length (64 chars) fails."""
        response = public_client.post(
            "/api/analytics/end",
            json={
                "session_token": "a" * 65,  # Exceeds 64 character limit
                "position": 120.0,
                "completed": True,
            },
        )
        assert response.status_code == 422  # Validation error


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

        Uses database-agnostic error detection to work with both SQLite and PostgreSQL.
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
        # This should raise a unique constraint violation
        with pytest.raises(Exception) as exc_info:
            await test_database.execute(
                playback_sessions.insert().values(
                    video_id=sample_video["id"],
                    session_token=session_token,  # Same token - should fail
                    started_at=now,
                )
            )

        # Verify it's a unique constraint violation
        assert is_unique_violation(exc_info.value, column="session_token")


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


# ============================================================================
# Security Headers Tests
# ============================================================================


class TestSecurityHeaders:
    """Tests for security headers in API responses."""

    def test_content_security_policy_header(self, public_client):
        """Test that Content-Security-Policy header is present in responses."""
        response = public_client.get("/health")
        assert "Content-Security-Policy" in response.headers
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_x_frame_options_header(self, public_client):
        """Test that X-Frame-Options header is present."""
        response = public_client.get("/health")
        assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"

    def test_x_content_type_options_header(self, public_client):
        """Test that X-Content-Type-Options header is present."""
        response = public_client.get("/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_referrer_policy_header(self, public_client):
        """Test that Referrer-Policy header is present."""
        response = public_client.get("/health")
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy_header(self, public_client):
        """Test that Permissions-Policy header is present."""
        response = public_client.get("/health")
        assert "Permissions-Policy" in response.headers


# ============================================================================
# Related Videos Tests
# ============================================================================


class TestRelatedVideosHTTP:
    """HTTP-level tests for related videos endpoint."""

    def test_related_videos_not_found(self, public_client):
        """Test related videos returns 404 for non-existent video."""
        response = public_client.get("/api/videos/nonexistent-slug/related")
        assert response.status_code == 404
        assert response.json()["detail"] == "Video not found"

    @pytest.mark.asyncio
    async def test_related_videos_excludes_deleted(self, public_client, sample_deleted_video):
        """Test related videos returns 404 for deleted video."""
        response = public_client.get(f"/api/videos/{sample_deleted_video['slug']}/related")
        assert response.status_code == 404
        assert response.json()["detail"] == "Video not found"

    @pytest.mark.asyncio
    async def test_related_videos_empty_when_no_related(self, public_client, sample_video):
        """Test related videos returns empty list when no related videos exist."""
        response = public_client.get(f"/api/videos/{sample_video['slug']}/related")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_related_videos_same_category(self, public_client, test_database, sample_video, sample_category):
        """Test related videos returns videos from same category."""
        now = datetime.now(timezone.utc)
        # Create another video in the same category
        await test_database.execute(
            videos.insert().values(
                title="Related Video",
                slug="related-video",
                description="Another video in the same category",
                category_id=sample_category["id"],
                duration=90.0,
                status=VideoStatus.READY,
                created_at=now,
                published_at=now,
            )
        )

        response = public_client.get(f"/api/videos/{sample_video['slug']}/related")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slug"] == "related-video"

    @pytest.mark.asyncio
    async def test_related_videos_excludes_current_video(
        self, public_client, test_database, sample_video, sample_category
    ):
        """Test related videos excludes the current video from results."""
        now = datetime.now(timezone.utc)
        # Create another video in the same category
        await test_database.execute(
            videos.insert().values(
                title="Related Video",
                slug="related-video",
                description="Another video",
                category_id=sample_category["id"],
                duration=90.0,
                status=VideoStatus.READY,
                created_at=now,
                published_at=now,
            )
        )

        response = public_client.get(f"/api/videos/{sample_video['slug']}/related")
        assert response.status_code == 200
        data = response.json()
        # Should only have the related video, not the current one
        slugs = [v["slug"] for v in data]
        assert sample_video["slug"] not in slugs
        assert "related-video" in slugs

    @pytest.mark.asyncio
    async def test_related_videos_shared_tags(self, public_client, test_database, sample_video_with_tag, sample_tag):
        """Test related videos returns videos with shared tags."""
        now = datetime.now(timezone.utc)
        # Create a video in a different category (or no category)
        video_id = await test_database.execute(
            videos.insert().values(
                title="Tagged Video",
                slug="tagged-video",
                description="A video with the same tag",
                category_id=None,  # Different category
                duration=60.0,
                status=VideoStatus.READY,
                created_at=now,
                published_at=now,
            )
        )
        # Attach the same tag
        await test_database.execute(
            video_tags.insert().values(
                video_id=video_id,
                tag_id=sample_tag["id"],
            )
        )

        response = public_client.get(f"/api/videos/{sample_video_with_tag['slug']}/related")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        slugs = [v["slug"] for v in data]
        assert "tagged-video" in slugs

    @pytest.mark.asyncio
    async def test_related_videos_excludes_deleted_videos(
        self, public_client, test_database, sample_video, sample_category
    ):
        """Test related videos excludes soft-deleted videos from results."""
        now = datetime.now(timezone.utc)
        # Create a deleted video in the same category
        await test_database.execute(
            videos.insert().values(
                title="Deleted Related Video",
                slug="deleted-related",
                description="A deleted video",
                category_id=sample_category["id"],
                duration=90.0,
                status=VideoStatus.READY,
                created_at=now,
                published_at=now,
                deleted_at=now,  # Soft deleted
            )
        )

        response = public_client.get(f"/api/videos/{sample_video['slug']}/related")
        assert response.status_code == 200
        data = response.json()
        slugs = [v["slug"] for v in data]
        assert "deleted-related" not in slugs

    @pytest.mark.asyncio
    async def test_related_videos_excludes_non_ready_videos(
        self, public_client, test_database, sample_video, sample_category
    ):
        """Test related videos excludes non-ready (pending/processing) videos."""
        now = datetime.now(timezone.utc)
        # Create a pending video in the same category
        await test_database.execute(
            videos.insert().values(
                title="Pending Related Video",
                slug="pending-related",
                description="A pending video",
                category_id=sample_category["id"],
                duration=0,
                status=VideoStatus.PENDING,
                created_at=now,
            )
        )

        response = public_client.get(f"/api/videos/{sample_video['slug']}/related")
        assert response.status_code == 200
        data = response.json()
        slugs = [v["slug"] for v in data]
        assert "pending-related" not in slugs

    @pytest.mark.asyncio
    async def test_related_videos_limit_parameter(self, public_client, test_database, sample_video, sample_category):
        """Test related videos respects limit parameter."""
        now = datetime.now(timezone.utc)
        # Create multiple videos in the same category
        for i in range(5):
            await test_database.execute(
                videos.insert().values(
                    title=f"Related Video {i}",
                    slug=f"related-video-{i}",
                    description=f"Related video {i}",
                    category_id=sample_category["id"],
                    duration=60.0,
                    status=VideoStatus.READY,
                    created_at=now,
                    published_at=now,
                )
            )

        # Test with limit=2
        response = public_client.get(f"/api/videos/{sample_video['slug']}/related?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_related_videos_limit_min_bound(self, public_client, sample_video):
        """Test related videos rejects limit below minimum (1)."""
        response = public_client.get("/api/videos/test-video/related?limit=0")
        assert response.status_code == 422  # Validation error

    def test_related_videos_limit_max_bound(self, public_client, sample_video):
        """Test related videos rejects limit above maximum (24)."""
        response = public_client.get("/api/videos/test-video/related?limit=25")
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_related_videos_returns_correct_fields(
        self, public_client, test_database, sample_video, sample_category
    ):
        """Test related videos returns expected fields."""
        now = datetime.now(timezone.utc)
        await test_database.execute(
            videos.insert().values(
                title="Related Video",
                slug="related-video",
                description="A related video",
                category_id=sample_category["id"],
                duration=120.0,
                source_width=1920,
                source_height=1080,
                status=VideoStatus.READY,
                created_at=now,
                published_at=now,
            )
        )

        response = public_client.get(f"/api/videos/{sample_video['slug']}/related")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

        video = data[0]
        # Check expected fields are present
        assert "id" in video
        assert "title" in video
        assert "slug" in video
        assert "duration" in video
        assert video["title"] == "Related Video"
        assert video["slug"] == "related-video"

    @pytest.mark.asyncio
    async def test_related_videos_fallback_to_recent(self, public_client, test_database, sample_category):
        """Test related videos falls back to recent videos when no matches."""
        now = datetime.now(timezone.utc)
        # Create a video with no category or tags
        await test_database.execute(
            videos.insert().values(
                title="Isolated Video",
                slug="isolated-video",
                description="A video with no category",
                category_id=None,
                duration=60.0,
                status=VideoStatus.READY,
                created_at=now,
                published_at=now,
            )
        )
        # Create some recent videos
        for i in range(3):
            await test_database.execute(
                videos.insert().values(
                    title=f"Recent Video {i}",
                    slug=f"recent-video-{i}",
                    description=f"Recent video {i}",
                    category_id=sample_category["id"],
                    duration=60.0,
                    status=VideoStatus.READY,
                    created_at=now,
                    published_at=now,
                )
            )

        response = public_client.get("/api/videos/isolated-video/related")
        assert response.status_code == 200
        data = response.json()
        # Should return recent videos as fallback
        assert len(data) >= 1
