"""
Tests for CDN integration functionality.
See: https://github.com/filthyrake/vlog/issues/222
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.public import (
    get_cdn_settings,
    get_video_url_prefix,
    reset_cdn_settings_cache,
)


class TestCDNSettings:
    """Test suite for CDN settings retrieval and caching."""

    def setup_method(self):
        """Reset CDN cache before each test."""
        reset_cdn_settings_cache()

    def teardown_method(self):
        """Reset CDN cache after each test."""
        reset_cdn_settings_cache()

    @pytest.mark.asyncio
    async def test_get_cdn_settings_defaults_when_not_configured(self):
        """Test that CDN defaults to disabled when not configured."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get = AsyncMock(side_effect=lambda key, default: default)
            mock_get_service.return_value = mock_service

            settings = await get_cdn_settings()

            assert settings["enabled"] is False
            assert settings["base_url"] == ""

    @pytest.mark.asyncio
    async def test_get_cdn_settings_returns_configured_values(self):
        """Test that CDN settings returns configured values from database."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()

            async def mock_get(key, default):
                if key == "cdn.enabled":
                    return True
                if key == "cdn.base_url":
                    return "https://cdn.example.com"
                return default

            mock_service.get = mock_get
            mock_get_service.return_value = mock_service

            settings = await get_cdn_settings()

            assert settings["enabled"] is True
            assert settings["base_url"] == "https://cdn.example.com"

    @pytest.mark.asyncio
    async def test_get_cdn_settings_caches_results(self):
        """Test that CDN settings are cached and not refetched immediately."""
        call_count = 0

        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()

            async def mock_get(key, default):
                nonlocal call_count
                call_count += 1
                if key == "cdn.enabled":
                    return True
                if key == "cdn.base_url":
                    return "https://cdn.example.com"
                return default

            mock_service.get = mock_get
            mock_get_service.return_value = mock_service

            # First call should hit the service
            settings1 = await get_cdn_settings()
            assert call_count == 2  # cdn.enabled and cdn.base_url

            # Second call should use cache
            settings2 = await get_cdn_settings()
            assert call_count == 2  # No additional calls

            assert settings1 == settings2

    @pytest.mark.asyncio
    async def test_get_cdn_settings_cache_invalidation(self):
        """Test that reset_cdn_settings_cache clears the cache."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()
            call_count = 0

            async def mock_get(key, default):
                nonlocal call_count
                call_count += 1
                return default

            mock_service.get = mock_get
            mock_get_service.return_value = mock_service

            # First call
            await get_cdn_settings()
            first_count = call_count

            # Invalidate cache
            reset_cdn_settings_cache()

            # Second call should hit the service again
            await get_cdn_settings()
            assert call_count > first_count

    @pytest.mark.asyncio
    async def test_get_cdn_settings_falls_back_on_error(self):
        """Test that CDN settings defaults to disabled on database error."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_get_service.side_effect = Exception("Database unavailable")

            settings = await get_cdn_settings()

            assert settings["enabled"] is False
            assert settings["base_url"] == ""


class TestVideoURLPrefix:
    """Test suite for video URL prefix generation."""

    def setup_method(self):
        """Reset CDN cache before each test."""
        reset_cdn_settings_cache()

    def teardown_method(self):
        """Reset CDN cache after each test."""
        reset_cdn_settings_cache()

    @pytest.mark.asyncio
    async def test_get_video_url_prefix_empty_when_disabled(self):
        """Test that URL prefix is empty when CDN is disabled."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get = AsyncMock(side_effect=lambda key, default: default)
            mock_get_service.return_value = mock_service

            prefix = await get_video_url_prefix()

            assert prefix == ""

    @pytest.mark.asyncio
    async def test_get_video_url_prefix_returns_cdn_url_when_enabled(self):
        """Test that URL prefix returns CDN base URL when enabled."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()

            async def mock_get(key, default):
                if key == "cdn.enabled":
                    return True
                if key == "cdn.base_url":
                    return "https://cdn.example.com"
                return default

            mock_service.get = mock_get
            mock_get_service.return_value = mock_service

            prefix = await get_video_url_prefix()

            assert prefix == "https://cdn.example.com"

    @pytest.mark.asyncio
    async def test_get_video_url_prefix_strips_trailing_slash(self):
        """Test that trailing slash is stripped from CDN URL."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()

            async def mock_get(key, default):
                if key == "cdn.enabled":
                    return True
                if key == "cdn.base_url":
                    return "https://cdn.example.com/"
                return default

            mock_service.get = mock_get
            mock_get_service.return_value = mock_service

            prefix = await get_video_url_prefix()

            assert prefix == "https://cdn.example.com"
            assert not prefix.endswith("/")

    @pytest.mark.asyncio
    async def test_get_video_url_prefix_empty_when_base_url_empty(self):
        """Test that URL prefix is empty when CDN is enabled but no URL configured."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()

            async def mock_get(key, default):
                if key == "cdn.enabled":
                    return True
                if key == "cdn.base_url":
                    return ""  # Empty URL
                return default

            mock_service.get = mock_get
            mock_get_service.return_value = mock_service

            prefix = await get_video_url_prefix()

            assert prefix == ""


class TestCDNURLGeneration:
    """Test suite for CDN URL generation in video responses."""

    def setup_method(self):
        """Reset CDN cache before each test."""
        reset_cdn_settings_cache()

    def teardown_method(self):
        """Reset CDN cache after each test."""
        reset_cdn_settings_cache()

    @pytest.mark.asyncio
    async def test_url_construction_with_cdn_prefix(self):
        """Test that URL construction works correctly with CDN prefix."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()

            async def mock_get(key, default):
                if key == "cdn.enabled":
                    return True
                if key == "cdn.base_url":
                    return "https://cdn.example.com"
                return default

            mock_service.get = mock_get
            mock_get_service.return_value = mock_service

            prefix = await get_video_url_prefix()
            slug = "test-video"

            # Simulate how the API constructs URLs
            stream_url = f"{prefix}/videos/{slug}/master.m3u8"
            dash_url = f"{prefix}/videos/{slug}/manifest.mpd"
            thumbnail_url = f"/videos/{slug}/thumbnail.jpg"  # No CDN prefix

            assert stream_url == "https://cdn.example.com/videos/test-video/master.m3u8"
            assert dash_url == "https://cdn.example.com/videos/test-video/manifest.mpd"
            assert thumbnail_url == "/videos/test-video/thumbnail.jpg"
            assert "cdn.example.com" not in thumbnail_url

    @pytest.mark.asyncio
    async def test_url_construction_without_cdn(self):
        """Test that URL construction works correctly without CDN."""
        with patch("api.settings_service.get_settings_service") as mock_get_service:
            mock_service = AsyncMock()
            mock_service.get = AsyncMock(side_effect=lambda key, default: default)
            mock_get_service.return_value = mock_service

            prefix = await get_video_url_prefix()
            slug = "test-video"

            # Simulate how the API constructs URLs
            stream_url = f"{prefix}/videos/{slug}/master.m3u8"
            thumbnail_url = f"/videos/{slug}/thumbnail.jpg"

            assert stream_url == "/videos/test-video/master.m3u8"
            assert thumbnail_url == "/videos/test-video/thumbnail.jpg"


