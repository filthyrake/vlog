"""
Tests for reliability fixes (Issues #450 and #451).

#451: TAR extraction timeout - prevents thread pool exhaustion from stale NFS
#450: Orphaned quality file cleanup - prevents disk space leaks from partial uploads
"""

import os
import tarfile
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# Ensure test mode
os.environ["VLOG_TEST_MODE"] = "1"


class TestTarExtractionTimeout:
    """Tests for TAR extraction timeout functionality (Issue #451)."""

    @pytest.mark.asyncio
    async def test_extract_tar_async_normal_operation(self, tmp_path):
        """Test that normal tar extraction works with timeout."""
        from api.worker_api import extract_tar_async

        # Create a simple tar.gz file
        tar_path = tmp_path / "test.tar.gz"
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Create a file to add to the archive
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        test_file = source_dir / "test.m3u8"
        test_file.write_text("#EXTM3U\n")

        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(test_file, arcname="test.m3u8")

        # Extract with timeout
        await extract_tar_async(
            tar_path,
            output_dir,
            allowed_extensions=(".m3u8",),
            max_files=10,
            max_size=1024 * 1024,
            max_single_file=512 * 1024,
            timeout=30,  # 30 second timeout
        )

        # Verify extraction
        assert (output_dir / "test.m3u8").exists()

    @pytest.mark.asyncio
    async def test_extract_tar_async_timeout_error(self, tmp_path):
        """Test that extraction times out when it takes too long."""
        from api.worker_api import extract_tar_async

        # Create a simple tar.gz file
        tar_path = tmp_path / "test.tar.gz"
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        source_dir = tmp_path / "source"
        source_dir.mkdir()
        test_file = source_dir / "test.m3u8"
        test_file.write_text("#EXTM3U\n")

        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(test_file, arcname="test.m3u8")

        # Mock the sync extraction to simulate slow NAS
        def slow_extract(*args, **kwargs):
            time.sleep(10)  # Simulate slow extraction

        with patch("api.worker_api._extract_tar_sync", slow_extract):
            with pytest.raises(ValueError, match="timed out"):
                await extract_tar_async(
                    tar_path,
                    output_dir,
                    allowed_extensions=(".m3u8",),
                    max_files=10,
                    max_size=1024 * 1024,
                    max_single_file=512 * 1024,
                    timeout=0.1,  # Very short timeout
                )

    @pytest.mark.asyncio
    async def test_extract_tar_async_uses_default_timeout(self, tmp_path):
        """Test that extraction uses default timeout from config."""
        from api.worker_api import extract_tar_async
        from config import TAR_EXTRACTION_TIMEOUT

        # Create a simple tar.gz file
        tar_path = tmp_path / "test.tar.gz"
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        source_dir = tmp_path / "source"
        source_dir.mkdir()
        test_file = source_dir / "test.m3u8"
        test_file.write_text("#EXTM3U\n")

        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(test_file, arcname="test.m3u8")

        # Verify default timeout is configured
        assert TAR_EXTRACTION_TIMEOUT >= 60, "Default timeout should be at least 60 seconds"

        # Extract without explicit timeout - should use default
        await extract_tar_async(
            tar_path,
            output_dir,
            allowed_extensions=(".m3u8",),
            max_files=10,
            max_size=1024 * 1024,
            max_single_file=512 * 1024,
        )

        # Verify extraction worked
        assert (output_dir / "test.m3u8").exists()


