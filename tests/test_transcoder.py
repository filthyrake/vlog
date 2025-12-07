"""
Tests for transcoder utility functions.
Tests pure functions that don't require ffmpeg.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from config import (
    FFMPEG_TIMEOUT_BASE_MULTIPLIER,
    FFMPEG_TIMEOUT_MAXIMUM,
    FFMPEG_TIMEOUT_MINIMUM,
    FFMPEG_TIMEOUT_RESOLUTION_MULTIPLIERS,
    MAX_RETRY_ATTEMPTS,
    QUALITY_PRESETS,
)
from worker.transcoder import (
    MAX_DURATION_SECONDS,
    calculate_ffmpeg_timeout,
    generate_master_playlist,
    get_applicable_qualities,
    validate_duration,
)


class TestValidateDuration:
    """Tests for duration validation."""

    def test_valid_duration_float(self):
        """Test valid float duration."""
        result = validate_duration(120.5)
        assert result == 120.5

    def test_valid_duration_int(self):
        """Test valid integer duration is converted to float."""
        result = validate_duration(60)
        assert result == 60.0
        assert isinstance(result, float)

    def test_valid_duration_string(self):
        """Test string duration is converted to float."""
        result = validate_duration("90.5")
        assert result == 90.5

    def test_none_duration_fails(self):
        """Test None duration raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_duration(None)
        assert "could not determine" in str(exc_info.value).lower()

    def test_zero_duration_fails(self):
        """Test zero duration raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_duration(0)
        assert "must be positive" in str(exc_info.value).lower()

    def test_negative_duration_fails(self):
        """Test negative duration raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_duration(-10)
        assert "must be positive" in str(exc_info.value).lower()

    def test_nan_duration_fails(self):
        """Test NaN duration raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_duration(float("nan"))
        assert "invalid duration value" in str(exc_info.value).lower()

    def test_inf_duration_fails(self):
        """Test infinite duration raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_duration(float("inf"))
        assert "invalid duration value" in str(exc_info.value).lower()

    def test_duration_too_long_fails(self):
        """Test duration exceeding max raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_duration(MAX_DURATION_SECONDS + 1)
        assert "too long" in str(exc_info.value).lower()

    def test_max_duration_is_valid(self):
        """Test max duration is accepted."""
        result = validate_duration(MAX_DURATION_SECONDS)
        assert result == MAX_DURATION_SECONDS

    def test_invalid_string_fails(self):
        """Test non-numeric string raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            validate_duration("not a number")
        assert "could not convert" in str(exc_info.value).lower()

    def test_small_duration_valid(self):
        """Test small positive duration is valid."""
        result = validate_duration(0.001)
        assert result == 0.001


class TestGetApplicableQualities:
    """Tests for quality selection based on source resolution."""

    def test_4k_source(self):
        """Test 4K source gets all quality levels."""
        qualities = get_applicable_qualities(2160)
        quality_names = [q["name"] for q in qualities]
        assert "2160p" in quality_names
        assert "1440p" in quality_names
        assert "1080p" in quality_names
        assert "720p" in quality_names
        assert "480p" in quality_names
        assert "360p" in quality_names

    def test_1080p_source(self):
        """Test 1080p source gets 1080p and below."""
        qualities = get_applicable_qualities(1080)
        quality_names = [q["name"] for q in qualities]
        assert "2160p" not in quality_names
        assert "1440p" not in quality_names
        assert "1080p" in quality_names
        assert "720p" in quality_names
        assert "480p" in quality_names
        assert "360p" in quality_names

    def test_720p_source(self):
        """Test 720p source gets 720p and below."""
        qualities = get_applicable_qualities(720)
        quality_names = [q["name"] for q in qualities]
        assert "1080p" not in quality_names
        assert "720p" in quality_names
        assert "480p" in quality_names
        assert "360p" in quality_names

    def test_480p_source(self):
        """Test 480p source gets 480p and below."""
        qualities = get_applicable_qualities(480)
        quality_names = [q["name"] for q in qualities]
        assert "720p" not in quality_names
        assert "480p" in quality_names
        assert "360p" in quality_names

    def test_360p_source(self):
        """Test 360p source only gets 360p."""
        qualities = get_applicable_qualities(360)
        quality_names = [q["name"] for q in qualities]
        assert quality_names == ["360p"]

    def test_below_360p_source(self):
        """Test very low resolution source gets no qualities."""
        qualities = get_applicable_qualities(240)
        assert qualities == []

    def test_non_standard_resolution(self):
        """Test non-standard resolution maps correctly."""
        # 900p should get 720p and below (not 1080p)
        qualities = get_applicable_qualities(900)
        quality_names = [q["name"] for q in qualities]
        assert "1080p" not in quality_names
        assert "720p" in quality_names

    def test_1440p_source(self):
        """Test 1440p source gets 1440p and below."""
        qualities = get_applicable_qualities(1440)
        quality_names = [q["name"] for q in qualities]
        assert "2160p" not in quality_names
        assert "1440p" in quality_names
        assert "1080p" in quality_names


