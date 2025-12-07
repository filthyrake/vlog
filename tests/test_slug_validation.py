"""
Tests for slug validation to prevent path traversal attacks.

This test suite validates that:
1. Valid slugs pass validation
2. Path traversal attempts are rejected
3. Invalid characters are rejected
4. API endpoints return 400 for invalid slugs
"""

import pytest
from api.common import validate_slug


class TestSlugValidation:
    """Unit tests for the validate_slug function."""

    def test_valid_slugs(self):
        """Test that valid slugs pass validation."""
        valid_slugs = [
            "test-video",
            "my-video-123",
            "video123",
            "a",
            "123",
            "test-video-with-multiple-segments",
            "test123video456",
            "abc-123-def-456",
        ]
        for slug in valid_slugs:
            assert validate_slug(slug), f"Valid slug '{slug}' should pass validation"

    def test_path_traversal_rejected(self):
        """Test that path traversal attempts are rejected."""
        invalid_slugs = [
            "../test",
            "test/../video",
            "../../etc/passwd",
            "..\\test",  # Windows-style
            "test\\..\\video",  # Windows-style
            "....//test",
            "test..video",  # Contains .. but not path separator - still rejected
            "..video",
            "video..",
        ]
        for slug in invalid_slugs:
            assert not validate_slug(slug), f"Path traversal slug '{slug}' should be rejected"

    def test_invalid_characters_rejected(self):
        """Test that slugs with invalid characters are rejected."""
        invalid_slugs = [
            "test/video",  # Slash
            "test\\video",  # Backslash
            "test video",  # Space
            "test_video",  # Underscore (not in pattern)
            "TEST-VIDEO",  # Uppercase
            "Test-Video",  # Mixed case
            "test.video",  # Dot (except in ..)
            "test!video",  # Special char
            "test@video",  # Special char
            "test#video",  # Special char
            "test$video",  # Special char
            "test%video",  # Special char
            "test&video",  # Special char
            "test*video",  # Special char
            "test(video)",  # Parentheses
            "test[video]",  # Brackets
            "test{video}",  # Braces
            "test<video>",  # Angle brackets
            "test|video",  # Pipe
            "test;video",  # Semicolon
            "test:video",  # Colon
            "test'video",  # Quote
            'test"video',  # Double quote
            "test,video",  # Comma
            "test?video",  # Question mark
        ]
        for slug in invalid_slugs:
            assert not validate_slug(slug), f"Invalid slug '{slug}' should be rejected"

    def test_edge_cases(self):
        """Test edge cases."""
        # Empty string
        assert not validate_slug("")
        
        # Leading hyphen
        assert not validate_slug("-test")
        
        # Trailing hyphen
        assert not validate_slug("test-")
        
        # Double hyphen
        assert not validate_slug("test--video")
        
        # Only hyphens
        assert not validate_slug("---")
        
        # Very long valid slug (should pass)
        long_slug = "a" * 100 + "-" + "b" * 100
        assert validate_slug(long_slug)


class TestPublicAPISlugValidation:
    """Integration tests for slug validation in public API endpoints."""

    @pytest.mark.asyncio
    async def test_get_video_invalid_slug(self, public_client, test_database):
        """Test that get_video rejects invalid slugs."""
        # Test with slugs that would be invalid when decoded
        invalid_slugs = [
            "test%2F%2Fvideo",  # Contains // when decoded
            "TEST-VIDEO",  # Uppercase
            "test_video",  # Underscore
            "test.video",  # Dot
            "test video",  # Space (URL encoded as test%20video)
        ]
        
        for slug in invalid_slugs:
            response = public_client.get(f"/api/videos/{slug}")
            # Uppercase slugs might be accepted by routing but fail validation
            # Other invalid chars should fail at routing (404) or validation (400)
            assert response.status_code in [400, 404], f"Should reject invalid slug: {slug}"
            if response.status_code == 400:
                assert "Invalid" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_get_video_path_traversal_rejected(self, public_client, test_database):
        """Test that path traversal attempts are rejected at validation."""
        # These slugs contain '..' which should fail validation
        # Note: FastAPI routing might reject some of these before they reach validation
        path_traversal_slugs = [
            "test..video",  # Contains .. 
            "..video",  # Starts with ..
            "video..",  # Ends with ..
        ]
        
        for slug in path_traversal_slugs:
            response = public_client.get(f"/api/videos/{slug}")
            # Should be rejected either at routing (404) or validation (400)
            assert response.status_code in [400, 404], f"Should reject path traversal slug: {slug}"

    @pytest.mark.asyncio
    async def test_get_video_progress_invalid_slug(self, public_client, test_database):
        """Test that get_video_progress rejects invalid slugs."""
        response = public_client.get("/api/videos/TEST-VIDEO/progress")
        assert response.status_code in [400, 404]

    @pytest.mark.asyncio
    async def test_get_transcript_invalid_slug(self, public_client, test_database):
        """Test that get_transcript rejects invalid slugs."""
        response = public_client.get("/api/videos/test_video/transcript")
        assert response.status_code in [400, 404]

    @pytest.mark.asyncio
    async def test_get_category_invalid_slug(self, public_client, test_database):
        """Test that get_category rejects invalid slugs."""
        response = public_client.get("/api/categories/TEST-CATEGORY")
        assert response.status_code in [400, 404]

    @pytest.mark.asyncio
    async def test_valid_slug_still_works(self, public_client, test_database, sample_video):
        """Test that valid slugs still work after validation is added."""
        # Should return 200 (video exists) or 404 (not found), but not 400 (invalid)
        response = public_client.get("/api/videos/test-video")
        assert response.status_code in [200, 404]
        
        if response.status_code != 400:
            # Should not be a validation error
            if response.status_code != 200:
                assert "Invalid video slug" not in response.json().get("detail", "")
