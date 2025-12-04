"""
Integration tests for analytics caching with FastAPI endpoints.
"""

import pytest


class TestAnalyticsEndpointCaching:
    """Integration tests for analytics endpoint caching."""

    def test_analytics_overview_caching(self, admin_client):
        """Test that analytics overview endpoint uses cache."""
        # First request - cache miss
        response1 = admin_client.get("/api/analytics/overview")
        assert response1.status_code == 200
        data1 = response1.json()

        # Verify Cache-Control header is set
        assert "Cache-Control" in response1.headers
        assert "max-age" in response1.headers["Cache-Control"]
        assert "private" in response1.headers["Cache-Control"]

        # Second request - should hit cache
        response2 = admin_client.get("/api/analytics/overview")
        assert response2.status_code == 200
        data2 = response2.json()

        # Data should be identical (from cache)
        assert data1 == data2

    def test_analytics_videos_caching_with_params(self, admin_client):
        """Test that analytics videos endpoint caches based on parameters."""
        # First request with specific parameters
        response1 = admin_client.get("/api/analytics/videos?limit=10&offset=0&sort_by=views&period=all")
        assert response1.status_code == 200
        data1 = response1.json()

        # Verify Cache-Control header
        assert "Cache-Control" in response1.headers
        assert "private" in response1.headers["Cache-Control"]

        # Same parameters - should hit cache
        response2 = admin_client.get("/api/analytics/videos?limit=10&offset=0&sort_by=views&period=all")
        assert response2.status_code == 200
        data2 = response2.json()
        assert data1 == data2

        # Different parameters - should be cache miss (different cache key)
        response3 = admin_client.get("/api/analytics/videos?limit=20&offset=0&sort_by=views&period=all")
        assert response3.status_code == 200
        # data3 might be different or same depending on actual data

    def test_analytics_trends_caching_with_period(self, admin_client):
        """Test that analytics trends endpoint caches based on period."""
        # Test with different periods
        response1 = admin_client.get("/api/analytics/trends?period=7d")
        assert response1.status_code == 200
        data1 = response1.json()
        assert "Cache-Control" in response1.headers
        assert "private" in response1.headers["Cache-Control"]

        # Same period - should hit cache
        response2 = admin_client.get("/api/analytics/trends?period=7d")
        assert response2.status_code == 200
        data2 = response2.json()
        assert data1 == data2

        # Different period - different cache key
        response3 = admin_client.get("/api/analytics/trends?period=30d")
        assert response3.status_code == 200
        # Should have different cache key

    def test_analytics_trends_caching_with_video_id(self, admin_client):
        """Test that analytics trends endpoint caches based on video_id."""
        # Global trends (no video_id)
        response1 = admin_client.get("/api/analytics/trends?period=30d")
        assert response1.status_code == 200

        # Trends for specific video (may not exist, but uses different cache key)
        response2 = admin_client.get("/api/analytics/trends?period=30d&video_id=1")
        assert response2.status_code == 200
        # Should use different cache key even if data is similar

    def test_cache_control_headers_present(self, admin_client):
        """Test that all analytics endpoints return Cache-Control headers."""
        # Test overview
        response = admin_client.get("/api/analytics/overview")
        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        assert "private" in response.headers["Cache-Control"]

        # Test videos
        response = admin_client.get("/api/analytics/videos")
        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        assert "private" in response.headers["Cache-Control"]

        # Test trends
        response = admin_client.get("/api/analytics/trends")
        assert response.status_code == 200
        assert "Cache-Control" in response.headers
        assert "private" in response.headers["Cache-Control"]

    @pytest.mark.asyncio
    async def test_analytics_video_detail_caching(self, admin_client, sample_video):
        """Test that analytics video detail endpoint uses cache."""
        video_id = sample_video["id"]

        # First request - cache miss
        response1 = admin_client.get(f"/api/analytics/videos/{video_id}")
        assert response1.status_code == 200
        data1 = response1.json()

        # Verify Cache-Control header
        assert "Cache-Control" in response1.headers
        assert "private" in response1.headers["Cache-Control"]

        # Second request - should hit cache
        response2 = admin_client.get(f"/api/analytics/videos/{video_id}")
        assert response2.status_code == 200
        data2 = response2.json()

        # Data should be identical (from cache)
        assert data1 == data2