class TestCalculateFfmpegTimeout:
    """Tests for ffmpeg timeout calculation."""

    def test_minimum_timeout(self):
        """Test short videos get minimum timeout."""
        # 10 second video * multiplier might be less than minimum
        timeout = calculate_ffmpeg_timeout(10, 1080)
        assert timeout >= FFMPEG_TIMEOUT_MINIMUM

    def test_maximum_timeout(self):
        """Test very long videos are capped at maximum."""
        # 10 hour video would exceed max timeout
        timeout = calculate_ffmpeg_timeout(36000, 2160)
        assert timeout <= FFMPEG_TIMEOUT_MAXIMUM

    def test_normal_duration_default_resolution(self):
        """Test normal video duration with default resolution (1080p)."""
        duration = 600  # 10 minutes
        res_mult = FFMPEG_TIMEOUT_RESOLUTION_MULTIPLIERS.get(1080, 2.0)
        expected = duration * FFMPEG_TIMEOUT_BASE_MULTIPLIER * res_mult
        timeout = calculate_ffmpeg_timeout(duration)

        # Should be multiplier * duration, clamped to min/max
        if expected < FFMPEG_TIMEOUT_MINIMUM:
            assert timeout == FFMPEG_TIMEOUT_MINIMUM
        elif expected > FFMPEG_TIMEOUT_MAXIMUM:
            assert timeout == FFMPEG_TIMEOUT_MAXIMUM
        else:
            assert timeout == expected

    def test_resolution_scaling(self):
        """Test that higher resolutions get longer timeouts."""
        duration = 600  # 10 minutes - short enough to avoid hitting max cap
        timeout_360p = calculate_ffmpeg_timeout(duration, 360)
        timeout_1080p = calculate_ffmpeg_timeout(duration, 1080)
        timeout_2160p = calculate_ffmpeg_timeout(duration, 2160)

        # Higher resolutions should have longer timeouts
        assert timeout_360p < timeout_1080p < timeout_2160p

    def test_all_resolutions_covered(self):
        """Test all quality presets have timeout multipliers."""
        for preset in QUALITY_PRESETS:
            height = preset["height"]
            assert height in FFMPEG_TIMEOUT_RESOLUTION_MULTIPLIERS, f"Missing timeout multiplier for {height}p"

    def test_zero_duration(self):
        """Test zero duration gets minimum timeout."""
        timeout = calculate_ffmpeg_timeout(0, 1080)
        assert timeout == FFMPEG_TIMEOUT_MINIMUM

    def test_unknown_resolution_uses_default(self):
        """Test unknown resolution uses default multiplier."""
        duration = 600
        timeout = calculate_ffmpeg_timeout(duration, 999)  # Non-standard height
        # Should use default multiplier of 2.0
        expected = duration * FFMPEG_TIMEOUT_BASE_MULTIPLIER * 2.0
        assert timeout == max(FFMPEG_TIMEOUT_MINIMUM, min(expected, FFMPEG_TIMEOUT_MAXIMUM))


