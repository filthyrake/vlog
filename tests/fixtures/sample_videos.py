"""
Test fixtures for creating sample video files.

Provides helpers to generate minimal valid video files for testing
without requiring actual video content or ffmpeg in unit tests.
"""

from pathlib import Path
from typing import Optional


def create_minimal_mp4(size_bytes: int = 1024) -> bytes:
    """
    Create minimal valid MP4 file data for testing.

    This is a minimal MP4 container with required atoms.
    Not playable but sufficient for upload/storage tests.

    Args:
        size_bytes: Approximate size in bytes

    Returns:
        Bytes representing a minimal MP4 file
    """
    """
    Creates a minimal but valid MP4 container structure with required atoms.
    The file is not playable (no actual media data), but it's valid for:
    - File type detection and validation
    - Upload and storage tests
    - Metadata parsing tests
    """
    # MP4 file signature (ftyp atom)
    ftyp = b"\x00\x00\x00\x20"  # size
    ftyp += b"ftyp"  # type
    ftyp += b"isom"  # major brand
    ftyp += b"\x00\x00\x02\x00"  # minor version
    ftyp += b"isomiso2mp41"  # compatible brands

    # moov atom (movie header)
    moov = b"\x00\x00\x00\x08"  # size
    moov += b"moov"  # type

    # mdat atom (media data) - pad to desired size
    remaining = max(0, size_bytes - len(ftyp) - len(moov) - 8)
    mdat = (remaining + 8).to_bytes(4, byteorder="big")
    mdat += b"mdat"
    mdat += b"\x00" * remaining

    return ftyp + moov + mdat


def create_sample_hls_playlist(
    video_dir: Path,
    qualities: Optional[list] = None
) -> Path:
    """
    Create sample HLS playlist files for testing.

    Args:
        video_dir: Directory where HLS files should be created
        qualities: List of quality names (default: ["720p", "480p"])

    Returns:
        Path to master playlist
    """
    if qualities is None:
        qualities = ["720p", "480p"]

    video_dir.mkdir(parents=True, exist_ok=True)

    # Create master playlist
    master_content = "#EXTM3U\n#EXT-X-VERSION:3\n"
    for quality in qualities:
        if quality == "720p":
            master_content += "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n"
        elif quality == "480p":
            master_content += "#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=854x480\n"
        elif quality == "1080p":
            master_content += "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n"
        else:
            master_content += "#EXT-X-STREAM-INF:BANDWIDTH=1000000\n"
        master_content += f"{quality}.m3u8\n"

    master_path = video_dir / "master.m3u8"
    master_path.write_text(master_content)

    # Create quality playlists
    for quality in qualities:
        playlist_content = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-TARGETDURATION:6\n"
            "#EXTINF:6.0,\n"
            f"{quality}_0000.ts\n"
            "#EXTINF:6.0,\n"
            f"{quality}_0001.ts\n"
            "#EXT-X-ENDLIST\n"
        )
        (video_dir / f"{quality}.m3u8").write_text(playlist_content)

        # Create segment files
        (video_dir / f"{quality}_0000.ts").write_bytes(b"fake_segment_0")
        (video_dir / f"{quality}_0001.ts").write_bytes(b"fake_segment_1")

    # Create thumbnail
    (video_dir / "thumbnail.jpg").write_bytes(b"fake_thumbnail_jpg_data")

    return master_path
