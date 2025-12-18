"""
Tests for thumbnail selection functionality.

Tests the thumbnail-related API endpoints:
- GET /api/videos/{video_id}/thumbnail - Get thumbnail info
- POST /api/videos/{video_id}/thumbnail/frames - Generate frame options
- POST /api/videos/{video_id}/thumbnail/upload - Upload custom thumbnail
- POST /api/videos/{video_id}/thumbnail/select - Select frame at timestamp
- POST /api/videos/{video_id}/thumbnail/revert - Revert to auto-generated
"""

import io
from unittest.mock import AsyncMock, patch

import pytest

from api.database import videos
from api.enums import VideoStatus


class TestThumbnailEndpoints:
    """HTTP-level tests for thumbnail API endpoints."""

    @pytest.mark.asyncio
    async def test_get_thumbnail_info_not_found(self, admin_client):
        """Test getting thumbnail info for non-existent video returns 404."""
        response = admin_client.get("/api/videos/99999/thumbnail")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_get_thumbnail_info_success(self, admin_client, sample_video, test_storage):
        """Test getting thumbnail info for existing video."""
        # Create video directory and thumbnail
        video_dir = test_storage["videos"] / sample_video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "thumbnail.jpg").write_bytes(b"fake jpeg data")

        response = admin_client.get(f"/api/videos/{sample_video['id']}/thumbnail")
        assert response.status_code == 200
        data = response.json()
        assert data["video_id"] == sample_video["id"]
        assert data["thumbnail_source"] == "auto"
        assert data["thumbnail_timestamp"] is None
        assert f"/videos/{sample_video['slug']}/thumbnail.jpg" in data["thumbnail_url"]

    @pytest.mark.asyncio
    async def test_generate_frames_video_not_found(self, admin_client):
        """Test generating frames for non-existent video returns 404."""
        response = admin_client.post("/api/videos/99999/thumbnail/frames")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_generate_frames_no_duration(self, admin_client, test_database, sample_category):
        """Test generating frames for video with no duration returns 400."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        video_id = await test_database.execute(
            videos.insert().values(
                title="No Duration Video",
                slug="no-duration-video",
                description="",
                category_id=sample_category["id"],
                duration=0,
                source_width=1920,
                source_height=1080,
                status=VideoStatus.READY,
                created_at=now,
            )
        )

        response = admin_client.post(f"/api/videos/{video_id}/thumbnail/frames")
        assert response.status_code == 400
        assert "duration" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_generate_frames_no_source(self, admin_client, sample_video, test_storage):
        """Test generating frames when no source file exists returns 400."""
        # Video exists but no source file in uploads or videos dir
        response = admin_client.post(f"/api/videos/{sample_video['id']}/thumbnail/frames")
        assert response.status_code == 400
        assert "source" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_generate_frames_success(self, admin_client, sample_video, test_storage):
        """Test successful frame generation."""
        # Create a fake source file in uploads
        source_path = test_storage["uploads"] / f"{sample_video['id']}.mp4"
        source_path.write_bytes(b"fake video data")

        # Mock the generate_thumbnail function to avoid actual ffmpeg calls
        with patch("api.admin.generate_thumbnail", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = None  # Success

            response = admin_client.post(f"/api/videos/{sample_video['id']}/thumbnail/frames")
            assert response.status_code == 200

            data = response.json()
            assert data["video_id"] == sample_video["id"]
            assert len(data["frames"]) == 5  # 5 frame options at different percentages
            assert all("timestamp" in f and "url" in f for f in data["frames"])

    @pytest.mark.asyncio
    async def test_upload_thumbnail_not_found(self, admin_client):
        """Test uploading thumbnail for non-existent video returns 404."""
        response = admin_client.post(
            "/api/videos/99999/thumbnail/upload",
            files={"file": ("test.jpg", io.BytesIO(b"fake image"), "image/jpeg")},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_upload_thumbnail_invalid_format(self, admin_client, sample_video):
        """Test uploading thumbnail with invalid format returns 400."""
        response = admin_client.post(
            f"/api/videos/{sample_video['id']}/thumbnail/upload",
            files={"file": ("test.txt", io.BytesIO(b"not an image"), "text/plain")},
        )
        assert response.status_code == 400
        assert "unsupported" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_upload_thumbnail_success(self, admin_client, sample_video, test_storage, test_database):
        """Test successful thumbnail upload."""
        # Create video directory
        video_dir = test_storage["videos"] / sample_video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)

        # Create fake image data (minimal JPEG header)
        jpeg_data = bytes(
            [
                0xFF,
                0xD8,
                0xFF,
                0xE0,
                0x00,
                0x10,
                0x4A,
                0x46,
                0x49,
                0x46,
                0x00,
            ]
            + [0x00] * 100
        )

        # Mock ffmpeg conversion
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.returncode = 0
            mock_process.communicate = AsyncMock(return_value=(b"", b""))
            mock_subprocess.return_value = mock_process

            response = admin_client.post(
                f"/api/videos/{sample_video['id']}/thumbnail/upload",
                files={"file": ("test.jpg", io.BytesIO(jpeg_data), "image/jpeg")},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["thumbnail_source"] == "custom"
            assert data["thumbnail_timestamp"] is None

        # Verify database was updated
        video = await test_database.fetch_one(videos.select().where(videos.c.id == sample_video["id"]))
        assert video["thumbnail_source"] == "custom"
        assert video["thumbnail_timestamp"] is None

    @pytest.mark.asyncio
    async def test_select_frame_not_found(self, admin_client):
        """Test selecting frame for non-existent video returns 404."""
        response = admin_client.post(
            "/api/videos/99999/thumbnail/select",
            data={"timestamp": 5.0},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_select_frame_invalid_timestamp(self, admin_client, sample_video):
        """Test selecting frame with timestamp beyond duration returns 400."""
        # sample_video has duration of 120.5 seconds
        response = admin_client.post(
            f"/api/videos/{sample_video['id']}/thumbnail/select",
            data={"timestamp": 200.0},  # Beyond duration
        )
        assert response.status_code == 400
        assert "timestamp" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_select_frame_no_source(self, admin_client, sample_video):
        """Test selecting frame when no source file exists returns 400."""
        response = admin_client.post(
            f"/api/videos/{sample_video['id']}/thumbnail/select",
            data={"timestamp": 30.0},
        )
        assert response.status_code == 400
        assert "source" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_select_frame_success(self, admin_client, sample_video, test_storage, test_database):
        """Test successful frame selection."""
        # Create video directory
        video_dir = test_storage["videos"] / sample_video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)

        # Create a fake source file
        source_path = test_storage["uploads"] / f"{sample_video['id']}.mp4"
        source_path.write_bytes(b"fake video data")

        # Mock the generate_thumbnail function
        with patch("api.admin.generate_thumbnail", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = None

            response = admin_client.post(
                f"/api/videos/{sample_video['id']}/thumbnail/select",
                data={"timestamp": 30.0},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["thumbnail_source"] == "selected"
            assert data["thumbnail_timestamp"] == 30.0

        # Verify database was updated
        video = await test_database.fetch_one(videos.select().where(videos.c.id == sample_video["id"]))
        assert video["thumbnail_source"] == "selected"
        assert video["thumbnail_timestamp"] == 30.0

    @pytest.mark.asyncio
    async def test_revert_thumbnail_not_found(self, admin_client):
        """Test reverting thumbnail for non-existent video returns 404."""
        response = admin_client.post("/api/videos/99999/thumbnail/revert")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_revert_thumbnail_no_source(self, admin_client, sample_video):
        """Test reverting thumbnail when no source file exists returns 400."""
        response = admin_client.post(f"/api/videos/{sample_video['id']}/thumbnail/revert")
        assert response.status_code == 400
        assert "source" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_revert_thumbnail_success(self, admin_client, sample_video, test_storage, test_database):
        """Test successful thumbnail revert."""
        # Create video directory
        video_dir = test_storage["videos"] / sample_video["slug"]
        video_dir.mkdir(parents=True, exist_ok=True)

        # Set up the video as having a custom thumbnail first
        await test_database.execute(
            videos.update()
            .where(videos.c.id == sample_video["id"])
            .values(thumbnail_source="custom", thumbnail_timestamp=None)
        )

        # Create a fake source file
        source_path = test_storage["uploads"] / f"{sample_video['id']}.mp4"
        source_path.write_bytes(b"fake video data")

        # Mock the generate_thumbnail function
        with patch("api.admin.generate_thumbnail", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = None

            response = admin_client.post(f"/api/videos/{sample_video['id']}/thumbnail/revert")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["thumbnail_source"] == "auto"
            assert data["thumbnail_timestamp"] is None

        # Verify database was updated
        video = await test_database.fetch_one(videos.select().where(videos.c.id == sample_video["id"]))
        assert video["thumbnail_source"] == "auto"
        assert video["thumbnail_timestamp"] is None


class TestThumbnailInVideoResponse:
    """Test that thumbnail metadata appears in video list/detail responses."""

    @pytest.mark.asyncio
    async def test_video_list_includes_thumbnail_source(self, admin_client, sample_video, test_database):
        """Test that video list response includes thumbnail_source field."""
        # Set a custom thumbnail source
        await test_database.execute(
            videos.update()
            .where(videos.c.id == sample_video["id"])
            .values(thumbnail_source="selected", thumbnail_timestamp=45.0)
        )

        response = admin_client.get("/api/videos")
        assert response.status_code == 200
        data = response.json()

        # Find our sample video
        video = next((v for v in data if v["id"] == sample_video["id"]), None)
        assert video is not None
        assert video["thumbnail_source"] == "selected"
        assert video["thumbnail_timestamp"] == 45.0

    @pytest.mark.asyncio
    async def test_video_detail_includes_thumbnail_source(self, admin_client, sample_video, test_database):
        """Test that video detail response includes thumbnail_source field."""
        # Set a custom thumbnail source
        await test_database.execute(
            videos.update()
            .where(videos.c.id == sample_video["id"])
            .values(thumbnail_source="custom", thumbnail_timestamp=None)
        )

        response = admin_client.get(f"/api/videos/{sample_video['id']}")
        assert response.status_code == 200
        data = response.json()

        assert data["thumbnail_source"] == "custom"
        assert data["thumbnail_timestamp"] is None
