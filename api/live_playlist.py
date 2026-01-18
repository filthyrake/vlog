"""HLS playlist generation for live streams.

Generates master and variant playlists for HLS playback:
- Master playlist: Lists all available quality variants
- Variant playlist: Lists segments with proper timing

Design decisions (from Ada/Brendan reviews):
- Playlists are written to disk on each segment upload for performance
- Static file serving via nginx/Starlette is more scalable than dynamic generation
- DVR window is respected in playlist generation
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import sqlalchemy as sa

from api.database import database, live_stream_segments, live_streams
from api.db_retry import fetch_all_with_retry, fetch_one_with_retry
from config import (
    LIVE_HLS_PLAYLIST_LENGTH,
    LIVE_HLS_SEGMENT_DURATION,
    LIVE_STORAGE_PATH,
    QUALITY_PRESETS,
)

logger = logging.getLogger(__name__)

# Quality to bandwidth mapping (approximate, based on QUALITY_PRESETS)
QUALITY_BANDWIDTH = {
    "2160p": 15000000,  # 15 Mbps
    "1440p": 8000000,   # 8 Mbps
    "1080p": 5000000,   # 5 Mbps
    "720p": 2500000,    # 2.5 Mbps
    "480p": 1000000,    # 1 Mbps
    "360p": 600000,     # 600 Kbps
}

# Quality to resolution mapping
QUALITY_RESOLUTION = {
    "2160p": (3840, 2160),
    "1440p": (2560, 1440),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
    "480p": (854, 480),
    "360p": (640, 360),
}


def generate_master_playlist(qualities: List[str], slug: str) -> str:
    """
    Generate HLS master playlist for a live stream.

    Args:
        qualities: List of available quality names (e.g., ["720p", "480p"])
        slug: Stream slug for URL generation

    Returns:
        Master playlist content as string
    """
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        "",
    ]

    # Sort qualities by bandwidth (highest first for adaptive streaming)
    sorted_qualities = sorted(
        qualities,
        key=lambda q: QUALITY_BANDWIDTH.get(q, 0),
        reverse=True,
    )

    for quality in sorted_qualities:
        bandwidth = QUALITY_BANDWIDTH.get(quality, 2500000)
        resolution = QUALITY_RESOLUTION.get(quality, (1280, 720))

        lines.extend([
            f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={resolution[0]}x{resolution[1]},CODECS="avc1.64001f,mp4a.40.2",NAME="{quality}"',
            f"{quality}/stream.m3u8",
            "",
        ])

    return "\n".join(lines)


def generate_variant_playlist(
    segments: List[dict],
    media_sequence: int,
    target_duration: int,
    is_live: bool = True,
) -> str:
    """
    Generate HLS variant playlist for a specific quality.

    Args:
        segments: List of segment records with sequence_number, duration_ms, filename
        media_sequence: Media sequence number for first segment
        target_duration: Target segment duration in seconds
        is_live: Whether stream is live (affects playlist type)

    Returns:
        Variant playlist content as string
    """
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        f"#EXT-X-MEDIA-SEQUENCE:{media_sequence}",
    ]

    if is_live:
        # Live stream - no end tag
        lines.append("#EXT-X-PLAYLIST-TYPE:EVENT")
    else:
        # VOD - add end tag
        lines.append("#EXT-X-PLAYLIST-TYPE:VOD")

    # Add init segment
    lines.extend([
        "",
        '#EXT-X-MAP:URI="init.mp4"',
        "",
    ])

    for segment in segments:
        # Calculate duration in seconds
        duration_ms = segment.get("duration_ms") or (target_duration * 1000)
        duration = duration_ms / 1000.0

        lines.extend([
            f"#EXTINF:{duration:.3f},",
            segment["filename"],
        ])

    if not is_live:
        lines.append("#EXT-X-ENDLIST")

    return "\n".join(lines)


async def write_playlist_atomic(dest_path: Path, content: str) -> None:
    """
    Write playlist atomically using tempfile and rename.

    This ensures readers never see partial playlists.
    Uses fsync for durability on NAS.
    """
    loop = asyncio.get_event_loop()

    def _write():
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file in same directory
        fd, temp_path = tempfile.mkstemp(dir=str(dest_path.parent), suffix=".m3u8.tmp")
        fd_closed = False
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            fd_closed = True
            # Atomic rename
            os.rename(temp_path, str(dest_path))
        except Exception:
            if not fd_closed:
                os.close(fd)
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    await loop.run_in_executor(None, _write)


async def update_master_playlist(stream_id: int, slug: str) -> None:
    """
    Update master playlist for a stream.

    Should be called when a new quality variant is first seen.
    """
    # Get stream to get qualities
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )

    if not stream:
        logger.warning(f"Stream {stream_id} not found for master playlist update")
        return

    if not stream["qualities"]:
        logger.debug(f"No qualities yet for stream {slug}")
        return

    try:
        qualities = json.loads(stream["qualities"])
    except json.JSONDecodeError:
        logger.warning(f"Invalid qualities JSON for stream {slug}")
        return

    if not qualities:
        return

    content = generate_master_playlist(qualities, slug)
    dest_path = LIVE_STORAGE_PATH / slug / "master.m3u8"

    await write_playlist_atomic(dest_path, content)
    logger.debug(f"Updated master playlist for {slug} with qualities: {qualities}")


async def update_variant_playlist(
    stream_id: int,
    slug: str,
    quality: str,
    dvr_window_seconds: int,
) -> None:
    """
    Update variant playlist for a specific quality.

    Should be called after each segment upload.

    Args:
        stream_id: Database ID of the stream
        slug: Stream slug
        quality: Quality name (e.g., "720p")
        dvr_window_seconds: DVR window in seconds (0 = unlimited)
    """
    # Calculate DVR window cutoff
    now = datetime.now(timezone.utc)
    cutoff = None
    if dvr_window_seconds > 0:
        cutoff = now.timestamp() - dvr_window_seconds

    # Fetch segments for this quality
    query = (
        live_stream_segments.select()
        .where(live_stream_segments.c.stream_id == stream_id)
        .where(live_stream_segments.c.quality == quality)
        .order_by(live_stream_segments.c.sequence_number)
    )

    segments = await fetch_all_with_retry(query)

    if not segments:
        logger.debug(f"No segments yet for {slug}/{quality}")
        return

    # Apply DVR window filter
    if cutoff:
        filtered_segments = []
        for seg in segments:
            received_at = seg["received_at"]
            if received_at and received_at.timestamp() >= cutoff:
                filtered_segments.append(dict(seg))
        segments = filtered_segments
    else:
        segments = [dict(seg) for seg in segments]

    if not segments:
        return

    # Limit to playlist length
    if len(segments) > LIVE_HLS_PLAYLIST_LENGTH:
        segments = segments[-LIVE_HLS_PLAYLIST_LENGTH:]

    # Get media sequence (first segment's sequence number)
    media_sequence = segments[0]["sequence_number"]

    # Get stream status
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )
    is_live = stream and stream["status"] in ("live", "ending")

    content = generate_variant_playlist(
        segments,
        media_sequence,
        LIVE_HLS_SEGMENT_DURATION,
        is_live=is_live,
    )

    dest_path = LIVE_STORAGE_PATH / slug / quality / "stream.m3u8"
    await write_playlist_atomic(dest_path, content)
    logger.debug(f"Updated variant playlist for {slug}/{quality} with {len(segments)} segments")


async def update_all_playlists_for_stream(stream_id: int) -> None:
    """
    Update master and all variant playlists for a stream.

    Useful when stream status changes or for periodic refresh.
    """
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )

    if not stream:
        return

    slug = stream["slug"]
    dvr_window = stream["dvr_window_seconds"]

    # Update master playlist
    await update_master_playlist(stream_id, slug)

    # Get qualities
    if not stream["qualities"]:
        return

    try:
        qualities = json.loads(stream["qualities"])
    except json.JSONDecodeError:
        return

    # Update each variant playlist
    for quality in qualities:
        await update_variant_playlist(stream_id, slug, quality, dvr_window)


async def finalize_playlists_for_vod(stream_id: int) -> None:
    """
    Finalize playlists for VOD by adding #EXT-X-ENDLIST.

    Called when stream ends and VOD recording is triggered.
    """
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )

    if not stream:
        return

    slug = stream["slug"]

    if not stream["qualities"]:
        return

    try:
        qualities = json.loads(stream["qualities"])
    except json.JSONDecodeError:
        return

    # Update each variant playlist with is_live=False
    for quality in qualities:
        # Fetch ALL segments for VOD (no DVR window)
        segments = await fetch_all_with_retry(
            live_stream_segments.select()
            .where(live_stream_segments.c.stream_id == stream_id)
            .where(live_stream_segments.c.quality == quality)
            .order_by(live_stream_segments.c.sequence_number)
        )

        if not segments:
            continue

        segments = [dict(seg) for seg in segments]
        media_sequence = segments[0]["sequence_number"]

        content = generate_variant_playlist(
            segments,
            media_sequence,
            LIVE_HLS_SEGMENT_DURATION,
            is_live=False,  # VOD mode
        )

        dest_path = LIVE_STORAGE_PATH / slug / quality / "stream.m3u8"
        await write_playlist_atomic(dest_path, content)

    logger.info(f"Finalized VOD playlists for stream {slug}")


async def get_playlist_segments(
    stream_id: int,
    quality: str,
) -> Tuple[List[dict], int]:
    """
    Get segments for playlist generation.

    Returns:
        Tuple of (segments list, media sequence number)
    """
    segments = await fetch_all_with_retry(
        live_stream_segments.select()
        .where(live_stream_segments.c.stream_id == stream_id)
        .where(live_stream_segments.c.quality == quality)
        .order_by(live_stream_segments.c.sequence_number)
    )

    if not segments:
        return [], 0

    segments = [dict(seg) for seg in segments]
    media_sequence = segments[0]["sequence_number"] if segments else 0

    return segments, media_sequence