class TestCDNSettingsValidation:
    """Test suite for CDN settings validation."""

    def test_cdn_base_url_pattern_accepts_valid_urls(self):
        """Test that CDN base URL pattern accepts valid URLs."""
        import re

        from api.settings_service import KNOWN_SETTINGS

        # Find the cdn.base_url pattern
        pattern = None
        for setting in KNOWN_SETTINGS:
            if setting[0] == "cdn.base_url":
                pattern = setting[4]["pattern"]
                break

        assert pattern is not None

        valid_urls = [
            "https://cdn.example.com",
            "http://cdn.example.com",
            "https://cdn.example.com/",
            "https://cdn.damenknight.com",
            "https://a.io",
            "https://cdn.example.com:8080",
            "https://cdn.example.com:8080/",
        ]

        for url in valid_urls:
            assert re.match(pattern, url), f"Pattern should accept: {url}"

    def test_cdn_base_url_pattern_rejects_invalid_urls(self):
        """Test that CDN base URL pattern rejects invalid URLs."""
        import re

        from api.settings_service import KNOWN_SETTINGS

        # Find the cdn.base_url pattern
        pattern = None
        for setting in KNOWN_SETTINGS:
            if setting[0] == "cdn.base_url":
                pattern = setting[4]["pattern"]
                break

        assert pattern is not None

        invalid_urls = [
            "ftp://cdn.example.com",  # Wrong protocol
            "cdn.example.com",  # Missing protocol
            "https://",  # Missing host
            "https://cdn.example.com/path",  # Has path
        ]

        for url in invalid_urls:
            assert not re.match(pattern, url), f"Pattern should reject: {url}"