class TestOrphanedQualityCleanup:
    """Tests for orphaned quality file cleanup functionality (Issue #450)."""

    @pytest.fixture
    def past_startup_time(self):
        """Set _api_start_time to past the grace period."""
        # Set startup time far in the past so we're past the grace period
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        return past_time

    @pytest.mark.asyncio
    async def test_cleanup_skips_registered_qualities(self, test_database, test_storage, past_startup_time):
        """Test that cleanup does not remove quality dirs with database records."""
        from api.database import video_qualities, videos
        from api.worker_api import _cleanup_orphaned_quality_directories

        videos_dir = test_storage["videos"]

        # Create a video in the database
        video_id = await test_database.execute(
            videos.insert().values(
                title="Test Video",
                slug="test-cleanup-registered",
                status="ready",
            )
        )

        # Create a quality record
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_id,
                quality="1080p",
                width=1920,
                height=1080,
                bitrate=5000,
            )
        )

        # Create the quality directory
        video_dir = videos_dir / "test-cleanup-registered"
        video_dir.mkdir(parents=True, exist_ok=True)
        quality_dir = video_dir / "1080p"
        quality_dir.mkdir(exist_ok=True)
        (quality_dir / "test.m3u8").write_text("#EXTM3U\n")

        # Make directory old enough
        old_time = time.time() - 86400 * 2  # 2 days old
        os.utime(quality_dir, (old_time, old_time))

        # Run cleanup with patched database, VIDEOS_DIR, and _api_start_time
        import api.worker_api as worker_api_module
        with patch.object(worker_api_module, "database", test_database), \
             patch.object(worker_api_module, "VIDEOS_DIR", videos_dir), \
             patch.object(worker_api_module, "_api_start_time", past_startup_time):
            cleaned = await _cleanup_orphaned_quality_directories()

        # Verify directory was NOT removed
        assert quality_dir.exists(), "Registered quality directory should not be removed"
        assert cleaned == 0

    @pytest.mark.asyncio
    async def test_cleanup_removes_orphaned_old_directories(self, test_database, test_storage, past_startup_time):
        """Test that cleanup removes old orphaned quality directories."""
        from api.database import videos
        from api.worker_api import _cleanup_orphaned_quality_directories

        videos_dir = test_storage["videos"]

        # Create a video in the database (no quality records)
        await test_database.execute(
            videos.insert().values(
                title="Test Video Orphan",
                slug="test-cleanup-orphan",
                status="ready",
            )
        )

        # Create the orphaned quality directory
        video_dir = videos_dir / "test-cleanup-orphan"
        video_dir.mkdir(parents=True, exist_ok=True)
        quality_dir = video_dir / "720p"
        quality_dir.mkdir(exist_ok=True)
        (quality_dir / "test.m3u8").write_text("#EXTM3U\n")

        # Make directory old enough (older than ORPHAN_CLEANUP_MIN_AGE)
        old_time = time.time() - 86400 * 2  # 2 days old
        os.utime(quality_dir, (old_time, old_time))

        # Run cleanup with patched database, VIDEOS_DIR, and _api_start_time
        import api.worker_api as worker_api_module
        with patch.object(worker_api_module, "database", test_database), \
             patch.object(worker_api_module, "VIDEOS_DIR", videos_dir), \
             patch.object(worker_api_module, "_api_start_time", past_startup_time):
            cleaned = await _cleanup_orphaned_quality_directories()

        # Verify directory was removed
        assert not quality_dir.exists(), "Orphaned quality directory should be removed"
        assert cleaned == 1

    @pytest.mark.asyncio
    async def test_cleanup_skips_new_directories(self, test_database, test_storage, past_startup_time):
        """Test that cleanup does not remove recently created directories."""
        from api.database import videos
        from api.worker_api import _cleanup_orphaned_quality_directories

        videos_dir = test_storage["videos"]

        # Create a video in the database (no quality records)
        await test_database.execute(
            videos.insert().values(
                title="Test Video New",
                slug="test-cleanup-new",
                status="processing",
            )
        )

        # Create the orphaned quality directory (but it's new)
        video_dir = videos_dir / "test-cleanup-new"
        video_dir.mkdir(parents=True, exist_ok=True)
        quality_dir = video_dir / "480p"
        quality_dir.mkdir(exist_ok=True)
        (quality_dir / "test.m3u8").write_text("#EXTM3U\n")

        # Directory is new (just created), should not be cleaned up

        # Run cleanup with patched database, VIDEOS_DIR, and _api_start_time
        import api.worker_api as worker_api_module
        with patch.object(worker_api_module, "database", test_database), \
             patch.object(worker_api_module, "VIDEOS_DIR", videos_dir), \
             patch.object(worker_api_module, "_api_start_time", past_startup_time):
            cleaned = await _cleanup_orphaned_quality_directories()

        # Verify directory was NOT removed (too new)
        assert quality_dir.exists(), "New orphaned directory should not be removed yet"
        assert cleaned == 0

    @pytest.mark.asyncio
    async def test_cleanup_skips_videos_with_active_jobs(self, test_database, test_storage, past_startup_time):
        """Test that cleanup skips videos with active transcoding jobs."""
        from api.database import transcoding_jobs, videos
        from api.worker_api import _cleanup_orphaned_quality_directories

        videos_dir = test_storage["videos"]

        # Create a video in the database
        video_id = await test_database.execute(
            videos.insert().values(
                title="Test Video Active Job",
                slug="test-cleanup-active-job",
                status="processing",
            )
        )

        # Create an active transcoding job (completed_at=None means it's active)
        await test_database.execute(
            transcoding_jobs.insert().values(
                video_id=video_id,
                started_at=datetime.now(timezone.utc),
                attempt_number=1,
            )
        )

        # Create the orphaned quality directory
        video_dir = videos_dir / "test-cleanup-active-job"
        video_dir.mkdir(parents=True, exist_ok=True)
        quality_dir = video_dir / "1080p"
        quality_dir.mkdir(exist_ok=True)
        (quality_dir / "test.m3u8").write_text("#EXTM3U\n")

        # Make directory old enough
        old_time = time.time() - 86400 * 2  # 2 days old
        os.utime(quality_dir, (old_time, old_time))

        # Run cleanup with patched database, VIDEOS_DIR, and _api_start_time
        import api.worker_api as worker_api_module
        with patch.object(worker_api_module, "database", test_database), \
             patch.object(worker_api_module, "VIDEOS_DIR", videos_dir), \
             patch.object(worker_api_module, "_api_start_time", past_startup_time):
            cleaned = await _cleanup_orphaned_quality_directories()

        # Verify directory was NOT removed (active job)
        assert quality_dir.exists(), "Directory with active job should not be removed"
        assert cleaned == 0

    @pytest.mark.asyncio
    async def test_cleanup_ignores_non_quality_directories(self, test_database, test_storage, past_startup_time):
        """Test that cleanup ignores directories that aren't quality names."""
        from api.database import videos
        from api.worker_api import _cleanup_orphaned_quality_directories

        videos_dir = test_storage["videos"]

        # Create a video in the database
        await test_database.execute(
            videos.insert().values(
                title="Test Video Non-Quality",
                slug="test-cleanup-non-quality",
                status="ready",
            )
        )

        # Create a non-quality directory
        video_dir = videos_dir / "test-cleanup-non-quality"
        video_dir.mkdir(parents=True, exist_ok=True)
        other_dir = video_dir / "thumbnails"  # Not a quality directory
        other_dir.mkdir(exist_ok=True)
        (other_dir / "thumb.jpg").write_bytes(b"fake image")

        # Make directory old enough
        old_time = time.time() - 86400 * 2  # 2 days old
        os.utime(other_dir, (old_time, old_time))

        # Run cleanup with patched database, VIDEOS_DIR, and _api_start_time
        import api.worker_api as worker_api_module
        with patch.object(worker_api_module, "database", test_database), \
             patch.object(worker_api_module, "VIDEOS_DIR", videos_dir), \
             patch.object(worker_api_module, "_api_start_time", past_startup_time):
            cleaned = await _cleanup_orphaned_quality_directories()

        # Verify directory was NOT removed (not a quality directory)
        assert other_dir.exists(), "Non-quality directory should not be removed"
        assert cleaned == 0


class TestConfigDefaults:
    """Test that configuration defaults are sensible."""

    def test_tar_extraction_timeout_default(self):
        """Test that TAR extraction timeout has a sensible default."""
        from config import TAR_EXTRACTION_TIMEOUT

        # Should be at least 60 seconds (1 minute)
        assert TAR_EXTRACTION_TIMEOUT >= 60
        # Should not be more than 1 hour
        assert TAR_EXTRACTION_TIMEOUT <= 3600

    def test_orphan_cleanup_settings(self):
        """Test that orphan cleanup settings have sensible defaults."""
        from config import (
            ORPHAN_CLEANUP_ENABLED,
            ORPHAN_CLEANUP_INTERVAL,
            ORPHAN_CLEANUP_MIN_AGE,
        )

        # Cleanup should be enabled by default
        assert ORPHAN_CLEANUP_ENABLED is True

        # Interval should be reasonable (at least 5 minutes)
        assert ORPHAN_CLEANUP_INTERVAL >= 300

        # Min age should be at least 1 hour
        assert ORPHAN_CLEANUP_MIN_AGE >= 3600