class TestGenerateMasterPlaylist:
    """Tests for master playlist generation."""

    async def test_generate_basic_playlist(self, tmp_path: Path):
        """Test generating a basic master playlist."""
        qualities = [
            {"name": "1080p", "width": 1920, "height": 1080, "bitrate": "5000k"},
            {"name": "720p", "width": 1280, "height": 720, "bitrate": "2500k"},
        ]

        await generate_master_playlist(tmp_path, qualities)

        master_path = tmp_path / "master.m3u8"
        assert master_path.exists()

        content = master_path.read_text()
        assert "#EXTM3U" in content
        assert "#EXT-X-VERSION:3" in content
        assert "1080p.m3u8" in content
        assert "720p.m3u8" in content
        assert "BANDWIDTH=5000000" in content
        assert "BANDWIDTH=2500000" in content
        assert "RESOLUTION=1920x1080" in content
        assert "RESOLUTION=1280x720" in content

    async def test_generate_single_quality_playlist(self, tmp_path: Path):
        """Test generating playlist with single quality."""
        qualities = [
            {"name": "360p", "width": 640, "height": 360, "bitrate": "600k"},
        ]

        await generate_master_playlist(tmp_path, qualities)

        content = (tmp_path / "master.m3u8").read_text()
        assert "360p.m3u8" in content
        assert "BANDWIDTH=600000" in content

    async def test_qualities_sorted_by_bandwidth(self, tmp_path: Path):
        """Test that qualities are sorted by bandwidth (highest first)."""
        # Provide in random order
        qualities = [
            {"name": "480p", "width": 854, "height": 480, "bitrate": "1000k"},
            {"name": "1080p", "width": 1920, "height": 1080, "bitrate": "5000k"},
            {"name": "720p", "width": 1280, "height": 720, "bitrate": "2500k"},
        ]

        await generate_master_playlist(tmp_path, qualities)

        content = (tmp_path / "master.m3u8").read_text()
        lines = content.split("\n")

        # Find quality references in order
        quality_order = []
        for line in lines:
            if line.endswith(".m3u8"):
                quality_order.append(line)

        # Should be sorted by bandwidth descending
        assert quality_order == ["1080p.m3u8", "720p.m3u8", "480p.m3u8"]

    async def test_original_quality_with_bitrate_bps(self, tmp_path: Path):
        """Test original quality uses bitrate_bps field."""
        qualities = [
            {
                "name": "original",
                "width": 3840,
                "height": 2160,
                "bitrate": "0k",
                "bitrate_bps": 15000000,
                "is_original": True,
            },
            {"name": "1080p", "width": 1920, "height": 1080, "bitrate": "5000k"},
        ]

        await generate_master_playlist(tmp_path, qualities)

        content = (tmp_path / "master.m3u8").read_text()
        assert "original.m3u8" in content
        assert "BANDWIDTH=15000000" in content  # From bitrate_bps

    async def test_empty_qualities_creates_minimal_playlist(self, tmp_path: Path):
        """Test empty qualities list creates minimal playlist."""
        await generate_master_playlist(tmp_path, [])

        content = (tmp_path / "master.m3u8").read_text()
        assert "#EXTM3U" in content
        assert "#EXT-X-VERSION:3" in content
        # No quality entries
        assert ".m3u8" not in content.split("\n")[2]  # After header

    async def test_validates_dimensions_from_segments(self, tmp_path: Path):
        """Test that actual dimensions are read from segment files when they exist."""
        # Create fake segment files
        segment_720p = tmp_path / "720p_0000.ts"
        segment_480p = tmp_path / "480p_0000.ts"
        segment_720p.write_bytes(b"fake video data")
        segment_480p.write_bytes(b"fake video data")

        # Quality presets with incorrect dimensions
        # (simulating aspect ratio differences)
        qualities = [
            {"name": "720p", "width": 1280, "height": 720, "bitrate": "2500k"},  # Preset says 1280x720
            {"name": "480p", "width": 854, "height": 480, "bitrate": "1000k"},   # Preset says 854x480
        ]

        # Mock get_output_dimensions to return actual dimensions
        # Simulating a 2.4:1 aspect ratio source (like 1920x800)
        # When scaled to 720p height: width = 720 * 2.4 = 1728 (rounded to even)
        # When scaled to 480p height: width = 480 * 2.4 = 1152
        async def mock_get_dimensions(path):
            if "720p" in str(path):
                return (1728, 720)  # Actual output is wider than preset
            elif "480p" in str(path):
                return (1152, 480)  # Actual output is wider than preset
            return (0, 0)

        with patch('worker.transcoder.get_output_dimensions', new=mock_get_dimensions):
            await generate_master_playlist(tmp_path, qualities)

        content = (tmp_path / "master.m3u8").read_text()

        # Verify the playlist uses actual dimensions, not preset dimensions
        assert "RESOLUTION=1728x720" in content  # Should use actual width from segment
        assert "RESOLUTION=1152x480" in content  # Should use actual width from segment
        assert "RESOLUTION=1280x720" not in content  # Should NOT use preset width
        assert "RESOLUTION=854x480" not in content   # Should NOT use preset width

    async def test_falls_back_to_preset_dimensions_when_segment_missing(self, tmp_path: Path):
        """Test that preset dimensions are used when segment files don't exist."""
        # Don't create any segment files
        qualities = [
            {"name": "720p", "width": 1280, "height": 720, "bitrate": "2500k"},
        ]

        await generate_master_playlist(tmp_path, qualities)

        content = (tmp_path / "master.m3u8").read_text()

        # Should use preset dimensions since no segments exist
        assert "RESOLUTION=1280x720" in content

    async def test_handles_ffprobe_failure_gracefully(self, tmp_path: Path):
        """Test that ffprobe failures fall back to preset dimensions."""
        # Create a segment file
        segment_720p = tmp_path / "720p_0000.ts"
        segment_720p.write_bytes(b"corrupted video data")

        qualities = [
            {"name": "720p", "width": 1280, "height": 720, "bitrate": "2500k"},
        ]

        # Mock get_output_dimensions to return (0, 0) indicating failure
        async def mock_get_dimensions_fail(path):
            return (0, 0)

        with patch('worker.transcoder.get_output_dimensions', new=mock_get_dimensions_fail):
            await generate_master_playlist(tmp_path, qualities)

        content = (tmp_path / "master.m3u8").read_text()

        # Should fall back to preset dimensions when ffprobe fails
        assert "RESOLUTION=1280x720" in content


