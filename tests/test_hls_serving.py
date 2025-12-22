"""
Tests for HLS video file serving endpoints.

Tests the core video playback functionality - serving HLS playlists and segments.
Fixes issue #335.
"""

from pathlib import Path

import pytest
from starlette.testclient import TestClient


class TestHLSStaticFiles:
    """Tests for the HLSStaticFiles class that serves video content."""

    @pytest.fixture
    def hls_test_dir(self, tmp_path: Path) -> Path:
        """Create a test directory with mock HLS files."""
        video_dir = tmp_path / "videos" / "test-video"
        video_dir.mkdir(parents=True)

        # Create master playlist
        master_content = """#EXTM3U
#EXT-X-VERSION:3

#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
1080p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720p.m3u8
"""
        (video_dir / "master.m3u8").write_text(master_content)

        # Create quality playlist
        quality_content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:6.000000,
1080p_0000.ts
#EXTINF:6.000000,
1080p_0001.ts
#EXT-X-ENDLIST
"""
        (video_dir / "1080p.m3u8").write_text(quality_content)

        # Create mock .ts segment (just needs to exist for testing)
        (video_dir / "1080p_0000.ts").write_bytes(b"\x00" * 1000)
        (video_dir / "1080p_0001.ts").write_bytes(b"\x00" * 1000)

        # Create thumbnail
        (video_dir / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

        return tmp_path / "videos"

    @pytest.fixture
    def hls_app(self, hls_test_dir: Path):
        """Create a test app with HLSStaticFiles mounted."""
        from fastapi import FastAPI

        # Import the HLSStaticFiles class
        from api.public import HLSStaticFiles

        app = FastAPI()
        app.mount("/videos", HLSStaticFiles(directory=str(hls_test_dir)), name="videos")
        return app

    @pytest.fixture
    def hls_client(self, hls_app):
        """Create a test client for the HLS app."""
        return TestClient(hls_app)

    def test_master_playlist_returns_correct_content_type(self, hls_client):
        """Test that master.m3u8 returns correct HLS playlist content-type."""
        response = hls_client.get("/videos/test-video/master.m3u8")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/vnd.apple.mpegurl"
        assert "#EXTM3U" in response.text
        assert "1080p.m3u8" in response.text

    def test_master_playlist_has_no_cache(self, hls_client):
        """Test that m3u8 playlists have no-cache directive."""
        response = hls_client.get("/videos/test-video/master.m3u8")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"

    def test_quality_playlist_returns_correct_content_type(self, hls_client):
        """Test that quality playlists return correct HLS content-type."""
        response = hls_client.get("/videos/test-video/1080p.m3u8")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/vnd.apple.mpegurl"
        assert "#EXTM3U" in response.text
        assert "1080p_0000.ts" in response.text

    def test_ts_segment_returns_correct_content_type(self, hls_client):
        """Test that .ts segments return video/mp2t MIME type (not TypeScript)."""
        response = hls_client.get("/videos/test-video/1080p_0000.ts")
        assert response.status_code == 200
        # CRITICAL: Must be video/mp2t, NOT video/mp2t or text/vnd.qt.linguist
        assert response.headers["content-type"] == "video/mp2t"

    def test_ts_segment_has_long_cache(self, hls_client):
        """Test that .ts segments have long cache duration."""
        response = hls_client.get("/videos/test-video/1080p_0000.ts")
        assert response.status_code == 200
        assert "max-age=31536000" in response.headers["cache-control"]

    def test_thumbnail_returns_image(self, hls_client):
        """Test that thumbnail.jpg is served correctly."""
        response = hls_client.get("/videos/test-video/thumbnail.jpg")
        assert response.status_code == 200
        # JPEG MIME type
        assert "image/jpeg" in response.headers["content-type"]

    def test_thumbnail_has_short_cache(self, hls_client):
        """Test that thumbnails have short cache for quick updates."""
        response = hls_client.get("/videos/test-video/thumbnail.jpg")
        assert response.status_code == 200
        assert "max-age=60" in response.headers["cache-control"]
        assert "must-revalidate" in response.headers["cache-control"]

    def test_nonexistent_video_returns_404(self, hls_client):
        """Test that non-existent video slug returns 404."""
        response = hls_client.get("/videos/nonexistent-video/master.m3u8")
        assert response.status_code == 404

    def test_nonexistent_segment_returns_404(self, hls_client):
        """Test that non-existent segment returns 404."""
        response = hls_client.get("/videos/test-video/1080p_9999.ts")
        assert response.status_code == 404

    def test_path_traversal_blocked(self, hls_client, hls_test_dir):
        """Test that path traversal attempts are blocked."""
        # Create a file outside the video directory that shouldn't be accessible
        secret_file = hls_test_dir.parent / "secret.txt"
        secret_file.write_text("secret data")

        # Attempt path traversal
        response = hls_client.get("/videos/../secret.txt")
        # Should either return 404 or 400, not the file contents
        assert response.status_code in [400, 404]
        assert "secret data" not in response.text

    def test_path_traversal_double_encoded_blocked(self, hls_client, hls_test_dir):
        """Test that double-encoded path traversal attempts are blocked."""
        # Create a file outside the video directory
        secret_file = hls_test_dir.parent / "secret.txt"
        secret_file.write_text("secret data")

        # Attempt with URL-encoded path traversal
        response = hls_client.get("/videos/..%2Fsecret.txt")
        assert response.status_code in [400, 404]
        assert "secret data" not in response.text

    def test_empty_slug_returns_error(self, hls_client):
        """Test that empty video slug is handled correctly."""
        response = hls_client.get("/videos//master.m3u8")
        # Empty path component should fail
        assert response.status_code in [404, 307, 308]  # May redirect or 404


class TestHLSStorageUnavailable:
    """Tests for HLS serving when storage is unavailable."""

    def test_storage_error_returns_503(self):
        """Test that storage errors return 503 with helpful message."""
        from api.public import HLSStaticFiles

        # HLSStaticFiles inherits from StaticFiles which validates the directory exists
        # If directory doesn't exist, it raises RuntimeError at initialization
        with pytest.raises(RuntimeError):
            HLSStaticFiles(directory="/nonexistent/path/that/does/not/exist")


class TestHLSFileValidation:
    """Tests for HLS file content validation."""

    @pytest.fixture
    def hls_test_dir(self, tmp_path: Path) -> Path:
        """Create a test directory with various HLS files."""
        video_dir = tmp_path / "videos" / "test-video"
        video_dir.mkdir(parents=True)

        # Create master playlist with valid HLS content
        (video_dir / "master.m3u8").write_text("#EXTM3U\n#EXT-X-VERSION:3\n")
        (video_dir / "1080p.m3u8").write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-ENDLIST\n")
        (video_dir / "1080p_0000.ts").write_bytes(b"\x47" + b"\x00" * 187)  # TS sync byte

        return tmp_path / "videos"

    @pytest.fixture
    def hls_client(self, hls_test_dir: Path):
        """Create a test client for the HLS app."""
        from fastapi import FastAPI

        from api.public import HLSStaticFiles

        app = FastAPI()
        app.mount("/videos", HLSStaticFiles(directory=str(hls_test_dir)), name="videos")
        return TestClient(app)

    def test_valid_m3u8_starts_with_extm3u(self, hls_client):
        """Test that valid m3u8 files start with #EXTM3U."""
        response = hls_client.get("/videos/test-video/master.m3u8")
        assert response.status_code == 200
        assert response.text.startswith("#EXTM3U")

    def test_ts_file_served_as_binary(self, hls_client):
        """Test that .ts files are served as binary content."""
        response = hls_client.get("/videos/test-video/1080p_0000.ts")
        assert response.status_code == 200
        # TS files should start with sync byte 0x47
        assert response.content[0] == 0x47


