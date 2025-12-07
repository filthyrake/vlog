"""
Tests for cleanup_partial_output function with dynamic quality pattern matching.
"""

from pathlib import Path

import pytest

from config import QUALITY_NAMES
from worker.transcoder import cleanup_partial_output


@pytest.fixture
def video_slug():
    """Return a test video slug."""
    return "test-video-slug"


@pytest.fixture
def output_dir(test_storage: dict, video_slug: str) -> Path:
    """Create output directory for a test video."""
    video_dir = test_storage["videos"] / video_slug
    video_dir.mkdir(parents=True, exist_ok=True)
    return video_dir


def create_quality_files(output_dir: Path, quality: str, segment_count: int = 3):
    """Helper to create quality files (playlist + segments)."""
    # Create playlist file
    playlist = output_dir / f"{quality}.m3u8"
    playlist.write_text(f"#EXTM3U\n#test playlist for {quality}")

    # Create segment files
    for i in range(segment_count):
        segment = output_dir / f"{quality}_{i:04d}.ts"
        segment.write_bytes(b"fake video segment data")


class TestCleanupPartialOutputFullCleanup:
    """Tests for full cleanup mode."""

    async def test_full_cleanup_removes_all_files(self, test_storage: dict, video_slug: str, output_dir: Path, monkeypatch):
        """Test full cleanup removes all files when keep_completed_qualities is False."""
        # Create various quality files
        create_quality_files(output_dir, "1080p")
        create_quality_files(output_dir, "720p")
        create_quality_files(output_dir, "original")

        # Create master playlist
        master = output_dir / "master.m3u8"
        master.write_text("#EXTM3U\nmaster playlist")

        # Patch worker.transcoder.VIDEOS_DIR to use test storage
        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        # Perform full cleanup
        await cleanup_partial_output(video_slug, keep_completed_qualities=False)

        # Directory should still exist but be empty (recreated)
        assert output_dir.exists()
        assert len(list(output_dir.iterdir())) == 0

    async def test_full_cleanup_when_completed_names_is_none(self, test_storage: dict, video_slug: str, output_dir: Path, monkeypatch):
        """Test full cleanup when completed_quality_names is None."""
        # Create quality files
        create_quality_files(output_dir, "1080p")

        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        await cleanup_partial_output(video_slug, keep_completed_qualities=True, completed_quality_names=None)

        # Should perform full cleanup
        assert output_dir.exists()
        assert len(list(output_dir.iterdir())) == 0

    async def test_cleanup_nonexistent_directory(self, test_storage: dict, monkeypatch):
        """Test cleanup of nonexistent directory does nothing."""
        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        # Should not raise an error
        await cleanup_partial_output("nonexistent-slug")


class TestCleanupPartialOutputSelectiveCleanup:
    """Tests for selective cleanup mode."""

    async def test_selective_cleanup_keeps_completed_qualities(self, test_storage: dict, video_slug: str, output_dir: Path, monkeypatch):
        """Test selective cleanup keeps completed quality files."""
        # Create files for multiple qualities
        create_quality_files(output_dir, "1080p")
        create_quality_files(output_dir, "720p")
        create_quality_files(output_dir, "480p")

        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        # Keep only 1080p and 720p
        await cleanup_partial_output(
            video_slug,
            keep_completed_qualities=True,
            completed_quality_names=["1080p", "720p"]
        )

        # Check 1080p files exist
        assert (output_dir / "1080p.m3u8").exists()
        assert (output_dir / "1080p_0000.ts").exists()

        # Check 720p files exist
        assert (output_dir / "720p.m3u8").exists()
        assert (output_dir / "720p_0000.ts").exists()

        # Check 480p files were removed
        assert not (output_dir / "480p.m3u8").exists()
        assert not (output_dir / "480p_0000.ts").exists()

    async def test_selective_cleanup_removes_incomplete_qualities(self, test_storage: dict, video_slug: str, output_dir: Path, monkeypatch):
        """Test selective cleanup removes incomplete quality files."""
        # Create original and transcoded quality files
        create_quality_files(output_dir, "original")
        create_quality_files(output_dir, "1080p")
        create_quality_files(output_dir, "720p")

        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        # Only keep original
        await cleanup_partial_output(
            video_slug,
            keep_completed_qualities=True,
            completed_quality_names=["original"]
        )

        # Original should exist
        assert (output_dir / "original.m3u8").exists()
        assert (output_dir / "original_0000.ts").exists()

        # Transcoded qualities should be removed
        assert not (output_dir / "1080p.m3u8").exists()
        assert not (output_dir / "720p.m3u8").exists()

    async def test_master_playlist_always_removed(self, test_storage: dict, video_slug: str, output_dir: Path, monkeypatch):
        """Test that master.m3u8 is always removed in selective cleanup."""
        # Create quality files and master playlist
        create_quality_files(output_dir, "1080p")
        master = output_dir / "master.m3u8"
        master.write_text("#EXTM3U\nmaster playlist")

        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        await cleanup_partial_output(
            video_slug,
            keep_completed_qualities=True,
            completed_quality_names=["1080p"]
        )

        # Master playlist should be removed
        assert not master.exists()

        # Quality files should remain
        assert (output_dir / "1080p.m3u8").exists()