class TestQualityPresets:
    """Tests for quality preset configuration."""

    def test_presets_exist(self):
        """Test that quality presets are defined."""
        assert len(QUALITY_PRESETS) > 0

    def test_presets_have_required_fields(self):
        """Test all presets have required fields."""
        for preset in QUALITY_PRESETS:
            assert "name" in preset
            assert "height" in preset
            assert "bitrate" in preset
            assert "audio_bitrate" in preset

    def test_presets_sorted_by_height(self):
        """Test presets are sorted by height descending."""
        heights = [p["height"] for p in QUALITY_PRESETS]
        assert heights == sorted(heights, reverse=True)

    def test_preset_heights_are_standard(self):
        """Test preset heights are standard resolutions."""
        expected_heights = {2160, 1440, 1080, 720, 480, 360}
        actual_heights = {p["height"] for p in QUALITY_PRESETS}
        assert actual_heights == expected_heights

    def test_bitrate_format(self):
        """Test bitrates are in correct format."""
        for preset in QUALITY_PRESETS:
            assert preset["bitrate"].endswith("k")
            # Should be convertible to int after removing 'k'
            bitrate_value = int(preset["bitrate"].replace("k", ""))
            assert bitrate_value > 0


class TestConfigConstants:
    """Tests for configuration constants."""

    def test_retry_settings(self):
        """Test retry configuration is sensible."""
        assert MAX_RETRY_ATTEMPTS >= 1
        assert MAX_RETRY_ATTEMPTS <= 10  # Reasonable upper bound

    def test_timeout_settings(self):
        """Test timeout configuration is sensible."""
        assert FFMPEG_TIMEOUT_MINIMUM > 0
        assert FFMPEG_TIMEOUT_MAXIMUM > FFMPEG_TIMEOUT_MINIMUM
        assert FFMPEG_TIMEOUT_BASE_MULTIPLIER > 0
        # All resolution multipliers should be positive
        for height, mult in FFMPEG_TIMEOUT_RESOLUTION_MULTIPLIERS.items():
            assert mult > 0, f"Invalid multiplier for {height}p"

    def test_max_duration(self):
        """Test max duration is reasonable."""
        # Should be at least a few hours
        assert MAX_DURATION_SECONDS >= 3600
        # Should be less than a month
        assert MAX_DURATION_SECONDS <= 30 * 24 * 3600