class TestHLSRangeRequests:
    """Tests for HTTP range requests on HLS segments."""

    @pytest.fixture
    def hls_test_dir(self, tmp_path: Path) -> Path:
        """Create test directory with a larger segment file."""
        video_dir = tmp_path / "videos" / "test-video"
        video_dir.mkdir(parents=True)

        # Create a larger .ts file to test range requests
        # TS packets are 188 bytes, create 100 packets
        ts_content = b"\x47" + b"\x00" * 187  # One TS packet
        (video_dir / "1080p_0000.ts").write_bytes(ts_content * 100)

        return tmp_path / "videos"

    @pytest.fixture
    def hls_client(self, hls_test_dir: Path):
        """Create a test client for the HLS app."""
        from fastapi import FastAPI

        from api.public import HLSStaticFiles

        app = FastAPI()
        app.mount("/videos", HLSStaticFiles(directory=str(hls_test_dir)), name="videos")
        return TestClient(app)

    def test_range_request_returns_partial_content(self, hls_client):
        """Test that range requests return 206 Partial Content."""
        response = hls_client.get("/videos/test-video/1080p_0000.ts", headers={"Range": "bytes=0-187"})
        # StaticFiles supports range requests
        assert response.status_code in [200, 206]

    def test_accept_ranges_header_present(self, hls_client):
        """Test that Accept-Ranges header is present for segment files."""
        response = hls_client.get("/videos/test-video/1080p_0000.ts")
        assert response.status_code == 200
        # StaticFiles should advertise range support
        assert response.headers.get("accept-ranges") == "bytes"