class TestDynamicQualityPatternMatching:
    """Tests for dynamic quality pattern matching from QUALITY_NAMES."""

    async def test_matches_all_standard_qualities(self, test_storage: dict, video_slug: str, output_dir: Path, monkeypatch):
        """Test that all standard quality presets are matched."""
        # Create files for all standard qualities
        standard_qualities = ["2160p", "1440p", "1080p", "720p", "480p", "360p"]
        for quality in standard_qualities:
            create_quality_files(output_dir, quality)

        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        # Keep only 1080p
        await cleanup_partial_output(
            video_slug,
            keep_completed_qualities=True,
            completed_quality_names=["1080p"]
        )

        # Only 1080p should remain
        assert (output_dir / "1080p.m3u8").exists()

        # All others should be removed
        for quality in ["2160p", "1440p", "720p", "480p", "360p"]:
            assert not (output_dir / f"{quality}.m3u8").exists()

    async def test_matches_original_quality(self, test_storage: dict, video_slug: str, output_dir: Path, monkeypatch):
        """Test that 'original' quality is properly matched."""
        create_quality_files(output_dir, "original")
        create_quality_files(output_dir, "720p")

        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        # Keep only 720p
        await cleanup_partial_output(
            video_slug,
            keep_completed_qualities=True,
            completed_quality_names=["720p"]
        )

        # Original should be removed
        assert not (output_dir / "original.m3u8").exists()
        assert not (output_dir / "original_0000.ts").exists()

        # 720p should remain
        assert (output_dir / "720p.m3u8").exists()

    async def test_ignores_non_quality_files(self, test_storage: dict, video_slug: str, output_dir: Path, monkeypatch):
        """Test that non-quality files are not removed in selective cleanup."""
        # Create quality files
        create_quality_files(output_dir, "1080p")
        create_quality_files(output_dir, "720p")

        # Create non-quality files that should be ignored
        (output_dir / "thumbnail.jpg").write_bytes(b"fake thumbnail")
        (output_dir / "metadata.json").write_text('{"test": "data"}')
        (output_dir / "README.txt").write_text("test readme")
        (output_dir / "1080p_extra.log").write_text("not a valid segment")

        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        # Keep 720p, remove 1080p - using non-empty list to avoid full cleanup
        await cleanup_partial_output(
            video_slug,
            keep_completed_qualities=True,
            completed_quality_names=["720p"]
        )

        # 1080p quality files should be removed
        assert not (output_dir / "1080p.m3u8").exists()

        # 720p quality files should remain
        assert (output_dir / "720p.m3u8").exists()

        # Non-quality files should remain
        assert (output_dir / "thumbnail.jpg").exists()
        assert (output_dir / "metadata.json").exists()
        assert (output_dir / "README.txt").exists()
        assert (output_dir / "1080p_extra.log").exists()

    async def test_handles_segment_files_with_various_numbers(self, test_storage: dict, video_slug: str, output_dir: Path, monkeypatch):
        """Test that segment files with various numbering are matched."""
        # Create segments with different numbering patterns
        (output_dir / "720p.m3u8").write_text("#EXTM3U")
        (output_dir / "720p_0000.ts").write_bytes(b"segment 0")
        (output_dir / "720p_0001.ts").write_bytes(b"segment 1")
        (output_dir / "720p_0099.ts").write_bytes(b"segment 99")
        (output_dir / "720p_9999.ts").write_bytes(b"segment 9999")

        import worker.transcoder
        monkeypatch.setattr(worker.transcoder, "VIDEOS_DIR", test_storage["videos"])

        await cleanup_partial_output(
            video_slug,
            keep_completed_qualities=True,
            completed_quality_names=["1080p"]  # Don't keep 720p
        )

        # All 720p files should be removed
        assert not (output_dir / "720p.m3u8").exists()
        assert not (output_dir / "720p_0000.ts").exists()
        assert not (output_dir / "720p_0001.ts").exists()
        assert not (output_dir / "720p_0099.ts").exists()
        assert not (output_dir / "720p_9999.ts").exists()


class TestQualityNamesConstant:
    """Tests for QUALITY_NAMES constant."""

    def test_quality_names_includes_all_presets(self):
        """Test that QUALITY_NAMES includes all quality presets."""
        from config import QUALITY_PRESETS

        preset_names = {q["name"] for q in QUALITY_PRESETS}

        # All preset names should be in QUALITY_NAMES
        for name in preset_names:
            assert name in QUALITY_NAMES

    def test_quality_names_includes_original(self):
        """Test that QUALITY_NAMES includes 'original'."""
        assert "original" in QUALITY_NAMES

    def test_quality_names_is_frozenset(self):
        """Test that QUALITY_NAMES is a frozenset (immutable)."""
        assert isinstance(QUALITY_NAMES, frozenset)

    def test_quality_names_count(self):
        """Test that QUALITY_NAMES has expected number of entries."""
        from config import QUALITY_PRESETS

        # Should have all presets + "original"
        expected_count = len(QUALITY_PRESETS) + 1
        assert len(QUALITY_NAMES) == expected_count
