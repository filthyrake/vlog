"""
Tests for enhanced search filters and sorting.
"""

from datetime import datetime, timedelta, timezone

import pytest

from api.database import playback_sessions, transcriptions, video_qualities, videos, viewers
from api.enums import TranscriptionStatus, VideoStatus


class TestSearchFilters:
    """Test search filter functionality."""

    @pytest.mark.asyncio
    async def test_duration_filter_short(self, public_client, test_database):
        """Test filtering by short duration (<5min)."""
        now = datetime.now(timezone.utc)

        # Create videos with different durations
        await test_database.execute(
            videos.insert().values(
                title="Short Video",
                slug="short-video",
                duration=240,  # 4 minutes
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Medium Video",
                slug="medium-video",
                duration=600,  # 10 minutes
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Long Video",
                slug="long-video",
                duration=1500,  # 25 minutes
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?duration=short")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["slug"] == "short-video"

    @pytest.mark.asyncio
    async def test_duration_filter_medium(self, public_client, test_database):
        """Test filtering by medium duration (5-20min)."""
        now = datetime.now(timezone.utc)

        await test_database.execute(
            videos.insert().values(
                title="Short Video",
                slug="short-video",
                duration=240,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Medium Video 1",
                slug="medium-video-1",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Medium Video 2",
                slug="medium-video-2",
                duration=900,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?duration=medium")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        slugs = [v["slug"] for v in data["videos"]]
        assert "medium-video-1" in slugs
        assert "medium-video-2" in slugs

    @pytest.mark.asyncio
    async def test_duration_filter_long(self, public_client, test_database):
        """Test filtering by long duration (>20min)."""
        now = datetime.now(timezone.utc)

        await test_database.execute(
            videos.insert().values(
                title="Medium Video",
                slug="medium-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Long Video",
                slug="long-video",
                duration=1500,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?duration=long")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["slug"] == "long-video"

    @pytest.mark.asyncio
    async def test_duration_filter_multiple(self, public_client, test_database):
        """Test filtering by multiple duration ranges."""
        now = datetime.now(timezone.utc)

        await test_database.execute(
            videos.insert().values(
                title="Short Video",
                slug="short-video",
                duration=240,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Medium Video",
                slug="medium-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Long Video",
                slug="long-video",
                duration=1500,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?duration=short,long")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        slugs = [v["slug"] for v in data["videos"]]
        assert "short-video" in slugs
        assert "long-video" in slugs
        assert "medium-video" not in slugs

    @pytest.mark.asyncio
    async def test_quality_filter(self, public_client, test_database):
        """Test filtering by available quality."""
        now = datetime.now(timezone.utc)

        # Create videos
        video_1080_id = await test_database.execute(
            videos.insert().values(
                title="1080p Video",
                slug="video-1080p",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        video_4k_id = await test_database.execute(
            videos.insert().values(
                title="4K Video",
                slug="video-4k",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        video_both_id = await test_database.execute(
            videos.insert().values(
                title="4K and 1080p Video",
                slug="video-both",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        # Add quality variants
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_1080_id, quality="1080p", width=1920, height=1080, bitrate=5000
            )
        )
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_4k_id, quality="2160p", width=3840, height=2160, bitrate=15000
            )
        )
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_both_id, quality="1080p", width=1920, height=1080, bitrate=5000
            )
        )
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_both_id, quality="2160p", width=3840, height=2160, bitrate=15000
            )
        )

        # Filter by 1080p
        response = public_client.get("/api/videos?quality=1080p")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        slugs = [v["slug"] for v in data["videos"]]
        assert "video-1080p" in slugs
        assert "video-both" in slugs

        # Filter by 2160p (4K)
        response = public_client.get("/api/videos?quality=2160p")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        slugs = [v["slug"] for v in data["videos"]]
        assert "video-4k" in slugs
        assert "video-both" in slugs

        # Filter by multiple qualities
        response = public_client.get("/api/videos?quality=1080p,2160p")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 3

    @pytest.mark.asyncio
    async def test_date_range_filter(self, public_client, test_database):
        """Test filtering by publication date range."""
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        await test_database.execute(
            videos.insert().values(
                title="Old Video",
                slug="old-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=two_days_ago,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Yesterday Video",
                slug="yesterday-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=yesterday,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Today Video",
                slug="today-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        # Filter from yesterday (use params= for proper URL encoding of + in timezone)
        response = public_client.get("/api/videos", params={"date_from": yesterday.isoformat()})
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        slugs = [v["slug"] for v in data["videos"]]
        assert "yesterday-video" in slugs
        assert "today-video" in slugs

        # Filter until yesterday
        response = public_client.get("/api/videos", params={"date_to": yesterday.isoformat()})
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        slugs = [v["slug"] for v in data["videos"]]
        assert "old-video" in slugs
        assert "yesterday-video" in slugs

        # Filter range
        response = public_client.get(
            "/api/videos",
            params={"date_from": two_days_ago.isoformat(), "date_to": yesterday.isoformat()},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        slugs = [v["slug"] for v in data["videos"]]
        assert "old-video" in slugs
        assert "yesterday-video" in slugs

    @pytest.mark.asyncio
    async def test_has_transcription_filter(self, public_client, test_database):
        """Test filtering by transcription availability."""
        now = datetime.now(timezone.utc)

        # Video with transcription
        video_with_id = await test_database.execute(
            videos.insert().values(
                title="Video with Transcription",
                slug="video-with-transcription",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_with_id,
                status=TranscriptionStatus.COMPLETED,
                language="en",
                transcript_text="Sample transcript",
            )
        )

        # Video without transcription
        await test_database.execute(
            videos.insert().values(
                title="Video without Transcription",
                slug="video-without-transcription",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        # Video with pending transcription (should be filtered out)
        video_pending_id = await test_database.execute(
            videos.insert().values(
                title="Video with Pending Transcription",
                slug="video-pending-transcription",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_pending_id,
                status=TranscriptionStatus.PENDING,
            )
        )

        # Filter for videos with transcription
        response = public_client.get("/api/videos?has_transcription=true")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["slug"] == "video-with-transcription"

        # Filter for videos without transcription
        response = public_client.get("/api/videos?has_transcription=false")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        slugs = [v["slug"] for v in data["videos"]]
        assert "video-without-transcription" in slugs
        assert "video-pending-transcription" in slugs


class TestSearchSorting:
    """Test sorting functionality."""

    @pytest.mark.asyncio
    async def test_sort_by_date_desc(self, public_client, test_database):
        """Test sorting by date descending (newest first)."""
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        await test_database.execute(
            videos.insert().values(
                title="Old Video",
                slug="old-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=two_days_ago,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Yesterday Video",
                slug="yesterday-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=yesterday,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Today Video",
                slug="today-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?sort=date&order=desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 3
        assert data["videos"][0]["slug"] == "today-video"
        assert data["videos"][1]["slug"] == "yesterday-video"
        assert data["videos"][2]["slug"] == "old-video"

    @pytest.mark.asyncio
    async def test_sort_by_date_asc(self, public_client, test_database):
        """Test sorting by date ascending (oldest first)."""
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        await test_database.execute(
            videos.insert().values(
                title="Old Video",
                slug="old-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=two_days_ago,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Yesterday Video",
                slug="yesterday-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=yesterday,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Today Video",
                slug="today-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?sort=date&order=asc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 3
        assert data["videos"][0]["slug"] == "old-video"
        assert data["videos"][1]["slug"] == "yesterday-video"
        assert data["videos"][2]["slug"] == "today-video"

    @pytest.mark.asyncio
    async def test_sort_by_duration_desc(self, public_client, test_database):
        """Test sorting by duration descending (longest first)."""
        now = datetime.now(timezone.utc)

        await test_database.execute(
            videos.insert().values(
                title="Short Video",
                slug="short-video",
                duration=300,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Medium Video",
                slug="medium-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Long Video",
                slug="long-video",
                duration=1200,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?sort=duration&order=desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 3
        assert data["videos"][0]["slug"] == "long-video"
        assert data["videos"][1]["slug"] == "medium-video"
        assert data["videos"][2]["slug"] == "short-video"

    @pytest.mark.asyncio
    async def test_sort_by_duration_asc(self, public_client, test_database):
        """Test sorting by duration ascending (shortest first)."""
        now = datetime.now(timezone.utc)

        await test_database.execute(
            videos.insert().values(
                title="Short Video",
                slug="short-video",
                duration=300,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Medium Video",
                slug="medium-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Long Video",
                slug="long-video",
                duration=1200,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?sort=duration&order=asc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 3
        assert data["videos"][0]["slug"] == "short-video"
        assert data["videos"][1]["slug"] == "medium-video"
        assert data["videos"][2]["slug"] == "long-video"

    @pytest.mark.asyncio
    async def test_sort_by_title_asc(self, public_client, test_database):
        """Test sorting by title ascending (alphabetical A-Z)."""
        now = datetime.now(timezone.utc)

        await test_database.execute(
            videos.insert().values(
                title="Charlie Video",
                slug="charlie-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Alpha Video",
                slug="alpha-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Bravo Video",
                slug="bravo-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?sort=title&order=asc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 3
        assert data["videos"][0]["slug"] == "alpha-video"
        assert data["videos"][1]["slug"] == "bravo-video"
        assert data["videos"][2]["slug"] == "charlie-video"

    @pytest.mark.asyncio
    async def test_sort_by_title_desc(self, public_client, test_database):
        """Test sorting by title descending (reverse alphabetical Z-A)."""
        now = datetime.now(timezone.utc)

        await test_database.execute(
            videos.insert().values(
                title="Charlie Video",
                slug="charlie-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Alpha Video",
                slug="alpha-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Bravo Video",
                slug="bravo-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?sort=title&order=desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 3
        assert data["videos"][0]["slug"] == "charlie-video"
        assert data["videos"][1]["slug"] == "bravo-video"
        assert data["videos"][2]["slug"] == "alpha-video"

    @pytest.mark.asyncio
    async def test_sort_by_views_desc(self, public_client, test_database):
        """Test sorting by views descending (most viewed first)."""
        now = datetime.now(timezone.utc)

        # Create videos
        video_1_id = await test_database.execute(
            videos.insert().values(
                title="Popular Video",
                slug="popular-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        video_2_id = await test_database.execute(
            videos.insert().values(
                title="Medium Video",
                slug="medium-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Unpopular Video",
                slug="unpopular-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        # Create a viewer
        viewer_id = await test_database.execute(
            viewers.insert().values(
                session_id="test-viewer-1",
                first_seen=now,
                last_seen=now,
            )
        )

        # Create playback sessions (views)
        # Popular video: 5 views
        for i in range(5):
            await test_database.execute(
                playback_sessions.insert().values(
                    video_id=video_1_id,
                    viewer_id=viewer_id,
                    session_token=f"session-1-{i}",
                    started_at=now,
                )
            )

        # Medium video: 2 views
        for i in range(2):
            await test_database.execute(
                playback_sessions.insert().values(
                    video_id=video_2_id,
                    viewer_id=viewer_id,
                    session_token=f"session-2-{i}",
                    started_at=now,
                )
            )

        # Unpopular video: no views

        response = public_client.get("/api/videos?sort=views&order=desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 3
        assert data["videos"][0]["slug"] == "popular-video"
        assert data["videos"][1]["slug"] == "medium-video"
        assert data["videos"][2]["slug"] == "unpopular-video"

    @pytest.mark.asyncio
    async def test_sort_by_views_asc(self, public_client, test_database):
        """Test sorting by views ascending (least viewed first)."""
        now = datetime.now(timezone.utc)

        # Create videos
        video_1_id = await test_database.execute(
            videos.insert().values(
                title="Popular Video",
                slug="popular-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Unpopular Video",
                slug="unpopular-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        # Create a viewer
        viewer_id = await test_database.execute(
            viewers.insert().values(
                session_id="test-viewer-2",
                first_seen=now,
                last_seen=now,
            )
        )

        # Popular video: 3 views
        for i in range(3):
            await test_database.execute(
                playback_sessions.insert().values(
                    video_id=video_1_id,
                    viewer_id=viewer_id,
                    session_token=f"session-3-{i}",
                    started_at=now,
                )
            )

        response = public_client.get("/api/videos?sort=views&order=asc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        assert data["videos"][0]["slug"] == "unpopular-video"
        assert data["videos"][1]["slug"] == "popular-video"

    @pytest.mark.asyncio
    async def test_default_sort_with_search(self, public_client, test_database):
        """Test that default sort for search queries is relevance (date desc as fallback)."""
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)

        await test_database.execute(
            videos.insert().values(
                title="Testing Video",
                slug="testing-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=yesterday,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Another Testing Video",
                slug="another-testing-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?search=Testing")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        # Should be sorted by date desc (most recent first) as relevance fallback
        assert data["videos"][0]["slug"] == "another-testing-video"
        assert data["videos"][1]["slug"] == "testing-video"

    @pytest.mark.asyncio
    async def test_default_sort_without_search(self, public_client, test_database):
        """Test that default sort without search is date descending."""
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)

        await test_database.execute(
            videos.insert().values(
                title="Old Video",
                slug="old-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=yesterday,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="New Video",
                slug="new-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        assert data["videos"][0]["slug"] == "new-video"
        assert data["videos"][1]["slug"] == "old-video"


class TestCombinedFiltersAndSorting:
    """Test combinations of filters and sorting."""

    @pytest.mark.asyncio
    async def test_duration_filter_with_sort(self, public_client, test_database):
        """Test combining duration filter with sorting."""
        now = datetime.now(timezone.utc)

        await test_database.execute(
            videos.insert().values(
                title="Short Video A",
                slug="short-video-a",
                duration=200,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Short Video B",
                slug="short-video-b",
                duration=100,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Long Video",
                slug="long-video",
                duration=1500,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?duration=short&sort=duration&order=asc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        assert data["videos"][0]["slug"] == "short-video-b"  # 100 seconds
        assert data["videos"][1]["slug"] == "short-video-a"  # 200 seconds

    @pytest.mark.asyncio
    async def test_search_with_quality_filter_and_sort(self, public_client, test_database):
        """Test combining search, quality filter, and sorting."""
        now = datetime.now(timezone.utc)

        # Create videos
        video_1_id = await test_database.execute(
            videos.insert().values(
                title="Tutorial in 4K",
                slug="tutorial-4k",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        video_2_id = await test_database.execute(
            videos.insert().values(
                title="Tutorial in HD",
                slug="tutorial-hd",
                duration=300,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Guide in SD",
                slug="guide-sd",
                duration=400,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        # Add quality variants
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_1_id, quality="2160p", width=3840, height=2160, bitrate=15000
            )
        )
        await test_database.execute(
            video_qualities.insert().values(video_id=video_2_id, quality="1080p", width=1920, height=1080, bitrate=5000)
        )

        response = public_client.get("/api/videos?search=tutorial&quality=1080p,2160p&sort=duration&order=desc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 2
        assert data["videos"][0]["slug"] == "tutorial-4k"  # 600 seconds, longer
        assert data["videos"][1]["slug"] == "tutorial-hd"  # 300 seconds, shorter

    @pytest.mark.asyncio
    async def test_all_filters_combined(self, public_client, test_database):
        """Test combining multiple filters."""
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)

        # Video that matches all filters
        video_match_id = await test_database.execute(
            videos.insert().values(
                title="Perfect Match Video",
                slug="perfect-match",
                duration=800,  # Medium (5-20 min)
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_match_id, quality="1080p", width=1920, height=1080, bitrate=5000
            )
        )
        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_match_id,
                status=TranscriptionStatus.COMPLETED,
                language="en",
                transcript_text="Sample",
            )
        )

        # Video that doesn't match duration
        video_wrong_duration_id = await test_database.execute(
            videos.insert().values(
                title="Wrong Duration Video",
                slug="wrong-duration",
                duration=200,  # Short
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_wrong_duration_id, quality="1080p", width=1920, height=1080, bitrate=5000
            )
        )
        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_wrong_duration_id,
                status=TranscriptionStatus.COMPLETED,
                language="en",
                transcript_text="Sample",
            )
        )

        # Video that doesn't match quality
        video_wrong_quality_id = await test_database.execute(
            videos.insert().values(
                title="Wrong Quality Video",
                slug="wrong-quality",
                duration=800,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_wrong_quality_id, quality="720p", width=1280, height=720, bitrate=2500
            )
        )
        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_wrong_quality_id,
                status=TranscriptionStatus.COMPLETED,
                language="en",
                transcript_text="Sample",
            )
        )

        # Video without transcription
        video_no_trans_id = await test_database.execute(
            videos.insert().values(
                title="No Transcription Video",
                slug="no-transcription",
                duration=800,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_no_trans_id, quality="1080p", width=1920, height=1080, bitrate=5000
            )
        )

        # Video from yesterday (wrong date)
        video_old_id = await test_database.execute(
            videos.insert().values(
                title="Old Video",
                slug="old-video",
                duration=800,
                status=VideoStatus.READY,
                published_at=yesterday,
            )
        )
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_old_id, quality="1080p", width=1920, height=1080, bitrate=5000
            )
        )
        await test_database.execute(
            transcriptions.insert().values(
                video_id=video_old_id,
                status=TranscriptionStatus.COMPLETED,
                language="en",
                transcript_text="Sample",
            )
        )

        # Apply all filters (use params= for proper URL encoding of + in timezone)
        date_from_str = (now - timedelta(hours=1)).isoformat()
        response = public_client.get(
            "/api/videos",
            params={
                "duration": "medium",
                "quality": "1080p",
                "has_transcription": "true",
                "date_from": date_from_str,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 1
        assert data["videos"][0]["slug"] == "perfect-match"


class TestInvalidInputValidation:
    """Test validation of invalid input parameters."""

    def test_invalid_duration_value(self, public_client):
        """Test that invalid duration values return 400 error."""
        response = public_client.get("/api/videos?duration=invalid")
        assert response.status_code == 400
        assert "Invalid duration value" in response.json()["detail"]

    def test_invalid_duration_multiple_values(self, public_client):
        """Test that one invalid duration among valid ones returns 400 error."""
        response = public_client.get("/api/videos?duration=short,xshort,medium")
        assert response.status_code == 400
        assert "Invalid duration value" in response.json()["detail"]

    def test_invalid_quality_silently_ignored(self, public_client):
        """Test that invalid quality values are silently ignored (existing behavior)."""
        # Invalid quality values are filtered out, not rejected
        response = public_client.get("/api/videos?quality=8k,invalid")
        assert response.status_code == 200
        # This tests current behavior - invalid qualities are just filtered out

    def test_invalid_sort_value(self, public_client):
        """Test that invalid sort values return 400 error."""
        response = public_client.get("/api/videos?sort=popularity")
        assert response.status_code == 400
        assert "Invalid sort value" in response.json()["detail"]

    def test_invalid_sort_rating(self, public_client):
        """Test that invalid sort value 'rating' returns 400 error."""
        response = public_client.get("/api/videos?sort=rating")
        assert response.status_code == 400
        assert "Invalid sort value" in response.json()["detail"]

    def test_invalid_order_value(self, public_client):
        """Test that invalid order values return 400 error."""
        response = public_client.get("/api/videos?order=ascending")
        assert response.status_code == 400
        assert "Invalid order value" in response.json()["detail"]

    def test_invalid_order_random(self, public_client):
        """Test that invalid order value 'random' returns 400 error."""
        response = public_client.get("/api/videos?order=random")
        assert response.status_code == 400
        assert "Invalid order value" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_date_from_after_date_to(self, public_client, test_database):
        """Test that date_from after date_to returns 400 error."""
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)

        # Create a video
        await test_database.execute(
            videos.insert().values(
                title="Test Video",
                slug="test-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        # date_from is after date_to (use params= for proper URL encoding)
        response = public_client.get(
            "/api/videos",
            params={"date_from": now.isoformat(), "date_to": yesterday.isoformat()},
        )
        assert response.status_code == 400
        assert "Invalid date range" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_date_from_equal_to_date_to(self, public_client, test_database):
        """Test that date_from equal to date_to is valid."""
        now = datetime.now(timezone.utc)

        # Create a video
        await test_database.execute(
            videos.insert().values(
                title="Test Video",
                slug="test-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        # date_from equals date_to - should be valid (use params= for proper URL encoding)
        response = public_client.get(
            "/api/videos",
            params={"date_from": now.isoformat(), "date_to": now.isoformat()},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_future_date_filter(self, public_client, test_database):
        """Test filtering with future dates (edge case)."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=30)

        # Create a video published today
        await test_database.execute(
            videos.insert().values(
                title="Test Video",
                slug="test-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        # Filter for videos published in the future (use params= for proper URL encoding)
        response = public_client.get("/api/videos", params={"date_from": future.isoformat()})
        assert response.status_code == 200
        data = response.json()
        # Should return empty list since no videos are published in the future
        assert len(data["videos"]) == 0


class TestTitleSortingCaseSensitivity:
    """Test case-insensitive title sorting."""

    @pytest.mark.asyncio
    async def test_title_sorting_case_insensitive(self, public_client, test_database):
        """Test that title sorting is case-insensitive."""
        now = datetime.now(timezone.utc)

        # Create videos with mixed case titles
        await test_database.execute(
            videos.insert().values(
                title="Zebra Video",
                slug="zebra-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="apple Video",
                slug="apple-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )
        await test_database.execute(
            videos.insert().values(
                title="Banana Video",
                slug="banana-video",
                duration=600,
                status=VideoStatus.READY,
                published_at=now,
            )
        )

        response = public_client.get("/api/videos?sort=title&order=asc")
        assert response.status_code == 200
        data = response.json()
        assert len(data["videos"]) == 3
        # Should be sorted alphabetically regardless of case
        assert data["videos"][0]["slug"] == "apple-video"
        assert data["videos"][1]["slug"] == "banana-video"
        assert data["videos"][2]["slug"] == "zebra-video"
