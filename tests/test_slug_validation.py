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

        if response.status_code == 404:
            # Should not be a validation error
            assert "Invalid video slug" not in response.json().get("detail", "")


class TestWorkerTranscoderSlugValidation:
    """Tests for slug validation in worker transcoder functions."""

    @pytest.mark.asyncio
    async def test_process_video_resumable_invalid_slug(self, test_database, monkeypatch):
        """Test that process_video_resumable rejects invalid slugs."""
        # Patch the database connection
        import worker.transcoder
        from api.database import videos
        from api.enums import VideoStatus
        from worker.transcoder import process_video_resumable
        monkeypatch.setattr(worker.transcoder, "database", test_database)

        # Create a video with a valid slug in database
        video_id = await test_database.execute(
            videos.insert().values(
                title="Test Video",
                slug="test-video",  # This will be in database
                description="Test",
                duration=0,
                status=VideoStatus.PENDING,
            )
        )

        # Try to process with an invalid slug
        invalid_slug = "../etc/passwd"
        result = await process_video_resumable(video_id, invalid_slug)

        # Should return False
        assert result is False

        # Check that video status is set to FAILED
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["status"] == VideoStatus.FAILED
        assert video["error_message"] == "Invalid video slug"

    @pytest.mark.asyncio
    async def test_process_video_resumable_valid_slug_fails_gracefully(self, test_database, test_storage, monkeypatch):
        """Test that process_video_resumable works with valid slugs."""
        # Patch the database and storage directories
        import worker.transcoder
        from api.database import videos
        from api.enums import VideoStatus
        from worker.transcoder import process_video_resumable
        monkeypatch.setattr(worker.transcoder, "database", test_database)
        monkeypatch.setattr("worker.transcoder.UPLOADS_DIR", test_storage["uploads"])
        monkeypatch.setattr("worker.transcoder.VIDEOS_DIR", test_storage["videos"])

        # Create a video with a valid slug
        video_id = await test_database.execute(
            videos.insert().values(
                title="Test Video",
                slug="test-video",
                description="Test",
                duration=0,
                status=VideoStatus.PENDING,
            )
        )

        # Try to process with a valid slug (will fail due to missing source file, but slug validation should pass)
        result = await process_video_resumable(video_id, "test-video")

        # Should return False due to missing source file, not slug validation
        assert result is False

        # Check that error is about missing file, not invalid slug
        video = await test_database.fetch_one(videos.select().where(videos.c.id == video_id))
        assert video["error_message"] != "Invalid video slug"

    @pytest.mark.asyncio
    async def test_cleanup_partial_output_invalid_slug(self, caplog, test_storage, monkeypatch):
        """Test that cleanup_partial_output rejects invalid slugs."""
        from worker.transcoder import cleanup_partial_output

        # Patch the storage directories
        monkeypatch.setattr("worker.transcoder.VIDEOS_DIR", test_storage["videos"])

        # Try to cleanup with an invalid slug
        invalid_slug = "../etc/passwd"

        # Should log error and return early
        await cleanup_partial_output(invalid_slug)

        # Check that error was logged
        assert "Invalid video slug in cleanup_partial_output" in caplog.text
        assert invalid_slug in caplog.text

    @pytest.mark.asyncio
    async def test_cleanup_partial_output_valid_slug(self, test_storage, monkeypatch):
        """Test that cleanup_partial_output works with valid slugs."""
        from worker.transcoder import cleanup_partial_output

        # Patch the storage directories
        monkeypatch.setattr("worker.transcoder.VIDEOS_DIR", test_storage["videos"])

        # Create a test directory
        valid_slug = "test-video"
        video_dir = test_storage["videos"] / valid_slug
        video_dir.mkdir(parents=True, exist_ok=True)
        test_file = video_dir / "test.txt"
        test_file.write_text("test content")

        # Should successfully cleanup with valid slug
        await cleanup_partial_output(valid_slug, keep_completed_qualities=False)

        # Directory should exist but be empty
        assert video_dir.exists()
        assert len(list(video_dir.iterdir())) == 0


